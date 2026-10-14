"""Procrastinate background worker application for document ingestion pipeline.

Follows:
- LangSmith tracing strictly disabled before any task runs (enforce_langsmith_tracing_disabled).
- Procrastinate runs in isolated PostgreSQL schema 'procrastinate' via connector search_path option.
- Job payloads carry strictly IDs only (tenant_id, document_version_id, page_number);
  zero content/metadata.
- Tasks execute within WorkerContext, scoped to tenant_session(worker_ctx) for RLS.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from procrastinate import App, JobContext, PsycopgConnector
from procrastinate.retry import RetryStrategy
from sqlalchemy import text, update

from app.core.ingestion.pipeline import is_retryable_error
from app.db.env_utils import load_dotenv
from app.db.models import DocumentVersion
from app.db.session import unscoped_session, worker_tenant_session
from app.observability.tracing_guard import enforce_langsmith_tracing_disabled
from app.ports.document_repository import DocumentRepository
from app.ports.errors import MalformedTaskArgsError, TransientIngestionError
from app.ports.types import WorkerContext
from app.wiring import create_document_repository

# Mandatory: enforce LangSmith tracing disabled at module import / worker startup
enforce_langsmith_tracing_disabled()

ingestion_retry_strategy = RetryStrategy(
    max_attempts=5,
    exponential_wait=2,
    retry_exceptions=(TransientIngestionError,),
)


def get_procrastinate_connector(
    search_path: str = "procrastinate",
) -> PsycopgConnector:
    """Construct PsycopgConnector with isolated search_path kwargs."""
    load_dotenv()
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    db = os.getenv("POSTGRES_DB", "knowledge_assistant")
    user = os.getenv("DB_APP_USER", "app_user")
    password = os.getenv("DB_APP_PASSWORD", "")

    conninfo = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    return PsycopgConnector(
        conninfo=conninfo,
        kwargs={"options": f"-c search_path={search_path}"},
    )


def create_procrastinate_app(
    connector: PsycopgConnector | None = None,
    search_path: str = "procrastinate",
) -> App:
    """Create Procrastinate App instance enforcing tracing disabled and isolated schema."""
    enforce_langsmith_tracing_disabled()
    actual_connector = connector or get_procrastinate_connector(search_path=search_path)
    return App(connector=actual_connector)


app = create_procrastinate_app()


async def run_split_document_job(
    context: JobContext,
    tenant_id: str,
    document_version_id: str,
) -> None:
    """Execute document splitting and enqueue page processing jobs.

    Job arguments carry IDs only (tenant_id, document_version_id).
    Executes under WorkerContext within worker_tenant_session.
    """
    import uuid

    from app.db.session import worker_tenant_session
    from app.ports.types import WorkerContext
    from app.wiring import create_document_repository

    v_id = UUID(document_version_id)
    worker_ctx = WorkerContext(tenant_id=tenant_id, job_id=str(uuid.uuid4()))
    try:
        async with worker_tenant_session(worker_ctx) as session:
            repo = create_document_repository(session)
            await repo.ensure_ingestion_job(
                tenant_id=tenant_id,
                version_id=v_id,
            )
            await enqueue_uncompleted_page_jobs(
                tenant_id=tenant_id,
                document_version_id=v_id,
                repository=repo,
                session=session,
            )
    except Exception as exc:
        if is_retryable_error(exc):
            orig = getattr(exc, "orig", exc)
            code = (
                getattr(orig, "sqlstate", None)
                or getattr(orig, "pgcode", None)
                or "TRANSIENT_DB_ERROR"
            )
            transient_exc = TransientIngestionError(
                message="Transient document split error",
                error_code=code,
            )
            retry_decision = ingestion_retry_strategy.get_retry_decision(
                exception=transient_exc,
                job=context.job,
            )
            if retry_decision is not None:
                # Re-raise with fixed message, SQLSTATE code only, from None (zero str(exc))
                raise transient_exc from None

        # Permanent failure or final retry exhausted: version must transition to 'failed'
        async with worker_tenant_session(worker_ctx) as session:
            repo = create_document_repository(session)
            await session.execute(
                update(DocumentVersion)
                .where(
                    DocumentVersion.tenant_id == UUID(tenant_id),
                    DocumentVersion.id == v_id,
                )
                .values(status="failed")
            )
            await repo.record_job_step(
                tenant_id=tenant_id,
                version_id=v_id,
                page_number=None,
                step="split",
                status="failed",
            )


@app.task(queue="ingestion", retry=ingestion_retry_strategy, pass_context=True)
async def split_document_job(
    context: JobContext,
    tenant_id: str,
    document_version_id: str,
) -> None:
    await run_split_document_job(
        context=context,
        tenant_id=tenant_id,
        document_version_id=document_version_id,
    )


async def run_page_job(
    context: JobContext,
    tenant_id: str,
    document_version_id: str,
    page_number: int,
) -> None:
    """Execute ingestion pipeline Steps 4 through 8 for a single page.

    Job arguments carry IDs only. No file names, titles, or page contents.
    Executes under WorkerContext within worker_tenant_session.
    1. Commits page processing (chunks and job_steps) in its own transaction.
    2. Runs completion check and version activation in a separate short transaction.
    """
    import uuid

    from app.db.session import worker_tenant_session
    from app.ports.types import WorkerContext
    from app.wiring import (
        create_document_repository,
        create_page_pipeline_service,
        create_version_activation_service,
    )

    v_id = UUID(document_version_id)
    profile = os.getenv("APP_PROFILE", "mock")
    worker_ctx = WorkerContext(tenant_id=tenant_id, job_id=str(uuid.uuid4()))

    # Transaction 0: Set marker to "running" before page work, only if page is uncompleted
    async with worker_tenant_session(worker_ctx) as session:
        repo = create_document_repository(session)
        uncompleted = await repo.get_uncompleted_page_numbers(
            tenant_id=tenant_id,
            version_id=v_id,
            step="chunking",
        )
        if page_number in uncompleted:
            await repo.record_page_processing_marker(
                tenant_id=tenant_id,
                version_id=v_id,
                page_number=page_number,
                status="running",
            )

    # Transaction 1: Commit page work (chunks, job_steps) in its own transaction
    async with worker_tenant_session(worker_ctx) as session:
        repo = create_page_pipeline_service(session, profile=profile).repository
        # Idempotency check: if page is already terminal in job_steps, skip re-processing
        uncompleted = await repo.get_uncompleted_page_numbers(
            tenant_id=tenant_id,
            version_id=v_id,
            step="chunking",
        )
        if page_number in uncompleted:
            pipeline = create_page_pipeline_service(session, profile=profile)
            try:
                await pipeline.process_page(
                    tenant_id=tenant_id,
                    document_version_id=v_id,
                    page_number=page_number,
                )
                await repo.record_page_processing_marker(
                    tenant_id=tenant_id,
                    version_id=v_id,
                    page_number=page_number,
                    status="completed",
                )
            except Exception as exc:
                if is_retryable_error(exc):
                    orig = getattr(exc, "orig", exc)
                    code = (
                        getattr(orig, "sqlstate", None)
                        or getattr(orig, "pgcode", None)
                        or "TRANSIENT_DB_ERROR"
                    )
                    transient_exc = TransientIngestionError(
                        message="Transient page processing error",
                        error_code=code,
                    )
                    retry_decision = ingestion_retry_strategy.get_retry_decision(
                        exception=transient_exc,
                        job=context.job,
                    )
                    if retry_decision is None:
                        # Final attempt: flag page (type and code only) and record failed step
                        await repo.flag_page_for_review(
                            tenant_id=tenant_id,
                            version_id=v_id,
                            page_number=page_number,
                            reason="processing_failed",
                        )
                        await repo.record_job_step(
                            tenant_id=tenant_id,
                            version_id=v_id,
                            page_number=page_number,
                            step="chunking",
                            status="failed",
                        )
                        await repo.record_page_processing_marker(
                            tenant_id=tenant_id,
                            version_id=v_id,
                            page_number=page_number,
                            status="failed",
                        )
                    else:
                        # Re-raise with fixed message, SQLSTATE code only, from None (zero str(exc))
                        raise transient_exc from None
                else:
                    # Permanent error: flag page for review and record failed step
                    await repo.flag_page_for_review(
                        tenant_id=tenant_id,
                        version_id=v_id,
                        page_number=page_number,
                        reason="processing_failed",
                    )
                    await repo.record_job_step(
                        tenant_id=tenant_id,
                        version_id=v_id,
                        page_number=page_number,
                        step="chunking",
                        status="failed",
                    )
                    await repo.record_page_processing_marker(
                        tenant_id=tenant_id,
                        version_id=v_id,
                        page_number=page_number,
                        status="failed",
                    )

    # Transaction 2: Separate short transaction that acquires locks and activates if complete
    async with worker_tenant_session(worker_ctx) as session:
        activation_service = create_version_activation_service(session)
        await activation_service.check_and_activate_if_complete(
            tenant_id=tenant_id,
            document_version_id=v_id,
        )


@app.task(queue="ingestion", retry=ingestion_retry_strategy, pass_context=True)
async def process_page_job(
    context: JobContext,
    tenant_id: str,
    document_version_id: str,
    page_number: int,
) -> None:
    await run_page_job(
        context=context,
        tenant_id=tenant_id,
        document_version_id=document_version_id,
        page_number=page_number,
    )


async def enqueue_uncompleted_page_jobs(
    tenant_id: str,
    document_version_id: UUID,
    repository: DocumentRepository,
    defer_fn: Callable[[str, str, int], Awaitable[None]] | None = None,
    session: object | None = None,
) -> list[int]:
    """Identify pages without a completed step and enqueue them for processing.

    Guarantees that a re-run of a split job whose subsequent queue transaction crashed
    will safely pick up all unprocessed pages without duplicating completed ones.
    If all pages are already accounted for, evaluates version activation immediately.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    uncompleted = await repository.get_uncompleted_page_numbers(
        tenant_id=tenant_id,
        version_id=document_version_id,
        step="chunking",
    )
    if not uncompleted and isinstance(session, AsyncSession):
        from app.wiring import create_version_activation_service

        activation_service = create_version_activation_service(session)
        await activation_service.check_and_activate_if_complete(
            tenant_id=tenant_id,
            document_version_id=document_version_id,
        )
        return []

    for p_num in uncompleted:
        if defer_fn is not None:
            await defer_fn(tenant_id, str(document_version_id), p_num)
        else:
            await process_page_job.defer_async(
                tenant_id=tenant_id,
                document_version_id=str(document_version_id),
                page_number=p_num,
            )
    return uncompleted


async def run_stalled_job_sweep(
    context: JobContext,
    timestamp: int = 0,
    seconds_since_heartbeat: float = 30,
) -> int:
    """Periodic sweep re-queueing stalled jobs in 'doing' whose worker heartbeat expired.

    Only queries procrastinate_jobs queue table in procrastinate schema;
    never queries tenant tables or scans across tenants.
    """
    _ = timestamp
    stalled_jobs = await context.app.job_manager.get_stalled_jobs(
        seconds_since_heartbeat=seconds_since_heartbeat,
        queue="ingestion",
    )
    requeued = 0
    for job in stalled_jobs:
        await context.app.job_manager.retry_job(job=job)
        requeued += 1
    return requeued


@app.periodic(cron="*/5 * * * *", periodic_id="stalled_job_sweep")
@app.task(queue="builtin", pass_context=True)
async def stalled_job_sweep_task(
    context: JobContext,
    timestamp: int = 0,
    seconds_since_heartbeat: float = 30,
) -> int:
    return await run_stalled_job_sweep(
        context=context,
        timestamp=timestamp,
        seconds_since_heartbeat=seconds_since_heartbeat,
    )


@dataclass(frozen=True)
class FailedPageJobSweepResult:
    repaired_count: int
    skipped_count: int
    malformed_args_count: int


FAILED_PAGE_JOB_SWEEP_LOOKBACK_DAYS: int = 7
FAILED_PAGE_JOB_SWEEP_LOOKBACK: timedelta = timedelta(days=FAILED_PAGE_JOB_SWEEP_LOOKBACK_DAYS)


def _parse_page_task_args(args: object) -> tuple[UUID, UUID, int]:
    """Parse and validate task arguments for process_page_job.

    Raises MalformedTaskArgsError with fixed message on any missing or invalid field.
    """
    if not isinstance(args, dict):
        raise MalformedTaskArgsError("Malformed task arguments") from None

    if "tenant_id" not in args:
        raise MalformedTaskArgsError("Malformed task arguments") from None
    if "document_version_id" not in args:
        raise MalformedTaskArgsError("Malformed task arguments") from None
    if "page_number" not in args:
        raise MalformedTaskArgsError("Malformed task arguments") from None

    raw_tenant = args["tenant_id"]
    raw_version = args["document_version_id"]
    raw_page = args["page_number"]

    if (
        not isinstance(raw_tenant, str)
        or not isinstance(raw_version, str)
        or not isinstance(raw_page, int)
    ):
        raise MalformedTaskArgsError("Malformed task arguments") from None

    try:
        tenant_id = UUID(raw_tenant)
        version_id = UUID(raw_version)
    except (ValueError, TypeError):
        raise MalformedTaskArgsError("Malformed task arguments") from None

    if raw_page < 1:
        raise MalformedTaskArgsError("Malformed task arguments") from None

    return tenant_id, version_id, raw_page


async def run_failed_page_job_sweep(
    context: JobContext | None = None,
    lookback_days: int = FAILED_PAGE_JOB_SWEEP_LOOKBACK_DAYS,
) -> FailedPageJobSweepResult:
    """Periodic sweep detecting page jobs that terminated in a failed/cancelled/aborted state.

    1. Queries procrastinate schema (unscoped) for terminal page jobs within lookback_days.
    2. Validates task args (counts MalformedTaskArgsError and continues).
    3. Scopes to each tenant via worker_tenant_session and invokes repo.fail_stale_page_marker.
    """
    _ = context
    lookback = timedelta(days=lookback_days)
    cutoff = datetime.now(UTC) - lookback

    terminal_jobs_stmt = text(
        "SELECT j.id AS job_id, j.args AS task_args, MAX(e.at) AS terminal_at "
        "FROM procrastinate.procrastinate_jobs j "
        "JOIN procrastinate.procrastinate_events e ON e.job_id = j.id "
        "WHERE j.task_name = :task_name "
        "  AND j.status IN ('failed', 'cancelled', 'aborted') "
        "  AND e.type IN ('failed', 'cancelled', 'aborted') "
        "  AND e.at >= :cutoff "
        "GROUP BY j.id, j.args "
        "ORDER BY MAX(e.at) ASC"
    )

    async with unscoped_session() as session:
        result = await session.execute(
            terminal_jobs_stmt,
            {
                "task_name": process_page_job.name,
                "cutoff": cutoff,
            },
        )
        candidates = result.all()

    repaired_count = 0
    skipped_count = 0
    malformed_args_count = 0

    for row in candidates:
        task_args: object = row.task_args
        terminal_at: datetime = row.terminal_at

        try:
            tenant_id, version_id, page_number = _parse_page_task_args(task_args)
        except MalformedTaskArgsError:
            malformed_args_count += 1
            continue

        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))
        async with worker_tenant_session(worker_ctx) as session:
            repo = create_document_repository(session)
            repaired = await repo.fail_stale_page_marker(
                tenant_id=str(tenant_id),
                version_id=version_id,
                page_number=page_number,
                terminal_at=terminal_at,
            )
            if repaired:
                repaired_count += 1
            else:
                skipped_count += 1

    return FailedPageJobSweepResult(
        repaired_count=repaired_count,
        skipped_count=skipped_count,
        malformed_args_count=malformed_args_count,
    )


@app.periodic(cron="*/5 * * * *", periodic_id="failed_page_job_sweep")
@app.task(queue="builtin", pass_context=True)
async def failed_page_job_sweep_task(
    context: JobContext,
    timestamp: int = 0,
) -> int:
    _ = timestamp
    result = await run_failed_page_job_sweep(context=context)
    return result.repaired_count
