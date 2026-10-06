"""Port interface for DocumentRepository.

Provides persistence operations for documents and document versions.
Enforces tenant scoping on all operations.
"""

from abc import ABC, abstractmethod
from uuid import UUID

from app.ports.types import DocumentRecord, DocumentVersionRecord


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
    ) -> None:
        """Record an idempotent job step execution in job_steps."""
        ...
