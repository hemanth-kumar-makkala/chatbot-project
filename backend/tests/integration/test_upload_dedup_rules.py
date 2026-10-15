"""Integration tests for upload deduplication rules (Part A).

Runs on an isolated throwaway database upgraded to head (migration 0010):
1. upload, retire, re-upload same bytes: accepted, new document id, the old one stays retired.
2. upload whose version ends 'failed', re-upload same bytes: accepted.
3. upload active, re-upload same bytes: duplicate (unchanged behaviour).
4. same bytes in two tenants: both accepted.
5. two concurrent uploads of the same bytes in one tenant: exactly one accepted, one duplicate.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from dotenv import load_dotenv
from sqlalchemy import select, text

from app.core.ingestion.upload import UploadPayload, UploadService
from app.db.models import Document, DocumentVersion
from app.db.session import (
    reset_session_engines,
    tenant_session,
    unscoped_session,
)
from app.observability.audit import AuditLogger
from app.ports.types import TenantContext, UserRole
from app.wiring import (
    create_document_repository,
    create_malware_scanner,
    create_storage_backend,
)
from tests.conftest import isolated_throwaway_database


class MockAuditLogger(AuditLogger):
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def record(
        self,
        tenant_ctx: TenantContext,
        action: str,
        event_type: str,
        resource_id: str | None = None,
    ) -> UUID:
        event_id = uuid4()
        self.events.append(
            {
                "event_id": event_id,
                "tenant_id": tenant_ctx.tenant_id,
                "user_id": tenant_ctx.user_id,
                "action": action,
                "event_type": event_type,
                "resource_id": resource_id,
            }
        )
        return event_id


@asynccontextmanager
async def throwaway_upload_env(
    tmp_path: Path,
) -> AsyncIterator[dict[str, Any]]:
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

        try:
            yield {
                "db_name": throwaway_db.db_name,
                "url": app_url,
                "storage_dir": str(storage_dir),
            }
        finally:
            if old_db_url is not None:
                os.environ["DATABASE_URL"] = old_db_url
            else:
                os.environ.pop("DATABASE_URL", None)
            reset_session_engines()


async def _seed_tenant_and_user(name: str = "Dedup Tenant") -> tuple[TenantContext, UUID, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    async with unscoped_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, name) VALUES (:t, :n);"),
            {"t": tenant_id, "n": name},
        )
        await session.execute(
            text("INSERT INTO users (id, email, hashed_password) VALUES (:u, :e, 'h');"),
            {"u": user_id, "e": f"user-{uuid4().hex[:6]}@example.com"},
        )

    ctx = TenantContext(
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        role=UserRole.CLIENT_ADMIN,
        session_id=str(uuid4()),
    )
    return ctx, tenant_id, user_id


def _create_upload_service(
    session: Any,
    storage_dir: str,
    before_lock_hook: Any = None,
) -> UploadService:
    repo = create_document_repository(session)
    storage = create_storage_backend(profile="mock", base_dir=storage_dir)
    scanner = create_malware_scanner(profile="mock")
    logger = MockAuditLogger()
    return UploadService(
        document_repo=repo,
        storage=storage,
        malware_scanner=scanner,
        audit_logger=logger,
        before_lock_hook=before_lock_hook,
    )


PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<\n>>\nendobj\ntrailer\n<<\n>>\n%%EOF\n"


@pytest.mark.asyncio
async def test_reupload_after_retire_creates_new_document(tmp_path: Path) -> None:
    """1. Upload, retire, re-upload same bytes: accepted, new doc, old doc stays retired."""
    async with throwaway_upload_env(tmp_path) as env:
        ctx, tenant_id, _ = await _seed_tenant_and_user("Retire Tenant")

        # Initial upload
        async with tenant_session(ctx) as session:
            svc = _create_upload_service(session, env["storage_dir"])
            res1 = await svc.upload_document(
                ctx,
                UploadPayload(filename="first.pdf", content=PDF_BYTES),
            )
            assert not res1.is_duplicate
            doc_id_1 = res1.document_id

        # Retire the first document
        async with tenant_session(ctx) as session:
            doc = (
                await session.execute(
                    select(Document).where(
                        Document.tenant_id == tenant_id,
                        Document.id == doc_id_1,
                    )
                )
            ).scalar_one()
            doc.is_retired = True
            await session.flush()

        # Re-upload same bytes
        async with tenant_session(ctx) as session:
            svc = _create_upload_service(session, env["storage_dir"])
            res2 = await svc.upload_document(
                ctx,
                UploadPayload(filename="second.pdf", content=PDF_BYTES),
            )
            assert not res2.is_duplicate
            assert res2.document_id != doc_id_1

        # Verify old document remains retired
        async with tenant_session(ctx) as session:
            old_doc = (
                await session.execute(
                    select(Document).where(
                        Document.tenant_id == tenant_id,
                        Document.id == doc_id_1,
                    )
                )
            ).scalar_one()
            assert old_doc.is_retired is True


@pytest.mark.asyncio
async def test_reupload_after_failed_version_creates_new_document(tmp_path: Path) -> None:
    """2. Upload whose version ends 'failed', re-upload same bytes: accepted."""
    async with throwaway_upload_env(tmp_path) as env:
        ctx, tenant_id, _ = await _seed_tenant_and_user("Failure Tenant")

        # Initial upload
        async with tenant_session(ctx) as session:
            svc = _create_upload_service(session, env["storage_dir"])
            res1 = await svc.upload_document(
                ctx,
                UploadPayload(filename="failed_doc.pdf", content=PDF_BYTES),
            )
            assert not res1.is_duplicate
            ver_id_1 = res1.document_version_id
            doc_id_1 = res1.document_id

        # Set version status to 'failed'
        async with tenant_session(ctx) as session:
            ver = (
                await session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.tenant_id == tenant_id,
                        DocumentVersion.id == ver_id_1,
                    )
                )
            ).scalar_one()
            ver.status = "failed"
            await session.flush()

        # Re-upload same bytes: must be accepted and create a new document
        async with tenant_session(ctx) as session:
            svc = _create_upload_service(session, env["storage_dir"])
            res2 = await svc.upload_document(
                ctx,
                UploadPayload(filename="retry_after_fail.pdf", content=PDF_BYTES),
            )
            assert not res2.is_duplicate
            assert res2.document_id != doc_id_1


@pytest.mark.asyncio
async def test_reupload_while_active_returns_duplicate(tmp_path: Path) -> None:
    """3. Upload active, re-upload same bytes: duplicate (unchanged behaviour)."""
    async with throwaway_upload_env(tmp_path) as env:
        ctx, _, _ = await _seed_tenant_and_user("Active Tenant")

        # Initial upload
        async with tenant_session(ctx) as session:
            svc = _create_upload_service(session, env["storage_dir"])
            res1 = await svc.upload_document(
                ctx,
                UploadPayload(filename="active.pdf", content=PDF_BYTES),
            )
            assert not res1.is_duplicate

        # Second upload: must return is_duplicate == True
        async with tenant_session(ctx) as session:
            svc = _create_upload_service(session, env["storage_dir"])
            res2 = await svc.upload_document(
                ctx,
                UploadPayload(filename="active_copy.pdf", content=PDF_BYTES),
            )
            assert res2.is_duplicate is True
            assert res2.document_id == res1.document_id
            assert res2.document_version_id == res1.document_version_id


@pytest.mark.asyncio
async def test_same_bytes_different_tenants_both_accepted(tmp_path: Path) -> None:
    """4. Same bytes in two tenants: both accepted (Invariant 15: dedup never crosses tenants)."""
    async with throwaway_upload_env(tmp_path) as env:
        ctx_a, _, _ = await _seed_tenant_and_user("Tenant Alpha")
        ctx_b, _, _ = await _seed_tenant_and_user("Tenant Beta")

        async with tenant_session(ctx_a) as session_a:
            svc_a = _create_upload_service(session_a, env["storage_dir"])
            res_a = await svc_a.upload_document(
                ctx_a,
                UploadPayload(filename="doc_a.pdf", content=PDF_BYTES),
            )
            assert not res_a.is_duplicate

        async with tenant_session(ctx_b) as session_b:
            svc_b = _create_upload_service(session_b, env["storage_dir"])
            res_b = await svc_b.upload_document(
                ctx_b,
                UploadPayload(filename="doc_b.pdf", content=PDF_BYTES),
            )
            assert not res_b.is_duplicate
            assert res_b.document_id != res_a.document_id


@pytest.mark.asyncio
async def test_concurrent_uploads_same_bytes_one_tenant(tmp_path: Path) -> None:
    """5. Two concurrent uploads in one tenant: exactly one accepted, one duplicate."""
    async with throwaway_upload_env(tmp_path) as env:
        ctx, tenant_id, _ = await _seed_tenant_and_user("Concurrent Tenant")

        barrier = asyncio.Barrier(2)

        async def race_hook() -> None:
            await barrier.wait()

        async def _do_upload() -> Any:
            async with tenant_session(ctx) as session:
                svc = _create_upload_service(
                    session,
                    env["storage_dir"],
                    before_lock_hook=race_hook,
                )
                return await svc.upload_document(
                    ctx,
                    UploadPayload(filename="concurrent.pdf", content=PDF_BYTES),
                )

        results = await asyncio.gather(_do_upload(), _do_upload())
        duplicates = [r for r in results if r.is_duplicate]
        creations = [r for r in results if not r.is_duplicate]

        assert len(creations) == 1
        assert len(duplicates) == 1

        async with tenant_session(ctx) as session:
            doc_count = (
                await session.execute(
                    text("SELECT COUNT(*) FROM documents WHERE tenant_id = :t_id"),
                    {"t_id": tenant_id},
                )
            ).scalar()
            ver_count = (
                await session.execute(
                    text("SELECT COUNT(*) FROM document_versions WHERE tenant_id = :t_id"),
                    {"t_id": tenant_id},
                )
            ).scalar()

        assert doc_count == 1
        assert ver_count == 1
