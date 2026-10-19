"""Port interface for Retriever.

The Retriever is the ONLY component in the architecture with database read
access to knowledge chunks. Enforces tenant scoping on every query before retrieval.
"""

from abc import ABC, abstractmethod

from app.ports.types import EvidenceId, RetrievalFilters, TenantContext


class Retriever(ABC):
    """Abstract port for hybrid search and clarify topic discovery."""

    @abstractmethod
    async def search(
        self,
        tenant_ctx: TenantContext,
        query: str,
        filters: RetrievalFilters | None = None,
    ) -> list[EvidenceId]:
        """Perform tenant-filtered hybrid search returning exact evidence IDs."""
        ...

    @abstractmethod
    async def search_titles(
        self,
        tenant_ctx: TenantContext,
        query: str,
        limit: int = 3,
    ) -> list[str]:
        """Search tenant document titles and section headings for clarify grounding (ADR-0002)."""
        ...

    @abstractmethod
    async def get_evidence_text(
        self,
        tenant_ctx: TenantContext,
        evidence_ids: list[str],
    ) -> dict[str, str]:
        """Fetch chunk texts and context prefixes for given evidence IDs within tenant scope."""
        ...
