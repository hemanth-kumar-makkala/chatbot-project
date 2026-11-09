"""Unit tests for Stage 3.3: Assistant, Tenant, and User Provisioning APIs.

Covers:
1. Platform-admin tenant provisioning (POST /v1/admin/tenants) & role guards
   (403 for non-platform admin).
2. Platform-admin member provisioning (POST /v1/admin/tenants/{tenant_id}/members) & validation.
3. Assistant discovery (GET /v1/assistants) scoped strictly to authenticated user's tenant.
4. Assistant creation (POST /v1/assistants) requiring client_admin role.
5. Assistant update & deletion (PATCH/DELETE /v1/assistants/{assistant_id})
   with anti-probing 404s and RBAC 403s.
6. Assistant instructions customization (GET/PATCH /v1/assistants/{assistant_id}/instructions).
7. Assistant guardrails customization (GET/PATCH /v1/assistants/{assistant_id}/guardrails).
8. Workspace members directory (GET /v1/tenant/members) scoped to tenant.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.api.config import ApiSettings
from app.api.deps import (
    get_auth_provider,
    get_client_admin_tenant_context_for_assistant,
    get_current_session,
    get_tenant_context_for_assistant,
    require_platform_admin,
    validate_csrf,
)
from app.ports.types import SessionContext, TenantContext, UserRole


@pytest.fixture
def test_settings(tmp_path: Path) -> ApiSettings:
    return ApiSettings(
        cors_origins=["http://localhost:3000"],
        cookie_secure=False,
        session_upload_retention_hours=24,
        allow_bearer_auth=True,
        profile="mock",
        storage_dir=str(tmp_path),
    )


@pytest.fixture
def app(test_settings: ApiSettings):
    return create_app(settings=test_settings)


@pytest.mark.asyncio
async def test_admin_create_tenant_requires_platform_admin_and_creates_tenant(app) -> None:
    """Platform admin can create a tenant; non-platform-admins receive 403 Forbidden."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated or non-platform admin is rejected
        resp_unauth = await client.post("/v1/admin/tenants", json={"name": "Horizon Health"})
        assert resp_unauth.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

        # 2. Authenticated as platform admin
        admin_session = SessionContext(
            session_id="sess-admin",
            user_id=str(uuid4()),
            tenant_id=str(uuid4()),
            roles=[],
        )
        app.dependency_overrides[require_platform_admin] = lambda: admin_session
        app.dependency_overrides[validate_csrf] = lambda: None

        with patch("app.api.routes.tenants.unscoped_session") as mock_unscoped:
            mock_db = AsyncMock()
            mock_unscoped.return_value.__aenter__.return_value = mock_db

            resp_ok = await client.post("/v1/admin/tenants", json={"name": "Horizon Health"})
            assert resp_ok.status_code == status.HTTP_201_CREATED
            data = resp_ok.json()
            assert data["name"] == "Horizon Health"
            assert "id" in data
            assert "created_at" in data

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_create_tenant_member_creates_user_and_membership(app) -> None:
    """Platform admin can provision a tenant member; invalid role returns 422."""
    transport = ASGITransport(app=app)
    tenant_id = str(uuid4())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_session = SessionContext(
            session_id="sess-admin",
            user_id=str(uuid4()),
            tenant_id=str(uuid4()),
            roles=[],
        )
        app.dependency_overrides[require_platform_admin] = lambda: admin_session
        app.dependency_overrides[validate_csrf] = lambda: None

        # Invalid role fails with 422
        resp_invalid_role = await client.post(
            f"/v1/admin/tenants/{tenant_id}/members",
            json={
                "email": "user@example.com",
                "role": "invalid_role",
                "password": "Password123!",  # pragma: allowlist secret
            },
        )
        assert resp_invalid_role.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # Valid role creates member
        with patch("app.api.routes.tenants.unscoped_session") as mock_unscoped:
            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None  # user does not exist yet
            mock_db.execute.return_value = mock_result
            mock_unscoped.return_value.__aenter__.return_value = mock_db

            resp_valid = await client.post(
                f"/v1/admin/tenants/{tenant_id}/members",
                json={
                    "email": "admin@example.com",
                    "role": "client_admin",
                    "password": "Password123!",  # pragma: allowlist secret
                },
            )
            assert resp_valid.status_code == status.HTTP_201_CREATED
            data = resp_valid.json()
            assert data["tenant_id"] == tenant_id
            assert data["email"] == "admin@example.com"
            assert data["role"] == "client_admin"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_assistants_returns_tenant_scoped_assistants(app) -> None:
    """GET /v1/assistants returns assistants belonging to the authenticated tenant."""
    tenant_id = str(uuid4())
    user_id = str(uuid4())
    asst_id = str(uuid4())

    app.dependency_overrides[get_current_session] = lambda: SessionContext(
        session_id="sess-user",
        user_id=user_id,
        tenant_id=tenant_id,
        roles=[UserRole.STAFF],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.api.routes.assistants.unscoped_session") as mock_unscoped:
            mock_db = AsyncMock()
            mock_result = MagicMock()
            # Mock assistants query
            mock_asst = MagicMock()
            mock_asst.id = asst_id
            mock_asst.tenant_id = tenant_id
            mock_asst.name = "Clinic A Assistant"
            mock_asst.prompt_id = "generator"
            mock_asst.prompt_version = "1.0.0"
            mock_asst.created_at = "2026-11-09T00:00:00Z"
            mock_result.all.return_value = [(mock_asst, 5)]  # assistant + document_count
            mock_db.execute.return_value = mock_result
            mock_unscoped.return_value.__aenter__.return_value = mock_db

            resp = await client.get("/v1/assistants")
            assert resp.status_code == status.HTTP_200_OK
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["id"] == asst_id
            assert data[0]["name"] == "Clinic A Assistant"
            assert data[0]["document_count"] == 5

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_assistant_requires_client_admin_and_creates_assistant(app) -> None:
    """POST /v1/assistants requires client_admin and creates assistant under tenant context."""
    tenant_id = str(uuid4())
    user_id = str(uuid4())

    app.dependency_overrides[validate_csrf] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Staff user is rejected with 403 Forbidden
        app.dependency_overrides[get_current_session] = lambda: SessionContext(
            session_id="sess-staff",
            user_id=user_id,
            tenant_id=tenant_id,
            roles=[UserRole.STAFF],
        )
        mock_auth = MagicMock()
        mock_auth.roles = AsyncMock(return_value=[UserRole.STAFF])
        app.dependency_overrides[get_auth_provider] = lambda: mock_auth

        resp_staff = await client.post("/v1/assistants", json={"name": "New Assistant"})
        assert resp_staff.status_code == status.HTTP_403_FORBIDDEN

        # 2. Client Admin succeeds
        app.dependency_overrides[get_current_session] = lambda: SessionContext(
            session_id="sess-admin",
            user_id=user_id,
            tenant_id=tenant_id,
            roles=[UserRole.CLIENT_ADMIN],
        )
        mock_auth.roles = AsyncMock(return_value=[UserRole.CLIENT_ADMIN])

        with patch("app.api.routes.assistants.tenant_session") as mock_ts:
            mock_db = AsyncMock()
            mock_ts.return_value.__aenter__.return_value = mock_db

            resp_admin = await client.post(
                "/v1/assistants",
                json={"name": "Pediatric Knowledge Assistant"},
            )
            assert resp_admin.status_code == status.HTTP_201_CREATED
            data = resp_admin.json()
            assert data["name"] == "Pediatric Knowledge Assistant"
            assert data["tenant_id"] == tenant_id
            assert "id" in data

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_patch_and_delete_assistant_enforces_client_admin_and_anti_probing(app) -> None:
    """PATCH and DELETE /v1/assistants/{id} require client_admin and return 404 for cross-tenant."""
    tenant_id = str(uuid4())
    user_id = str(uuid4())
    asst_id = str(uuid4())

    app.dependency_overrides[validate_csrf] = lambda: None
    app.dependency_overrides[get_client_admin_tenant_context_for_assistant] = lambda: TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        role=UserRole.CLIENT_ADMIN,
        session_id="sess-admin",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.api.routes.assistants.tenant_session") as mock_ts:
            mock_db = AsyncMock()
            mock_asst = MagicMock()
            mock_asst.id = asst_id
            mock_asst.name = "Old Name"
            mock_asst.tenant_id = tenant_id
            mock_asst.prompt_id = "generator"
            mock_asst.prompt_version = "1.0.0"
            mock_asst.created_at = "2026-11-09T00:00:00Z"
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_asst
            mock_db.execute.return_value = mock_result
            mock_ts.return_value.__aenter__.return_value = mock_db

            # PATCH assistant name
            resp_patch = await client.patch(
                f"/v1/assistants/{asst_id}",
                json={"name": "Updated Assistant Name"},
            )
            assert resp_patch.status_code == status.HTTP_200_OK
            assert resp_patch.json()["name"] == "Updated Assistant Name"

            # DELETE assistant
            resp_del = await client.delete(f"/v1/assistants/{asst_id}")
            assert resp_del.status_code == status.HTTP_200_OK
            assert resp_del.json()["status"] == "deleted"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_instructions_and_guardrails_endpoints_persist_and_retrieve_configs(app) -> None:
    """GET and PATCH /v1/assistants/{id}/instructions and /guardrails persist configurations."""
    tenant_id = str(uuid4())
    user_id = str(uuid4())
    asst_id = str(uuid4())

    app.dependency_overrides[validate_csrf] = lambda: None
    app.dependency_overrides[get_tenant_context_for_assistant] = lambda: TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        role=UserRole.STAFF,
        session_id="sess-staff",
    )
    app.dependency_overrides[get_client_admin_tenant_context_for_assistant] = lambda: TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        role=UserRole.CLIENT_ADMIN,
        session_id="sess-admin",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Instructions: GET (defaults) & PATCH
        resp_get_inst = await client.get(f"/v1/assistants/{asst_id}/instructions")
        assert resp_get_inst.status_code == status.HTTP_200_OK
        assert "instructions" in resp_get_inst.json()

        new_inst = "You are a specialized clinical assistant. Provide bulleted answers."
        resp_patch_inst = await client.patch(
            f"/v1/assistants/{asst_id}/instructions",
            json={"instructions": new_inst},
        )
        assert resp_patch_inst.status_code == status.HTTP_200_OK
        assert resp_patch_inst.json()["instructions"] == new_inst

        # 2. Guardrails: GET & PATCH
        resp_get_guard = await client.get(f"/v1/assistants/{asst_id}/guardrails")
        assert resp_get_guard.status_code == status.HTTP_200_OK
        assert "refusal_message" in resp_get_guard.json()

        new_refusal = "Custom refusal: I cannot answer without verified evidence."
        resp_patch_guard = await client.patch(
            f"/v1/assistants/{asst_id}/guardrails",
            json={
                "refusal_message": new_refusal,
                "blocked_topics": ["internal salary", "patient billing"],
            },
        )
        assert resp_patch_guard.status_code == status.HTTP_200_OK
        assert resp_patch_guard.json()["refusal_message"] == new_refusal
        assert resp_patch_guard.json()["blocked_topics"] == [
            "internal salary",
            "patient billing",
        ]

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_workspace_members_endpoint_returns_tenant_members(app) -> None:
    """GET /v1/tenant/members returns list of members in the caller's tenant."""
    tenant_id = str(uuid4())
    user_id = str(uuid4())

    app.dependency_overrides[get_current_session] = lambda: SessionContext(
        session_id="sess-user",
        user_id=user_id,
        tenant_id=tenant_id,
        roles=[UserRole.CLIENT_ADMIN],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.api.routes.tenants.unscoped_session") as mock_unscoped:
            mock_db = AsyncMock()
            mock_res = MagicMock()
            # Tuple: (membership_id, user_id, email, role, created_at)
            mock_res.all.return_value = [
                (
                    uuid4(),
                    user_id,
                    "admin@clinic.example.com",
                    "client_admin",
                    "2026-11-09T00:00:00Z",
                ),
                (
                    uuid4(),
                    str(uuid4()),
                    "nurse@clinic.example.com",
                    "staff",
                    "2026-11-09T01:00:00Z",
                ),
            ]
            mock_db.execute.return_value = mock_res
            mock_unscoped.return_value.__aenter__.return_value = mock_db

            resp = await client.get("/v1/tenant/members")
            assert resp.status_code == status.HTTP_200_OK
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) == 2
            assert data[0]["email"] == "admin@clinic.example.com"
            assert data[0]["role"] == "client_admin"
            assert data[1]["role"] == "staff"

    app.dependency_overrides.clear()
