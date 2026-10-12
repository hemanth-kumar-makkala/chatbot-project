"""Port interface for DocumentRepository.

Provides persistence operations for documents and document versions.
Enforces tenant scoping on all operations.
"""

from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

from app.ports.types import (
    DocumentRecord,
    DocumentStatusInfo,
    DocumentVersionRecord,
    PageRecord,
    PageType,
    ReviewReason,
)


class DocumentRepository(ABC):
    """Abstract port for document and version persistence."""

    @abstractmethod
    async def find_version_by_sha(
        self,
        tenant_id: str,
        sha256: str,
    ) -> DocumentVersionRecord | None:
        """Find an existing document version by sha256 within the tenant."""
        ...

    @abstractmethod
    async def get_document(
        self,
        tenant_id: str,
        document_id: UUID,
    ) -> DocumentRecord | None:
        """Find a document by ID within the tenant."""
        ...

    @abstractmethod
    async def create_document(
        self,
        tenant_id: str,
        title: str,
        document_id: UUID | None = None,
    ) -> DocumentRecord:
        """Create a new document entity."""
        ...

    @abstractmethod
    async def create_version(
        self,
        tenant_id: str,
        document_id: UUID,
        sha256: str,
        uploaded_by: str,
        status: str = "received",
        version_id: UUID | None = None,
    ) -> DocumentVersionRecord:
        """Create a new document version."""
        ...

    @abstractmethod
    async def create_document_with_version(
        self,
        tenant_id: str,
        title: str,
        sha256: str,
        uploaded_by: str,
        status: str = "received",
        document_id: UUID | None = None,
        version_id: UUID | None = None,
    ) -> tuple[DocumentRecord, DocumentVersionRecord]:
        """Atomically create a document (if new) and version inside a single savepoint."""
        ...

    @abstractmethod
    async def set_active_version(
        self,
        tenant_id: str,
        document_id: UUID,
        version_id: UUID,
    ) -> None:
        """Set the active version for a document."""
        ...

    @abstractmethod
    async def get_page_numbers(
        self,
        tenant_id: str,
        version_id: UUID,
    ) -> list[int]:
        """Retrieve existing page numbers for a version in ascending order."""
        ...

    @abstractmethod
    async def create_page(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
        page_sha256: str,
        page_type: str,
        image_key: str,
    ) -> None:
        """Create a page evidence record."""
        ...

    @abstractmethod
    async def get_page_type(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
    ) -> PageType | None:
        """Retrieve classified page type for a specific page."""
        ...

    @abstractmethod
    async def get_page(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
    ) -> PageRecord | None:
        """Retrieve page evidence record by tenant, version, and page number."""
        ...

    @abstractmethod
    async def update_version_status(
        self,
        tenant_id: str,
        version_id: UUID,
        status: str,
    ) -> None:
        """Update lifecycle status of a document version."""
        ...

    @abstractmethod
    async def record_job_step(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int | None,
        step: str,
        status: str,
        processing_version: str = "v1",
        job_id: UUID | None = None,
    ) -> None:
        """Record an idempotent job step execution in job_steps."""
        ...

    @abstractmethod
    async def create_table_data_batch(
        self,
        tenant_id: str,
        records: list[dict[str, Any]],
    ) -> None:
        """Insert a batch of table_data rows idempotently (ON CONFLICT DO NOTHING)."""
        ...

    @abstractmethod
    async def get_uncompleted_page_numbers(
        self,
        tenant_id: str,
        version_id: UUID,
        step: str = "chunking",
    ) -> list[int]:
        """Retrieve page numbers that do not yet have a completed job_step."""
        ...

    @abstractmethod
    async def lock_version_for_activation(
        self,
        tenant_id: str,
        version_id: UUID,
    ) -> tuple[DocumentRecord, DocumentVersionRecord, int, int, bool]:
        """Lock version row (FOR UPDATE) and evaluate completion and review state.

        Returns: (doc, version, total_pages, completed_pages, has_flagged_pages)
        """
        ...

    @abstractmethod
    async def execute_version_activation(
        self,
        tenant_id: str,
        version_id: UUID,
        branch: str,
    ) -> str:
        """Execute Branch A, Branch B, or Branch C activation in current transaction."""
        ...

    @abstractmethod
    async def create_chunks_batch(
        self,
        tenant_id: str,
        records: list[dict[str, Any]],
    ) -> None:
        """Insert a batch of chunks idempotently (ON CONFLICT DO NOTHING)."""
        ...

    @abstractmethod
    async def flag_page_for_review(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
        reason: ReviewReason,
    ) -> None:
        """Mark a page as needs_review=True with a required review_reason."""
        ...

    @abstractmethod
    async def clear_page_review_flag(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
    ) -> None:
        """Clear review flag on a page: set needs_review=False and review_reason=NULL."""
        ...

    @abstractmethod
    async def create_page_reading(
        self,
        tenant_id: str,
        page_id: UUID,
        source: str,
        text: str,
        model_id: str,
        model_version: str,
    ) -> UUID:
        """Insert a page_reading idempotently (ON CONFLICT DO NOTHING) and return reading ID."""
        ...

    @abstractmethod
    async def list_documents_with_status(
        self,
        tenant_id: str,
    ) -> list[DocumentStatusInfo]:
        """Retrieve all documents and versions with derived UI states for tenant."""
        ...
