"""Integration tests for the per-page 'page_processing' active job marker.

Verifies:
a. While page work is running, marker is visible as 'running' and accept returns
   PageJobConflictError.
b. After a successful run, marker is 'completed' and accept succeeds.
c. Retry sets marker 'queued': accept and a second retry both get PageJobConflictError.
   After worker runs: 'completed', accept succeeds.
d. A leftover JobStep(step="vision", status="processing") does not block accept.
e. Final-retry failure: marker ends 'failed'.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from procrastinate import JobContext
from procrastinate.jobs import Job as ProcrastinateJob
from sqlalchemy import select, text

from app.adapters.db.postgres_document_repository import PostgresDocumentRepository
from app.adapters.models.mock import MockModelProvider
from app.adapters.storage.local import LocalStorageBackend
from app.core.ingestion.pipeline import PagePipelineService
from app.db.models import JobStep, Page
from app.db.session import unscoped_session, worker_tenant_session
from app.ports.errors import PageJobConflictError, TransientIngestionError
from app.ports.types import (
    ModelRole,
    StorageKey,
    WorkerContext,
)
from app.ports.types import (
    PageReading as PageReadingDTO,
)
from app.worker import app as procrastinate_app
from app.worker import process_page_job, run_page_job
from tests.fixtures.database_seeding import (
    seed_document,
    seed_page,
    seed_reading,
    seed_version,
)
from tests.integration.test_upload_vision import (
    _create_sample_png_bytes,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def seeded_tenant_and_user() -> tuple[UUID, UUID]:
    """Helper to seed a tenant and user for testing."""
    tenant_id = uuid4()
    user_id = uuid4()

    async with unscoped_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, name) VALUES (:tid, 'Marker Test Tenant')"),
            {"tid": tenant_id},
        )
        await session.execute(
            text("INSERT INTO users (id, email, hashed_password) VALUES (:uid, :email, 'hash')"),
            {"uid": user_id, "email": f"marker-test-{user_id.hex[:8]}@example.com"},
        )
    return tenant_id, user_id


def _create_job_context(
    tenant_id: UUID,
    version_id: UUID,
    page_number: int,
    attempts: int = 1,
) -> JobContext:
    proc_job = ProcrastinateJob(
        id=1,
        queue="ingestion",
        lock=None,
        queueing_lock=None,
        task_name=process_page_job.name,
        task_kwargs={
            "tenant_id": str(tenant_id),
            "document_version_id": str(version_id),
            "page_number": page_number,
        },
        attempts=attempts,
    )
    return JobContext(
        app=procrastinate_app,
        job=proc_job,
        start_timestamp=0.0,
        abort_reason=lambda: None,
    )


class RecoveringVisionProvider(MockModelProvider):
    async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReadingDTO:
        _ = (role, image)
        return PageReadingDTO(
            reading="Clean vision transcription text.",
            confidence=0.95,
            model_id="mock-vision",
            model_version="1.0",
        )


@pytest.mark.asyncio
async def test_page_processing_marker_running_blocks_accept(
    seeded_tenant_and_user: tuple[UUID, UUID],
    tmp_path: Any,
) -> None:
    """a. While page work is running (hook inside process_page, check from a
    separate session/connection): marker is visible as 'running' and accept returns
    PageJobConflictError.
    """
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Marker Running Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        p_id = await seed_page(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            page_number=1,
            page_type="scanned",
            needs_review=True,
            review_reason="low_ocr_confidence",
        )
        await seed_reading(
            session,
            tenant_id=tenant_id,
            page_id=p_id,
            source="ocr",
            body="Existing reading",
            model_id="mock-ocr",
            model_version="1.0",
        )

        storage = LocalStorageBackend(base_dir=str(tmp_path))
        storage_key = StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/p1.png")
        await storage.put(storage_key, _create_sample_png_bytes(), "image/png")

    hook_entered = asyncio.Event()
    allow_finish = asyncio.Event()
    orig_process_page = PagePipelineService.process_page

    async def hooked_process_page(
        self_pipeline: PagePipelineService, *args: Any, **kwargs: Any
    ) -> None:
        hook_entered.set()
        await allow_finish.wait()
        await orig_process_page(self_pipeline, *args, **kwargs)

    job_ctx = _create_job_context(tenant_id, ver_id, page_number=1)

    with patch.object(
        PagePipelineService, "process_page", side_effect=hooked_process_page, autospec=True
    ):
        worker_task = asyncio.create_task(
            run_page_job(
                context=job_ctx,
                tenant_id=str(tenant_id),
                document_version_id=str(ver_id),
                page_number=1,
            )
        )

        try:
            await hook_entered.wait()

            # Separate session/connection to observe Transaction 0 committed state
            sep_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))
            async with worker_tenant_session(sep_ctx) as sep_session:
                marker_stmt = select(JobStep).where(
                    JobStep.tenant_id == tenant_id,
                    JobStep.document_version_id == ver_id,
                    JobStep.page_number == 1,
                    JobStep.step == "page_processing",
                )
                marker_res = await sep_session.execute(marker_stmt)
                marker = marker_res.scalars().first()
                assert marker is not None, (
                    "page_processing marker row was not written by Transaction 0"
                )
                assert marker.status == "running", (
                    f"Expected status 'running', got '{marker.status}'"
                )

                sep_repo = PostgresDocumentRepository(sep_session)
                with pytest.raises(PageJobConflictError):
                    await sep_repo.accept_page_review(
                        tenant_id=str(tenant_id),
                        version_id=ver_id,
                        page_number=1,
                    )
        finally:
            allow_finish.set()
            await worker_task


@pytest.mark.asyncio
async def test_page_processing_successful_run_sets_marker_completed_and_accept_succeeds(
    seeded_tenant_and_user: tuple[UUID, UUID],
    tmp_path: Any,
) -> None:
    """b. After a successful run: marker is 'completed' and accept succeeds."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Success Marker Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        p_id = await seed_page(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            page_number=1,
            page_type="scanned",
            needs_review=True,
            review_reason="low_ocr_confidence",
        )
        await seed_reading(
            session,
            tenant_id=tenant_id,
            page_id=p_id,
            source="ocr",
            body="Existing reading",
            model_id="mock-ocr",
            model_version="1.0",
        )

        storage = LocalStorageBackend(base_dir=str(tmp_path))
        storage_key = StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/p1.png")
        await storage.put(storage_key, _create_sample_png_bytes(), "image/png")

    job_ctx = _create_job_context(tenant_id, ver_id, page_number=1)
    await run_page_job(
        context=job_ctx,
        tenant_id=str(tenant_id),
        document_version_id=str(ver_id),
        page_number=1,
    )

    sep_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))
    async with worker_tenant_session(sep_ctx) as sep_session:
        marker_stmt = select(JobStep).where(
            JobStep.tenant_id == tenant_id,
            JobStep.document_version_id == ver_id,
            JobStep.page_number == 1,
            JobStep.step == "page_processing",
        )
        marker_res = await sep_session.execute(marker_stmt)
        marker = marker_res.scalars().first()
        assert marker is not None, "page_processing marker row was not found"
        assert marker.status == "completed", f"Expected 'completed', got '{marker.status}'"

        repo = PostgresDocumentRepository(sep_session)
        await repo.flag_page_for_review(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
            reason="low_ocr_confidence",
        )

        page_rec, _ = await repo.accept_page_review(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
        )
        assert page_rec.needs_review is False


@pytest.mark.asyncio
async def test_retry_sets_marker_queued_and_blocks_accept_and_second_retry(
    seeded_tenant_and_user: tuple[UUID, UUID],
    tmp_path: Any,
) -> None:
    """c. Retry sets marker 'queued': accept and a second retry both get PageJobConflictError.

    After the worker runs it: marker 'completed', accept succeeds.
    """
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Retry Marker Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        p_id = await seed_page(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            page_number=1,
            page_type="scanned",
            needs_review=True,
            review_reason="low_ocr_confidence",
        )
        await seed_reading(
            session,
            tenant_id=tenant_id,
            page_id=p_id,
            source="ocr",
            body="Existing reading",
            model_id="mock-ocr",
            model_version="1.0",
        )

        storage = LocalStorageBackend(base_dir=str(tmp_path))
        storage_key = StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/p1.png")
        await storage.put(storage_key, _create_sample_png_bytes(), "image/png")

        repo = PostgresDocumentRepository(session)
        # Enqueue page retry
        await repo.enqueue_page_retry(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
        )

        # Marker must be 'queued'
        marker_stmt = select(JobStep).where(
            JobStep.tenant_id == tenant_id,
            JobStep.document_version_id == ver_id,
            JobStep.page_number == 1,
            JobStep.step == "page_processing",
        )
        marker = (await session.execute(marker_stmt)).scalars().first()
        assert marker is not None, "page_processing marker not found after retry"
        assert marker.status == "queued", f"Expected 'queued', got '{marker.status}'"

        # Accept gets PageJobConflictError
        with pytest.raises(PageJobConflictError):
            await repo.accept_page_review(
                tenant_id=str(tenant_id),
                version_id=ver_id,
                page_number=1,
            )

        # Second retry gets PageJobConflictError
        with pytest.raises(PageJobConflictError):
            await repo.check_page_retry_eligibility(
                tenant_id=str(tenant_id),
                version_id=ver_id,
                page_number=1,
            )

    # Worker runs it
    job_ctx = _create_job_context(tenant_id, ver_id, page_number=1)
    await run_page_job(
        context=job_ctx,
        tenant_id=str(tenant_id),
        document_version_id=str(ver_id),
        page_number=1,
    )

    # After worker: marker is 'completed', accept succeeds
    async with worker_tenant_session(worker_ctx) as session:
        marker = (await session.execute(marker_stmt)).scalars().first()
        assert marker is not None
        assert marker.status == "completed", f"Expected 'completed', got '{marker.status}'"

        repo = PostgresDocumentRepository(session)
        await repo.flag_page_for_review(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
            reason="low_ocr_confidence",
        )
        page_rec, _ = await repo.accept_page_review(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
        )
        assert page_rec.needs_review is False


@pytest.mark.asyncio
async def test_leftover_vision_processing_step_does_not_block_accept(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """d. A leftover JobStep(step='vision', status='processing') does not block accept."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Leftover Step Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        p_id = await seed_page(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            page_number=1,
            page_type="scanned",
            needs_review=True,
            review_reason="low_ocr_confidence",
        )
        await seed_reading(
            session,
            tenant_id=tenant_id,
            page_id=p_id,
            source="ocr",
            body="Existing reading",
            model_id="mock-ocr",
            model_version="1.0",
        )

        repo = PostgresDocumentRepository(session)
        # Record leftover vision step in 'processing' status
        await repo.record_job_step(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
            step="vision",
            status="processing",
            processing_version="v1",
        )

        # Call accept on that page - must succeed and NOT block
        page_rec, _ = await repo.accept_page_review(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
        )
        assert page_rec.needs_review is False


@pytest.mark.asyncio
async def test_final_retry_failure_sets_marker_failed(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """e. Final-retry failure: marker ends 'failed'."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Failed Marker Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        await seed_page(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            page_number=1,
            page_type="scanned",
            needs_review=False,
            review_reason=None,
        )

    # attempts=5 is final attempt for ingestion_retry_strategy (max_attempts=5)
    job_ctx = _create_job_context(tenant_id, ver_id, page_number=1, attempts=5)

    async def fail_process_page(
        self_pipeline: PagePipelineService, *args: Any, **kwargs: Any
    ) -> None:
        _ = (self_pipeline, args, kwargs)
        raise TransientIngestionError(message="Database error", error_code="40001")

    with patch.object(
        PagePipelineService, "process_page", side_effect=fail_process_page, autospec=True
    ):
        await run_page_job(
            context=job_ctx,
            tenant_id=str(tenant_id),
            document_version_id=str(ver_id),
            page_number=1,
        )

    async with worker_tenant_session(worker_ctx) as session:
        marker_stmt = select(JobStep).where(
            JobStep.tenant_id == tenant_id,
            JobStep.document_version_id == ver_id,
            JobStep.page_number == 1,
            JobStep.step == "page_processing",
        )
        marker = (await session.execute(marker_stmt)).scalars().first()
        assert marker is not None, "page_processing marker was not written on final-retry failure"
        assert marker.status == "failed", f"Expected 'failed', got '{marker.status}'"

        page_stmt = select(Page).where(
            Page.tenant_id == tenant_id,
            Page.document_version_id == ver_id,
            Page.page_number == 1,
        )
        page = (await session.execute(page_stmt)).scalar_one()
        assert page.needs_review is True
        assert page.review_reason == "processing_failed"
