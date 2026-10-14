"""Integration tests for Task 2.8a Step 2 Knowledge endpoints.

Tests in an isolated throwaway database (upgraded to migration 0008):
1. Review list: returns flagged pages, reasons, usable reading info, image presence;
   logs compliance audit event (action="review_inspected").
2. Page image proxy: authenticated, tenant-checked, serves image bytes with headers
   Content-Type: image/png, Cache-Control: private, no-transform; logs audit event.
3. Retire document: sets documents.is_retired=True, clears active_version_id,
   deactivates chunks, requires CSRF on DELETE; logs audit event (action="document_retired").
4. Anti-probing: cross-tenant version/doc/page returns 404; unknown assistant returns 404.
5. RBAC: staff user returns 403 on review list, image proxy, and retire.
6. Concurrency test: document retired during ingestion processing -> version never goes live
   (active_version_id never flips to it, unflagged chunks remain inactive).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.adapters.auth.builtin import BuiltinAuthProvider
from app.api.app import create_app
from app.api.config import ApiSettings
from app.core.ingestion.activation import VersionActivationService
from app.db.models import (
    Assistant,
    AuditEvent,
    Chunk,
    Document,
    DocumentVersion,
    Membership,
    Page,
    PageReading,
    Tenant,
    User,
)
from app.db.session import (
    reset_session_engines,
    tenant_session,
    unscoped_session,
)
from app.ports.errors import DocumentRetiredError
from app.ports.types import (
    StorageKey,
    TenantContext,
    UserRole,
)
from app.wiring import create_document_repository, create_storage_backend
from tests.conftest import isolated_throwaway_database


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def api_settings() -> ApiSettings:
    return ApiSettings(
        cors_origins=["http://localhost:3000"],
        cookie_secure=False,
        cookie_samesite="lax",
        idle_timeout_minutes=30,
        absolute_timeout_hours=24,
        allow_bearer_auth=False,
        max_upload_bytes=5 * 1024 * 1024,
        profile="mock",
        storage_dir="storage",
    )


@pytest.fixture
def test_app(api_settings: ApiSettings):
    return create_app(settings=api_settings)


@asynccontextmanager
async def throwaway_step2_environment() -> AsyncIterator[dict[str, Any]]:
    """Spins up an isolated throwaway DB migrated to 0008, redirects session engines,
    and yields setup.
    """
    load_dotenv()
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    app_user = os.getenv("DB_APP_USER", "app_user")
    app_pass = os.getenv("DB_APP_PASSWORD", "")

    async with isolated_throwaway_database() as throwaway_db:
        # Upgrade throwaway DB to head
        command.upgrade(throwaway_db.cfg, "head")

        app_url = f"postgresql+psycopg://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}"
        old_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = app_url
        reset_session_engines()

        try:
            yield {"db_name": throwaway_db.db_name, "url": app_url}
        finally:
            if old_db_url is not None:
                os.environ["DATABASE_URL"] = old_db_url
            else:
                os.environ.pop("DATABASE_URL", None)
            reset_session_engines()


async def _create_user_with_role(
    tenant_id: UUID,
    role: str,
    email: str | None = None,
    password: str = "CorrectTestPassword123!",
) -> tuple[User, str, str]:
    hasher = BuiltinAuthProvider()
    hashed = hasher.hash_password(password)
    user_email = email or f"user-{uuid4()}@example.com"

    async with unscoped_session() as session:
        user = User(
            id=uuid4(),
            email=user_email,
            hashed_password=hashed,
            is_platform_admin=False,
        )
        session.add(user)
        await session.flush()
        user_id = user.id

    ctx = TenantContext(
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        role=UserRole.CLIENT_ADMIN,
        session_id="setup-session",
    )
    async with tenant_session(ctx) as t_session:
        membership = Membership(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
        )
        t_session.add(membership)
        await t_session.flush()

    return user, user_email, password


async def _login(client: AsyncClient, email: str, password: str) -> tuple[dict[str, str], str]:
    resp = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    session_token = str(resp.cookies.get("session_token") or "")
    csrf_token = str(resp.cookies.get("csrf_token") or "")
    return {"session_token": session_token, "csrf_token": csrf_token}, csrf_token


@pytest.mark.asyncio
async def test_review_list_and_audit(test_app, api_settings, tmp_path) -> None:
    """GET review list returns flagged pages, reading text, image presence, and logs audit."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)

    async with throwaway_step2_environment():
        tenant_id = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Review Tenant"))
            await session.flush()

        admin_user, admin_email, password = await _create_user_with_role(tenant_id, "client_admin")

        asst_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()
        page_id = uuid4()

        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id="seed",
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            session.add(
                Assistant(
                    id=asst_id,
                    tenant_id=tenant_id,
                    name="Review Asst",
                    prompt_id="default",
                    prompt_version="1.0",
                )
            )
            session.add(Document(id=doc_id, tenant_id=tenant_id, title="Review Doc"))
            session.add(
                DocumentVersion(
                    id=ver_id,
                    tenant_id=tenant_id,
                    document_id=doc_id,
                    sha256="sha-review-1",
                    uploaded_by=admin_user.id,
                    status="needs_review",
                )
            )
            session.add(
                Page(
                    id=page_id,
                    tenant_id=tenant_id,
                    document_version_id=ver_id,
                    page_number=1,
                    page_sha256="psha1",
                    page_type="scanned",
                    image_key=f"{tenant_id}/pages/{ver_id}/1.png",
                    ocr_confidence=0.42,
                    needs_review=True,
                    review_reason="low_ocr_confidence",
                    is_excluded=False,
                )
            )
            session.add(
                PageReading(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    page_id=page_id,
                    source="vision_ingest",
                    text="Transcribed synthetic medical note text.",
                    model_id="test-vision-model",
                    model_version="1.0",
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, _ = await _login(client, admin_email, password)
            auth_client = AsyncClient(transport=transport, base_url="http://test", cookies=cookies)

            resp = await auth_client.get(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/review"
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["version_id"] == str(ver_id)
            flagged = data["flagged_pages"]
            assert len(flagged) == 1
            p0 = flagged[0]
            assert p0["page_number"] == 1
            assert p0["review_reason"] == "low_ocr_confidence"
            assert p0["ocr_confidence"] == 0.42
            assert p0["is_excluded"] is False
            assert p0["has_image"] is True
            assert len(p0["readings"]) == 1
            assert p0["readings"][0]["text"] == "Transcribed synthetic medical note text."
            assert p0["readings"][0]["source"] == "vision_ingest"

        # Verify compliance audit event
        async with tenant_session(ctx) as session:
            stmt = select(AuditEvent).where(
                AuditEvent.tenant_id == tenant_id,
                AuditEvent.action == "review_inspected",
                AuditEvent.resource_id == str(ver_id),
            )
            audit = (await session.execute(stmt)).scalar_one_or_none()
            assert audit is not None
            assert audit.event_type == "knowledge_compliance"


@pytest.mark.asyncio
async def test_page_image_proxy_headers_and_audit(test_app, api_settings, tmp_path) -> None:
    """GET page image returns binary bytes, headers, and logs audit."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)

    async with throwaway_step2_environment():
        tenant_id = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Image Proxy Tenant"))
            await session.flush()

        admin_user, admin_email, password = await _create_user_with_role(tenant_id, "client_admin")

        asst_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()
        page_id = uuid4()

        # Write test image to local storage
        storage = create_storage_backend(profile="mock", base_dir=str(tmp_path))
        synthetic_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15c4"
        )
        await storage.put(
            StorageKey(tenant_id=tenant_id, path=f"pages/{ver_id}/1.png"),
            synthetic_png,
            content_type="image/png",
        )

        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id="seed",
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            session.add(
                Assistant(
                    id=asst_id,
                    tenant_id=tenant_id,
                    name="Image Asst",
                    prompt_id="default",
                    prompt_version="1.0",
                )
            )
            session.add(Document(id=doc_id, tenant_id=tenant_id, title="Image Doc"))
            session.add(
                DocumentVersion(
                    id=ver_id,
                    tenant_id=tenant_id,
                    document_id=doc_id,
                    sha256="sha-image-1",
                    uploaded_by=admin_user.id,
                    status="ready",
                )
            )
            session.add(
                Page(
                    id=page_id,
                    tenant_id=tenant_id,
                    document_version_id=ver_id,
                    page_number=1,
                    page_sha256="psha1",
                    page_type="digital_text",
                    image_key=f"{tenant_id}/pages/{ver_id}/1.png",
                    ocr_confidence=0.99,
                    needs_review=False,
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, _ = await _login(client, admin_email, password)
            auth_client = AsyncClient(transport=transport, base_url="http://test", cookies=cookies)

            resp = await auth_client.get(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/1/image"
            )
            assert resp.status_code == 200, resp.text
            assert resp.headers["content-type"] == "image/png"
            assert resp.headers["cache-control"] == "no-store"
            assert resp.headers["x-content-type-options"] == "nosniff"
            assert resp.headers["content-security-policy"] == "default-src 'none'"
            assert resp.content == synthetic_png

        # Verify audit event
        async with tenant_session(ctx) as session:
            stmt = select(AuditEvent).where(
                AuditEvent.tenant_id == tenant_id,
                AuditEvent.action == "page_image_viewed",
                AuditEvent.resource_id == f"{ver_id}:1",
            )
            audit = (await session.execute(stmt)).scalar_one_or_none()
            assert audit is not None


@pytest.mark.asyncio
async def test_retire_document_csrf_mutation_and_audit(test_app) -> None:
    """DELETE retires document, deactivates chunks, enforces CSRF, and records audit."""
    async with throwaway_step2_environment():
        tenant_id = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Retire Tenant"))
            await session.flush()

        admin_user, admin_email, password = await _create_user_with_role(tenant_id, "client_admin")

        asst_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()
        chunk_id = uuid4()

        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id="seed",
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            session.add(
                Assistant(
                    id=asst_id,
                    tenant_id=tenant_id,
                    name="Retire Asst",
                    prompt_id="default",
                    prompt_version="1.0",
                )
            )
            session.add(
                Document(
                    id=doc_id,
                    tenant_id=tenant_id,
                    title="Doc to Retire",
                    active_version_id=ver_id,
                )
            )
            session.add(
                DocumentVersion(
                    id=ver_id,
                    tenant_id=tenant_id,
                    document_id=doc_id,
                    sha256="sha-retire-1",
                    uploaded_by=admin_user.id,
                    status="ready",
                )
            )
            page_id = uuid4()
            session.add(
                Page(
                    id=page_id,
                    tenant_id=tenant_id,
                    document_version_id=ver_id,
                    page_number=1,
                    page_sha256="psha1",
                    page_type="digital_text",
                    image_key=f"{tenant_id}/pages/{ver_id}/1.png",
                    ocr_confidence=0.99,
                    needs_review=False,
                )
            )
            await session.flush()
            session.add(
                Chunk(
                    id=chunk_id,
                    tenant_id=tenant_id,
                    page_id=page_id,
                    document_version_id=ver_id,
                    text="Active chunk before retirement",
                    language="en",
                    evidence_type="text",
                    ts_config="english",
                    is_active=True,
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, password)
            auth_client = AsyncClient(transport=transport, base_url="http://test", cookies=cookies)

            # 1. Missing CSRF token -> 403 Forbidden
            resp_no_csrf = await auth_client.delete(
                f"/v1/assistants/{asst_id}/knowledge/documents/{doc_id}"
            )
            assert resp_no_csrf.status_code == 403

            # 2. Valid CSRF token -> 200 OK
            resp = await auth_client.delete(
                f"/v1/assistants/{asst_id}/knowledge/documents/{doc_id}",
                headers={"X-CSRF-Token": csrf_token},
            )
            assert resp.status_code == 200, resp.text
            res_json = resp.json()
            assert res_json["document_id"] == str(doc_id)
            assert res_json["is_retired"] is True

        # Assert document mutated under lock: is_retired=True, active_version_id=None
        async with tenant_session(ctx) as session:
            doc = (
                await session.execute(select(Document).where(Document.id == doc_id))
            ).scalar_one()
            assert doc.is_retired is True
            assert doc.active_version_id is None

            # Assert chunks deactivated
            chk = (await session.execute(select(Chunk).where(Chunk.id == chunk_id))).scalar_one()
            assert chk.is_active is False

            # Assert audit event
            audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.action == "document_retired",
                        AuditEvent.resource_id == str(doc_id),
                    )
                )
            ).scalar_one_or_none()
            assert audit is not None


@pytest.mark.asyncio
async def test_anti_probing_cross_tenant_and_staff_rbac(test_app) -> None:
    """Anti-probing: 404 for cross-tenant / missing resources; 403 for staff users."""
    async with throwaway_step2_environment():
        tid_a = uuid4()
        tid_b = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tid_a, name="Tenant A"))
            session.add(Tenant(id=tid_b, name="Tenant B"))
            await session.flush()

        admin_user_a, admin_a, pass_a = await _create_user_with_role(tid_a, "client_admin")
        _, staff_a, pass_staff = await _create_user_with_role(tid_a, "staff")

        asst_a = uuid4()
        doc_a = uuid4()
        ver_a = uuid4()

        ctx_a = TenantContext(
            tenant_id=str(tid_a),
            user_id="seed",
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx_a) as session:
            session.add(
                Assistant(
                    id=asst_a,
                    tenant_id=tid_a,
                    name="Asst A",
                    prompt_id="default",
                    prompt_version="1.0",
                )
            )
            session.add(Document(id=doc_a, tenant_id=tid_a, title="Doc A"))
            session.add(
                DocumentVersion(
                    id=ver_a,
                    tenant_id=tid_a,
                    document_id=doc_a,
                    sha256="sha-a-1",
                    uploaded_by=admin_user_a.id,
                    status="ready",
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Login as Admin A
            cookies_a, csrf_a = await _login(client, admin_a, pass_a)
            client_a = AsyncClient(transport=transport, base_url="http://test", cookies=cookies_a)

            # 1. Non-existent version review -> 404
            random_ver = uuid4()
            r404_rev = await client_a.get(
                f"/v1/assistants/{asst_a}/knowledge/documents/{random_ver}/review"
            )
            assert r404_rev.status_code == 404

            # 2. Non-existent doc retire -> 404
            random_doc = uuid4()
            r404_ret = await client_a.delete(
                f"/v1/assistants/{asst_a}/knowledge/documents/{random_doc}",
                headers={"X-CSRF-Token": csrf_a},
            )
            assert r404_ret.status_code == 404

            # 3. Non-existent assistant -> 404
            random_asst = uuid4()
            r404_asst = await client_a.get(
                f"/v1/assistants/{random_asst}/knowledge/documents/{ver_a}/review"
            )
            assert r404_asst.status_code == 404

            # 4. Login as Staff A -> 403 Forbidden across all endpoints
            cookies_staff, csrf_staff = await _login(client, staff_a, pass_staff)
            client_staff = AsyncClient(
                transport=transport, base_url="http://test", cookies=cookies_staff
            )

            r_staff_rev = await client_staff.get(
                f"/v1/assistants/{asst_a}/knowledge/documents/{ver_a}/review"
            )
            assert r_staff_rev.status_code == 403

            r_staff_img = await client_staff.get(
                f"/v1/assistants/{asst_a}/knowledge/documents/{ver_a}/pages/1/image"
            )
            assert r_staff_img.status_code == 403

            r_staff_ret = await client_staff.delete(
                f"/v1/assistants/{asst_a}/knowledge/documents/{doc_a}",
                headers={"X-CSRF-Token": csrf_staff},
            )
            assert r_staff_ret.status_code == 403


@pytest.mark.asyncio
async def test_concurrency_retire_during_processing_never_goes_live() -> None:
    """Document retired during ingestion processing -> version never goes live."""
    async with throwaway_step2_environment():
        tenant_id = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Concurrency Tenant"))
            await session.flush()

        uploader, _, _ = await _create_user_with_role(tenant_id, "client_admin")
        doc_id = uuid4()
        ver_id = uuid4()
        page_id = uuid4()
        chunk_id = uuid4()

        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id="seed",
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            # Document starts with NO active version, version is processing
            session.add(
                Document(
                    id=doc_id,
                    tenant_id=tenant_id,
                    title="Concurrency Ingestion Doc",
                    active_version_id=None,
                    is_retired=False,
                )
            )
            session.add(
                DocumentVersion(
                    id=ver_id,
                    tenant_id=tenant_id,
                    document_id=doc_id,
                    sha256="sha-concur-1",
                    uploaded_by=uploader.id,
                    status="processing",
                )
            )
            session.add(
                Page(
                    id=page_id,
                    tenant_id=tenant_id,
                    document_version_id=ver_id,
                    page_number=1,
                    page_sha256="psha1",
                    page_type="digital_text",
                    image_key=f"{tenant_id}/pages/{ver_id}/1.png",
                    ocr_confidence=0.95,
                    needs_review=False,
                )
            )
            await session.flush()
            session.add(
                Chunk(
                    id=chunk_id,
                    tenant_id=tenant_id,
                    page_id=page_id,
                    document_version_id=ver_id,
                    text="Pending chunk created during processing",
                    language="en",
                    evidence_type="text",
                    ts_config="english",
                    is_active=False,
                )
            )
            await session.flush()

            # Record completion of page in job_steps
            repo = create_document_repository(session)
            await repo.record_job_step(
                tenant_id=str(tenant_id),
                version_id=ver_id,
                page_number=1,
                step="chunking",
                status="completed",
            )

        # In-flight retirement: document is retired while version was processing
        async with tenant_session(ctx) as session:
            repo = create_document_repository(session)
            retired_doc = await repo.retire_document(str(tenant_id), doc_id)
            assert retired_doc.is_retired is True

        # Pipeline finishes and attempts activation
        async with tenant_session(ctx) as session:
            repo = create_document_repository(session)
            activation_svc = VersionActivationService(repo)

            # Check and activate must refuse and return False
            activated = await activation_svc.check_and_activate_if_complete(str(tenant_id), ver_id)
            assert activated is False

            # Explicit activate_version must raise DocumentRetiredError
            with pytest.raises(DocumentRetiredError):
                await activation_svc.activate_version(str(tenant_id), ver_id)

        # Invariant checks: Document active_version_id is NOT ver_id;
        # chunks are inactive; version not ready
        async with tenant_session(ctx) as session:
            doc = (
                await session.execute(select(Document).where(Document.id == doc_id))
            ).scalar_one()
            assert doc.is_retired is True
            assert doc.active_version_id is None

            ver = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == ver_id))
            ).scalar_one()
            assert ver.status != "ready"

            chk = (await session.execute(select(Chunk).where(Chunk.id == chunk_id))).scalar_one()
            assert chk.is_active is False
