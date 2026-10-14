"""Integration tests verifying split_document_job creates and reuses Job row."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from procrastinate import JobContext
from procrastinate.jobs import Job as ProcrastinateJob
from sqlalchemy import select, text

from app.adapters.db.postgres_document_repository import PostgresDocumentRepository
from app.db.models import Job, JobStep
from app.db.session import unscoped_session, worker_tenant_session
from app.ports.types import WorkerContext
from app.worker import app as procrastinate_app
from app.worker import process_page_job, run_split_document_job, split_document_job
from tests.fixtures.database_seeding import seed_document, seed_page, seed_version


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def seeded_tenant_and_user() -> tuple[UUID, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()

    async with unscoped_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, name) VALUES (:tid, 'Split Job Tenant')"),
            {"tid": tenant_id},
        )
        await session.execute(
            text("INSERT INTO users (id, email, hashed_password) VALUES (:uid, :email, 'hash')"),
            {"uid": user_id, "email": f"split-test-{user_id.hex[:8]}@example.com"},
        )
    return tenant_id, user_id


def _create_split_job_context(
    tenant_id: UUID,
    version_id: UUID,
) -> JobContext:
    proc_job = ProcrastinateJob(
        id=1,
        queue="ingestion",
        lock=None,
        queueing_lock=None,
        task_name=split_document_job.name,
        task_kwargs={
            "tenant_id": str(tenant_id),
            "document_version_id": str(version_id),
        },
        attempts=1,
    )
    return JobContext(
        app=procrastinate_app,
        job=proc_job,
        start_timestamp=0.0,
        abort_reason=lambda: None,
    )


@pytest.mark.asyncio
async def test_split_job_creates_exactly_one_job_row(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """a. Running the split job for a version with no Job row creates exactly one Job row."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Split Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)

        # Confirm no Job row initially
        stmt = select(Job).where(
            Job.tenant_id == tenant_id,
            Job.document_version_id == ver_id,
        )
        initial_jobs = (await session.execute(stmt)).scalars().all()
        assert len(initial_jobs) == 0

    job_ctx = _create_split_job_context(tenant_id, ver_id)
    with patch.object(process_page_job, "defer_async", new_callable=AsyncMock):
        await run_split_document_job(
            context=job_ctx,
            tenant_id=str(tenant_id),
            document_version_id=str(ver_id),
        )

    async with worker_tenant_session(worker_ctx) as session:
        stmt = select(Job).where(
            Job.tenant_id == tenant_id,
            Job.document_version_id == ver_id,
        )
        jobs = (await session.execute(stmt)).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].status == "processing"


@pytest.mark.asyncio
async def test_split_job_retried_leaves_exactly_one_job_row_and_reuses_it(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """b. Running it twice (split retried) still leaves exactly one Job row, and reuses it."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Split Retry Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)

    job_ctx = _create_split_job_context(tenant_id, ver_id)
    with patch.object(process_page_job, "defer_async", new_callable=AsyncMock):
        # First run
        await run_split_document_job(
            context=job_ctx,
            tenant_id=str(tenant_id),
            document_version_id=str(ver_id),
        )

    async with worker_tenant_session(worker_ctx) as session:
        stmt = select(Job).where(
            Job.tenant_id == tenant_id,
            Job.document_version_id == ver_id,
        )
        first_jobs = (await session.execute(stmt)).scalars().all()
        assert len(first_jobs) == 1
        first_job_id = first_jobs[0].id

    # Second run (simulating retried split job)
    with patch.object(process_page_job, "defer_async", new_callable=AsyncMock):
        await run_split_document_job(
            context=job_ctx,
            tenant_id=str(tenant_id),
            document_version_id=str(ver_id),
        )

    async with worker_tenant_session(worker_ctx) as session:
        stmt = select(Job).where(
            Job.tenant_id == tenant_id,
            Job.document_version_id == ver_id,
        )
        second_jobs = (await session.execute(stmt)).scalars().all()
        assert len(second_jobs) == 1
        assert second_jobs[0].id == first_job_id


@pytest.mark.asyncio
async def test_after_split_record_page_processing_marker_succeeds(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """c. After the split job, record_page_processing_marker succeeds (no QueueError)."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Split Marker Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        await seed_page(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            page_number=1,
            page_type="scanned",
            needs_review=True,
            review_reason="low_ocr_confidence",
        )

    job_ctx = _create_split_job_context(tenant_id, ver_id)
    with patch.object(process_page_job, "defer_async", new_callable=AsyncMock):
        await run_split_document_job(
            context=job_ctx,
            tenant_id=str(tenant_id),
            document_version_id=str(ver_id),
        )

    # Now Transaction 0 of run_page_job runs record_page_processing_marker
    async with worker_tenant_session(worker_ctx) as session:
        repo = PostgresDocumentRepository(session)
        # Must not raise QueueError
        await repo.record_page_processing_marker(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
            status="running",
        )

        marker_stmt = select(JobStep).where(
            JobStep.tenant_id == tenant_id,
            JobStep.document_version_id == ver_id,
            JobStep.page_number == 1,
            JobStep.step == "page_processing",
        )
        marker = (await session.execute(marker_stmt)).scalars().first()
        assert marker is not None
        assert marker.status == "running"
