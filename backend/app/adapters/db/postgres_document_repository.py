"""PostgreSQL adapter for DocumentRepository port."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentVersion, Job, JobStep, Page
from app.ports.document_repository import DocumentRepository
from app.ports.errors import DocumentNotFoundError, DuplicateVersionError
from app.ports.types import DocumentRecord, DocumentVersionRecord


class PostgresDocumentRepository(DocumentRepository):
    """PostgreSQL implementation of DocumentRepository using tenant-scoped queries."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_version_by_sha(
        self,
        tenant_id: str,
        sha256: str,
    ) -> DocumentVersionRecord | None:
        """Find an existing document version by sha256 within the tenant."""
        stmt = select(DocumentVersion).where(
            DocumentVersion.tenant_id == UUID(str(tenant_id)),
            DocumentVersion.sha256 == sha256,
        )
        res = await self.session.execute(stmt)
        row = res.scalars().first()
        if row is None:
            return None
        return DocumentVersionRecord(
            id=row.id,
            tenant_id=str(row.tenant_id),
            document_id=row.document_id,
            sha256=row.sha256,
            uploaded_by=str(row.uploaded_by),
            status=row.status,
            created_at=row.created_at,
        )

    async def get_document(
        self,
        tenant_id: str,
        document_id: UUID,
    ) -> DocumentRecord | None:
        """Find a document by ID within the tenant."""
        stmt = select(Document).where(
            Document.tenant_id == UUID(str(tenant_id)),
            Document.id == document_id,
        )
        res = await self.session.execute(stmt)
        row = res.scalars().first()
        if row is None:
            return None
        return DocumentRecord(
            id=row.id,
            tenant_id=str(row.tenant_id),
            title=row.title,
            active_version_id=row.active_version_id,
            created_at=row.created_at,
        )

    async def create_document(
        self,
        tenant_id: str,
        title: str,
        document_id: UUID | None = None,
    ) -> DocumentRecord:
        """Create a new document entity."""
        doc_id = document_id or uuid4()
        now = datetime.now(UTC)
        doc = Document(
            id=doc_id,
            tenant_id=UUID(str(tenant_id)),
            title=title,
            active_version_id=None,
            created_at=now,
            updated_at=now,
        )
        self.session.add(doc)
        await self.session.flush()
        return DocumentRecord(
            id=doc.id,
            tenant_id=str(doc.tenant_id),
            title=doc.title,
            active_version_id=doc.active_version_id,
            created_at=doc.created_at,
        )

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
        ver_id = version_id or uuid4()
        now = datetime.now(UTC)
        ver = DocumentVersion(
            id=ver_id,
            tenant_id=UUID(str(tenant_id)),
            document_id=document_id,
            sha256=sha256,
            uploaded_by=UUID(str(uploaded_by)),
            status=status,
            created_at=now,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(ver)
                await self.session.flush()
        except IntegrityError as exc:
            existing = await self.find_version_by_sha(tenant_id, sha256)
            if existing is not None:
                raise DuplicateVersionError(existing_version=existing) from exc
            raise

        return DocumentVersionRecord(
            id=ver.id,
            tenant_id=str(ver.tenant_id),
            document_id=ver.document_id,
            sha256=ver.sha256,
            uploaded_by=str(ver.uploaded_by),
            status=ver.status,
            created_at=ver.created_at,
        )

    async def set_active_version(
        self,
        tenant_id: str,
        document_id: UUID,
        version_id: UUID,
    ) -> None:
        """Set the active version for a document."""
        stmt = select(Document).where(
            Document.tenant_id == UUID(str(tenant_id)),
            Document.id == document_id,
        )
        res = await self.session.execute(stmt)
        row = res.scalars().first()
        if row is not None:
            row.active_version_id = version_id
            await self.session.flush()

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
        """Atomically create a document (if new) and version inside a single savepoint.

        Raises:
            DocumentNotFoundError: If document_id is supplied but not found in the tenant.
            DuplicateVersionError: If version sha256 already exists in the tenant.
        """
        ver_id = version_id or uuid4()
        now = datetime.now(UTC)

        try:
            async with self.session.begin_nested():
                if document_id is not None:
                    stmt = select(Document).where(
                        Document.tenant_id == UUID(str(tenant_id)),
                        Document.id == document_id,
                    )
                    res = await self.session.execute(stmt)
                    doc = res.scalars().first()
                    if doc is None:
                        raise DocumentNotFoundError(
                            f"Document {document_id} not found in tenant {tenant_id}"
                        )
                else:
                    doc = Document(
                        id=uuid4(),
                        tenant_id=UUID(str(tenant_id)),
                        title=title,
                        active_version_id=None,
                        created_at=now,
                        updated_at=now,
                    )
                    self.session.add(doc)
                    await self.session.flush()

                ver = DocumentVersion(
                    id=ver_id,
                    tenant_id=UUID(str(tenant_id)),
                    document_id=doc.id,
                    sha256=sha256,
                    uploaded_by=UUID(str(uploaded_by)),
                    status=status,
                    created_at=now,
                )
                self.session.add(ver)
                await self.session.flush()

        except IntegrityError as exc:
            existing = await self.find_version_by_sha(tenant_id, sha256)
            if existing is not None:
                raise DuplicateVersionError(existing_version=existing) from exc
            raise

        return DocumentRecord(
            id=doc.id,
            tenant_id=str(doc.tenant_id),
            title=doc.title,
            active_version_id=doc.active_version_id,
            created_at=doc.created_at,
        ), DocumentVersionRecord(
            id=ver.id,
            tenant_id=str(ver.tenant_id),
            document_id=ver.document_id,
            sha256=ver.sha256,
            uploaded_by=str(ver.uploaded_by),
            status=ver.status,
            created_at=ver.created_at,
        )

    async def get_page_numbers(
        self,
        tenant_id: str,
        version_id: UUID,
    ) -> list[int]:
        """Retrieve existing page numbers for a version in ascending order."""
        stmt = (
            select(Page.page_number)
            .where(
                Page.tenant_id == UUID(str(tenant_id)),
                Page.document_version_id == version_id,
            )
            .order_by(Page.page_number)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

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
        page = Page(
            id=uuid4(),
            tenant_id=UUID(str(tenant_id)),
            document_version_id=version_id,
            page_number=page_number,
            page_sha256=page_sha256,
            page_type=page_type,
            image_key=image_key,
        )
        self.session.add(page)
        await self.session.flush()

    async def update_version_status(
        self,
        tenant_id: str,
        version_id: UUID,
        status: str,
    ) -> None:
        """Update lifecycle status of a document version."""
        stmt = (
            update(DocumentVersion)
            .where(
                DocumentVersion.tenant_id == UUID(str(tenant_id)),
                DocumentVersion.id == version_id,
            )
            .values(status=status)
        )
        await self.session.execute(stmt)
        await self.session.flush()

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
        job_stmt = (
            select(Job)
            .where(
                Job.tenant_id == UUID(str(tenant_id)),
                Job.document_version_id == version_id,
            )
            .order_by(Job.created_at.desc())
        )
        job_res = await self.session.execute(job_stmt)
        job = job_res.scalars().first()
        if job is None:
            job = Job(
                id=uuid4(),
                tenant_id=UUID(str(tenant_id)),
                document_version_id=version_id,
                status="processing",
            )
            self.session.add(job)
            await self.session.flush()

        step_row = JobStep(
            id=uuid4(),
            tenant_id=UUID(str(tenant_id)),
            job_id=job.id,
            document_version_id=version_id,
            page_number=page_number,
            step=step,
            processing_version=processing_version,
            status=status,
        )
        self.session.add(step_row)
        await self.session.flush()
