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
from uuid import UUID

from procrastinate import App, PsycopgConnector

from app.db.env_utils import load_dotenv
from app.observability.tracing_guard import enforce_langsmith_tracing_disabled
from app.ports.document_repository import DocumentRepository

# Mandatory: enforce LangSmith tracing disabled at module import / worker startup
enforce_langsmith_tracing_disabled()


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


@app.task(queue="ingestion", retry=5)
async def split_document_job(
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
    async with worker_tenant_session(worker_ctx) as session:
        repo = create_document_repository(session)
        await enqueue_uncompleted_page_jobs(
            tenant_id=tenant_id,
            document_version_id=v_id,
            repository=repo,
        )


@app.task(queue="ingestion", retry=5)
async def process_page_job(
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
    import os
    import uuid

    from app.db.session import worker_tenant_session
    from app.ports.types import WorkerContext
    from app.wiring import create_page_pipeline_service, create_version_activation_service

    v_id = UUID(document_version_id)
    profile = os.getenv("APP_PROFILE", "mock")
    worker_ctx = WorkerContext(tenant_id=tenant_id, job_id=str(uuid.uuid4()))

    # Transaction 1: Commit page work (chunks, job_steps) in its own transaction
    async with worker_tenant_session(worker_ctx) as session:
        pipeline = create_page_pipeline_service(session, profile=profile)
        await pipeline.process_page(
            tenant_id=tenant_id,
            document_version_id=v_id,
            page_number=page_number,
        )

    # Transaction 2: Separate short transaction that acquires locks and activates if complete
    async with worker_tenant_session(worker_ctx) as session:
        activation_service = create_version_activation_service(session)
        await activation_service.check_and_activate_if_complete(
            tenant_id=tenant_id,
            document_version_id=v_id,
        )


async def enqueue_uncompleted_page_jobs(
    tenant_id: str,
    document_version_id: UUID,
    repository: DocumentRepository,
    defer_fn: Callable[[str, str, int], Awaitable[None]] | None = None,
) -> list[int]:
    """Identify pages without a completed step and enqueue them for processing.

    Guarantees that a re-run of a split job whose subsequent queue transaction crashed
    will safely pick up all unprocessed pages without duplicating completed ones.
    """
    uncompleted = await repository.get_uncompleted_page_numbers(
        tenant_id=tenant_id,
        version_id=document_version_id,
        step="chunking",
    )
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
