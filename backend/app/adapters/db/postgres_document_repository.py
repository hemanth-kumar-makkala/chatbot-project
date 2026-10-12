"""PostgreSQL adapter for DocumentRepository port."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
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
from app.ports.errors import DocumentNotFoundError, DuplicateVersionError
from app.ports.types import (
    DocumentRecord,
    DocumentVersionRecord,
    PageRecord,
    PageType,
    ReviewReason,
)


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
            .on_conflict_do_nothing(
                index_elements=[
                    JobStep.tenant_id,
                    JobStep.document_version_id,
                    JobStep.page_number,
                    JobStep.step,
                    JobStep.processing_version,
                ]
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
        values: dict[str, Any] = {
            "needs_review": True,
            "review_reason": reason,
        }
        stmt = (
            update(Page)
            .where(
                Page.tenant_id == UUID(str(tenant_id)),
                Page.document_version_id == version_id,
                Page.page_number == page_number,
            )
            .values(**values)
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
            )
            .values(needs_review=False, review_reason=None)
        )
        await self.session.execute(stmt)
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
