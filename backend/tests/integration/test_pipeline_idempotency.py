import asyncio
import contextlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from procrastinate import PsycopgConnector
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.adapters.chunking.mock import MockChunker
from app.adapters.db.postgres_document_repository import PostgresDocumentRepository
from app.adapters.embedding.mock import MockEmbeddingProvider
from app.adapters.parsing.mock import MockDocumentParser
from app.core.ingestion.pipeline import PagePipelineService, is_retryable_error
from app.db.env_utils import load_dotenv
from app.db.models import Chunk, Document, DocumentVersion, JobStep, Page, PageReading, TableData
from app.db.session import (
    reset_session_engines,
    tenant_session,
    unscoped_session,
    worker_tenant_session,
)
from app.ports.types import (
    ParsedPageContent,
    TenantContext,
    UploadPayload,
    UserRole,
    WorkerContext,
)
from app.wiring import create_upload_service
from app.worker import app as worker_app
from app.worker import split_document_job
from tests.conftest import isolated_throwaway_database
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
        from app.db.models import DocumentVersion
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

        from app.db.models import DocumentVersion
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
        from app.db.models import DocumentVersion
        from app.ports.errors import TransientIngestionError
        from app.worker import app, process_page_job, run_page_job
        from tests.fixtures.database_seeding import seed_chunk, seed_job

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
            await seed_job(
                session, tenant_id=tenant_id, ver_id=ver_id, job_id=UUID(worker_ctx.job_id)
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


@pytest.mark.models
@pytest.mark.asyncio
class TestPhase2ExitGateGoldenDocuments:
    """Phase 2 exit gate: all golden documents ingested twice with zero duplicates."""

    async def test_golden_documents_ingested_twice_zero_duplicates(
        self,
        tmp_path: Path,
    ) -> None:
        """Phase 2 exit gate verification:

        1. Ingests all 15 golden documents from manifest.json on desktop-gpu profile
           (Docling on GPU, Ollama vision, BGE-M3 embeddings on CPU).
        2. Re-ingests all 15 documents a second time.
        3. Asserts zero new rows and zero duplicates across pages, page_readings,
           tables_data, chunks, and embeddings.
        4. Asserts every version ends Ready or Ready (N pages need review), and zero jobs failed.
        5. Writes evals/reports/phase2_gate.json.
        """
        load_dotenv()
        host = os.environ.get("POSTGRES_HOST")
        if host is None or not host.strip():
            host = "localhost"
        port = os.environ.get("POSTGRES_PORT")
        if port is None or not port.strip():
            port = "5432"
        app_user = os.environ.get("DB_APP_USER")
        if app_user is None or not app_user.strip():
            app_user = "app_user"
        app_pass = os.environ.get("DB_APP_PASSWORD")
        if app_pass is None:
            app_pass = ""

        storage_dir = tmp_path / "storage"
        storage_dir.mkdir(parents=True, exist_ok=True)

        artifacts_path = os.environ.get("DOCLING_ARTIFACTS_PATH")
        if artifacts_path is None or not artifacts_path.strip():
            artifacts_path = os.path.expandvars(
                r"%USERPROFILE%\Downloads\Projects\chatbots\docling-models"
            )

        async with isolated_throwaway_database() as throwaway_db:
            command.upgrade(throwaway_db.cfg, "head")

            app_url = (
                f"postgresql+psycopg://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}"
            )
            old_db_url: str | None = None
            if "DATABASE_URL" in os.environ:
                old_db_url = os.environ["DATABASE_URL"]
            old_profile: str | None = None
            if "APP_PROFILE" in os.environ:
                old_profile = os.environ["APP_PROFILE"]
            old_storage: str | None = None
            if "LOCAL_STORAGE_PATH" in os.environ:
                old_storage = os.environ["LOCAL_STORAGE_PATH"]
            old_offline: str | None = None
            if "HF_HUB_OFFLINE" in os.environ:
                old_offline = os.environ["HF_HUB_OFFLINE"]
            old_artifacts: str | None = None
            if "DOCLING_ARTIFACTS_PATH" in os.environ:
                old_artifacts = os.environ["DOCLING_ARTIFACTS_PATH"]
            old_ollama: str | None = None
            if "OLLAMA_BASE_URL" in os.environ:
                old_ollama = os.environ["OLLAMA_BASE_URL"]

            os.environ["DATABASE_URL"] = app_url
            os.environ["APP_PROFILE"] = "desktop-gpu"
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["LOCAL_STORAGE_PATH"] = str(storage_dir)
            os.environ["DOCLING_ARTIFACTS_PATH"] = artifacts_path
            if "OLLAMA_BASE_URL" not in os.environ:
                os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"

            reset_session_engines()

            connector = PsycopgConnector(
                conninfo=f"postgresql://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}",
                kwargs={"options": "-c search_path=procrastinate,public"},
            )
            original_connector = worker_app.connector
            worker_app.connector = connector
            worker_app.job_manager.connector = connector

            try:
                # 1. Seed Tenant A and Tenant B, and client_admin users
                tenant_a_id = uuid4()
                tenant_b_id = uuid4()
                user_a_id = uuid4()
                user_b_id = uuid4()

                async with unscoped_session() as session:
                    await session.execute(
                        text(
                            "INSERT INTO tenants (id, name) VALUES "
                            "(:ta, 'Tenant A'), (:tb, 'Tenant B')"
                        ),
                        {"ta": tenant_a_id, "tb": tenant_b_id},
                    )
                    await session.execute(
                        text(
                            "INSERT INTO users (id, email, hashed_password) VALUES "
                            "(:ua, 'admin_a@tenant-a.com', 'h'), "
                            "(:ub, 'admin_b@tenant-b.com', 'h')"
                        ),
                        {"ua": user_a_id, "ub": user_b_id},
                    )

                ctx_map: dict[str, TenantContext] = {
                    "A": TenantContext(
                        tenant_id=str(tenant_a_id),
                        user_id=str(user_a_id),
                        role=UserRole.CLIENT_ADMIN,
                        session_id=str(uuid4()),
                    ),
                    "B": TenantContext(
                        tenant_id=str(tenant_b_id),
                        user_id=str(user_b_id),
                        role=UserRole.CLIENT_ADMIN,
                        session_id=str(uuid4()),
                    ),
                }

                # 2. Read golden manifest
                manifest_path = (
                    Path(__file__).resolve().parents[3] / "evals" / "golden" / "manifest.json"
                )
                docs_dir = manifest_path.parent / "docs"
                with open(manifest_path, encoding="utf-8") as f:
                    manifest_data = json.load(f)
                manifest_docs: list[dict[str, Any]] = manifest_data["documents"]

                # 3. Start Procrastinate worker
                async with worker_app.open_async():
                    worker_task = asyncio.create_task(
                        worker_app.run_worker_async(
                            queues=["ingestion"],
                            concurrency=1,
                            wait=True,
                            fetch_job_polling_interval=0.5,
                            install_signal_handlers=False,
                        )
                    )

                    try:
                        per_doc_records: list[dict[str, Any]] = []

                        # 4. RUN 1: Ingest all 15 documents
                        for doc in manifest_docs:
                            doc_file_name: str = doc["file_name"]
                            doc_tenant: str = doc["tenant"]
                            ctx = ctx_map[doc_tenant]
                            file_path = docs_dir / doc["relative_path"]
                            file_bytes = file_path.read_bytes()

                            t0 = time.monotonic()
                            print(
                                f"\n--> [Run 1] Ingesting {doc_file_name} "
                                f"({doc['page_count']} pages, Tenant {doc_tenant})...",
                                flush=True,
                            )

                            async with tenant_session(ctx) as session:
                                upload_svc = create_upload_service(
                                    session, profile="desktop-gpu", storage_dir=storage_dir
                                )
                                upload_res = await upload_svc.upload_document(
                                    ctx,
                                    UploadPayload(filename=doc_file_name, content=file_bytes),
                                )

                            assert not upload_res.is_duplicate, (
                                f"Run 1 upload for {doc_file_name} unexpectedly reported duplicate!"
                            )

                            await split_document_job.defer_async(
                                tenant_id=ctx.tenant_id,
                                document_version_id=str(upload_res.document_version_id),
                            )

                            # Wait for version to reach terminal status
                            # with per-page progress logging
                            ver_row: DocumentVersion | None = None
                            t_last_log = t0
                            while True:
                                async with tenant_session(ctx) as s:
                                    ver_row = (
                                        await s.execute(
                                            select(DocumentVersion).where(
                                                DocumentVersion.id == upload_res.document_version_id
                                            )
                                        )
                                    ).scalar_one_or_none()

                                    now = time.monotonic()
                                    is_terminal = ver_row is not None and ver_row.status in (
                                        "ready",
                                        "needs_review",
                                        "failed",
                                    )
                                    if now - t_last_log >= 2.0 or is_terminal:
                                        pages_count = (
                                            await s.execute(
                                                select(func.count(Page.id)).where(
                                                    Page.document_version_id
                                                    == upload_res.document_version_id
                                                )
                                            )
                                        ).scalar_one()
                                        steps = (
                                            await s.execute(
                                                select(
                                                    JobStep.page_number,
                                                    JobStep.step,
                                                    JobStep.status,
                                                )
                                                .where(
                                                    JobStep.document_version_id
                                                    == upload_res.document_version_id
                                                )
                                                .order_by(JobStep.created_at)
                                            )
                                        ).all()
                                        steps_summary = [
                                            (
                                                f"p{st[0] if st[0] is not None else '*'}:"
                                                f"{st[1]}:{st[2]}"
                                            )
                                            for st in steps
                                        ]
                                        v_status = ver_row.status if ver_row else "unknown"
                                        print(
                                            f"    [{doc_file_name}] {now - t0:.1f}s | "
                                            f"status={v_status} | pages={pages_count} | "
                                            f"steps={steps_summary}",
                                            flush=True,
                                        )
                                        t_last_log = now

                                    if is_terminal:
                                        break

                                if time.monotonic() - t0 > 300.0:
                                    raise TimeoutError(
                                        f"Document {doc_file_name} version "
                                        f"{upload_res.document_version_id} timed out."
                                    )
                                await asyncio.sleep(0.5)

                            assert ver_row is not None
                            assert ver_row.status in ("ready", "needs_review"), (
                                f"Document {doc_file_name} ended in status '{ver_row.status}'"
                            )

                            elapsed = round(time.monotonic() - t0, 3)
                            print(
                                f"--> Completed {doc_file_name} in {elapsed}s "
                                f"(status={ver_row.status})",
                                flush=True,
                            )

                            # Query document metrics under tenant session
                            async with tenant_session(ctx) as s:
                                doc_pages: int = (
                                    await s.execute(
                                        select(func.count(Page.id)).where(
                                            Page.document_version_id == ver_row.id
                                        )
                                    )
                                ).scalar_one()

                                doc_readings: int = (
                                    await s.execute(
                                        select(func.count(PageReading.id))
                                        .join(Page, PageReading.page_id == Page.id)
                                        .where(Page.document_version_id == ver_row.id)
                                    )
                                ).scalar_one()

                                doc_chunks: int = (
                                    await s.execute(
                                        select(func.count(Chunk.id)).where(
                                            Chunk.document_version_id == ver_row.id
                                        )
                                    )
                                ).scalar_one()

                                flagged_pages_seq = (
                                    (
                                        await s.execute(
                                            select(Page.page_number).where(
                                                Page.document_version_id == ver_row.id,
                                                Page.needs_review.is_(True),
                                            )
                                        )
                                    )
                                    .scalars()
                                    .all()
                                )
                                flagged_pages: list[int] = list(flagged_pages_seq)

                            per_doc_records.append(
                                {
                                    "file_name": doc_file_name,
                                    "document_id": str(ver_row.document_id),
                                    "version_id": str(ver_row.id),
                                    "status": ver_row.status,
                                    "pages": doc_pages,
                                    "page_readings": doc_readings,
                                    "chunks": doc_chunks,
                                    "review_flagged_pages": flagged_pages,
                                    "time_taken_seconds": elapsed,
                                }
                            )

                        # Ensure all queued background jobs finished
                        while True:
                            async with unscoped_session() as s:
                                pending: int = (
                                    await s.execute(
                                        text(
                                            "SELECT count(*) FROM procrastinate_jobs "
                                            "WHERE status IN ('todo', 'doing')"
                                        )
                                    )
                                ).scalar_one()
                                if pending == 0:
                                    break
                            await asyncio.sleep(0.5)

                        # Total counts after Run 1 across all tenants
                        total_pages_1 = 0
                        total_readings_1 = 0
                        total_tables_1 = 0
                        total_chunks_1 = 0
                        total_embeddings_1 = 0
                        for t_ctx in ctx_map.values():
                            async with tenant_session(t_ctx) as s:
                                total_pages_1 += (
                                    await s.execute(select(func.count(Page.id)))
                                ).scalar_one()
                                total_readings_1 += (
                                    await s.execute(select(func.count(PageReading.id)))
                                ).scalar_one()
                                total_tables_1 += (
                                    await s.execute(select(func.count(TableData.id)))
                                ).scalar_one()
                                total_chunks_1 += (
                                    await s.execute(select(func.count(Chunk.id)))
                                ).scalar_one()
                                total_embeddings_1 += (
                                    await s.execute(
                                        select(func.count(Chunk.id)).where(
                                            Chunk.embedding.isnot(None)
                                        )
                                    )
                                ).scalar_one()

                        assert total_pages_1 > 0
                        assert total_chunks_1 > 0
                        assert total_tables_1 > 0
                        assert total_embeddings_1 == total_chunks_1

                        # 5. RUN 2: Re-ingest all 15 documents
                        for doc in manifest_docs:
                            doc_file_name = doc["file_name"]
                            doc_tenant = doc["tenant"]
                            ctx = ctx_map[doc_tenant]
                            file_path = docs_dir / doc["relative_path"]
                            file_bytes = file_path.read_bytes()

                            async with tenant_session(ctx) as session:
                                upload_svc = create_upload_service(
                                    session, profile="desktop-gpu", storage_dir=storage_dir
                                )
                                upload_res_2 = await upload_svc.upload_document(
                                    ctx,
                                    UploadPayload(filename=doc_file_name, content=file_bytes),
                                )

                            assert upload_res_2.is_duplicate is True, (
                                f"Run 2 for {doc_file_name} was expected to be duplicate, "
                                "but was treated as new!"
                            )

                        # Ensure no new jobs appeared
                        async with unscoped_session() as s:
                            pending_2: int = (
                                await s.execute(
                                    text(
                                        "SELECT count(*) FROM procrastinate_jobs "
                                        "WHERE status IN ('todo', 'doing')"
                                    )
                                )
                            ).scalar_one()
                            assert pending_2 == 0

                        # Total counts after Run 2 across all tenants
                        total_pages_2 = 0
                        total_readings_2 = 0
                        total_tables_2 = 0
                        total_chunks_2 = 0
                        total_embeddings_2 = 0
                        for t_ctx in ctx_map.values():
                            async with tenant_session(t_ctx) as s:
                                total_pages_2 += (
                                    await s.execute(select(func.count(Page.id)))
                                ).scalar_one()
                                total_readings_2 += (
                                    await s.execute(select(func.count(PageReading.id)))
                                ).scalar_one()
                                total_tables_2 += (
                                    await s.execute(select(func.count(TableData.id)))
                                ).scalar_one()
                                total_chunks_2 += (
                                    await s.execute(select(func.count(Chunk.id)))
                                ).scalar_one()
                                total_embeddings_2 += (
                                    await s.execute(
                                        select(func.count(Chunk.id)).where(
                                            Chunk.embedding.isnot(None)
                                        )
                                    )
                                ).scalar_one()

                                # Assert zero duplicates in each table for this tenant
                                dup_pages: int = (
                                    await s.execute(
                                        text(
                                            "SELECT count(*) FROM ("
                                            "SELECT document_version_id, page_number FROM pages "
                                            "GROUP BY document_version_id, page_number "
                                            "HAVING count(*) > 1) d"
                                        )
                                    )
                                ).scalar_one()
                                assert dup_pages == 0, (
                                    f"Duplicate pages found in tenant {t_ctx.tenant_id}"
                                )

                                dup_readings: int = (
                                    await s.execute(
                                        text(
                                            "SELECT count(*) FROM ("
                                            "SELECT page_id, id FROM page_readings "
                                            "GROUP BY page_id, id HAVING count(*) > 1) d"
                                        )
                                    )
                                ).scalar_one()
                                assert dup_readings == 0, (
                                    f"Duplicate page_readings in tenant {t_ctx.tenant_id}"
                                )

                                dup_tables: int = (
                                    await s.execute(
                                        text(
                                            "SELECT count(*) FROM ("
                                            "SELECT document_version_id, sheet_name, row_index "
                                            "FROM tables_data GROUP BY document_version_id, "
                                            "sheet_name, row_index HAVING count(*) > 1) d"
                                        )
                                    )
                                ).scalar_one()
                                assert dup_tables == 0, (
                                    f"Duplicate tables_data in tenant {t_ctx.tenant_id}"
                                )

                                dup_chunks: int = (
                                    await s.execute(
                                        text(
                                            "SELECT count(*) FROM ("
                                            "SELECT page_id, id FROM chunks "
                                            "GROUP BY page_id, id HAVING count(*) > 1) d"
                                        )
                                    )
                                ).scalar_one()
                                assert dup_chunks == 0, (
                                    f"Duplicate chunks in tenant {t_ctx.tenant_id}"
                                )

                                failed_job_steps: int = (
                                    await s.execute(
                                        text(
                                            "SELECT count(*) FROM job_steps WHERE status = 'failed'"
                                        )
                                    )
                                ).scalar_one()
                                assert failed_job_steps == 0, (
                                    f"Failed job steps in tenant {t_ctx.tenant_id}"
                                )

                        async with unscoped_session() as s:
                            failed_proc_jobs: int = (
                                await s.execute(
                                    text(
                                        "SELECT count(*) FROM procrastinate_jobs "
                                        "WHERE status = 'failed'"
                                    )
                                )
                            ).scalar_one()
                            assert failed_proc_jobs == 0, (
                                f"Failed procrastinate jobs: {failed_proc_jobs}"
                            )

                        delta_pages = total_pages_2 - total_pages_1
                        delta_readings = total_readings_2 - total_readings_1
                        delta_tables = total_tables_2 - total_tables_1
                        delta_chunks = total_chunks_2 - total_chunks_1
                        delta_embeddings = total_embeddings_2 - total_embeddings_1

                        assert delta_pages == 0, f"New pages created in Run 2: {delta_pages}"
                        assert delta_readings == 0, (
                            f"New page readings created in Run 2: {delta_readings}"
                        )
                        assert delta_tables == 0, f"New table rows created in Run 2: {delta_tables}"
                        assert delta_chunks == 0, f"New chunks created in Run 2: {delta_chunks}"
                        assert delta_embeddings == 0, (
                            f"New embeddings created in Run 2: {delta_embeddings}"
                        )

                        # Write report
                        report_path = (
                            Path(__file__).resolve().parents[3]
                            / "evals"
                            / "reports"
                            / "phase2_gate.json"
                        )
                        report_path.parent.mkdir(parents=True, exist_ok=True)
                        report_payload = {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "profile": "desktop-gpu",
                            "documents": per_doc_records,
                            "run_1_totals": {
                                "pages": total_pages_1,
                                "page_readings": total_readings_1,
                                "tables_data": total_tables_1,
                                "chunks": total_chunks_1,
                                "embeddings": total_embeddings_1,
                            },
                            "run_2_totals": {
                                "pages": total_pages_2,
                                "page_readings": total_readings_2,
                                "tables_data": total_tables_2,
                                "chunks": total_chunks_2,
                                "embeddings": total_embeddings_2,
                            },
                            "run_2_deltas": {
                                "pages": delta_pages,
                                "page_readings": delta_readings,
                                "tables_data": delta_tables,
                                "chunks": delta_chunks,
                                "embeddings": delta_embeddings,
                            },
                            "all_zero_deltas": True,
                            "all_versions_ready_or_review": True,
                            "no_jobs_failed": True,
                            "gate_passed": True,
                        }
                        with open(report_path, "w", encoding="utf-8") as rf:
                            json.dump(report_payload, rf, indent=2)
                    finally:
                        worker_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await worker_task

            finally:
                worker_app.connector = original_connector
                worker_app.job_manager.connector = original_connector
                if old_db_url is not None:
                    os.environ["DATABASE_URL"] = old_db_url
                elif "DATABASE_URL" in os.environ:
                    del os.environ["DATABASE_URL"]

                if old_profile is not None:
                    os.environ["APP_PROFILE"] = old_profile
                elif "APP_PROFILE" in os.environ:
                    del os.environ["APP_PROFILE"]

                if old_storage is not None:
                    os.environ["LOCAL_STORAGE_PATH"] = old_storage
                elif "LOCAL_STORAGE_PATH" in os.environ:
                    del os.environ["LOCAL_STORAGE_PATH"]

                if old_offline is not None:
                    os.environ["HF_HUB_OFFLINE"] = old_offline
                elif "HF_HUB_OFFLINE" in os.environ:
                    del os.environ["HF_HUB_OFFLINE"]

                if old_artifacts is not None:
                    os.environ["DOCLING_ARTIFACTS_PATH"] = old_artifacts
                elif "DOCLING_ARTIFACTS_PATH" in os.environ:
                    del os.environ["DOCLING_ARTIFACTS_PATH"]

                if old_ollama is not None:
                    os.environ["OLLAMA_BASE_URL"] = old_ollama
                elif "OLLAMA_BASE_URL" in os.environ:
                    del os.environ["OLLAMA_BASE_URL"]

                reset_session_engines()
