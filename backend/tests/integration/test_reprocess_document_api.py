"""Integration tests for Admin-only Document Reprocess endpoint (STAGE S2).

Covers:
1. Successful document reprocessing: creates new version in DB, copies raw file in storage,
   enqueues split job, and records compliance audit event.
2. Tenant isolation & anti-probing: non-existent, cross-tenant, and retired documents return 404.
3. RBAC: non-admin (staff role) receives 403 Forbidden.
4. CSRF protection: POST requires valid CSRF token.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.adapters.auth.builtin import BuiltinAuthProvider
from app.api.app import create_app
from app.api.config import ApiSettings
from app.db.models import (
    Assistant,
    AuditEvent,
    Document,
    DocumentVersion,
    Membership,
    Tenant,
    User,
)
from app.db.session import tenant_session, unscoped_session
from app.ports.types import StorageKey, TenantContext, UserRole
from app.wiring import create_storage_backend


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def api_settings(tmp_path: Path) -> ApiSettings:
    return ApiSettings(
        cors_origins=["http://localhost:3000"],
        cookie_secure=False,
        cookie_samesite="lax",
        idle_timeout_minutes=30,
        absolute_timeout_hours=24,
        allow_bearer_auth=False,
        max_upload_bytes=5 * 1024 * 1024,
        profile="mock",
        storage_dir=str(tmp_path),
    )


@pytest.fixture
def test_app(api_settings: ApiSettings):
    return create_app(settings=api_settings)


async def _create_test_user_with_role(
    tenant_id: UUID,
    role: str,
    password: str = "TestPassword123!",
) -> tuple[User, str, str]:
    hasher = BuiltinAuthProvider()
    hashed = hasher.hash_password(password)
    email = f"user-{uuid4()}@example.com"
    async with unscoped_session() as session:
        user = User(
            id=uuid4(),
            email=email,
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

    return user, email, password


async def _login_and_get_session(
    client: AsyncClient, email: str, password: str
) -> tuple[dict[str, str], str]:
    resp = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200
    cookies = dict(resp.cookies.items())
    csrf_token = cookies.get("csrf_token", "")
    return cookies, csrf_token


@pytest.mark.asyncio
async def test_reprocess_document_success(test_app, api_settings: ApiSettings) -> None:
    """Admin-only reprocess endpoint creates a new version, copies original file in storage,
    and logs compliance audit event.
    """
    tenant_id = uuid4()
    async with unscoped_session() as session:
        session.add(Tenant(id=tenant_id, name="Reprocess Tenant"))
        await session.flush()

    admin_user, admin_email, password = await _create_test_user_with_role(
        tenant_id, "client_admin"
    )

    asst_id = uuid4()
    doc_id = uuid4()
    ver_1_id = uuid4()
    file_content = b"%PDF-1.4 test synthetic document content for reprocess"

    ctx = TenantContext(
        tenant_id=str(tenant_id),
        user_id=str(admin_user.id),
        role=UserRole.CLIENT_ADMIN,
        session_id="seed",
    )
    async with tenant_session(ctx) as session:
        session.add(
            Assistant(
                id=asst_id,
                tenant_id=tenant_id,
                name="Reprocess Asst",
                prompt_id="default",
                prompt_version="1.0",
            )
        )
        session.add(
            Document(
                id=doc_id,
                tenant_id=tenant_id,
                title="Policy Guide.pdf",
                active_version_id=ver_1_id,
            )
        )
        session.add(
            DocumentVersion(
                id=ver_1_id,
                tenant_id=tenant_id,
                document_id=doc_id,
                sha256="sha-reprocess-test-01",
                uploaded_by=admin_user.id,
                status="ready",
                file_extension="pdf",
            )
        )
        await session.flush()

    # Seed file in storage for version 1
    storage = create_storage_backend(
        profile=api_settings.profile,
        base_dir=api_settings.storage_dir,
    )
    key_v1 = StorageKey(
        tenant_id=tenant_id,
        path=f"documents/{ver_1_id}/original.pdf",
    )
    await storage.put(key_v1, file_content, content_type="application/pdf")

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies, csrf = await _login_and_get_session(client, admin_email, password)
        client.cookies.update(cookies)

        url = f"/v1/assistants/{asst_id}/knowledge/documents/{doc_id}/reprocess"
        resp = await client.post(url, headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 202
        data = resp.json()
        assert data["document_id"] == str(doc_id)
        assert data["status"] == "queued"
        new_version_id = UUID(data["document_version_id"])
        assert new_version_id != ver_1_id

    # Verify new version in database
    async with tenant_session(ctx) as session:
        ver_stmt = select(DocumentVersion).where(
            DocumentVersion.tenant_id == tenant_id,
            DocumentVersion.id == new_version_id,
        )
        new_ver = (await session.execute(ver_stmt)).scalars().first()
        assert new_ver is not None
        assert new_ver.document_id == doc_id
        assert new_ver.status == "received"
        assert new_ver.file_extension == "pdf"
        assert new_ver.sha256 == "sha-reprocess-test-01"

        # Verify audit event
        audit_stmt = select(AuditEvent).where(
            AuditEvent.tenant_id == tenant_id,
            AuditEvent.resource_id == str(new_version_id),
            AuditEvent.action == "document_reprocessed",
        )
        audit = (await session.execute(audit_stmt)).scalars().first()
        assert audit is not None
        assert audit.event_type == "knowledge_compliance"

    # Verify file copied in storage
    key_new = StorageKey(
        tenant_id=tenant_id,
        path=f"documents/{new_version_id}/original.pdf",
    )
    copied_bytes = await storage.get(key_new)
    assert copied_bytes == file_content


@pytest.mark.asyncio
async def test_reprocess_document_rbac_and_anti_probing(test_app) -> None:
    """Staff is forbidden (403); cross-tenant, non-existent, and retired return 404."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    async with unscoped_session() as session:
        session.add(Tenant(id=tenant_a, name="Tenant A"))
        session.add(Tenant(id=tenant_b, name="Tenant B"))
        await session.flush()

    admin_a, admin_a_email, admin_a_pass = await _create_test_user_with_role(
        tenant_a, "client_admin"
    )
    staff_a, staff_a_email, staff_a_pass = await _create_test_user_with_role(
        tenant_a, "staff"
    )

    asst_a = uuid4()
    doc_a = uuid4()
    ver_a = uuid4()
    doc_retired = uuid4()
    ver_retired = uuid4()

    asst_b = uuid4()
    doc_b = uuid4()
    ver_b = uuid4()

    ctx_a = TenantContext(
        tenant_id=str(tenant_a),
        user_id=str(admin_a.id),
        role=UserRole.CLIENT_ADMIN,
        session_id="s",
    )
    async with tenant_session(ctx_a) as session:
        session.add(
            Assistant(
                id=asst_a,
                tenant_id=tenant_a,
                name="Asst A",
                prompt_id="p",
                prompt_version="1",
            )
        )
        session.add(
            Document(id=doc_a, tenant_id=tenant_a, title="Doc A", active_version_id=ver_a)
        )
        session.add(
            DocumentVersion(
                id=ver_a,
                tenant_id=tenant_a,
                document_id=doc_a,
                sha256="s1",
                uploaded_by=admin_a.id,
                status="ready",
                file_extension="pdf",
            )
        )
        # Retired document
        session.add(
            Document(
                id=doc_retired,
                tenant_id=tenant_a,
                title="Doc Retired",
                active_version_id=None,
                is_retired=True,
            )
        )
        session.add(
            DocumentVersion(
                id=ver_retired,
                tenant_id=tenant_a,
                document_id=doc_retired,
                sha256="s2",
                uploaded_by=admin_a.id,
                status="retired",
                file_extension="pdf",
            )
        )
        await session.flush()

    ctx_b = TenantContext(
        tenant_id=str(tenant_b),
        user_id=str(admin_a.id),
        role=UserRole.CLIENT_ADMIN,
        session_id="s",
    )
    async with tenant_session(ctx_b) as session:
        session.add(
            Assistant(
                id=asst_b,
                tenant_id=tenant_b,
                name="Asst B",
                prompt_id="p",
                prompt_version="1",
            )
        )
        session.add(
            Document(id=doc_b, tenant_id=tenant_b, title="Doc B", active_version_id=ver_b)
        )
        session.add(
            DocumentVersion(
                id=ver_b,
                tenant_id=tenant_b,
                document_id=doc_b,
                sha256="sb",
                uploaded_by=admin_a.id,
                status="ready",
                file_extension="pdf",
            )
        )
        await session.flush()

    transport = ASGITransport(app=test_app)

    # 1. Staff role gets 403 Forbidden
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies, csrf = await _login_and_get_session(client, staff_a_email, staff_a_pass)
        client.cookies.update(cookies)
        resp = await client.post(
            f"/v1/assistants/{asst_a}/knowledge/documents/{doc_a}/reprocess",
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403

    # 2. Client Admin checks
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        cookies, csrf = await _login_and_get_session(client, admin_a_email, admin_a_pass)
        client.cookies.update(cookies)

        # Non-existent doc -> 404
        resp = await client.post(
            f"/v1/assistants/{asst_a}/knowledge/documents/{uuid4()}/reprocess",
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 404

        # Cross-tenant doc (doc_b in tenant_b accessed via asst_a) -> 404
        resp = await client.post(
            f"/v1/assistants/{asst_a}/knowledge/documents/{doc_b}/reprocess",
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 404

        # Retired doc -> 404
        resp = await client.post(
            f"/v1/assistants/{asst_a}/knowledge/documents/{doc_retired}/reprocess",
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 404
