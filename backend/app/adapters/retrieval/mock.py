"""Mock Retriever adapter for Phase 1 local development and testing.

Enforces tenant scoping, returns synthetic evidence IDs, and provides
grounded document titles for clarify queries (ADR-0002).
"""

from __future__ import annotations

from app.ports.retriever import Retriever
from app.ports.types import (
    EvidenceId,
    EvidenceType,
    RetrievalFilters,
    TableCandidate,
    TenantContext,
)


class MockRetriever(Retriever):
    """Deterministic mock retriever operating purely on synthetic data."""

    def __init__(
        self,
        canned_evidence_ids: list[EvidenceId] | None = None,
        canned_titles: list[str] | None = None,
        canned_evidence_text: dict[str, str] | None = None,
        canned_table_candidates: list[TableCandidate] | None = None,
    ) -> None:
        self._canned_evidence_ids_provided = canned_evidence_ids is not None
        self._canned_table_candidates = canned_table_candidates
        if canned_evidence_ids is not None:
            self._canned_evidence_ids = canned_evidence_ids
        else:
            self._canned_evidence_ids = [
                EvidenceId(
                    chunk_id="chunk-syn-001",
                    page_id="page-syn-001",
                    document_version_id="ver-syn-001",
                    evidence_type=EvidenceType.TEXT,
                    tenant_id="00000000-0000-0000-0000-000000000001",
                ),
                EvidenceId(
                    chunk_id="chunk-syn-002",
                    page_id="page-syn-001",
                    document_version_id="ver-syn-001",
                    evidence_type=EvidenceType.TEXT,
                    tenant_id="00000000-0000-0000-0000-000000000001",
                ),
            ]
        self._canned_titles = (
            canned_titles
            if canned_titles is not None
            else [
                "General Practice Guidelines",
                "Billing and Insurance Schedule",
                "Patient Referral Protocols",
            ]
        )
        if canned_evidence_text is not None:
            self._canned_evidence_text = canned_evidence_text
        else:
            self._canned_evidence_text = {
                "chunk-syn-001": "General practice guidelines, clinic hours, and protocol summary.",
                "chunk-syn-002": "Billing schedule rates and prior authorization fax numbers.",
            }

    async def search(
        self,
        tenant_ctx: TenantContext,
        query: str,
        filters: RetrievalFilters | None = None,
    ) -> list[EvidenceId]:
        """Perform tenant-filtered hybrid search returning synthetic evidence IDs."""
        _ = (query, filters)
        if self._canned_evidence_ids_provided:
            return [
                EvidenceId(
                    chunk_id=e.chunk_id,
                    page_id=e.page_id,
                    document_version_id=e.document_version_id,
                    evidence_type=e.evidence_type,
                    tenant_id=getattr(e, "tenant_id", None) or str(tenant_ctx.tenant_id),
                )
                for e in self._canned_evidence_ids
            ]

        try:
            from uuid import UUID

            from sqlalchemy import select

            from app.db.models import Chunk, Document, DocumentVersion
            from app.db.session import tenant_session

            t_uuid = UUID(str(tenant_ctx.tenant_id))
            async with tenant_session(tenant_ctx) as session:
                stmt = (
                    select(
                        Chunk.id,
                        Chunk.page_id,
                        Chunk.document_version_id,
                        Chunk.evidence_type,
                        Chunk.text,
                    )
                    .join(
                        DocumentVersion,
                        (DocumentVersion.id == Chunk.document_version_id)
                        & (DocumentVersion.tenant_id == Chunk.tenant_id),
                    )
                    .join(
                        Document,
                        (Document.id == DocumentVersion.document_id)
                        & (Document.tenant_id == DocumentVersion.tenant_id),
                    )
                    .where(
                        Chunk.tenant_id == t_uuid,
                        Chunk.is_active.is_(True),
                        Document.is_retired.is_(False),
                    )
                    .limit(5)
                )
                rows = list((await session.execute(stmt)).all())
                if rows:
                    for r in rows:
                        self._canned_evidence_text[str(r.id)] = r.text
                    return [
                        EvidenceId(
                            chunk_id=str(r.id),
                            page_id=str(r.page_id),
                            document_version_id=str(r.document_version_id),
                            evidence_type=EvidenceType(r.evidence_type)
                            if hasattr(EvidenceType, str(r.evidence_type))
                            else EvidenceType.TEXT,
                            tenant_id=str(tenant_ctx.tenant_id),
                        )
                        for r in rows
                    ]
        except Exception:
            pass

        return [
            EvidenceId(
                chunk_id=e.chunk_id,
                page_id=e.page_id,
                document_version_id=e.document_version_id,
                evidence_type=e.evidence_type,
                tenant_id=str(tenant_ctx.tenant_id),
            )
            for e in self._canned_evidence_ids
        ]

    async def search_titles(
        self,
        tenant_ctx: TenantContext,
        query: str,
        limit: int = 3,
    ) -> list[str]:
        """Return deterministic grounded titles up to the specified limit."""
        _ = (tenant_ctx, query)
        return self._canned_titles[:limit]

    async def get_evidence_text(
        self,
        tenant_ctx: TenantContext,
        evidence_ids: list[str],
    ) -> dict[str, str]:
        """Return deterministic grounded evidence texts for requested IDs."""
        result: dict[str, str] = {}
        missing: list[str] = []
        for eid in evidence_ids:
            if eid in self._canned_evidence_text:
                result[eid] = self._canned_evidence_text[eid]
            else:
                missing.append(eid)
        if missing:
            try:
                from uuid import UUID

                from sqlalchemy import select

                from app.db.models import Chunk
                from app.db.session import tenant_session

                t_uuid = UUID(str(tenant_ctx.tenant_id))
                chunk_uuids = [UUID(m) for m in missing]
                async with tenant_session(tenant_ctx) as session:
                    stmt = select(Chunk.id, Chunk.text).where(
                        Chunk.tenant_id == t_uuid,
                        Chunk.id.in_(chunk_uuids),
                    )
                    db_rows = list((await session.execute(stmt)).all())
                    for r in db_rows:
                        result[str(r.id)] = r.text
            except Exception:
                pass
            for m in missing:
                if m not in result:
                    result[m] = f"Mock synthetic text for {m}"
        return result

    async def search_tables(
        self,
        tenant_ctx: TenantContext,
        query: str,
        limit: int = 15,
    ) -> list[TableCandidate]:
        """Return canned table candidate rows for mock profile."""
        _ = (tenant_ctx, query)
        if self._canned_table_candidates is not None:
            return self._canned_table_candidates[:limit]
        return [
            TableCandidate(
                id="mock-table-row-1",
                document_title="synthetic_plans_table.csv",
                document_version_id="ver-syn-table",
                sheet_name="Sheet1",
                row_index=1,
                cells={
                    "Plan Name": "Aetna Choice POS II",
                    "Status": "In-Network",
                    "Copay": "$25",
                },
                tenant_id=str(tenant_ctx.tenant_id),
            ),
            TableCandidate(
                id="mock-table-row-2",
                document_title="synthetic_plans_table.csv",
                document_version_id="ver-syn-table",
                sheet_name="Sheet1",
                row_index=2,
                cells={
                    "Plan Name": "Blue Cross Blue Shield PPO",
                    "Status": "In-Network",
                    "Copay": "$20",
                },
                tenant_id=str(tenant_ctx.tenant_id),
            ),
        ][:limit]

    async def get_neighbouring_chunks(
        self,
        tenant_ctx: TenantContext,
        chunk_ids: list[str],
    ) -> list[EvidenceId]:
        """Return canned neighbouring chunks for mock profile."""
        _ = (tenant_ctx, chunk_ids)
        return []

    async def get_inventory(
        self,
        tenant_ctx: TenantContext,
        heading_limit: int = 5,
    ) -> dict[str, list[str]]:
        """Return canned document titles and headings for mock profile."""
        _ = tenant_ctx
        return {
            "titles": list(self._canned_titles),
            "headings": ["General Practice Guidelines", "Billing Schedule", "Accepted Plans"][
                :heading_limit
            ],
        }
