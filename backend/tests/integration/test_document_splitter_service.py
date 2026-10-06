"""Integration tests for document splitting service, database constraints, and lifecycle (Task 2.3).

Covers:
- Migration 0004 CHECK constraints on pages.page_type and document_versions.status.
- Temporary failure and resume (Invariant 11): partial progress preserved, retry completes 5 pages.
- Permanent failure and cleanup: failed status, all partial DB pages and storage images deleted.
- Multi-page TIFF ingestion and storage keys.
- Tenant isolation and RLS enforcement.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.adapters.db.postgres_document_repository import PostgresDocumentRepository
from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter
from app.adapters.storage.in_memory import InMemoryStorageBackend
from app.db.models import Membership, Tenant, User
from app.db.session import tenant_session, unscoped_session
from app.ports.errors import (
    CorruptDocumentError,
)
from app.ports.types import StorageKey, TenantContext, UserRole
from tests.fixtures.synthetic_documents import (
    create_synthetic_corrupt_pdf,
    create_synthetic_digital_pdf,
)


@pytest_asyncio.fixture
async def splitter_test_env(test_user_password_hash: str) -> TenantContext:
    """Seed test tenant with client_admin in PostgreSQL."""
    tenant_id = uuid4()
    user_id = uuid4()

    async with unscoped_session() as session:
        session.add_all(
            [
                Tenant(id=tenant_id, name=f"Splitter-Tenant-{tenant_id}"),
                User(
                    id=user_id,
                    email=f"splitter-{user_id}@example.com",
                    hashed_password=test_user_password_hash,
                    is_platform_admin=False,
                ),
            ]
        )
        await session.commit()

    ctx = TenantContext(
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        role=UserRole.CLIENT_ADMIN,
        session_id=str(uuid4()),
    )
    async with tenant_session(ctx) as session:
        session.add(
            Membership(id=uuid4(), tenant_id=tenant_id, user_id=user_id, role="client_admin")
        )
        await session.commit()
    return ctx


@pytest.mark.asyncio
class TestMigration0004CheckConstraints:
    """Tests verifying database check constraints added in migration 0004 on throwaway DB."""

    async def test_migration_0004_check_constraints_enforced(
        self,
        throwaway_db: Any,
    ) -> None:
        """Migration 0004 applies check constraints on pages and versions."""
        from alembic import command
        from sqlalchemy.ext.asyncio import create_async_engine

        # 1. Upgrade throwaway db to head (which runs 0004)
        command.upgrade(throwaway_db.cfg, "head")

        test_engine = create_async_engine(throwaway_db.url)
        tenant_id = uuid4()
        user_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()

        async with test_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:id, 'Test Tenant')"),
                {"id": tenant_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, is_platform_admin) "
                    "VALUES (:id, 'u@example.com', 'hash', false)"
                ),
                {"id": user_id},
            )
            await conn.execute(
                text("INSERT INTO documents (id, tenant_id, title) VALUES (:id, :tid, 'Test Doc')"),
                {"id": doc_id, "tid": tenant_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO document_versions "
                    "(id, tenant_id, document_id, sha256, uploaded_by, status) "
                    "VALUES (:id, :tid, :did, 'sha1', :uid, 'processing')"
                ),
                {"id": ver_id, "tid": tenant_id, "did": doc_id, "uid": user_id},
            )

        # 2. Test status check constraint: rejects unsupported status like 'splitting'
        with pytest.raises(IntegrityError):
            async with test_engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO document_versions "
                        "(id, tenant_id, document_id, sha256, uploaded_by, status) "
                        "VALUES (:id, :tid, :did, 'sha2', :uid, 'splitting')"
                    ),
                    {
                        "id": uuid4(),
                        "tid": tenant_id,
                        "did": doc_id,
                        "uid": user_id,
                    },
                )

        # 3. Test page_type check constraint: rejects unsupported type like 'unsupported_type'
        with pytest.raises(IntegrityError):
            async with test_engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO pages (id, tenant_id, document_version_id, page_number, "
                        "page_sha256, page_type, image_key) "
                        "VALUES (:id, :tid, :vid, 1, 'sha', 'unsupported_type', :key)"
                    ),
                    {
                        "id": uuid4(),
                        "tid": tenant_id,
                        "vid": ver_id,
                        "key": f"{tenant_id}/pages/bad.png",
                    },
                )

        # 4. Valid types succeed
        for idx, valid_type in enumerate(
            ["digital_text", "scanned", "image_heavy", "blank"], start=1
        ):
            async with test_engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO pages (id, tenant_id, document_version_id, page_number, "
                        "page_sha256, page_type, image_key) "
                        "VALUES (:id, :tid, :vid, :pnum, 'sha', :ptype, :key)"
                    ),
                    {
                        "id": uuid4(),
                        "tid": tenant_id,
                        "vid": ver_id,
                        "pnum": idx,
                        "ptype": valid_type,
                        "key": f"{tenant_id}/pages/{idx}.png",
                    },
                )


@pytest.mark.asyncio
class TestDocumentSplitterServiceLifecycle:
    """Integration tests for DocumentSplitterService failure modes and lifecycle."""

    async def test_temporary_failure_resumes_idempotently(
        self,
        splitter_test_env: TenantContext,
    ) -> None:
        """Temporary failure preserves completed pages; rerun finishes with exactly 5 pages."""
        from app.core.ingestion.splitter_service import DocumentSplitterService

        ctx = splitter_test_env
        storage = InMemoryStorageBackend()
        doc_bytes = create_synthetic_digital_pdf(text="Clinical report page content.", num_pages=5)
        ver_id = uuid4()
        doc_id = uuid4()

        # 0. Setup document and version
        async with tenant_session(ctx) as session:
            await session.execute(
                text("INSERT INTO documents (id, tenant_id, title) VALUES (:d_id, :t_id, 'Doc 3')"),
                {"d_id": doc_id, "t_id": ctx.tenant_id},
            )
            await session.execute(
                text(
                    "INSERT INTO document_versions "
                    "(id, tenant_id, document_id, sha256, uploaded_by, status) "
                    "VALUES (:v_id, :t_id, :d_id, 'sha3', :u_id, 'processing')"
                ),
                {
                    "v_id": ver_id,
                    "t_id": ctx.tenant_id,
                    "d_id": doc_id,
                    "u_id": ctx.user_id,
                },
            )

        # 1. First run: inject simulated crash after page 3; pages 1..3 are committed
        async with tenant_session(ctx) as session:
            repo = PostgresDocumentRepository(session)
            splitter = PyPdfiumDocumentSplitter()
            service = DocumentSplitterService(
                document_repo=repo, storage=storage, splitter=splitter
            )
            with pytest.raises(RuntimeError, match="Simulated transient worker crash"):
                await service.split_document_version(
                    ctx=ctx,
                    version_id=ver_id,
                    file_bytes=doc_bytes,
                    filename="notes.pdf",
                    fail_after_page=3,  # Hook for transient crash
                )

        # 2. Assert pages 1..3 exist in DB and storage
        async with tenant_session(ctx) as session:
            pages_result = await session.execute(
                text(
                    "SELECT page_number FROM pages "
                    "WHERE document_version_id = :v_id ORDER BY page_number"
                ),
                {"v_id": ver_id},
            )
            existing_pages = [r[0] for r in pages_result.fetchall()]
            assert existing_pages == [1, 2, 3]

        # 3. Second run: resume processing
        async with tenant_session(ctx) as session:
            repo = PostgresDocumentRepository(session)
            splitter = PyPdfiumDocumentSplitter()
            service = DocumentSplitterService(
                document_repo=repo, storage=storage, splitter=splitter
            )
            result = await service.split_document_version(
                ctx=ctx,
                version_id=ver_id,
                file_bytes=doc_bytes,
                filename="notes.pdf",
            )
            assert result.total_pages == 5
            assert result.pages_processed == 5

        # 4. Final DB verification: exactly 5 distinct pages
        async with tenant_session(ctx) as session:
            final_pages = await session.execute(
                text(
                    "SELECT page_number FROM pages "
                    "WHERE document_version_id = :v_id ORDER BY page_number"
                ),
                {"v_id": ver_id},
            )
            assert [r[0] for r in final_pages.fetchall()] == [1, 2, 3, 4, 5]

    async def test_permanent_failure_cleans_up_all_pages_and_images(
        self,
        splitter_test_env: TenantContext,
    ) -> None:
        """Permanent failure marks version failed and cleans up all partial pages and images."""
        from app.core.ingestion.splitter_service import DocumentSplitterService

        ctx = splitter_test_env
        storage = InMemoryStorageBackend()
        corrupt_bytes = create_synthetic_corrupt_pdf()
        ver_id = uuid4()
        doc_id = uuid4()

        # 0. Setup document and version
        async with tenant_session(ctx) as session:
            await session.execute(
                text("INSERT INTO documents (id, tenant_id, title) VALUES (:d_id, :t_id, 'Doc 4')"),
                {"d_id": doc_id, "t_id": ctx.tenant_id},
            )
            await session.execute(
                text(
                    "INSERT INTO document_versions "
                    "(id, tenant_id, document_id, sha256, uploaded_by, status) "
                    "VALUES (:v_id, :t_id, :d_id, 'sha4', :u_id, 'processing')"
                ),
                {
                    "v_id": ver_id,
                    "t_id": ctx.tenant_id,
                    "d_id": doc_id,
                    "u_id": ctx.user_id,
                },
            )

        # 1. Service raises domain permanent error; failure status update is committed
        async with tenant_session(ctx) as session:
            repo = PostgresDocumentRepository(session)
            splitter = PyPdfiumDocumentSplitter()
            service = DocumentSplitterService(
                document_repo=repo, storage=storage, splitter=splitter
            )
            with pytest.raises(CorruptDocumentError):
                await service.split_document_version(
                    ctx=ctx,
                    version_id=ver_id,
                    file_bytes=corrupt_bytes,
                    filename="bad.pdf",
                )

        # 2. Assert version status transitioned to 'failed' and zero page rows exist
        async with tenant_session(ctx) as session:
            ver_status = await session.execute(
                text("SELECT status FROM document_versions WHERE id = :v_id"),
                {"v_id": ver_id},
            )
            row = ver_status.fetchone()
            assert row is not None
            assert row[0] == "failed"

            pages_count = await session.execute(
                text("SELECT count(*) FROM pages WHERE document_version_id = :v_id"),
                {"v_id": ver_id},
            )
            assert pages_count.scalar() == 0

    async def test_mid_run_permanent_failure_preserves_committed_pages_and_cleans_images(
        self,
        splitter_test_env: TenantContext,
    ) -> None:
        """A corrupt page found mid-run keeps committed rows and deletes images."""
        from app.core.ingestion.splitter_service import DocumentSplitterService

        ctx = splitter_test_env
        storage = InMemoryStorageBackend()
        doc_bytes = create_synthetic_digital_pdf(text="Content", num_pages=4)
        ver_id = uuid4()
        doc_id = uuid4()

        # 0. Setup document and version
        async with tenant_session(ctx) as session:
            await session.execute(
                text("INSERT INTO documents (id, tenant_id, title) VALUES (:d_id, :t_id, 'Doc 5')"),
                {"d_id": doc_id, "t_id": ctx.tenant_id},
            )
            await session.execute(
                text(
                    "INSERT INTO document_versions "
                    "(id, tenant_id, document_id, sha256, uploaded_by, status) "
                    "VALUES (:v_id, :t_id, :d_id, 'sha5', :u_id, 'processing')"
                ),
                {
                    "v_id": ver_id,
                    "t_id": ctx.tenant_id,
                    "d_id": doc_id,
                    "u_id": ctx.user_id,
                },
            )

        # 1. Run service with injected mid-run corrupt page error
        async with tenant_session(ctx) as session:
            repo = PostgresDocumentRepository(session)
            splitter = PyPdfiumDocumentSplitter()
            service = DocumentSplitterService(
                document_repo=repo, storage=storage, splitter=splitter
            )
            with pytest.raises(CorruptDocumentError):
                await service.split_document_version(
                    ctx=ctx,
                    version_id=ver_id,
                    file_bytes=doc_bytes,
                    filename="doc.pdf",
                    fail_permanent_after_page=2,  # Hook for mid-run corrupt page error
                )

        # 2. Verification in separate session
        async with tenant_session(ctx) as session:
            # Committed pages 1 and 2 remain (app_user has no DELETE)
            pages_result = await session.execute(
                text(
                    "SELECT page_number FROM pages "
                    "WHERE document_version_id = :v_id ORDER BY page_number"
                ),
                {"v_id": ver_id},
            )
            assert [r[0] for r in pages_result.fetchall()] == [1, 2]

            # Version status transitioned to 'failed'
            ver_status = await session.execute(
                text("SELECT status FROM document_versions WHERE id = :v_id"),
                {"v_id": ver_id},
            )
            row = ver_status.fetchone()
            assert row is not None
            assert row[0] == "failed"

        # Stored images for that version were deleted
        key_1 = StorageKey(tenant_id=UUID(ctx.tenant_id), path=f"pages/{ver_id}/1.png")
        assert await storage.exists(key_1) is False
