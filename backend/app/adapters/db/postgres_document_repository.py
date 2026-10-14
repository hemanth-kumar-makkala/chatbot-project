import logging
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditEvent,
    Chunk,
    Document,
    DocumentVersion,
    Job,
    JobStep,
    Page,
    PageReading,
    TableData,
)
from app.ports.document_repository import DocumentRepository
from app.ports.errors import (
    DocumentNotFoundError,
    DocumentRetiredError,
    DuplicateVersionError,
    InvalidPageStateError,
    InvalidVersionStateError,
    NoUsableReadingError,
    PageJobConflictError,
    QueueError,
    RetryNotUsefulError,
)
from app.ports.types import (
    ACTIVE_PAGE_MARKER_STATUSES,
    CLEARABLE_REVIEW_REASONS,
    CURRENT_PROCESSING_VERSION,
    DocumentRecord,
    DocumentStatusInfo,
    DocumentVersionRecord,
    JobStepStatus,
    PageRecord,
    PageType,
    ReviewPageInfo,
    ReviewReadingInfo,
    ReviewReason,
    VersionStatusInfo,
)

logger = logging.getLogger(__name__)


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
            is_retired=getattr(row, "is_retired", False),
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
            is_retired=getattr(doc, "is_retired", False),
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
            is_retired=getattr(doc, "is_retired", False),
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

    async def get_page_type(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
    ) -> PageType | None:
        stmt = select(Page.page_type).where(
            Page.tenant_id == UUID(str(tenant_id)),
            Page.document_version_id == version_id,
            Page.page_number == page_number,
        )
        res = (await self.session.execute(stmt)).scalar_one_or_none()
        if res is None:
            return None
        return PageType(res)

    async def get_page(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
    ) -> PageRecord | None:
        stmt = select(Page).where(
            Page.tenant_id == UUID(str(tenant_id)),
            Page.document_version_id == version_id,
            Page.page_number == page_number,
        )
        p = (await self.session.execute(stmt)).scalars().first()
        if p is None:
            return None
        return PageRecord(
            id=p.id,
            tenant_id=str(p.tenant_id),
            document_version_id=p.document_version_id,
            page_number=p.page_number,
            page_sha256=p.page_sha256,
            page_type=PageType(p.page_type),
            image_key=p.image_key,
            ocr_confidence=p.ocr_confidence,
            needs_review=p.needs_review,
            review_reason=cast(ReviewReason | None, p.review_reason),
            is_excluded=getattr(p, "is_excluded", False),
        )

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

    async def ensure_ingestion_job(
        self,
        tenant_id: str,
        version_id: UUID,
    ) -> UUID:
        """Find existing Job for the document version or create one with status 'processing'."""
        tid = UUID(str(tenant_id))
        job_stmt = (
            select(Job)
            .where(
                Job.tenant_id == tid,
                Job.document_version_id == version_id,
            )
            .order_by(Job.created_at.desc())
        )
        job_res = await self.session.execute(job_stmt)
        job = job_res.scalars().first()
        if job is not None:
            return job.id

        new_job = Job(
            id=uuid4(),
            tenant_id=tid,
            document_version_id=version_id,
            status="processing",
        )
        self.session.add(new_job)
        await self.session.flush()
        return new_job.id

    async def record_job_step(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int | None,
        step: str,
        status: str,
        processing_version: str = CURRENT_PROCESSING_VERSION,
        job_id: UUID | None = None,
    ) -> None:
        """Record an idempotent job step execution in job_steps."""
        if job_id is not None:
            job_stmt = select(Job).where(
                Job.tenant_id == UUID(str(tenant_id)),
                Job.id == job_id,
            )
            job_res = await self.session.execute(job_stmt)
            job = job_res.scalars().first()
            if job is None:
                job = Job(
                    id=job_id,
                    tenant_id=UUID(str(tenant_id)),
                    document_version_id=version_id,
                    status="processing",
                )
                self.session.add(job)
                await self.session.flush()
            actual_job_id = job.id
        else:
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
            actual_job_id = job.id

        stmt = (
            pg_insert(JobStep)
            .values(
                id=uuid4(),
                tenant_id=UUID(str(tenant_id)),
                job_id=actual_job_id,
                document_version_id=version_id,
                page_number=page_number,
                step=step,
                processing_version=processing_version,
                status=status,
            )
            .on_conflict_do_update(
                index_elements=[
                    JobStep.tenant_id,
                    JobStep.document_version_id,
                    JobStep.page_number,
                    JobStep.step,
                    JobStep.processing_version,
                ],
                set_={
                    "status": status,
                    "job_id": actual_job_id,
                    "updated_at": func.now(),
                },
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def record_page_processing_marker(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
        status: JobStepStatus,
        processing_version: str = CURRENT_PROCESSING_VERSION,
    ) -> None:
        """Lock documents, document_versions (FOR KEY SHARE), then Page (FOR UPDATE),
        and upsert the page_processing marker in job_steps.
        """
        tid = UUID(str(tenant_id))

        # 1. Lock Document FOR KEY SHARE
        doc_stmt = (
            select(Document)
            .where(
                Document.tenant_id == tid,
                Document.id
                == (
                    select(DocumentVersion.document_id)
                    .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
                    .scalar_subquery()
                ),
            )
            .with_for_update(key_share=True)
        )
        doc = (await self.session.execute(doc_stmt)).scalars().first()
        if doc is None:
            raise DocumentNotFoundError(
                f"Document for version {version_id} not found in tenant {tenant_id}"
            )

        # 2. Lock DocumentVersion FOR KEY SHARE
        ver_stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
            .with_for_update(key_share=True)
        )
        ver = (await self.session.execute(ver_stmt)).scalars().first()
        if ver is None:
            raise DocumentNotFoundError(f"Version {version_id} not found in tenant {tenant_id}")

        # 3. Lock Page FOR UPDATE
        page_stmt = (
            select(Page)
            .where(
                Page.tenant_id == tid,
                Page.document_version_id == version_id,
                Page.page_number == page_number,
            )
            .with_for_update()
        )
        page = (await self.session.execute(page_stmt)).scalars().first()
        if page is None:
            raise DocumentNotFoundError("Page not found in tenant")

        job_stmt = (
            select(Job)
            .where(
                Job.tenant_id == tid,
                Job.document_version_id == version_id,
            )
            .order_by(Job.created_at.desc())
        )
        job_res = await self.session.execute(job_stmt)
        job = job_res.scalars().first()
        if job is None:
            raise QueueError("No job found for version")
        actual_job_id = job.id

        stmt = (
            pg_insert(JobStep)
            .values(
                id=uuid4(),
                tenant_id=tid,
                job_id=actual_job_id,
                document_version_id=version_id,
                page_number=page_number,
                step="page_processing",
                processing_version=processing_version,
                status=status,
            )
            .on_conflict_do_update(
                index_elements=[
                    JobStep.tenant_id,
                    JobStep.document_version_id,
                    JobStep.page_number,
                    JobStep.step,
                    JobStep.processing_version,
                ],
                set_={
                    "status": status,
                    "job_id": actual_job_id,
                    "updated_at": func.now(),
                },
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def create_table_data_batch(
        self,
        tenant_id: str,
        records: list[dict[str, Any]],
    ) -> None:
        """Insert a batch of table_data rows idempotently (ON CONFLICT DO NOTHING)."""
        if not records:
            return
        clean_records: list[dict[str, Any]] = []
        for r in records:
            rec = dict(r)
            rec["tenant_id"] = UUID(str(tenant_id))
            if "id" not in rec or rec["id"] is None:
                rec["id"] = uuid4()
            clean_records.append(rec)

        stmt = (
            pg_insert(TableData)
            .values(clean_records)
            .on_conflict_do_nothing(constraint="uq_tables_data_idempotency")
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def get_uncompleted_page_numbers(
        self,
        tenant_id: str,
        version_id: UUID,
        step: str = "chunking",
    ) -> list[int]:
        """Retrieve page numbers that do not yet have a completed job_step."""
        tid = UUID(str(tenant_id))
        all_pages_stmt = (
            select(Page.page_number)
            .where(Page.tenant_id == tid, Page.document_version_id == version_id)
            .order_by(Page.page_number)
        )
        all_pages_res = await self.session.execute(all_pages_stmt)
        all_pages = set(all_pages_res.scalars().all())

        completed_stmt = select(JobStep.page_number).where(
            JobStep.tenant_id == tid,
            JobStep.document_version_id == version_id,
            JobStep.step == step,
            JobStep.status.in_(["completed", "failed"]),
            JobStep.page_number.is_not(None),
        )
        completed_res = await self.session.execute(completed_stmt)
        completed_pages = set(completed_res.scalars().all())

        return sorted(all_pages - completed_pages)

    async def lock_version_for_activation(
        self,
        tenant_id: str,
        version_id: UUID,
    ) -> tuple[DocumentRecord, DocumentVersionRecord, int, int, bool]:
        """Lock version row (FOR UPDATE) and evaluate completion and review state."""
        tid = UUID(str(tenant_id))
        # Strict global lock order: 1. Lock parent Document row first
        doc_stmt = (
            select(Document)
            .where(
                Document.tenant_id == tid,
                Document.id
                == (
                    select(DocumentVersion.document_id)
                    .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
                    .scalar_subquery()
                ),
            )
            .with_for_update()
        )
        doc_res = await self.session.execute(doc_stmt)
        doc = doc_res.scalars().first()
        if doc is None:
            raise DocumentNotFoundError(
                f"Document for version {version_id} not found in tenant {tenant_id}"
            )

        # Strict global lock order: 2. Lock child DocumentVersion row second
        ver_stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
            .with_for_update()
        )
        ver_res = await self.session.execute(ver_stmt)
        ver = ver_res.scalars().first()
        if ver is None:
            raise DocumentNotFoundError(f"Version {version_id} not found in tenant {tenant_id}")

        pages_stmt = select(Page).where(
            Page.tenant_id == tid, Page.document_version_id == version_id
        )
        pages_res = await self.session.execute(pages_stmt)
        pages = pages_res.scalars().all()
        total_pages = len(pages)
        has_flagged = any(p.needs_review for p in pages)

        completed_stmt = select(func.count(func.distinct(JobStep.page_number))).where(
            JobStep.tenant_id == tid,
            JobStep.document_version_id == version_id,
            JobStep.status.in_(["completed", "failed"]),
            JobStep.page_number.is_not(None),
        )
        completed_res = await self.session.execute(completed_stmt)
        completed_pages = completed_res.scalar_one() or 0

        doc_rec = DocumentRecord(
            id=doc.id,
            tenant_id=str(doc.tenant_id),
            title=doc.title,
            active_version_id=doc.active_version_id,
            created_at=doc.created_at,
            is_retired=getattr(doc, "is_retired", False),
        )
        ver_rec = DocumentVersionRecord(
            id=ver.id,
            tenant_id=str(ver.tenant_id),
            document_id=ver.document_id,
            sha256=ver.sha256,
            uploaded_by=str(ver.uploaded_by),
            status=ver.status,
            created_at=ver.created_at,
        )
        return doc_rec, ver_rec, total_pages, completed_pages, has_flagged

    async def execute_version_activation(
        self,
        tenant_id: str,
        version_id: UUID,
        branch: str,
    ) -> str:
        """Execute Branch A, Branch B, or Branch C activation in current transaction."""
        tid = UUID(str(tenant_id))
        doc = (
            (
                await self.session.execute(
                    select(Document).where(
                        Document.tenant_id == tid,
                        Document.id
                        == (
                            select(DocumentVersion.document_id)
                            .where(
                                DocumentVersion.tenant_id == tid,
                                DocumentVersion.id == version_id,
                            )
                            .scalar_subquery()
                        ),
                    )
                )
            )
            .scalars()
            .one()
        )
        ver = (
            (
                await self.session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id
                    )
                )
            )
            .scalars()
            .one()
        )

        old_active_id = doc.active_version_id

        if branch == "Branch A":
            ver.status = "needs_review"
            await self.session.flush()
            return "needs_review"

        if branch == "Branch B":
            doc.active_version_id = version_id
            ver.status = "needs_review"
            flagged_pages_stmt = select(Page.id).where(
                Page.tenant_id == tid,
                Page.document_version_id == version_id,
                Page.needs_review.is_(True),
            )
            flagged_page_ids = set((await self.session.execute(flagged_pages_stmt)).scalars().all())

            # Clean pages chunks active
            if flagged_page_ids:
                await self.session.execute(
                    update(Chunk)
                    .where(
                        Chunk.tenant_id == tid,
                        Chunk.document_version_id == version_id,
                        Chunk.page_id.not_in(flagged_page_ids),
                    )
                    .values(is_active=True)
                )
                await self.session.execute(
                    update(Chunk)
                    .where(
                        Chunk.tenant_id == tid,
                        Chunk.document_version_id == version_id,
                        Chunk.page_id.in_(flagged_page_ids),
                    )
                    .values(is_active=False)
                )
            else:
                await self.session.execute(
                    update(Chunk)
                    .where(Chunk.tenant_id == tid, Chunk.document_version_id == version_id)
                    .values(is_active=True)
                )

            await self.session.flush()
            return "needs_review"

        # Branch C: All pages clean
        doc.active_version_id = version_id
        ver.status = "ready"
        await self.session.execute(
            update(Chunk)
            .where(Chunk.tenant_id == tid, Chunk.document_version_id == version_id)
            .values(is_active=True)
        )
        if old_active_id is not None and old_active_id != version_id:
            await self.session.execute(
                update(DocumentVersion)
                .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == old_active_id)
                .values(status="retired")
            )
            await self.session.execute(
                update(Chunk)
                .where(Chunk.tenant_id == tid, Chunk.document_version_id == old_active_id)
                .values(is_active=False)
            )

        await self.session.flush()
        return "ready"

    async def create_chunks_batch(
        self,
        tenant_id: str,
        records: list[dict[str, Any]],
    ) -> None:
        """Insert a batch of chunks idempotently (ON CONFLICT DO NOTHING)."""
        if not records:
            return
        tid = UUID(str(tenant_id))
        clean_records: list[dict[str, Any]] = []
        for r in records:
            rec = dict(r)
            rec["tenant_id"] = tid
            if "id" not in rec or rec["id"] is None:
                rec["id"] = uuid4()
            if rec.get("page_id") is None and "page_number" in rec and "document_version_id" in rec:
                p_stmt = select(Page.id).where(
                    Page.tenant_id == tid,
                    Page.document_version_id == rec["document_version_id"],
                    Page.page_number == rec["page_number"],
                )
                p_res = await self.session.execute(p_stmt)
                rec["page_id"] = p_res.scalar_one_or_none()
            rec.pop("page_number", None)
            clean_records.append(rec)

        stmt = (
            pg_insert(Chunk).values(clean_records).on_conflict_do_nothing(index_elements=[Chunk.id])
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def flag_page_for_review(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
        reason: ReviewReason,
    ) -> None:
        stmt = (
            update(Page)
            .where(
                Page.tenant_id == UUID(str(tenant_id)),
                Page.document_version_id == version_id,
                Page.page_number == page_number,
            )
            .values(
                needs_review=True,
                review_reason=case(
                    (Page.review_reason == "legacy_unspecified", Page.review_reason),
                    else_=reason,
                ),
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def clear_page_review_flag(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
    ) -> None:
        """Clear review flag on a page: set needs_review=False and review_reason=NULL."""
        stmt = (
            update(Page)
            .where(
                Page.tenant_id == UUID(str(tenant_id)),
                Page.document_version_id == version_id,
                Page.page_number == page_number,
                Page.review_reason.in_(CLEARABLE_REVIEW_REASONS),
            )
            .values(needs_review=False, review_reason=None)
            .returning(Page.id)
        )
        res = await self.session.execute(stmt)
        updated_id = res.scalar_one_or_none()
        if updated_id is None:
            raise InvalidPageStateError(
                "Review flag cannot be cleared: page not flagged with a clearable reason"
            )
        await self.session.flush()

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
        tid = UUID(str(tenant_id))
        rid = uuid4()
        stmt = (
            pg_insert(PageReading)
            .values(
                id=rid,
                tenant_id=tid,
                page_id=page_id,
                source=source,
                text=text,
                model_id=model_id,
                model_version=model_version,
            )
            .on_conflict_do_nothing(constraint="uq_page_readings_idempotency")
            .returning(PageReading.id)
        )
        res = await self.session.execute(stmt)
        inserted_id = res.scalar_one_or_none()
        if inserted_id is not None:
            await self.session.flush()
            return inserted_id

        existing_stmt = select(PageReading.id).where(
            PageReading.tenant_id == tid,
            PageReading.page_id == page_id,
            PageReading.source == source,
            PageReading.model_id == model_id,
            PageReading.model_version == model_version,
        )
        existing_res = await self.session.execute(existing_stmt)
        return existing_res.scalar_one()

    @staticmethod
    def _map_version_ui_status(
        status: str,
        flagged_count: int,
        version_id: UUID,
        completed_split_versions: set[UUID] | None = None,
        processing_vision_versions: set[UUID] | None = None,
        steps: list[JobStep] | None = None,
    ) -> str:
        """Map raw document_version status to explicit allowlist UI status.

        Allowlist statuses:
        - 'ready' -> 'Ready'
        - 'needs_review' -> 'Ready (N pages need review)' (or 'Ready' if 0)
        - 'failed' -> 'Failed'
        - 'retired' -> 'Retired'
        - 'received' -> 'Queued'
        - 'processing' -> 'Scanning images' (if vision step processing),
                          'Reading' (if split completed and chunking in progress),
                          'Queued' (otherwise)

        Any unknown status raises ValueError and is logged by code only.
        Never falls back to 'Queued'.
        """
        if status == "ready":
            return "Ready"
        if status == "needs_review":
            plural = "s" if flagged_count != 1 else ""
            return f"Ready ({flagged_count} page{plural} need review)"
        if status == "failed":
            return "Failed"
        if status == "retired":
            return "Retired"
        if status == "received":
            return "Queued"
        if status == "processing":
            has_active_vision = False
            has_split_completed = False
            if steps is not None:
                has_active_vision = any(
                    s.step == "vision" and s.status == "processing" for s in steps
                )
                has_split_completed = any(
                    s.step == "split" and s.status == "completed" for s in steps
                )
            if processing_vision_versions and version_id in processing_vision_versions:
                has_active_vision = True
            if completed_split_versions and version_id in completed_split_versions:
                has_split_completed = True

            if has_active_vision:
                return "Scanning images"
            if has_split_completed:
                return "Reading"
            return "Queued"

        logger.error(
            "UNKNOWN_DOCUMENT_VERSION_STATUS",
            extra={"status_code": status, "version_id": str(version_id)},
        )
        raise ValueError(f"Unknown document version status: {status}")

    async def list_documents_with_status(
        self,
        tenant_id: str,
    ) -> list[DocumentStatusInfo]:
        """Retrieve all documents and versions with derived UI states for tenant."""
        tid = UUID(str(tenant_id))

        doc_stmt = (
            select(Document).where(Document.tenant_id == tid).order_by(Document.created_at.desc())
        )
        docs = (await self.session.execute(doc_stmt)).scalars().all()
        if not docs:
            return []

        doc_ids = [doc.id for doc in docs]

        ver_stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.tenant_id == tid, DocumentVersion.document_id.in_(doc_ids))
            .order_by(DocumentVersion.created_at.desc())
        )
        versions = (await self.session.execute(ver_stmt)).scalars().all()
        ver_ids = [ver.id for ver in versions]

        job_steps_by_ver: dict[UUID, list[JobStep]] = {}
        if ver_ids:
            steps_stmt = select(JobStep).where(
                JobStep.tenant_id == tid, JobStep.document_version_id.in_(ver_ids)
            )
            all_steps = (await self.session.execute(steps_stmt)).scalars().all()
            for step in all_steps:
                job_steps_by_ver.setdefault(step.document_version_id, []).append(step)

        page_stats: dict[UUID, tuple[int, int]] = {}
        if ver_ids:
            pages_stmt = (
                select(
                    Page.document_version_id,
                    func.count(Page.id).label("total_pages"),
                    func.count(Page.id).filter(Page.needs_review.is_(True)).label("flagged_pages"),
                )
                .where(Page.tenant_id == tid, Page.document_version_id.in_(ver_ids))
                .group_by(Page.document_version_id)
            )
            for ver_id, total_pages, flagged_pages in (
                await self.session.execute(pages_stmt)
            ).all():
                page_stats[ver_id] = (int(total_pages), int(flagged_pages))

        versions_by_doc: dict[UUID, list[VersionStatusInfo]] = {}
        for ver in versions:
            total_pages, flagged_pages = page_stats.get(ver.id, (0, 0))
            steps = job_steps_by_ver.get(ver.id, [])

            ui_status = self._map_version_ui_status(
                status=ver.status,
                flagged_count=flagged_pages,
                version_id=ver.id,
                steps=steps,
            )

            versions_by_doc.setdefault(ver.document_id, []).append(
                VersionStatusInfo(
                    version_id=str(ver.id),
                    ui_status=ui_status,
                    raw_status=ver.status,
                    flagged_pages_count=flagged_pages,
                    total_pages_count=total_pages,
                    created_at=ver.created_at,
                )
            )

        results: list[DocumentStatusInfo] = []
        for doc in docs:
            results.append(
                DocumentStatusInfo(
                    document_id=str(doc.id),
                    title=doc.title,
                    active_version_id=str(doc.active_version_id) if doc.active_version_id else None,
                    created_at=doc.created_at,
                    is_retired=getattr(doc, "is_retired", False),
                    versions=versions_by_doc.get(doc.id, []),
                )
            )

        return results

    async def retire_document(
        self,
        tenant_id: str,
        document_id: UUID,
    ) -> DocumentRecord:
        """Mark document as retired under row lock, clear active_version_id,
        and deactivate chunks.
        """
        tid = UUID(str(tenant_id))
        doc_stmt = (
            select(Document)
            .where(Document.tenant_id == tid, Document.id == document_id)
            .with_for_update()
        )
        doc = (await self.session.execute(doc_stmt)).scalars().first()
        if doc is None:
            raise DocumentNotFoundError(f"Document {document_id} not found in tenant {tenant_id}")

        doc.is_retired = True
        doc.active_version_id = None

        # Deactivate all chunks belonging to versions of this document
        chunk_stmt = (
            update(Chunk)
            .where(
                Chunk.tenant_id == tid,
                Chunk.document_version_id.in_(
                    select(DocumentVersion.id).where(
                        DocumentVersion.tenant_id == tid,
                        DocumentVersion.document_id == document_id,
                    )
                ),
            )
            .values(is_active=False)
        )
        await self.session.execute(chunk_stmt)

        # Mark all versions of this document as retired
        ver_stmt = (
            update(DocumentVersion)
            .where(DocumentVersion.tenant_id == tid, DocumentVersion.document_id == document_id)
            .values(status="retired")
        )
        await self.session.execute(ver_stmt)
        await self.session.flush()

        return DocumentRecord(
            id=doc.id,
            tenant_id=str(doc.tenant_id),
            title=doc.title,
            active_version_id=doc.active_version_id,
            created_at=doc.created_at,
            is_retired=doc.is_retired,
        )

    async def get_version_review_pages(
        self,
        tenant_id: str,
        version_id: UUID,
    ) -> list[ReviewPageInfo]:
        """Retrieve flagged pages and their readings for administrative review inspection."""
        tid = UUID(str(tenant_id))
        # Anti-probing: verify document version belongs to caller tenant
        ver_stmt = select(DocumentVersion.id).where(
            DocumentVersion.tenant_id == tid,
            DocumentVersion.id == version_id,
        )
        ver_exists = (await self.session.execute(ver_stmt)).scalar_one_or_none()
        if ver_exists is None:
            raise DocumentNotFoundError(f"Version {version_id} not found in tenant {tenant_id}")

        # Query flagged pages
        pages_stmt = (
            select(Page)
            .where(
                Page.tenant_id == tid,
                Page.document_version_id == version_id,
                Page.needs_review.is_(True),
            )
            .order_by(Page.page_number)
        )
        pages = (await self.session.execute(pages_stmt)).scalars().all()
        if not pages:
            return []

        page_ids = [p.id for p in pages]
        readings_stmt = (
            select(PageReading)
            .where(
                PageReading.tenant_id == tid,
                PageReading.page_id.in_(page_ids),
            )
            .order_by(PageReading.created_at.asc())
        )
        readings = (await self.session.execute(readings_stmt)).scalars().all()
        readings_by_page: dict[UUID, list[ReviewReadingInfo]] = {}
        for r in readings:
            readings_by_page.setdefault(r.page_id, []).append(
                ReviewReadingInfo(
                    id=str(r.id),
                    source=r.source,
                    text=r.text,
                    model_id=r.model_id,
                    model_version=r.model_version,
                    created_at=r.created_at,
                )
            )

        results: list[ReviewPageInfo] = []
        for p in pages:
            results.append(
                ReviewPageInfo(
                    page_id=str(p.id),
                    page_number=p.page_number,
                    review_reason=p.review_reason,
                    ocr_confidence=p.ocr_confidence,
                    page_type=p.page_type,
                    is_excluded=getattr(p, "is_excluded", False),
                    has_image=bool(p.image_key),
                    readings=readings_by_page.get(p.id, []),
                )
            )
        return results

    @staticmethod
    def _to_page_record(page: Page) -> PageRecord:
        return PageRecord(
            id=page.id,
            tenant_id=str(page.tenant_id),
            document_version_id=page.document_version_id,
            page_number=page.page_number,
            page_sha256=page.page_sha256,
            page_type=PageType(page.page_type),
            image_key=page.image_key,
            ocr_confidence=page.ocr_confidence,
            needs_review=page.needs_review,
            review_reason=cast(ReviewReason | None, page.review_reason),
            is_excluded=getattr(page, "is_excluded", False),
        )

    async def _assert_no_active_page_job(
        self,
        tenant_id: UUID,
        version_id: UUID,
        page_number: int,
    ) -> None:
        """Refuse with PageJobConflictError if a job for that page is already queued or running."""
        active_step_stmt = select(JobStep).where(
            JobStep.tenant_id == tenant_id,
            JobStep.document_version_id == version_id,
            JobStep.page_number == page_number,
            JobStep.step == "page_processing",
            JobStep.status.in_(ACTIVE_PAGE_MARKER_STATUSES),
        )
        active_step = (await self.session.execute(active_step_stmt)).scalars().first()
        if active_step is not None:
            raise PageJobConflictError(
                "A background processing job for this page is already queued or running"
            )

    async def accept_page_review(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
    ) -> tuple[PageRecord, bool]:
        """Accept flagged page, index reading (visual or text), and evaluate auto-promotion."""
        tid = UUID(str(tenant_id))

        # 1. Lock Document
        doc_stmt = (
            select(Document)
            .where(
                Document.tenant_id == tid,
                Document.id
                == (
                    select(DocumentVersion.document_id)
                    .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
                    .scalar_subquery()
                ),
            )
            .with_for_update()
        )
        doc = (await self.session.execute(doc_stmt)).scalars().first()
        if doc is None:
            raise DocumentNotFoundError(
                f"Document for version {version_id} not found in tenant {tenant_id}"
            )
        if doc.is_retired:
            raise DocumentRetiredError(f"Cannot accept page for retired document {doc.id}")

        # 2. Lock DocumentVersion
        ver_stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
            .with_for_update()
        )
        ver = (await self.session.execute(ver_stmt)).scalars().first()
        if ver is None:
            raise DocumentNotFoundError(f"Version {version_id} not found in tenant {tenant_id}")

        await self._assert_no_active_page_job(tid, version_id, page_number)

        # 3. Lock Page
        page_stmt = (
            select(Page)
            .where(
                Page.tenant_id == tid,
                Page.document_version_id == version_id,
                Page.page_number == page_number,
            )
            .with_for_update()
        )
        page = (await self.session.execute(page_stmt)).scalars().first()
        if page is None:
            raise DocumentNotFoundError(f"Page {page_number} for version {version_id} not found")

        # Double-click idempotency: already accepted
        if not page.needs_review and not getattr(page, "is_excluded", False):
            return self._to_page_record(page), False

        # Invalid page state (excluded or not flagged)
        if getattr(page, "is_excluded", False) or not page.needs_review:
            raise InvalidPageStateError(f"Page {page_number} is in invalid state")

        # Look up readings
        readings_stmt = select(PageReading).where(
            PageReading.tenant_id == tid, PageReading.page_id == page.id
        )
        readings = (await self.session.execute(readings_stmt)).scalars().all()

        vision_reading = next((r for r in readings if r.source == "vision_ingest"), None)
        ocr_reading = next(
            (r for r in readings if r.source in ("ocr", "docling_ocr", "digital_text")), None
        )

        if not vision_reading and not ocr_reading:
            raise NoUsableReadingError(f"No usable reading found for page {page_number}")

        is_live_version = doc.active_version_id == version_id

        if vision_reading:
            evidence_type = "visual"
            page_reading_id = vision_reading.id
            text = vision_reading.text
        else:
            evidence_type = "text"
            page_reading_id = None
            assert ocr_reading is not None
            text = ocr_reading.text

        # Create or update chunk
        chunk_stmt = select(Chunk).where(
            Chunk.tenant_id == tid,
            Chunk.document_version_id == version_id,
            Chunk.page_id == page.id,
        )
        chunk = (await self.session.execute(chunk_stmt)).scalars().first()

        if chunk is not None:
            chunk.evidence_type = evidence_type
            chunk.page_reading_id = page_reading_id
            chunk.text = text
            if is_live_version:
                chunk.is_active = True
        else:
            chunk = Chunk(
                id=uuid4(),
                tenant_id=tid,
                page_id=page.id,
                document_version_id=version_id,
                text=text,
                language="en",
                ts_config="english",
                evidence_type=evidence_type,
                page_reading_id=page_reading_id,
                is_active=is_live_version,
            )
            self.session.add(chunk)

        # Clear needs_review, preserve review_reason
        page.needs_review = False
        await self.session.flush()

        # Check remaining flagged pages
        remaining_stmt = select(func.count(Page.id)).where(
            Page.tenant_id == tid,
            Page.document_version_id == version_id,
            Page.needs_review.is_(True),
        )
        remaining_flagged = (await self.session.execute(remaining_stmt)).scalar_one() or 0

        auto_promoted = False
        if remaining_flagged == 0:
            if doc.active_version_id != version_id:
                # Branch A auto-promote
                old_active_id = doc.active_version_id
                doc.active_version_id = version_id
                ver.status = "ready"

                if old_active_id is not None:
                    old_ver_stmt = select(DocumentVersion).where(
                        DocumentVersion.tenant_id == tid, DocumentVersion.id == old_active_id
                    )
                    old_ver = (await self.session.execute(old_ver_stmt)).scalars().first()
                    if old_ver is not None:
                        old_ver.status = "retired"
                    await self.session.execute(
                        update(Chunk)
                        .where(Chunk.tenant_id == tid, Chunk.document_version_id == old_active_id)
                        .values(is_active=False)
                    )

                excluded_pages_stmt = select(Page.id).where(
                    Page.tenant_id == tid,
                    Page.document_version_id == version_id,
                    Page.is_excluded.is_(True),
                )
                excluded_page_ids = set(
                    (await self.session.execute(excluded_pages_stmt)).scalars().all()
                )

                if excluded_page_ids:
                    await self.session.execute(
                        update(Chunk)
                        .where(
                            Chunk.tenant_id == tid,
                            Chunk.document_version_id == version_id,
                            Chunk.page_id.not_in(excluded_page_ids),
                        )
                        .values(is_active=True)
                    )
                else:
                    await self.session.execute(
                        update(Chunk)
                        .where(Chunk.tenant_id == tid, Chunk.document_version_id == version_id)
                        .values(is_active=True)
                    )

                auto_audit = AuditEvent(
                    tenant_id=tid,
                    user_id=None,
                    event_type="knowledge_compliance",
                    action="version_auto_promoted",
                    resource_id=str(version_id),
                )
                self.session.add(auto_audit)
                auto_promoted = True
            else:
                # Branch B status transition to ready
                ver.status = "ready"

        await self.session.flush()
        return self._to_page_record(page), auto_promoted

    async def exclude_page_review(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
    ) -> tuple[PageRecord, bool]:
        """Exclude flagged page, keep chunks inactive, and evaluate auto-promotion."""
        tid = UUID(str(tenant_id))

        # 1. Lock Document
        doc_stmt = (
            select(Document)
            .where(
                Document.tenant_id == tid,
                Document.id
                == (
                    select(DocumentVersion.document_id)
                    .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
                    .scalar_subquery()
                ),
            )
            .with_for_update()
        )
        doc = (await self.session.execute(doc_stmt)).scalars().first()
        if doc is None:
            raise DocumentNotFoundError(
                f"Document for version {version_id} not found in tenant {tenant_id}"
            )
        if doc.is_retired:
            raise DocumentRetiredError(f"Cannot exclude page for retired document {doc.id}")

        # 2. Lock DocumentVersion
        ver_stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
            .with_for_update()
        )
        ver = (await self.session.execute(ver_stmt)).scalars().first()
        if ver is None:
            raise DocumentNotFoundError(f"Version {version_id} not found in tenant {tenant_id}")

        await self._assert_no_active_page_job(tid, version_id, page_number)

        # 3. Lock Page
        page_stmt = (
            select(Page)
            .where(
                Page.tenant_id == tid,
                Page.document_version_id == version_id,
                Page.page_number == page_number,
            )
            .with_for_update()
        )
        page = (await self.session.execute(page_stmt)).scalars().first()
        if page is None:
            raise DocumentNotFoundError(f"Page {page_number} for version {version_id} not found")

        # Double-click idempotency: already excluded
        if getattr(page, "is_excluded", False) and not page.needs_review:
            return self._to_page_record(page), False

        # Invalid page state (not flagged)
        if not page.needs_review:
            raise InvalidPageStateError(
                f"Page {page_number} is not flagged for review (needs_review=False)"
            )

        page.is_excluded = True
        page.needs_review = False

        # Deactivate chunks for page
        await self.session.execute(
            update(Chunk)
            .where(Chunk.tenant_id == tid, Chunk.page_id == page.id)
            .values(is_active=False)
        )
        await self.session.flush()

        # Check remaining flagged pages
        remaining_stmt = select(func.count(Page.id)).where(
            Page.tenant_id == tid,
            Page.document_version_id == version_id,
            Page.needs_review.is_(True),
        )
        remaining_flagged = (await self.session.execute(remaining_stmt)).scalar_one() or 0

        auto_promoted = False
        if remaining_flagged == 0:
            if doc.active_version_id != version_id:
                # Branch A auto-promote
                old_active_id = doc.active_version_id
                doc.active_version_id = version_id
                ver.status = "ready"

                if old_active_id is not None:
                    old_ver_stmt = select(DocumentVersion).where(
                        DocumentVersion.tenant_id == tid, DocumentVersion.id == old_active_id
                    )
                    old_ver = (await self.session.execute(old_ver_stmt)).scalars().first()
                    if old_ver is not None:
                        old_ver.status = "retired"
                    await self.session.execute(
                        update(Chunk)
                        .where(Chunk.tenant_id == tid, Chunk.document_version_id == old_active_id)
                        .values(is_active=False)
                    )

                excluded_pages_stmt = select(Page.id).where(
                    Page.tenant_id == tid,
                    Page.document_version_id == version_id,
                    Page.is_excluded.is_(True),
                )
                excluded_page_ids = set(
                    (await self.session.execute(excluded_pages_stmt)).scalars().all()
                )

                if excluded_page_ids:
                    await self.session.execute(
                        update(Chunk)
                        .where(
                            Chunk.tenant_id == tid,
                            Chunk.document_version_id == version_id,
                            Chunk.page_id.not_in(excluded_page_ids),
                        )
                        .values(is_active=True)
                    )
                else:
                    await self.session.execute(
                        update(Chunk)
                        .where(Chunk.tenant_id == tid, Chunk.document_version_id == version_id)
                        .values(is_active=True)
                    )

                auto_audit = AuditEvent(
                    tenant_id=tid,
                    user_id=None,
                    event_type="knowledge_compliance",
                    action="version_auto_promoted",
                    resource_id=str(version_id),
                )
                self.session.add(auto_audit)
                auto_promoted = True
            else:
                ver.status = "ready"

        await self.session.flush()
        return self._to_page_record(page), auto_promoted

    async def promote_version(
        self,
        tenant_id: str,
        version_id: UUID,
    ) -> DocumentVersionRecord:
        """Promote version with exclusions and retire previous active version."""
        tid = UUID(str(tenant_id))

        # 1. Lock Document
        doc_stmt = (
            select(Document)
            .where(
                Document.tenant_id == tid,
                Document.id
                == (
                    select(DocumentVersion.document_id)
                    .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
                    .scalar_subquery()
                ),
            )
            .with_for_update()
        )
        doc = (await self.session.execute(doc_stmt)).scalars().first()
        if doc is None:
            raise DocumentNotFoundError(
                f"Document for version {version_id} not found in tenant {tenant_id}"
            )
        if doc.is_retired:
            raise DocumentRetiredError(f"Cannot promote version for retired document {doc.id}")

        # 2. Lock DocumentVersion
        ver_stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
            .with_for_update()
        )
        ver = (await self.session.execute(ver_stmt)).scalars().first()
        if ver is None:
            raise DocumentNotFoundError(f"Version {version_id} not found in tenant {tenant_id}")

        # Precondition: already active ready version is an idempotent no-op
        if doc.active_version_id == version_id and ver.status == "ready":
            return DocumentVersionRecord(
                id=ver.id,
                tenant_id=str(ver.tenant_id),
                document_id=ver.document_id,
                sha256=ver.sha256,
                uploaded_by=str(ver.uploaded_by),
                status=ver.status,
                created_at=ver.created_at,
            )

        if ver.status != "needs_review":
            raise InvalidVersionStateError(
                f"Version {version_id} status '{ver.status}' is not eligible for promote; "
                "must be 'needs_review'"
            )

        old_active_id = doc.active_version_id
        doc.active_version_id = version_id

        # Retire previous version and deactivate its chunks
        if old_active_id is not None and old_active_id != version_id:
            old_ver_stmt = select(DocumentVersion).where(
                DocumentVersion.tenant_id == tid, DocumentVersion.id == old_active_id
            )
            old_ver = (await self.session.execute(old_ver_stmt)).scalars().first()
            if old_ver is not None:
                old_ver.status = "retired"
            await self.session.execute(
                update(Chunk)
                .where(Chunk.tenant_id == tid, Chunk.document_version_id == old_active_id)
                .values(is_active=False)
            )

        # Check remaining flagged pages
        remaining_stmt = select(func.count(Page.id)).where(
            Page.tenant_id == tid,
            Page.document_version_id == version_id,
            Page.needs_review.is_(True),
        )
        remaining_flagged = (await self.session.execute(remaining_stmt)).scalar_one() or 0

        if remaining_flagged > 0:
            ver.status = "needs_review"
        else:
            ver.status = "ready"

        # Inactive chunks for excluded or remaining flagged pages
        inactive_pages_stmt = select(Page.id).where(
            Page.tenant_id == tid,
            Page.document_version_id == version_id,
            (Page.is_excluded.is_(True)) | (Page.needs_review.is_(True)),
        )
        inactive_page_ids = set((await self.session.execute(inactive_pages_stmt)).scalars().all())

        if inactive_page_ids:
            await self.session.execute(
                update(Chunk)
                .where(
                    Chunk.tenant_id == tid,
                    Chunk.document_version_id == version_id,
                    Chunk.page_id.not_in(inactive_page_ids),
                )
                .values(is_active=True)
            )
            await self.session.execute(
                update(Chunk)
                .where(
                    Chunk.tenant_id == tid,
                    Chunk.document_version_id == version_id,
                    Chunk.page_id.in_(inactive_page_ids),
                )
                .values(is_active=False)
            )
        else:
            await self.session.execute(
                update(Chunk)
                .where(Chunk.tenant_id == tid, Chunk.document_version_id == version_id)
                .values(is_active=True)
            )

        await self.session.flush()
        return DocumentVersionRecord(
            id=ver.id,
            tenant_id=str(ver.tenant_id),
            document_id=ver.document_id,
            sha256=ver.sha256,
            uploaded_by=str(ver.uploaded_by),
            status=ver.status,
            created_at=ver.created_at,
        )

    async def check_page_retry_eligibility(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
        current_model_id: str | None = None,
        current_model_version: str | None = None,
        current_processing_version: str = CURRENT_PROCESSING_VERSION,
    ) -> None:
        """Validate whether a flagged page is eligible for retry under row locks."""
        tid = UUID(str(tenant_id))

        # 1. Lock Document
        doc_stmt = (
            select(Document)
            .where(
                Document.tenant_id == tid,
                Document.id
                == (
                    select(DocumentVersion.document_id)
                    .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
                    .scalar_subquery()
                ),
            )
            .with_for_update()
        )
        doc = (await self.session.execute(doc_stmt)).scalars().first()
        if doc is None:
            raise DocumentNotFoundError(
                f"Document for version {version_id} not found in tenant {tenant_id}"
            )
        if doc.is_retired:
            raise DocumentRetiredError(f"Cannot retry page for retired document {doc.id}")

        # 2. Lock DocumentVersion
        ver_stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
            .with_for_update()
        )
        ver = (await self.session.execute(ver_stmt)).scalars().first()
        if ver is None:
            raise DocumentNotFoundError(f"Version {version_id} not found in tenant {tenant_id}")

        # Active job check: Refuse with 409 PAGE_JOB_ALREADY_ACTIVE
        # if a job for that page is already queued or running
        await self._assert_no_active_page_job(tid, version_id, page_number)

        # 3. Lock Page
        page_stmt = (
            select(Page)
            .where(
                Page.tenant_id == tid,
                Page.document_version_id == version_id,
                Page.page_number == page_number,
            )
            .with_for_update()
        )
        page = (await self.session.execute(page_stmt)).scalars().first()
        if page is None:
            raise DocumentNotFoundError(f"Page {page_number} for version {version_id} not found")

        # Flagged check
        if not page.needs_review:
            raise InvalidPageStateError(
                f"Page {page_number} is not flagged for review (needs_review=False)"
            )

        # Check if processing version has changed on existing job step
        step_stmt = (
            select(JobStep)
            .where(
                JobStep.tenant_id == tid,
                JobStep.document_version_id == version_id,
                JobStep.page_number == page_number,
            )
            .order_by(JobStep.created_at.desc())
        )
        latest_step = (await self.session.execute(step_stmt)).scalars().first()

        processing_version_changed = False
        if latest_step is not None and latest_step.processing_version != current_processing_version:
            processing_version_changed = True

        if not processing_version_changed:
            reading_stmt = select(PageReading).where(
                PageReading.tenant_id == tid,
                PageReading.page_id == page.id,
            )
            if current_model_id is not None:
                reading_stmt = reading_stmt.where(PageReading.model_id == current_model_id)
            if current_model_version is not None:
                reading_stmt = reading_stmt.where(
                    PageReading.model_version == current_model_version
                )

            existing_reading = (await self.session.execute(reading_stmt)).scalars().first()
            if existing_reading is not None:
                raise RetryNotUsefulError(
                    f"Retry not useful: page reading already exists for page {page_number} "
                    f"with model {existing_reading.model_id}:{existing_reading.model_version}"
                )

    async def enqueue_page_retry(
        self,
        tenant_id: str,
        version_id: UUID,
        page_number: int,
    ) -> PageRecord:
        """Mark page as retry enqueued (page stays flagged) and record active job step."""
        tid = UUID(str(tenant_id))

        page_stmt = (
            select(Page)
            .where(
                Page.tenant_id == tid,
                Page.document_version_id == version_id,
                Page.page_number == page_number,
            )
            .with_for_update()
        )
        page = (await self.session.execute(page_stmt)).scalars().first()
        if page is None:
            raise DocumentNotFoundError(f"Page {page_number} for version {version_id} not found")

        job_stmt = select(Job).where(
            Job.tenant_id == tid,
            Job.document_version_id == version_id,
        )
        job = (await self.session.execute(job_stmt)).scalars().first()
        if job is None:
            job = Job(
                id=uuid4(),
                tenant_id=tid,
                document_version_id=version_id,
                status="processing",
            )
            self.session.add(job)
            await self.session.flush()
        else:
            job.status = "processing"

        await self.record_page_processing_marker(
            tenant_id=tenant_id,
            version_id=version_id,
            page_number=page_number,
            status="queued",
            processing_version=CURRENT_PROCESSING_VERSION,
        )

        # Page stays flagged until the retried run finishes!
        page.needs_review = True
        await self.session.flush()
        return self._to_page_record(page)

    async def re_evaluate_version_after_retry(
        self,
        tenant_id: str,
        version_id: UUID,
    ) -> tuple[DocumentVersionRecord, bool]:
        """Re-evaluate version completion after a retried page finishes under row locks."""
        tid = UUID(str(tenant_id))

        # 1. Lock Document
        doc_stmt = (
            select(Document)
            .where(
                Document.tenant_id == tid,
                Document.id
                == (
                    select(DocumentVersion.document_id)
                    .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
                    .scalar_subquery()
                ),
            )
            .with_for_update()
        )
        doc = (await self.session.execute(doc_stmt)).scalars().first()
        if doc is None:
            raise DocumentNotFoundError(
                f"Document for version {version_id} not found in tenant {tenant_id}"
            )
        if doc.is_retired:
            raise DocumentRetiredError(f"Cannot re-evaluate retry on retired document {doc.id}")

        # 2. Lock DocumentVersion
        ver_stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.tenant_id == tid, DocumentVersion.id == version_id)
            .with_for_update()
        )
        ver = (await self.session.execute(ver_stmt)).scalars().first()
        if ver is None:
            raise DocumentNotFoundError(f"Version {version_id} not found in tenant {tenant_id}")

        # Check remaining flagged pages (unexcluded pages that need review)
        remaining_stmt = select(func.count(Page.id)).where(
            Page.tenant_id == tid,
            Page.document_version_id == version_id,
            Page.needs_review.is_(True),
            Page.is_excluded.is_(False),
        )
        remaining_flagged = (await self.session.execute(remaining_stmt)).scalar_one() or 0

        is_live_version = doc.active_version_id == version_id
        auto_promoted = False

        if is_live_version:
            # Activate chunks for non-excluded, non-flagged pages on this version
            inactive_stmt = select(Page.id).where(
                Page.tenant_id == tid,
                Page.document_version_id == version_id,
                (Page.is_excluded.is_(True)) | (Page.needs_review.is_(True)),
            )
            inactive_page_ids = set((await self.session.execute(inactive_stmt)).scalars().all())
            if inactive_page_ids:
                await self.session.execute(
                    update(Chunk)
                    .where(
                        Chunk.tenant_id == tid,
                        Chunk.document_version_id == version_id,
                        Chunk.page_id.not_in(inactive_page_ids),
                    )
                    .values(is_active=True)
                )
            else:
                await self.session.execute(
                    update(Chunk)
                    .where(Chunk.tenant_id == tid, Chunk.document_version_id == version_id)
                    .values(is_active=True)
                )

            # Branch B transition: if no flagged pages remain, status goes to ready
            if remaining_flagged == 0:
                ver.status = "ready"
        else:
            # Branch A: if no flagged pages remain, auto-promote version
            if remaining_flagged == 0:
                old_active_id = doc.active_version_id
                doc.active_version_id = version_id
                ver.status = "ready"

                if old_active_id is not None:
                    old_ver_stmt = select(DocumentVersion).where(
                        DocumentVersion.tenant_id == tid, DocumentVersion.id == old_active_id
                    )
                    old_ver = (await self.session.execute(old_ver_stmt)).scalars().first()
                    if old_ver is not None:
                        old_ver.status = "retired"
                    await self.session.execute(
                        update(Chunk)
                        .where(Chunk.tenant_id == tid, Chunk.document_version_id == old_active_id)
                        .values(is_active=False)
                    )

                excluded_pages_stmt = select(Page.id).where(
                    Page.tenant_id == tid,
                    Page.document_version_id == version_id,
                    Page.is_excluded.is_(True),
                )
                excluded_page_ids = set(
                    (await self.session.execute(excluded_pages_stmt)).scalars().all()
                )
                if excluded_page_ids:
                    await self.session.execute(
                        update(Chunk)
                        .where(
                            Chunk.tenant_id == tid,
                            Chunk.document_version_id == version_id,
                            Chunk.page_id.not_in(excluded_page_ids),
                        )
                        .values(is_active=True)
                    )
                else:
                    await self.session.execute(
                        update(Chunk)
                        .where(Chunk.tenant_id == tid, Chunk.document_version_id == version_id)
                        .values(is_active=True)
                    )

                auto_audit = AuditEvent(
                    tenant_id=tid,
                    user_id=None,
                    event_type="knowledge_compliance",
                    action="version_auto_promoted",
                    resource_id=str(version_id),
                )
                self.session.add(auto_audit)
                auto_promoted = True

        await self.session.flush()
        ver_rec = DocumentVersionRecord(
            id=ver.id,
            tenant_id=str(ver.tenant_id),
            document_id=ver.document_id,
            sha256=ver.sha256,
            uploaded_by=str(ver.uploaded_by),
            status=ver.status,
            created_at=ver.created_at,
        )
        return ver_rec, auto_promoted
