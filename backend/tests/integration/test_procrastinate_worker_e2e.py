"""End-to-end integration test running the real Procrastinate worker in-process.

Proves the complete job queue execution:
1. Defer split_document_job to Procrastinate queue in schema 'procrastinate'.
2. Real in-process worker executes split job:
   - Reads version's original uploaded file from storage using file_extension.
   - Path A (PDF): DocumentSplitterService creates Page rows and images, transitions to
     "processing", then enqueues page jobs.
   - Path B (CSV/XLSX): TableIngestionService ingests tables into tables_data and transitions
     version to "ready" directly (zero page jobs).
   - Path C (Corrupt/Unparseable file or NULL file_extension): transitions version to "failed"
     (zero page jobs).
3. For PDF, worker executes per-page pipeline jobs.
4. VersionActivationService activates the document version under row lock.
5. Verifies documents.active_version_id and chunks.is_active.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from procrastinate import PsycopgConnector
from sqlalchemy import select, text

from app.adapters.storage.local import LocalStorageBackend
from app.db.env_utils import load_dotenv
from app.db.models import Chunk, Document, DocumentVersion, Page, TableData
from app.db.session import reset_session_engines, unscoped_session, worker_tenant_session
from app.ports.types import StorageKey, WorkerContext
from app.worker import app as worker_app
from app.worker import split_document_job
from tests.conftest import isolated_throwaway_database
from tests.fixtures.database_seeding import seed_document, seed_version
from tests.fixtures.synthetic_documents import create_synthetic_digital_pdf


@pytest.mark.asyncio
async def test_procrastinate_worker_in_process_e2e_activation(tmp_path: Path) -> None:
    """Run real Procrastinate worker in-process with real PDF and assert version activates."""
    load_dotenv()
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    app_user = os.getenv("DB_APP_USER", "app_user")
    app_pass = os.getenv("DB_APP_PASSWORD", "")

    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    os.environ["LOCAL_STORAGE_PATH"] = str(storage_dir)

    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "head")

        app_url = f"postgresql+psycopg://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}"
        old_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = app_url
        reset_session_engines()

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
                    status="received",
                    file_extension="pdf",
                )

            # Store real 2-page PDF in storage at documents/{ver_id}/original.pdf
            pdf_bytes = create_synthetic_digital_pdf(
                text="Patient medical history and clinical notes.",
                num_pages=2,
            )
            storage = LocalStorageBackend(base_dir=storage_dir)
            await storage.put(
                key=StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/original.pdf"),
                data=pdf_bytes,
                content_type="application/pdf",
            )

            # Open worker app and defer split_document_job (NO hand-seeded pages!)
            async with worker_app.open_async():
                await split_document_job.defer_async(
                    tenant_id=str(tenant_id),
                    document_version_id=str(ver_id),
                )

                worker_task = asyncio.create_task(
                    worker_app.run_worker_async(
                        queues=["ingestion"],
                        wait=True,
                        install_signal_handlers=False,
                    )
                )

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

                async with worker_tenant_session(worker_ctx) as s:
                    # Assert real pages were created by DocumentSplitterService
                    pages = (
                        (
                            await s.execute(
                                select(Page)
                                .where(
                                    Page.tenant_id == tenant_id,
                                    Page.document_version_id == ver_id,
                                )
                                .order_by(Page.page_number)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert len(pages) == 2, f"Expected 2 split pages, got {len(pages)}"

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


@pytest.mark.asyncio
async def test_procrastinate_worker_e2e_csv_table_ingestion(tmp_path: Path) -> None:
    """Run worker with CSV spreadsheet: creates tables_data rows and
    marks version ready directly.
    """
    load_dotenv()
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    app_user = os.getenv("DB_APP_USER", "app_user")
    app_pass = os.getenv("DB_APP_PASSWORD", "")

    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    os.environ["LOCAL_STORAGE_PATH"] = str(storage_dir)

    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "head")

        app_url = f"postgresql+psycopg://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}"
        old_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = app_url
        reset_session_engines()

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

            async with unscoped_session() as session:
                await session.execute(
                    text("INSERT INTO tenants (id, name) VALUES (:t, 'E2E CSV Tenant')"),
                    {"t": tenant_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO users (id, email, hashed_password) "
                        "VALUES (:u, 'worker_csv@test.com', 'h')"
                    ),
                    {"u": user_id},
                )

            worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))
            async with worker_tenant_session(worker_ctx) as session:
                doc_id = await seed_document(session, tenant_id=tenant_id, title="E2E CSV Document")
                ver_id = await seed_version(
                    session,
                    tenant_id=tenant_id,
                    doc_id=doc_id,
                    user_id=user_id,
                    status="received",
                    file_extension="csv",
                )

            # Store valid CSV file in storage
            csv_bytes = b"patient_id,name,diagnosis\n1,Alice,Flu\n2,Bob,Cold\n"
            storage = LocalStorageBackend(base_dir=storage_dir)
            await storage.put(
                key=StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/original.csv"),
                data=csv_bytes,
                content_type="text/csv",
            )

            async with worker_app.open_async():
                await split_document_job.defer_async(
                    tenant_id=str(tenant_id),
                    document_version_id=str(ver_id),
                )

                worker_task = asyncio.create_task(
                    worker_app.run_worker_async(
                        queues=["ingestion"],
                        wait=True,
                        install_signal_handlers=False,
                    )
                )

                async def wait_for_ready() -> bool:
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

                ready = await wait_for_ready()
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await worker_task

                assert ready is True, "CSV version failed to reach 'ready' status!"

                async with worker_tenant_session(worker_ctx) as s:
                    # Verify tables_data rows were populated
                    tbl_rows = (
                        (
                            await s.execute(
                                select(TableData).where(
                                    TableData.tenant_id == tenant_id,
                                    TableData.document_version_id == ver_id,
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert len(tbl_rows) == 2, f"Expected 2 table rows, got {len(tbl_rows)}"

                    # Zero page rows for spreadsheets
                    pages = (
                        (
                            await s.execute(
                                select(Page).where(
                                    Page.tenant_id == tenant_id,
                                    Page.document_version_id == ver_id,
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert len(pages) == 0, f"Spreadsheet should have 0 pages, got {len(pages)}"

        finally:
            worker_app.connector = original_connector
            worker_app.job_manager.connector = original_connector
            if old_db_url is not None:
                os.environ["DATABASE_URL"] = old_db_url
            else:
                os.environ.pop("DATABASE_URL", None)
            reset_session_engines()


@pytest.mark.asyncio
async def test_procrastinate_worker_e2e_null_file_extension_fails() -> None:
    """A version with file_extension NULL transitions to failed with zero page jobs."""
    load_dotenv()
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    app_user = os.getenv("DB_APP_USER", "app_user")
    app_pass = os.getenv("DB_APP_PASSWORD", "")

    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "head")

        app_url = f"postgresql+psycopg://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}"
        old_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = app_url
        reset_session_engines()

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

            async with unscoped_session() as session:
                await session.execute(
                    text("INSERT INTO tenants (id, name) VALUES (:t, 'E2E Null Ext Tenant')"),
                    {"t": tenant_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO users (id, email, hashed_password) "
                        "VALUES (:u, 'worker_null@test.com', 'h')"
                    ),
                    {"u": user_id},
                )

            worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))
            async with worker_tenant_session(worker_ctx) as session:
                doc_id = await seed_document(session, tenant_id=tenant_id, title="E2E Null Doc")
                ver_id = await seed_version(
                    session,
                    tenant_id=tenant_id,
                    doc_id=doc_id,
                    user_id=user_id,
                    status="received",
                    file_extension=None,  # file_extension is NULL
                )

            async with worker_app.open_async():
                await split_document_job.defer_async(
                    tenant_id=str(tenant_id),
                    document_version_id=str(ver_id),
                )

                worker_task = asyncio.create_task(
                    worker_app.run_worker_async(
                        queues=["ingestion"],
                        wait=True,
                        install_signal_handlers=False,
                    )
                )

                async def wait_for_failed() -> bool:
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
                            if v is not None and v.status == "failed":
                                return True
                    return False

                failed = await wait_for_failed()
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await worker_task

                assert failed is True, (
                    "Version with NULL file_extension must transition to 'failed'!"
                )

                async with worker_tenant_session(worker_ctx) as s:
                    # Assert 0 pages were created
                    pages = (
                        (
                            await s.execute(
                                select(Page).where(
                                    Page.tenant_id == tenant_id,
                                    Page.document_version_id == ver_id,
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert len(pages) == 0, (
                        "No pages should be enqueued or created for failed split!"
                    )

        finally:
            worker_app.connector = original_connector
            worker_app.job_manager.connector = original_connector
            if old_db_url is not None:
                os.environ["DATABASE_URL"] = old_db_url
            else:
                os.environ.pop("DATABASE_URL", None)
            reset_session_engines()


@pytest.mark.asyncio
async def test_procrastinate_worker_e2e_corrupt_file_fails(tmp_path: Path) -> None:
    """A corrupted file during split transitions version to failed with zero page jobs."""
    load_dotenv()
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    app_user = os.getenv("DB_APP_USER", "app_user")
    app_pass = os.getenv("DB_APP_PASSWORD", "")

    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    os.environ["LOCAL_STORAGE_PATH"] = str(storage_dir)

    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "head")

        app_url = f"postgresql+psycopg://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}"
        old_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = app_url
        reset_session_engines()

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

            async with unscoped_session() as session:
                await session.execute(
                    text("INSERT INTO tenants (id, name) VALUES (:t, 'E2E Corrupt Tenant')"),
                    {"t": tenant_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO users (id, email, hashed_password) "
                        "VALUES (:u, 'worker_corrupt@test.com', 'h')"
                    ),
                    {"u": user_id},
                )

            worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))
            async with worker_tenant_session(worker_ctx) as session:
                doc_id = await seed_document(session, tenant_id=tenant_id, title="E2E Corrupt Doc")
                ver_id = await seed_version(
                    session,
                    tenant_id=tenant_id,
                    doc_id=doc_id,
                    user_id=user_id,
                    status="received",
                    file_extension="pdf",
                )

            # Put corrupted bytes in storage
            storage = LocalStorageBackend(base_dir=storage_dir)
            await storage.put(
                key=StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/original.pdf"),
                data=b"corrupted invalid not a pdf",
                content_type="application/pdf",
            )

            async with worker_app.open_async():
                await split_document_job.defer_async(
                    tenant_id=str(tenant_id),
                    document_version_id=str(ver_id),
                )

                worker_task = asyncio.create_task(
                    worker_app.run_worker_async(
                        queues=["ingestion"],
                        wait=True,
                        install_signal_handlers=False,
                    )
                )

                async def wait_for_failed() -> bool:
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
                            if v is not None and v.status == "failed":
                                return True
                    return False

                failed = await wait_for_failed()
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await worker_task

                assert failed is True, "Corrupt file version must transition to 'failed'!"

                async with worker_tenant_session(worker_ctx) as s:
                    pages = (
                        (
                            await s.execute(
                                select(Page).where(
                                    Page.tenant_id == tenant_id,
                                    Page.document_version_id == ver_id,
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert len(pages) == 0, "No pages should be created for corrupt document!"

        finally:
            worker_app.connector = original_connector
            worker_app.job_manager.connector = original_connector
            if old_db_url is not None:
                os.environ["DATABASE_URL"] = old_db_url
            else:
                os.environ.pop("DATABASE_URL", None)
            reset_session_engines()
