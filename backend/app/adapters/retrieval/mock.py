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
    ) -> None:
        if canned_evidence_ids is not None:
            self._canned_evidence_ids = canned_evidence_ids
        else:
            self._canned_evidence_ids = [
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

    async def get_evidence_text(
        self,
        tenant_ctx: TenantContext,
        evidence_ids: list[str],
    ) -> dict[str, str]:
        """Return deterministic grounded evidence texts for requested IDs."""
        _ = tenant_ctx
        return {
            eid: self._canned_evidence_text.get(eid, f"Mock synthetic text for {eid}")
            for eid in evidence_ids
        }

    async def search_tables(
        self,
        tenant_ctx: TenantContext,
        query: str,
        limit: int = 15,
    ) -> list[TableCandidate]:
        """Return canned table candidate rows for mock profile."""
        _ = (tenant_ctx, query)
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
