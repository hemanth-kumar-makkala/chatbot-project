"""Integration tests for hybrid retrieval, RRF candidate merging, and tenant isolation (Task 3.1).

Verifies:
1. Retriever session connects via read-only role retriever_user (Invariant 1).
   - SELECT queries execute successfully.
   - Any INSERT/UPDATE/DELETE raises DBAPIError with SQLSTATE 42501 (permission denied).
2. Cross-tenant isolation (Invariant 2).
   - Tenant A search returns only Tenant A chunks.
   - Tenant B chunks are never returned even with identical query text.
3. Active chunks only.
   - Inactive chunks (is_active=False) are never returned in search results.
4. Dense vector search ordering (cosine distance via pgvector).
5. Sparse full-text keyword search ranking (tsvector via plainto_tsquery).
6. Reciprocal Rank Fusion (RRF) combining dense and sparse modalities.
7. Fallback when one modality produces 0 hits (dense-only or sparse-only).
8. Filters enforcement:
   - document_ids filter scopes search to specified documents.
   - evidence_types filter scopes search to specified evidence types.
   - limit parameter limits returned candidate count.
9. Clarify topic grounding (search_titles):
   - Returns grounded document titles within tenant boundary.
10. Chunk text loading (get_evidence_text):
   - Loads formatted chunk text for requested IDs within tenant boundary.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.adapters.embedding.mock import MockEmbeddingProvider
from app.adapters.retrieval.postgres_retriever import PostgresRetriever
from app.db.models import Membership, Tenant, User
from app.db.session import retriever_tenant_session, tenant_session, unscoped_session
from app.ports.types import (
    EvidenceType,
    RetrievalFilters,
    TenantContext,
    UserRole,
)
from tests.fixtures.database_seeding import (
    seed_chunk,
    seed_document,
    seed_page,
    seed_version,
)


def get_sqlstate(exc: BaseException) -> str | None:
    """Extract 5-character SQLSTATE code from SQLAlchemy DBAPIError or underlying driver."""
    orig: Any = getattr(exc, "orig", exc)
    raw_state: Any = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return str(raw_state) if raw_state is not None else None


@pytest.fixture
async def tenant_pair(test_user_password_hash: str) -> tuple[TenantContext, TenantContext]:
    """Provides two distinct isolated tenants with seeded User and Membership records."""
    t_a_id = uuid4()
    t_b_id = uuid4()
    u_a_id = uuid4()
    u_b_id = uuid4()

    async with unscoped_session() as session:
        session.add_all(
            [
                Tenant(id=t_a_id, name=f"Tenant-A-{t_a_id}"),
                Tenant(id=t_b_id, name=f"Tenant-B-{t_b_id}"),
                User(
                    id=u_a_id,
                    email=f"user-a-{u_a_id}@example.com",
                    hashed_password=test_user_password_hash,
                    is_platform_admin=False,
                ),
                User(
                    id=u_b_id,
                    email=f"user-b-{u_b_id}@example.com",
                    hashed_password=test_user_password_hash,
                    is_platform_admin=False,
                ),
            ]
        )
        await session.commit()

    ctx_a = TenantContext(
        tenant_id=str(t_a_id),
        user_id=str(u_a_id),
        role=UserRole.STAFF,
        session_id=f"sess-a-{uuid4()}",
    )
    ctx_b = TenantContext(
        tenant_id=str(t_b_id),
        user_id=str(u_b_id),
        role=UserRole.STAFF,
        session_id=f"sess-b-{uuid4()}",
    )

    async with tenant_session(ctx_a) as session:
        session.add(
            Membership(
                id=uuid4(),
                tenant_id=t_a_id,
                user_id=u_a_id,
                role="staff",
            )
        )
        await session.commit()

    async with tenant_session(ctx_b) as session:
        session.add(
            Membership(
                id=uuid4(),
                tenant_id=t_b_id,
                user_id=u_b_id,
                role="staff",
            )
        )
        await session.commit()

    return ctx_a, ctx_b


@pytest.mark.asyncio
class TestHybridRetrieval:
    """Verifies hybrid retrieval, RRF merging, and tenant boundaries."""

    async def test_retriever_user_cannot_write_invariant_1(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """Retriever session connects via read-only role retriever_user; writes fail with 42501."""
        ctx_a, _ = tenant_pair

        # 1. Seed document and page as app_user
        doc_id = uuid4()
        ver_id = uuid4()
        async with tenant_session(ctx_a) as session_app:
            await seed_document(session_app, ctx_a.tenant_id, doc_id=doc_id, title="Doc 1")
            await seed_version(session_app, ctx_a.tenant_id, doc_id, ctx_a.user_id, ver_id=ver_id)

        # 2. Retriever session: SELECT succeeds
        async with retriever_tenant_session(ctx_a) as session_ret:
            res = await session_ret.execute(text("SELECT COUNT(*) FROM chunks;"))
            assert isinstance(res.scalar_one(), int)

            # INSERT on chunks fails with 42501
            with pytest.raises(DBAPIError) as exc_insert:
                async with session_ret.begin_nested():
                    await seed_chunk(
                        session_ret,
                        ctx_a.tenant_id,
                        uuid4(),
                        ver_id,
                        body="Illegal insert from retriever",
                    )
            assert get_sqlstate(exc_insert.value) == "42501"
            assert "permission denied" in str(exc_insert.value).lower()

            # UPDATE on documents fails with 42501
            with pytest.raises(DBAPIError) as exc_update:
                async with session_ret.begin_nested():
                    await session_ret.execute(
                        text("UPDATE documents SET title = 'Altered' WHERE id = :id;"),
                        {"id": doc_id},
                    )
            assert get_sqlstate(exc_update.value) == "42501"
            assert "permission denied" in str(exc_update.value).lower()

            # DELETE on documents fails with 42501
            with pytest.raises(DBAPIError) as exc_delete:
                async with session_ret.begin_nested():
                    await session_ret.execute(
                        text("DELETE FROM documents WHERE id = :id;"),
                        {"id": doc_id},
                    )
            assert get_sqlstate(exc_delete.value) == "42501"
            assert "permission denied" in str(exc_delete.value).lower()

    async def test_hybrid_search_tenant_isolation(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """Tenant A search returns only Tenant A chunks; Tenant B chunks are never returned."""
        ctx_a, ctx_b = tenant_pair
        embedder = MockEmbeddingProvider()
        retriever = PostgresRetriever(embedding_provider=embedder)

        query = "patient referral guidelines"
        emb = (await embedder.embed([query]))[0]

        # Seed active chunk in Tenant A
        async with tenant_session(ctx_a) as session_a:
            doc_a = await seed_document(session_a, ctx_a.tenant_id, title="Tenant A Guidelines")
            ver_a = await seed_version(session_a, ctx_a.tenant_id, doc_a, ctx_a.user_id)
            pg_a = await seed_page(session_a, ctx_a.tenant_id, ver_a)
            chunk_a_id = await seed_chunk(
                session_a,
                ctx_a.tenant_id,
                pg_a,
                ver_a,
                body="Tenant A patient referral guidelines and policy details.",
                is_active=True,
                embedding=emb,
            )

        # Seed active chunk in Tenant B with identical text and embedding
        async with tenant_session(ctx_b) as session_b:
            doc_b = await seed_document(session_b, ctx_b.tenant_id, title="Tenant B Guidelines")
            ver_b = await seed_version(session_b, ctx_b.tenant_id, doc_b, ctx_b.user_id)
            pg_b = await seed_page(session_b, ctx_b.tenant_id, ver_b)
            chunk_b_id = await seed_chunk(
                session_b,
                ctx_b.tenant_id,
                pg_b,
                ver_b,
                body="Tenant B patient referral guidelines and policy details.",
                is_active=True,
                embedding=emb,
            )

        # Search as Tenant A
        results_a = await retriever.search(ctx_a, query)
        chunk_ids_a = [r.chunk_id for r in results_a]
        assert str(chunk_a_id) in chunk_ids_a
        assert str(chunk_b_id) not in chunk_ids_a

        # Search as Tenant B
        results_b = await retriever.search(ctx_b, query)
        chunk_ids_b = [r.chunk_id for r in results_b]
        assert str(chunk_b_id) in chunk_ids_b
        assert str(chunk_a_id) not in chunk_ids_b

    async def test_hybrid_search_active_chunks_only(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """Inactive chunks (is_active=False) are never returned in search results."""
        ctx_a, _ = tenant_pair
        embedder = MockEmbeddingProvider()
        retriever = PostgresRetriever(embedding_provider=embedder)

        query = "annual vaccination protocol"
        emb = (await embedder.embed([query]))[0]

        async with tenant_session(ctx_a) as session:
            doc = await seed_document(session, ctx_a.tenant_id, title="Vaccines")
            ver_old = await seed_version(
                session, ctx_a.tenant_id, doc, ctx_a.user_id, status="retired"
            )
            ver_new = await seed_version(
                session, ctx_a.tenant_id, doc, ctx_a.user_id, status="ready"
            )
            pg_old = await seed_page(session, ctx_a.tenant_id, ver_old)
            pg_new = await seed_page(session, ctx_a.tenant_id, ver_new)

            # Inactive chunk (retired version)
            old_chunk_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                pg_old,
                ver_old,
                body="Old annual vaccination protocol and guidelines.",
                is_active=False,
                embedding=emb,
            )
            # Active chunk (ready version)
            new_chunk_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                pg_new,
                ver_new,
                body="Current annual vaccination protocol and guidelines.",
                is_active=True,
                embedding=emb,
            )

        results = await retriever.search(ctx_a, query)
        retrieved_ids = [r.chunk_id for r in results]
        assert str(new_chunk_id) in retrieved_ids
        assert str(old_chunk_id) not in retrieved_ids

    async def test_dense_vector_search_ordering(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """Dense pgvector cosine distance orders semantically close candidate
        above distant candidate.
        """
        ctx_a, _ = tenant_pair
        embedder = MockEmbeddingProvider()
        retriever = PostgresRetriever(embedding_provider=embedder)

        query = "orthopedic surgery pre-op requirements"
        query_emb = (await embedder.embed([query]))[0]

        # Orthogonal / distant embedding (invert values)
        distant_emb = [-x for x in query_emb]

        async with tenant_session(ctx_a) as session:
            doc = await seed_document(session, ctx_a.tenant_id, title="Surgery Guide")
            ver = await seed_version(session, ctx_a.tenant_id, doc, ctx_a.user_id)
            pg = await seed_page(session, ctx_a.tenant_id, ver)

            # Close chunk (identical embedding to query)
            close_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                pg,
                ver,
                body="Neutral text for close embedding.",
                is_active=True,
                embedding=query_emb,
            )
            # Distant chunk
            far_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                pg,
                ver,
                body="Neutral text for distant embedding.",
                is_active=True,
                embedding=distant_emb,
            )

        results = await retriever.search(ctx_a, query)
        retrieved_ids = [r.chunk_id for r in results]
        assert str(close_id) in retrieved_ids
        assert str(far_id) in retrieved_ids
        # Close chunk must rank before distant chunk
        assert retrieved_ids.index(str(close_id)) < retrieved_ids.index(str(far_id))

    async def test_sparse_keyword_search_ranking(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """PostgreSQL tsvector text match ranks keyword-relevant chunk above unrelated chunk."""
        ctx_a, _ = tenant_pair
        embedder = MockEmbeddingProvider()
        retriever = PostgresRetriever(embedding_provider=embedder)

        # Uniform neutral embedding for both chunks so vector score is equal
        neutral_emb = [0.05] * 1024

        async with tenant_session(ctx_a) as session:
            doc = await seed_document(session, ctx_a.tenant_id, title="Office Handbook")
            ver = await seed_version(session, ctx_a.tenant_id, doc, ctx_a.user_id)
            pg = await seed_page(session, ctx_a.tenant_id, ver)

            # Keyword-rich chunk
            kw_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                pg,
                ver,
                body="Prior authorization submission fax number is 555-0199 for urgent requests.",
                is_active=True,
                embedding=neutral_emb,
            )
            # Unrelated chunk
            other_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                pg,
                ver,
                body="Cafeteria lunch hours and catering options for internal staff meetings.",
                is_active=True,
                embedding=neutral_emb,
            )

        results = await retriever.search(ctx_a, "prior authorization fax number")
        retrieved_ids = [r.chunk_id for r in results]
        assert str(kw_id) in retrieved_ids
        assert retrieved_ids[0] == str(kw_id)
        if str(other_id) in retrieved_ids:
            assert retrieved_ids.index(str(kw_id)) < retrieved_ids.index(str(other_id))

    async def test_rrf_merging_combined_modalities(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """RRF elevates candidate appearing in both dense and sparse candidate lists to top rank."""
        ctx_a, _ = tenant_pair
        embedder = MockEmbeddingProvider()
        retriever = PostgresRetriever(embedding_provider=embedder, rrf_k=60)

        query = "urgent pediatric asthma treatment"
        query_emb = (await embedder.embed([query]))[0]
        unrelated_emb = [-x for x in query_emb]

        async with tenant_session(ctx_a) as session:
            doc = await seed_document(session, ctx_a.tenant_id, title="Pediatric Clinical Manual")
            ver = await seed_version(session, ctx_a.tenant_id, doc, ctx_a.user_id)
            pg = await seed_page(session, ctx_a.tenant_id, ver)

            # 1. Chunk matching BOTH dense and sparse
            both_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                pg,
                ver,
                body="Urgent pediatric asthma treatment protocols and bronchodilator dosages.",
                is_active=True,
                embedding=query_emb,
            )

            # 2. Chunk matching DENSE only (neutral/unrelated words)
            dense_only_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                pg,
                ver,
                body="Warehouse packaging supplies and shipping pallets.",
                is_active=True,
                embedding=query_emb,
            )

            # 3. Chunk matching SPARSE only (matching keywords, unrelated vector)
            sparse_only_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                pg,
                ver,
                body="Pediatric asthma treatment protocols for home care management.",
                is_active=True,
                embedding=unrelated_emb,
            )

        results = await retriever.search(ctx_a, query)
        retrieved_ids = [r.chunk_id for r in results]
        assert str(both_id) in retrieved_ids
        # Chunk matching both modalities must be rank 1
        assert retrieved_ids[0] == str(both_id)
        assert str(dense_only_id) in retrieved_ids
        assert str(sparse_only_id) in retrieved_ids

    async def test_retrieval_fallback_when_modality_has_zero_hits(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """When keyword search has 0 hits, dense search candidate is still
        safely returned via RRF.
        """
        ctx_a, _ = tenant_pair
        embedder = MockEmbeddingProvider()
        retriever = PostgresRetriever(embedding_provider=embedder)

        query = "dermatology biopsy billing codes"
        query_emb = (await embedder.embed([query]))[0]

        async with tenant_session(ctx_a) as session:
            doc = await seed_document(session, ctx_a.tenant_id, title="Billing Guide")
            ver = await seed_version(session, ctx_a.tenant_id, doc, ctx_a.user_id)
            pg = await seed_page(session, ctx_a.tenant_id, ver)

            # Chunk with vector match but text has zero overlapping words with query
            chunk_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                pg,
                ver,
                body="Procedures and fee schedules for skin sample laboratory evaluations.",
                is_active=True,
                embedding=query_emb,
            )

        results = await retriever.search(ctx_a, query)
        retrieved_ids = [r.chunk_id for r in results]
        assert str(chunk_id) in retrieved_ids

    async def test_hybrid_search_filters(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """Filters scope search to specific document_ids, evidence_types, and limit."""
        ctx_a, _ = tenant_pair
        embedder = MockEmbeddingProvider()
        retriever = PostgresRetriever(embedding_provider=embedder)

        query = "clinical documentation protocol"
        emb = (await embedder.embed([query]))[0]

        async with tenant_session(ctx_a) as session:
            doc_1 = await seed_document(session, ctx_a.tenant_id, title="Doc 1")
            doc_2 = await seed_document(session, ctx_a.tenant_id, title="Doc 2")
            ver_1 = await seed_version(session, ctx_a.tenant_id, doc_1, ctx_a.user_id)
            ver_2 = await seed_version(session, ctx_a.tenant_id, doc_2, ctx_a.user_id)
            pg_1 = await seed_page(session, ctx_a.tenant_id, ver_1)
            pg_2 = await seed_page(session, ctx_a.tenant_id, ver_2)

            # Doc 1 text chunk
            c1_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                pg_1,
                ver_1,
                body="Clinical documentation protocol for general practice.",
                evidence_type="text",
                is_active=True,
                embedding=emb,
            )
            # Doc 2 visual chunk
            c2_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                pg_2,
                ver_2,
                body="Clinical documentation protocol from visual scanned form.",
                evidence_type="visual",
                is_active=True,
                embedding=emb,
            )

        # 1. Filter by document_ids
        filters_doc = RetrievalFilters(document_ids=[str(doc_1)])
        res_doc = await retriever.search(ctx_a, query, filters=filters_doc)
        ids_doc = [r.chunk_id for r in res_doc]
        assert str(c1_id) in ids_doc
        assert str(c2_id) not in ids_doc

        # 2. Filter by evidence_types
        filters_vis = RetrievalFilters(evidence_types=[EvidenceType.VISUAL])
        res_vis = await retriever.search(ctx_a, query, filters=filters_vis)
        ids_vis = [r.chunk_id for r in res_vis]
        assert str(c2_id) in ids_vis
        assert str(c1_id) not in ids_vis

        # 3. Limit enforcement
        filters_lim = RetrievalFilters(limit=1)
        res_lim = await retriever.search(ctx_a, query, filters=filters_lim)
        assert len(res_lim) == 1

    async def test_search_titles_for_clarify(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """search_titles returns grounded document titles for clarify queries, scoped to tenant."""
        ctx_a, ctx_b = tenant_pair
        retriever = PostgresRetriever(embedding_provider=MockEmbeddingProvider())

        async with tenant_session(ctx_a) as session_a:
            await seed_document(session_a, ctx_a.tenant_id, title="Prior Authorization Guide")
            await seed_document(session_a, ctx_a.tenant_id, title="Prior Authorization FAQ")
            await seed_document(session_a, ctx_a.tenant_id, title="Employee Holiday Schedule")

        async with tenant_session(ctx_b) as session_b:
            await seed_document(session_b, ctx_b.tenant_id, title="Prior Authorization Guide B")

        # Clarify query for Tenant A
        titles_a = await retriever.search_titles(ctx_a, query="prior authorization", limit=2)
        assert len(titles_a) <= 2
        assert any("Prior Authorization" in t for t in titles_a)
        # Tenant B document title must never be returned to Tenant A
        assert "Prior Authorization Guide B" not in titles_a

    async def test_get_evidence_text_within_tenant_boundary(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """get_evidence_text returns formatted chunk texts for requested IDs
        within tenant boundary.
        """
        ctx_a, ctx_b = tenant_pair
        retriever = PostgresRetriever(embedding_provider=MockEmbeddingProvider())

        async with tenant_session(ctx_a) as session_a:
            doc_a = await seed_document(session_a, ctx_a.tenant_id, title="Doc A")
            ver_a = await seed_version(session_a, ctx_a.tenant_id, doc_a, ctx_a.user_id)
            pg_a = await seed_page(session_a, ctx_a.tenant_id, ver_a)
            chunk_a_id = await seed_chunk(
                session_a,
                ctx_a.tenant_id,
                pg_a,
                ver_a,
                body="Body text of Chunk A.",
                context_prefix="Doc A - Page 1 - Section 2",
                is_active=True,
            )

        async with tenant_session(ctx_b) as session_b:
            doc_b = await seed_document(session_b, ctx_b.tenant_id, title="Doc B")
            ver_b = await seed_version(session_b, ctx_b.tenant_id, doc_b, ctx_b.user_id)
            pg_b = await seed_page(session_b, ctx_b.tenant_id, ver_b)
            chunk_b_id = await seed_chunk(
                session_b,
                ctx_b.tenant_id,
                pg_b,
                ver_b,
                body="Body text of Chunk B.",
                context_prefix="Doc B - Page 1 - Secret",
                is_active=True,
            )

        # Request both chunk IDs as Tenant A
        texts = await retriever.get_evidence_text(ctx_a, [str(chunk_a_id), str(chunk_b_id)])
        # Chunk A must be present and formatted with context prefix
        assert str(chunk_a_id) in texts
        assert texts[str(chunk_a_id)] == "Doc A - Page 1 - Section 2\n\nBody text of Chunk A."
        # Chunk B from Tenant B must NOT be returned
        assert str(chunk_b_id) not in texts
