"""Port interface for Retriever.

The Retriever is the ONLY component in the architecture with database read
access to knowledge chunks. Enforces tenant scoping on every query before retrieval.
"""

from abc import ABC, abstractmethod

from app.ports.types import EvidenceId, RetrievalFilters, TableCandidate, TenantContext


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

    @abstractmethod
    async def search_tables(
        self,
        tenant_ctx: TenantContext,
        query: str,
        limit: int = 15,
    ) -> list[TableCandidate]:
        """Retrieve matching rows from tables_data for active documents within tenant scope."""
        ...

    @abstractmethod
    async def get_inventory(
        self,
        tenant_ctx: TenantContext,
        heading_limit: int = 5,
    ) -> dict[str, list[str]]:
        """Retrieve active document titles and up to heading_limit distinct headings."""
        ...
