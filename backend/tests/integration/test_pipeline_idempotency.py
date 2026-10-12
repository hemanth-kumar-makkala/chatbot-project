"""Integration tests for pipeline idempotency and error classification (Task 2.7)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.adapters.chunking.mock import MockChunker
from app.adapters.db.postgres_document_repository import PostgresDocumentRepository
from app.adapters.embedding.mock import MockEmbeddingProvider
from app.adapters.parsing.mock import MockDocumentParser
from app.core.ingestion.pipeline import PagePipelineService, is_retryable_error
from app.db.models import Chunk, Page
from app.db.session import worker_tenant_session
from app.ports.types import ParsedPageContent, WorkerContext
from tests.fixtures.database_seeding import (
    seed_document,
    seed_page,
    seed_version,
)


@pytest.fixture
async def seeded_tenant_and_user() -> tuple[UUID, UUID]:
    """Helper to seed a tenant and user for testing."""
    tenant_id = uuid4()
    user_id = uuid4()
    from app.db.session import unscoped_session

    async with unscoped_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, name) VALUES (:tid, 'Pipeline Test Tenant')"),
            {"tid": tenant_id},
        )
        await session.execute(
            text("INSERT INTO users (id, email, hashed_password) VALUES (:uid, :email, 'hash')"),
            {"uid": user_id, "email": f"pipeline-test-{user_id.hex[:8]}@example.com"},
        )
    return tenant_id, user_id


class TestErrorClassification:
    """Verifies transient vs permanent error retry classification."""

    def test_temporary_error_retry_classification(self) -> None:
        """Transient DB or network errors marked retryable; logical/data errors are not."""
        # Transient errors -> True
        assert is_retryable_error(OperationalError("stmt", {}, Exception("connection closed")))
        assert is_retryable_error(ConnectionError("Connection reset by peer"))
        assert is_retryable_error(TimeoutError("Operation timed out"))
        assert is_retryable_error(OSError("Host unreachable"))

        invalidated_dbapi = DBAPIError("stmt", {}, Exception("broken"))
        invalidated_dbapi.connection_invalidated = True
        assert is_retryable_error(invalidated_dbapi)

        # Permanent errors -> False
        assert not is_retryable_error(ValueError("Invalid document structure"))
        assert not is_retryable_error(KeyError("missing_field"))
        assert not is_retryable_error(TypeError("unexpected type"))


@pytest.mark.asyncio
class TestPipelineExecutionAndIdempotency:
    """Verifies per-page pipeline idempotent re-runs and permanent failure handling."""

    async def test_pipeline_re_run_zero_duplicate_chunks(
        self, seeded_tenant_and_user: tuple[UUID, UUID]
    ) -> None:
        """Running page pipeline twice on the same page produces zero duplicate chunks."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Idempotency Test Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            page_id = await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)

            repo = PostgresDocumentRepository(session)
            parser = MockDocumentParser()
            chunker = MockChunker()
            embedder = MockEmbeddingProvider()

            pipeline = PagePipelineService(
                repository=repo,
                parser=parser,
                chunker=chunker,
                embedding_provider=embedder,
            )

            # First run: process page
            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )

            count_first_run = (
                await session.execute(select(func.count(Chunk.id)).where(Chunk.page_id == page_id))
            ).scalar_one()
            assert count_first_run > 0, "First pipeline run should produce chunks"

            # Second run: re-process the exact same page
            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )

            count_second_run = (
                await session.execute(select(func.count(Chunk.id)).where(Chunk.page_id == page_id))
            ).scalar_one()

            # Zero duplicate chunks on re-run
            assert count_second_run == count_first_run, (
                f"Duplicate chunks created! First: {count_first_run}, second: {count_second_run}"
            )

    async def test_permanent_error_flags_page_and_does_not_infinite_retry(
        self, seeded_tenant_and_user: tuple[UUID, UUID]
    ) -> None:
        """Permanent unrecoverable error marks page needs_review=True, avoiding infinite retry."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Corrupt Page Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            page_id = await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)

            repo = PostgresDocumentRepository(session)

            # Faulty parser simulating fatal permanent error (e.g. corrupt image / format)
            class FaultyParser(MockDocumentParser):
                async def extract(self, *args: object, **kwargs: object) -> Any:
                    _ = (args, kwargs)
                    raise ValueError("Fatal corrupt stream: invalid bytes")

            pipeline = PagePipelineService(
                repository=repo,
                parser=FaultyParser(),
                chunker=MockChunker(),
                embedding_provider=MockEmbeddingProvider(),
            )

            # Should catch permanent error, flag page needs_review=True, and NOT re-raise
            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )

            page_row = (await session.execute(select(Page).where(Page.id == page_id))).scalar_one()
            assert page_row.needs_review is True, (
                "Page with permanent failure must be flagged for review"
            )
            assert page_row.review_reason == "processing_failed"

    async def test_low_ocr_page_flagged_with_low_ocr_confidence_reason(
        self, seeded_tenant_and_user: tuple[UUID, UUID]
    ) -> None:
        """Low-OCR page evaluated by pipeline has review_reason='low_ocr_confidence'."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Low OCR Scanned Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            page_id = await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_id,
                page_number=1,
                page_type="scanned",
            )

            repo = PostgresDocumentRepository(session)

            class LowOcrParser(MockDocumentParser):
                async def extract(self, *args: object, **kwargs: object) -> ParsedPageContent:
                    _ = (args, kwargs)
                    return ParsedPageContent(
                        page_number=1,
                        text="Unclear degraded blurry scan",
                        confidence=0.45,
                    )

            pipeline = PagePipelineService(
                repository=repo,
                parser=LowOcrParser(),
                chunker=MockChunker(),
                embedding_provider=MockEmbeddingProvider(),
            )

            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )

            page_row = (await session.execute(select(Page).where(Page.id == page_id))).scalar_one()
            assert page_row.needs_review is True
            assert page_row.review_reason == "low_ocr_confidence"

            # Assert chunks were excluded from indexing (Invariant 6)
            chunk_count = (
                await session.execute(select(func.count(Chunk.id)).where(Chunk.page_id == page_id))
            ).scalar_one()
            assert chunk_count == 0

    async def test_split_job_rerun_queues_jobs_for_pages_without_completed_step(
        self, seeded_tenant_and_user: tuple[UUID, UUID]
    ) -> None:
        """Re-run of split job queues tasks for every page lacking a completed step."""
        from app.worker import enqueue_uncompleted_page_jobs
        from tests.fixtures.database_seeding import seed_job

        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Crash Recovery Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            # Create 3 pages (simulating pages persisted before crash)
            await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)
            await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=2)
            await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=3)

            # Page 1 has completed step in job_steps
            await seed_job(
                session, tenant_id=tenant_id, ver_id=ver_id, job_id=UUID(worker_ctx.job_id)
            )
            await session.execute(
                text(
                    "INSERT INTO job_steps "
                    "(id, tenant_id, job_id, document_version_id, page_number, step, "
                    "processing_version, status) "
                    "VALUES (:id, :tid, :jid, :vid, 1, 'chunking', 'v1', 'completed') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": uuid4(), "tid": tenant_id, "jid": UUID(worker_ctx.job_id), "vid": ver_id},
            )

            repo = PostgresDocumentRepository(session)
            enqueued: list[int] = []

            async def fake_defer(t_id: str, v_id: str, p_num: int) -> None:
                _ = (t_id, v_id)
                enqueued.append(p_num)

            # Run recovery: should pick up uncompleted pages [2, 3]
            res = await enqueue_uncompleted_page_jobs(
                tenant_id=str(tenant_id),
                document_version_id=ver_id,
                repository=repo,
                defer_fn=fake_defer,
            )
            assert res == [2, 3]
            assert enqueued == [2, 3]

            # Now simulate page 2 completing
            await session.execute(
                text(
                    "INSERT INTO job_steps "
                    "(id, tenant_id, job_id, document_version_id, page_number, step, "
                    "processing_version, status) "
                    "VALUES (:id, :tid, :jid, :vid, 2, 'chunking', 'v1', 'completed') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": uuid4(), "tid": tenant_id, "jid": UUID(worker_ctx.job_id), "vid": ver_id},
            )
            enqueued.clear()

            # Re-run: only page 3 is queued
            res2 = await enqueue_uncompleted_page_jobs(
                tenant_id=str(tenant_id),
                document_version_id=ver_id,
                repository=repo,
                defer_fn=fake_defer,
            )
            assert res2 == [3]
            assert enqueued == [3]

    async def test_all_pages_done_stuck_processing_activated_by_recovery(
        self, seeded_tenant_and_user: tuple[UUID, UUID]
    ) -> None:
        """Version stuck in processing when all pages are terminal is activated by recovery."""
        from app.db.models import Document, DocumentVersion
        from app.worker import enqueue_uncompleted_page_jobs
        from tests.fixtures.database_seeding import seed_chunk, seed_job

        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Stuck Recovery Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            p1 = await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)
            p2 = await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=2)
            await seed_chunk(
                session, tenant_id=tenant_id, page_id=p1, ver_id=ver_id, is_active=False
            )
            await seed_chunk(
                session, tenant_id=tenant_id, page_id=p2, ver_id=ver_id, is_active=False
            )

            # Both pages have terminal job_steps (1 completed, 1 failed)
            await seed_job(
                session, tenant_id=tenant_id, ver_id=ver_id, job_id=UUID(worker_ctx.job_id)
            )
            await session.execute(
                text(
                    "INSERT INTO job_steps "
                    "(id, tenant_id, job_id, document_version_id, page_number, step, "
                    "processing_version, status) "
                    "VALUES (:id1, :tid, :jid, :vid, 1, 'chunking', 'v1', 'completed'), "
                    "       (:id2, :tid, :jid, :vid, 2, 'chunking', 'v1', 'failed') "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id1": uuid4(),
                    "id2": uuid4(),
                    "tid": tenant_id,
                    "jid": UUID(worker_ctx.job_id),
                    "vid": ver_id,
                },
            )
            # Page 2 flagged for review
            await session.execute(
                text(
                    "UPDATE pages SET needs_review = true, "
                    "review_reason = 'low_ocr_confidence' WHERE id = :pid"
                ),
                {"pid": p2},
            )

            repo = PostgresDocumentRepository(session)
            # Recovery executes when 0 uncompleted pages remain
            uncompleted = await enqueue_uncompleted_page_jobs(
                tenant_id=str(tenant_id),
                document_version_id=ver_id,
                repository=repo,
                session=session,
            )
            assert uncompleted == []

            # Verify version activated and left 'processing'
            ver_row = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == ver_id))
            ).scalar_one()
            assert ver_row.status == "needs_review"
            doc_row = (
                await session.execute(select(Document).where(Document.id == doc_id))
            ).scalar_one()
            assert doc_row.active_version_id == ver_id

    async def test_stalled_job_sweep_retries_doing_job_and_version_leaves_processing(
        self,
        seeded_tenant_and_user: tuple[UUID, UUID],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Stalled job in 'doing' with dead worker is retried by sweep, activating version."""
        from procrastinate import JobContext
        from procrastinate.jobs import Job, Status

        from app.db.models import Document, DocumentVersion
        from app.worker import (
            app,
            process_page_job,
            run_page_job,
            run_stalled_job_sweep,
        )
        from tests.fixtures.database_seeding import seed_chunk, seed_job

        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Stalled Sweep Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            p1 = await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)
            await seed_chunk(
                session, tenant_id=tenant_id, page_id=p1, ver_id=ver_id, is_active=False
            )

            # Page 1 has completed step in job_steps
            await seed_job(
                session, tenant_id=tenant_id, ver_id=ver_id, job_id=UUID(worker_ctx.job_id)
            )
            await session.execute(
                text(
                    "INSERT INTO job_steps "
                    "(id, tenant_id, job_id, document_version_id, page_number, step, "
                    "processing_version, status) "
                    "VALUES (:id, :tid, :jid, :vid, 1, 'chunking', 'v1', 'completed') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": uuid4(), "tid": tenant_id, "jid": UUID(worker_ctx.job_id), "vid": ver_id},
            )

        # 1. Simulate stalled last-page job stuck in 'doing' state
        stalled_job = Job(
            id=101,
            queue="ingestion",
            lock=None,
            queueing_lock=None,
            task_name=process_page_job.name,
            task_kwargs={
                "tenant_id": str(tenant_id),
                "document_version_id": str(ver_id),
                "page_number": 1,
            },
            status=Status.DOING.value,
        )

        retried_jobs: list[Job] = []

        async def mock_get_stalled_jobs(
            seconds_since_heartbeat: float = 30, queue: str | None = None
        ) -> list[Job]:
            _ = (seconds_since_heartbeat, queue)
            return [stalled_job]

        async def mock_retry_job(job: Job) -> None:
            retried_jobs.append(job)

        monkeypatch.setattr(app.job_manager, "get_stalled_jobs", mock_get_stalled_jobs)
        monkeypatch.setattr(app.job_manager, "retry_job", mock_retry_job)

        sweep_context = JobContext(
            app=app,
            job=stalled_job,
            start_timestamp=0.0,
            abort_reason=lambda: None,
        )

        # 2. Run stalled job sweep
        requeued_count = await run_stalled_job_sweep(context=sweep_context)
        assert requeued_count == 1
        assert retried_jobs == [stalled_job]

        # 3. Simulate execution of the retried job
        retried_job = Job(
            id=102,
            queue="ingestion",
            lock=None,
            queueing_lock=None,
            task_name=process_page_job.name,
            task_kwargs=stalled_job.task_kwargs,
            attempts=1,
        )
        page_job_context = JobContext(
            app=app,
            job=retried_job,
            start_timestamp=0.0,
            abort_reason=lambda: None,
        )
        await run_page_job(
            context=page_job_context,
            tenant_id=str(tenant_id),
            document_version_id=str(ver_id),
            page_number=1,
        )

        # 4. Verify version left 'processing' and activated
        async with worker_tenant_session(worker_ctx) as session:
            ver_row = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == ver_id))
            ).scalar_one()
            assert ver_row.status == "ready"

            doc_row = (
                await session.execute(select(Document).where(Document.id == doc_id))
            ).scalar_one()
            assert doc_row.active_version_id == ver_id

            chunk_row = (
                await session.execute(select(Chunk).where(Chunk.page_id == p1))
            ).scalar_one()
            assert chunk_row.is_active is True

    async def test_transient_error_boundary_behavior_retries_then_activates(
        self,
        seeded_tenant_and_user: tuple[UUID, UUID],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Transient error at attempts=4 raises error; at attempts=5 flags page and activates."""
        from procrastinate import JobContext
        from procrastinate.jobs import Job

        from app.core.ingestion.pipeline import PagePipelineService
        from app.db.models import Document, DocumentVersion
        from app.ports.errors import TransientIngestionError
        from app.worker import app, process_page_job, run_page_job
        from tests.fixtures.database_seeding import seed_chunk

        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Boundary Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            p1 = await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)
            await seed_chunk(
                session, tenant_id=tenant_id, page_id=p1, ver_id=ver_id, is_active=False
            )

        class TransientDeadlockError(Exception):
            sqlstate = "40P01"
            pgcode = "40P01"

        async def failing_process_page(*args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)
            raise TransientDeadlockError("Deadlock detected")

        monkeypatch.setattr(PagePipelineService, "process_page", failing_process_page)

        # 1. Non-final attempt (attempts=4): get_retry_decision returns decision -> raises error
        job_4 = Job(
            id=201,
            queue="ingestion",
            lock=None,
            queueing_lock=None,
            task_name=process_page_job.name,
            task_kwargs={
                "tenant_id": str(tenant_id),
                "document_version_id": str(ver_id),
                "page_number": 1,
            },
            attempts=4,
        )
        ctx_4 = JobContext(app=app, job=job_4, start_timestamp=0.0, abort_reason=lambda: None)
        with pytest.raises(TransientIngestionError) as exc_info:
            await run_page_job(
                context=ctx_4,
                tenant_id=str(tenant_id),
                document_version_id=str(ver_id),
                page_number=1,
            )
        assert exc_info.value.error_code == "40P01"

        # 2. Final attempt (attempts=5): get_retry_decision returns None -> activates version
        job_5 = Job(
            id=202,
            queue="ingestion",
            lock=None,
            queueing_lock=None,
            task_name=process_page_job.name,
            task_kwargs=job_4.task_kwargs,
            attempts=5,
        )
        ctx_5 = JobContext(app=app, job=job_5, start_timestamp=0.0, abort_reason=lambda: None)
        # Does NOT raise TransientIngestionError
        await run_page_job(
            context=ctx_5,
            tenant_id=str(tenant_id),
            document_version_id=str(ver_id),
            page_number=1,
        )

        # Version must have left 'processing' and transitioned to 'needs_review' (page was flagged)
        async with worker_tenant_session(worker_ctx) as session:
            ver_row = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == ver_id))
            ).scalar_one()
            assert ver_row.status == "needs_review"
            doc_row = (
                await session.execute(select(Document).where(Document.id == doc_id))
            ).scalar_one()
            assert doc_row.active_version_id == ver_id
