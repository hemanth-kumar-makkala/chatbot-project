"""Integration tests for the stuck-marker periodic sweep (failed page job sweep).

Verifies:
a. Failed job, 'running' marker older than the event: marker transitioned to 'failed',
   page flagged 'processing_failed'.
b. Marker updated AFTER the failure event (retry case): left alone.
c. 'completed' marker: left alone.
d. Malformed task args: counted via MalformedTaskArgsError, sweep continues.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import JobStep, Page
from app.db.session import unscoped_session, worker_tenant_session
from app.ports.types import WorkerContext
from app.worker import (
    FailedPageJobSweepResult,
    run_failed_page_job_sweep,
)
from tests.fixtures.database_seeding import (
    seed_document,
    seed_job,
    seed_page,
    seed_version,
)


@pytest.fixture
async def seeded_tenant_and_user() -> tuple[UUID, UUID]:
    """Helper to seed a tenant and user for testing."""
    tenant_id = uuid4()
    user_id = uuid4()

    async with unscoped_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, name) VALUES (:tid, 'Failed Sweep Tenant')"),
            {"tid": tenant_id},
        )
        await session.execute(
            text("INSERT INTO users (id, email, hashed_password) VALUES (:uid, :email, 'hash')"),
            {"uid": user_id, "email": f"failed-sweep-{user_id.hex[:8]}@example.com"},
        )
    return tenant_id, user_id


async def _clean_page_jobs() -> None:
    async with unscoped_session() as session:
        await session.execute(text("SET LOCAL search_path TO public, procrastinate"))
        delete_events_stmt = text(
            "DELETE FROM procrastinate.procrastinate_events WHERE job_id IN ("
            "  SELECT id FROM procrastinate.procrastinate_jobs "
            "  WHERE task_name = 'app.worker.process_page_job'"
            ")"
        )
        await session.execute(delete_events_stmt)
        delete_jobs_stmt = text(
            "DELETE FROM procrastinate.procrastinate_jobs "
            "WHERE task_name = 'app.worker.process_page_job'"
        )
        await session.execute(delete_jobs_stmt)


@pytest.fixture(autouse=True)
async def cleanup_procrastinate_page_jobs() -> AsyncIterator[None]:
    """Clean up procrastinate page job rows before and after each test."""
    await _clean_page_jobs()
    try:
        yield
    finally:
        await _clean_page_jobs()


async def _seed_procrastinate_job_and_event(
    session: AsyncSession,
    *,
    task_name: str = "app.worker.process_page_job",
    status: str = "failed",
    args: dict[str, Any] | str,
    event_type: str = "failed",
    event_at: datetime,
) -> int:
    """Insert a terminal job and corresponding event into procrastinate schema."""
    await session.execute(text("SET LOCAL search_path TO public, procrastinate"))
    args_json = json.dumps(args) if isinstance(args, dict) else args
    stmt = text(
        "INSERT INTO procrastinate.procrastinate_jobs (queue_name, task_name, status, args) "
        "VALUES ("
        "  'ingestion', :task_name, "
        "  CAST(:status AS procrastinate.procrastinate_job_status), "
        "  CAST(:args AS jsonb)"
        ") RETURNING id"
    )
    job_id: int = (
        await session.execute(
            stmt,
            {"task_name": task_name, "status": status, "args": args_json},
        )
    ).scalar_one()

    event_stmt = text(
        "INSERT INTO procrastinate.procrastinate_events (job_id, type, at) "
        "VALUES (:job_id, CAST(:event_type AS procrastinate.procrastinate_job_event_type), :at)"
    )
    await session.execute(
        event_stmt,
        {"job_id": job_id, "event_type": event_type, "at": event_at},
    )
    return job_id


async def _seed_page_marker(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    ver_id: UUID,
    job_id: UUID,
    page_number: int,
    status: str,
    updated_at: datetime,
) -> None:
    """Seed or update a job_steps page_processing marker with an explicit updated_at."""
    stmt = text(
        "INSERT INTO job_steps "
        "(id, tenant_id, job_id, document_version_id, page_number, step, "
        " processing_version, status, updated_at) "
        "VALUES (:id, :tid, :jid, :vid, :pnum, 'page_processing', 'v1', :status, :updated_at) "
        "ON CONFLICT (tenant_id, document_version_id, page_number, step, processing_version) "
        "DO UPDATE SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at"
    )
    await session.execute(
        stmt,
        {
            "id": uuid4(),
            "tid": tenant_id,
            "jid": job_id,
            "vid": ver_id,
            "pnum": page_number,
            "status": status,
            "updated_at": updated_at,
        },
    )


@pytest.mark.asyncio
async def test_failed_job_older_running_marker_fails_and_flags_page(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """a. Failed job, 'running' marker older than the event:
    marker 'failed', page flagged processing_failed.
    """
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Failed Sweep Doc A")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)
        await seed_job(session, tenant_id=tenant_id, ver_id=ver_id, job_id=UUID(worker_ctx.job_id))

        # Marker updated at T - 10 minutes
        marker_time = datetime.now(UTC) - timedelta(minutes=10)
        await _seed_page_marker(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            job_id=UUID(worker_ctx.job_id),
            page_number=1,
            status="running",
            updated_at=marker_time,
        )

    # Terminal failure event occurred at T - 5 minutes (marker is older than failure event)
    terminal_time = datetime.now(UTC) - timedelta(minutes=5)
    async with unscoped_session() as session:
        await _seed_procrastinate_job_and_event(
            session,
            args={
                "tenant_id": str(tenant_id),
                "document_version_id": str(ver_id),
                "page_number": 1,
            },
            status="failed",
            event_type="failed",
            event_at=terminal_time,
        )

    # Run the sweep
    result: FailedPageJobSweepResult = await run_failed_page_job_sweep()
    assert result.repaired_count == 1
    assert result.skipped_count == 0
    assert result.malformed_args_count == 0

    # Verify marker is failed and page is flagged processing_failed
    async with worker_tenant_session(worker_ctx) as session:
        marker = (
            await session.execute(
                select(JobStep).where(
                    JobStep.tenant_id == tenant_id,
                    JobStep.document_version_id == ver_id,
                    JobStep.page_number == 1,
                    JobStep.step == "page_processing",
                )
            )
        ).scalar_one()
        assert marker.status == "failed"

        page = (
            await session.execute(
                select(Page).where(
                    Page.tenant_id == tenant_id,
                    Page.document_version_id == ver_id,
                    Page.page_number == 1,
                )
            )
        ).scalar_one()
        assert page.needs_review is True
        assert page.review_reason == "processing_failed"


@pytest.mark.asyncio
async def test_marker_updated_after_failure_left_alone(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """b. Marker updated AFTER the failure (retry case): left alone."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    # Terminal failure event occurred at T - 10 minutes
    terminal_time = datetime.now(UTC) - timedelta(minutes=10)

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Failed Sweep Doc B")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)
        await seed_job(session, tenant_id=tenant_id, ver_id=ver_id, job_id=UUID(worker_ctx.job_id))

        # Marker updated at T - 2 minutes (AFTER the failure event, e.g. retry started)
        newer_marker_time = datetime.now(UTC) - timedelta(minutes=2)
        await _seed_page_marker(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            job_id=UUID(worker_ctx.job_id),
            page_number=1,
            status="running",
            updated_at=newer_marker_time,
        )

    async with unscoped_session() as session:
        await _seed_procrastinate_job_and_event(
            session,
            args={
                "tenant_id": str(tenant_id),
                "document_version_id": str(ver_id),
                "page_number": 1,
            },
            status="failed",
            event_type="failed",
            event_at=terminal_time,
        )

    # Run the sweep
    result: FailedPageJobSweepResult = await run_failed_page_job_sweep()
    assert result.repaired_count == 0
    assert result.skipped_count == 1
    assert result.malformed_args_count == 0

    # Verify marker is still running and page is NOT flagged
    async with worker_tenant_session(worker_ctx) as session:
        marker = (
            await session.execute(
                select(JobStep).where(
                    JobStep.tenant_id == tenant_id,
                    JobStep.document_version_id == ver_id,
                    JobStep.page_number == 1,
                    JobStep.step == "page_processing",
                )
            )
        ).scalar_one()
        assert marker.status == "running"

        page = (
            await session.execute(
                select(Page).where(
                    Page.tenant_id == tenant_id,
                    Page.document_version_id == ver_id,
                    Page.page_number == 1,
                )
            )
        ).scalar_one()
        assert page.needs_review is False


@pytest.mark.asyncio
async def test_completed_marker_left_alone(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """c. 'completed' marker: left alone."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    # Terminal failure event occurred at T - 5 minutes
    terminal_time = datetime.now(UTC) - timedelta(minutes=5)

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Failed Sweep Doc C")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)
        await seed_job(session, tenant_id=tenant_id, ver_id=ver_id, job_id=UUID(worker_ctx.job_id))

        # Completed marker older than failure event
        marker_time = datetime.now(UTC) - timedelta(minutes=10)
        await _seed_page_marker(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            job_id=UUID(worker_ctx.job_id),
            page_number=1,
            status="completed",
            updated_at=marker_time,
        )

    async with unscoped_session() as session:
        await _seed_procrastinate_job_and_event(
            session,
            args={
                "tenant_id": str(tenant_id),
                "document_version_id": str(ver_id),
                "page_number": 1,
            },
            status="failed",
            event_type="failed",
            event_at=terminal_time,
        )

    # Run the sweep
    result: FailedPageJobSweepResult = await run_failed_page_job_sweep()
    assert result.repaired_count == 0
    assert result.skipped_count == 1
    assert result.malformed_args_count == 0

    # Verify marker is still completed
    async with worker_tenant_session(worker_ctx) as session:
        marker = (
            await session.execute(
                select(JobStep).where(
                    JobStep.tenant_id == tenant_id,
                    JobStep.document_version_id == ver_id,
                    JobStep.page_number == 1,
                    JobStep.step == "page_processing",
                )
            )
        ).scalar_one()
        assert marker.status == "completed"

        page = (
            await session.execute(
                select(Page).where(
                    Page.tenant_id == tenant_id,
                    Page.document_version_id == ver_id,
                    Page.page_number == 1,
                )
            )
        ).scalar_one()
        assert page.needs_review is False


@pytest.mark.asyncio
async def test_malformed_task_args_counted_and_sweep_continues(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """d. Malformed task args: counted, sweep continues."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    # Terminal failure event occurred at T - 5 minutes
    terminal_time = datetime.now(UTC) - timedelta(minutes=5)

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Failed Sweep Doc D")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)
        await seed_job(session, tenant_id=tenant_id, ver_id=ver_id, job_id=UUID(worker_ctx.job_id))

        # Running marker older than failure event
        marker_time = datetime.now(UTC) - timedelta(minutes=10)
        await _seed_page_marker(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            job_id=UUID(worker_ctx.job_id),
            page_number=1,
            status="running",
            updated_at=marker_time,
        )

    async with unscoped_session() as session:
        # Job 1: Malformed args (missing document_version_id and page_number)
        await _seed_procrastinate_job_and_event(
            session,
            args={"tenant_id": str(tenant_id), "invalid_payload": True},
            status="failed",
            event_type="failed",
            event_at=terminal_time - timedelta(minutes=1),
        )

        # Job 2: Valid args for page 1
        await _seed_procrastinate_job_and_event(
            session,
            args={
                "tenant_id": str(tenant_id),
                "document_version_id": str(ver_id),
                "page_number": 1,
            },
            status="failed",
            event_type="failed",
            event_at=terminal_time,
        )

    # Run the sweep
    result: FailedPageJobSweepResult = await run_failed_page_job_sweep()
    assert result.malformed_args_count == 1
    assert result.repaired_count == 1

    # Verify that Job 2 was successfully processed despite Job 1's malformed args
    async with worker_tenant_session(worker_ctx) as session:
        marker = (
            await session.execute(
                select(JobStep).where(
                    JobStep.tenant_id == tenant_id,
                    JobStep.document_version_id == ver_id,
                    JobStep.page_number == 1,
                    JobStep.step == "page_processing",
                )
            )
        ).scalar_one()
        assert marker.status == "failed"

        page = (
            await session.execute(
                select(Page).where(
                    Page.tenant_id == tenant_id,
                    Page.document_version_id == ver_id,
                    Page.page_number == 1,
                )
            )
        ).scalar_one()
        assert page.needs_review is True
        assert page.review_reason == "processing_failed"
