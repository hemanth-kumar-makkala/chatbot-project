"""Mock Retriever adapter for Phase 1 local development and testing.

Enforces tenant scoping, returns synthetic evidence IDs, and provides
grounded document titles for clarify queries (ADR-0002).
"""

from __future__ import annotations

from app.ports.retriever import Retriever
from app.ports.types import EvidenceId, EvidenceType, RetrievalFilters, TenantContext


class MockRetriever(Retriever):
    """Deterministic mock retriever operating purely on synthetic data."""

    def __init__(
        self,
        canned_evidence_ids: list[EvidenceId] | None = None,
        canned_titles: list[str] | None = None,
    ) -> None:
        self._canned_evidence_ids = canned_evidence_ids or [
            EvidenceId(
                chunk_id="chunk-syn-001",
                page_id="page-syn-001",
                document_version_id="ver-syn-001",
                evidence_type=EvidenceType.TEXT,
            ),
            EvidenceId(
                chunk_id="chunk-syn-002",
                page_id="page-syn-001",
                document_version_id="ver-syn-001",
                evidence_type=EvidenceType.TEXT,
            ),
        ]
        self._canned_titles = canned_titles or [
            "General Practice Guidelines",
            "Billing and Insurance Schedule",
            "Patient Referral Protocols",
        ]

    async def search(
        self,
        tenant_ctx: TenantContext,
        query: str,
        filters: RetrievalFilters | None = None,
    ) -> list[EvidenceId]:
        """Perform tenant-filtered hybrid search returning synthetic evidence IDs."""
        _ = (tenant_ctx, query, filters)
        return list(self._canned_evidence_ids)

    async def search_titles(
        self,
        tenant_ctx: TenantContext,
        query: str,
        limit: int = 3,
    ) -> list[str]:
        """Return deterministic grounded titles up to the specified limit."""
        _ = (tenant_ctx, query)
        return self._canned_titles[:limit]
