"""End-to-end integration test running the real Procrastinate worker in-process.

Proves the complete job queue execution:
1. Defer split_document_job to Procrastinate queue in schema 'procrastinate'.
2. Real in-process worker executes split job and enqueues page jobs.
3. Worker executes per-page pipeline jobs.
4. VersionActivationService activates the document version under row lock.
5. Verifies documents.active_version_id and chunks.is_active.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from uuid import uuid4

import pytest
from alembic import command
from procrastinate import PsycopgConnector
from sqlalchemy import select, text

from app.db.env_utils import load_dotenv
from app.db.models import Chunk, Document, DocumentVersion
from app.db.session import reset_session_engines, unscoped_session
from app.worker import app as worker_app
from app.worker import split_document_job
from tests.conftest import isolated_throwaway_database
from tests.fixtures.database_seeding import seed_document, seed_page, seed_version


@pytest.mark.asyncio
async def test_procrastinate_worker_in_process_e2e_activation() -> None:
    """Run real Procrastinate worker in-process and assert document version activates."""
    load_dotenv()
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    app_user = os.getenv("DB_APP_USER", "app_user")
    app_pass = os.getenv("DB_APP_PASSWORD", "")

    # Execute inside an isolated throwaway database to verify 0006 cleanly
    async with isolated_throwaway_database() as throwaway_db:
        # 1. Apply migrations up to head (including 0006)
        command.upgrade(throwaway_db.cfg, "head")

        app_url = f"postgresql+psycopg://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}"

        old_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = app_url
        reset_session_engines()

        # 2. Configure worker Procrastinate app connector for throwaway database
        connector = PsycopgConnector(
            conninfo=f"postgresql://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}",
            kwargs={"options": "-c search_path=procrastinate,public"},
        )
        original_connector = worker_app.connector
        worker_app.connector = connector
        worker_app.job_manager.connector = connector

        try:
            tenant_id = uuid4()
            user_id = uuid4()
            from app.db.session import worker_tenant_session
            from app.ports.types import WorkerContext

            # 3. Seed global tables (tenants, users) in unscoped_session
            async with unscoped_session() as session:
                await session.execute(
                    text("INSERT INTO tenants (id, name) VALUES (:t, 'E2E Worker Tenant')"),
                    {"t": tenant_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO users (id, email, hashed_password) "
                        "VALUES (:u, 'worker_e2e@test.com', 'h')"
                    ),
                    {"u": user_id},
                )

            # 4. Seed tenant tables under worker_tenant_session to satisfy RLS
            worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))
            async with worker_tenant_session(worker_ctx) as session:
                doc_id = await seed_document(
                    session, tenant_id=tenant_id, title="E2E Worker Document"
                )
                ver_id = await seed_version(
                    session,
                    tenant_id=tenant_id,
                    doc_id=doc_id,
                    user_id=user_id,
                    status="processing",
                )
                # Seed 2 pages to test multi-page queuing and completion
                await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)
                await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=2)

            # 5. Open worker app and defer split_document_job
            async with worker_app.open_async():
                await split_document_job.defer_async(
                    tenant_id=str(tenant_id),
                    document_version_id=str(ver_id),
                )

                # 6. Run in-process worker in background task
                worker_task = asyncio.create_task(
                    worker_app.run_worker_async(
                        queues=["ingestion"],
                        wait=True,
                        install_signal_handlers=False,
                    )
                )

                # 7. Poll for version activation with bounded timeout (max 10s)
                async def wait_for_activation() -> bool:
                    for _ in range(100):
                        await asyncio.sleep(0.1)
                        async with worker_tenant_session(worker_ctx) as s:
                            v = (
                                await s.execute(
                                    select(DocumentVersion).where(
                                        DocumentVersion.tenant_id == tenant_id,
                                        DocumentVersion.id == ver_id,
                                    )
                                )
                            ).scalar_one_or_none()
                            if v is not None and v.status == "ready":
                                return True
                    return False

                activated = await wait_for_activation()
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await worker_task

                assert activated is True, "Document version failed to activate via worker pipeline!"

                # 8. Verify document has active version flipped and chunks are active
                async with worker_tenant_session(worker_ctx) as s:
                    doc = (
                        await s.execute(
                            select(Document).where(
                                Document.tenant_id == tenant_id, Document.id == doc_id
                            )
                        )
                    ).scalar_one()
                    assert doc.active_version_id == ver_id

                    chunks = (
                        (
                            await s.execute(
                                select(Chunk).where(
                                    Chunk.tenant_id == tenant_id,
                                    Chunk.document_version_id == ver_id,
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert len(chunks) > 0, "No chunks created by worker pipeline!"
                    assert all(c.is_active for c in chunks), "Not all chunks activated!"

        finally:
            worker_app.connector = original_connector
            worker_app.job_manager.connector = original_connector
            if old_db_url is not None:
                os.environ["DATABASE_URL"] = old_db_url
            else:
                os.environ.pop("DATABASE_URL", None)
            reset_session_engines()
