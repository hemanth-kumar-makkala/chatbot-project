"""PostgreSQL hybrid search adapter combining pgvector dense search
and tsvector full-text search via RRF.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.db.session import retriever_tenant_session
from app.ports.embedding_provider import EmbeddingProvider
from app.ports.retriever import Retriever
from app.ports.types import (
    EvidenceId,
    EvidenceType,
    RetrievalFilters,
    TenantContext,
)


class PostgresRetriever(Retriever):
    """Hybrid retriever combining pgvector dense search and tsvector text search with RRF."""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        session_factory: (Callable[[TenantContext], Any] | None) = None,
        rrf_k: int = 60,
        default_limit: int = 10,
        candidate_pool_size: int = 20,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._session_factory = session_factory or retriever_tenant_session
        self._rrf_k = rrf_k
        self._default_limit = default_limit
        self._candidate_pool_size = candidate_pool_size

    async def search(
        self,
        tenant_ctx: TenantContext,
        query: str,
        filters: RetrievalFilters | None = None,
    ) -> list[EvidenceId]:
        """Perform tenant-filtered hybrid search returning exact evidence IDs ranked by RRF."""
        tid = UUID(str(tenant_ctx.tenant_id))
        limit = filters.limit if (filters and filters.limit) else self._default_limit
        pool_size = max(self._candidate_pool_size, limit * 2)

        # 1. Embed query text for dense similarity
        query_embs = await self._embedding_provider.embed([query])
        query_emb = query_embs[0]
        emb_str = f"[{','.join(str(x) for x in query_emb)}]"

        # 2. Build filter conditions
        extra_clauses: list[str] = []
        params: dict[str, Any] = {
            "tenant_id": tid,
            "pool_size": pool_size,
            "emb": emb_str,
            "query": query.strip(),
        }

        if filters and filters.document_ids:
            doc_uuids = [UUID(str(d)) for d in filters.document_ids]
            extra_clauses.append(
                """
                AND c.document_version_id IN (
                    SELECT dv.id FROM document_versions dv
                    WHERE dv.tenant_id = :tenant_id
                      AND dv.document_id = ANY(:filter_doc_ids)
                )
                """
            )
            params["filter_doc_ids"] = doc_uuids

        if filters and filters.evidence_types:
            evidence_types_str = [
                et.value if hasattr(et, "value") else str(et) for et in filters.evidence_types
            ]
            extra_clauses.append("AND c.evidence_type = ANY(:filter_evidence_types)")
            params["filter_evidence_types"] = evidence_types_str

        extra_where = " ".join(extra_clauses)

        # 3. Dense pgvector query
        dense_sql = f"""
            SELECT
                c.id,
                c.page_id,
                c.document_version_id,
                c.evidence_type,
                c.text,
                c.context_prefix,
                (c.embedding <=> CAST(:emb AS vector)) AS distance
            FROM chunks c
            WHERE c.tenant_id = :tenant_id
              AND c.is_active = true
              AND c.embedding IS NOT NULL
              {extra_where}
            ORDER BY distance ASC
            LIMIT :pool_size;
        """

        # 4. Sparse full-text query
        sparse_sql = f"""
            SELECT
                c.id,
                c.page_id,
                c.document_version_id,
                c.evidence_type,
                c.text,
                c.context_prefix,
                ts_rank_cd(c.tsv, plainto_tsquery('english', :query)) AS rank
            FROM chunks c
            WHERE c.tenant_id = :tenant_id
              AND c.is_active = true
              AND c.tsv @@ plainto_tsquery('english', :query)
              {extra_where}
            ORDER BY rank DESC
            LIMIT :pool_size;
        """

        dense_rows: list[dict[str, Any]] = []
        sparse_rows: list[dict[str, Any]] = []

        async with self._session_factory(tenant_ctx) as session:
            # Execute dense search
            res_dense = await session.execute(text(dense_sql), params)
            dense_rows = [dict(r) for r in res_dense.mappings().all()]

            # Execute sparse search if query has non-whitespace terms
            if query.strip():
                res_sparse = await session.execute(text(sparse_sql), params)
                sparse_rows = [dict(r) for r in res_sparse.mappings().all()]

        # 5. Reciprocal Rank Fusion (RRF)
        rrf_scores: dict[UUID, float] = {}
        candidate_meta: dict[UUID, dict[str, Any]] = {}

        for rank, row in enumerate(dense_rows, start=1):
            cid = row["id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (self._rrf_k + rank))
            candidate_meta[cid] = row

        for rank, row in enumerate(sparse_rows, start=1):
            cid = row["id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (self._rrf_k + rank))
            candidate_meta[cid] = row

        sorted_cids = sorted(
            rrf_scores.keys(),
            key=lambda cid: rrf_scores[cid],
            reverse=True,
        )

        top_cids = sorted_cids[:limit]

        return [
            EvidenceId(
                chunk_id=str(cid),
                page_id=str(candidate_meta[cid]["page_id"]),
                document_version_id=str(candidate_meta[cid]["document_version_id"]),
                evidence_type=EvidenceType(candidate_meta[cid]["evidence_type"]),
            )
            for cid in top_cids
        ]

    async def search_titles(
        self,
        tenant_ctx: TenantContext,
        query: str,
        limit: int = 3,
    ) -> list[str]:
        """Search tenant document titles and section headings for clarify grounding (ADR-0002)."""
        tid = UUID(str(tenant_ctx.tenant_id))
        clean_query = query.strip()
        pattern = f"%{clean_query}%" if clean_query else "%"

        async with self._session_factory(tenant_ctx) as session:
            stmt = text(
                """
                SELECT d.title
                FROM documents d
                WHERE d.tenant_id = :tenant_id
                  AND d.is_retired = false
                  AND (
                      d.title ILIKE :pattern
                      OR to_tsvector('english', d.title) @@ plainto_tsquery('english', :query)
                  )
                ORDER BY
                    CASE WHEN d.title ILIKE :pattern THEN 0 ELSE 1 END,
                    ts_rank_cd(
                        to_tsvector('english', d.title),
                        plainto_tsquery('english', :query)
                    ) DESC,
                    d.created_at DESC
                LIMIT :limit;
                """
            )
            res = await session.execute(
                stmt,
                {
                    "tenant_id": tid,
                    "pattern": pattern,
                    "query": clean_query,
                    "limit": limit,
                },
            )
            titles = [str(r[0]) for r in res.fetchall()]

            if len(titles) < limit:
                fallback_stmt = text(
                    """
                    SELECT d.title
                    FROM documents d
                    WHERE d.tenant_id = :tenant_id
                      AND d.is_retired = false
                      AND d.title != ALL(:existing)
                    ORDER BY d.created_at DESC
                    LIMIT :remaining;
                    """
                )
                res_fb = await session.execute(
                    fallback_stmt,
                    {
                        "tenant_id": tid,
                        "existing": titles,
                        "remaining": limit - len(titles),
                    },
                )
                titles.extend(str(r[0]) for r in res_fb.fetchall())

            return titles[:limit]

    async def get_evidence_text(
        self,
        tenant_ctx: TenantContext,
        evidence_ids: list[str],
    ) -> dict[str, str]:
        """Fetch chunk texts and context prefixes for given evidence IDs within tenant scope."""
        if not evidence_ids:
            return {}

        tid = UUID(str(tenant_ctx.tenant_id))
        chunk_uuids: list[UUID] = []
        for eid in evidence_ids:
            try:
                chunk_uuids.append(UUID(str(eid)))
            except (ValueError, TypeError):
                continue

        if not chunk_uuids:
            return {}

        async with self._session_factory(tenant_ctx) as session:
            stmt = text(
                """
                SELECT c.id, c.text, c.context_prefix
                FROM chunks c
                WHERE c.tenant_id = :tenant_id
                  AND c.id = ANY(:chunk_ids);
                """
            )
            res = await session.execute(stmt, {"tenant_id": tid, "chunk_ids": chunk_uuids})
            rows = res.mappings().all()

            results: dict[str, str] = {}
            for row in rows:
                cid_str = str(row["id"])
                pfx = row["context_prefix"]
                body = row["text"]
                if pfx:
                    results[cid_str] = f"{pfx}\n\n{body}"
                else:
                    results[cid_str] = body

            return results
