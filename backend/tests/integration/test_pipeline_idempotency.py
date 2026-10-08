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
from app.ports.types import WorkerContext
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
