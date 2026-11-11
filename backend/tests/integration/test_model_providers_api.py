"""Integration tests for model provider status API, connection test API, and safety lock fix."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.adapters.auth.builtin import BuiltinAuthProvider
from app.api.app import create_app
from app.api.config import ApiSettings
from app.api.routes import questions as questions_route
from app.core.graph import GraphPorts
from app.core.safety_lock import CloudModelSafetyLockError
from app.db.models import Membership, Tenant, User
from app.db.session import reset_session_engines, tenant_session, unscoped_session
from app.ports.auth import AuthProvider
from app.ports.model_provider import ModelCapabilities
from app.ports.storage import StorageKey
from app.ports.types import (
    AuthCredentials,
    AuthToken,
    SessionContext,
    TenantContext,
    UserRole,
)
from app.wiring import create_storage_backend
from tests.conftest import isolated_throwaway_database


class _StubAuthProvider(AuthProvider):
    """Deterministic in-memory AuthProvider for API RBAC tests without requiring DB."""

    def __init__(
        self,
        sessions: dict[str, SessionContext],
        roles_by_user: dict[str, list[UserRole]],
    ) -> None:
        self._sessions = sessions
        self._roles_by_user = roles_by_user

    async def authenticate(
        self,
        credentials: AuthCredentials,
        client_ip_hash: str | None = None,
    ) -> AuthToken:
        _ = credentials, client_ip_hash
        return AuthToken(token="stub", expires_in=3600)

    async def session(self, token: str) -> SessionContext:
        if token in self._sessions:
            return self._sessions[token]
        raise ValueError("Invalid token")

    async def roles(self, user_id: str, tenant_id: str) -> list[UserRole]:
        _ = tenant_id
        return self._roles_by_user.get(user_id, [])

    async def revoke(self, token: str) -> bool:
        _ = token
        return True


def _build_test_app_with_roles(
    tmp_path: Path,
    *,
    gemini_key: str | None = "secret-gemini-xyz-999",
    gemini_url: str | None = "https://generativelanguage.googleapis.com/v1beta/openai",
):
    settings = ApiSettings(
        profile="mock",
        storage_dir=str(tmp_path),
        session_upload_retention_hours=24,
        cookie_secure=False,
        cors_origins=["http://localhost:3000"],
        gemini_api_key=SecretStr(gemini_key) if gemini_key else None,
        gemini_base_url=gemini_url,
    )
    app = create_app(settings=settings)

    tenant_id = str(uuid4())
    staff_id = str(uuid4())
    cadmin_id = str(uuid4())
    padmin_id = str(uuid4())

    sessions = {
        "tok-staff": SessionContext(
            user_id=staff_id,
            tenant_id=tenant_id,
            roles=[UserRole.STAFF],
            session_id="s-staff",
        ),
        "tok-cadmin": SessionContext(
            user_id=cadmin_id,
            tenant_id=tenant_id,
            roles=[UserRole.CLIENT_ADMIN],
            session_id="s-cadmin",
        ),
        "tok-padmin": SessionContext(
            user_id=padmin_id,
            tenant_id="",
            roles=[UserRole.PLATFORM_ADMIN],
            session_id="s-padmin",
        ),
    }
    roles_map = {
        staff_id: [UserRole.STAFF],
        cadmin_id: [UserRole.CLIENT_ADMIN],
        padmin_id: [UserRole.PLATFORM_ADMIN],
    }
    app.state.auth_provider = _StubAuthProvider(sessions, roles_map)
    return app, settings


@pytest.mark.asyncio
async def test_get_model_providers_rbac_and_zero_secrets_exposed(tmp_path: Path) -> None:
    """GET /v1/models/providers permits admins only, rejects staff/unauth, and hides keys."""
    secret_key = "secret-gemini-xyz-999"  # pragma: allowlist secret
    app, _settings = _build_test_app_with_roles(tmp_path, gemini_key=secret_key)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Unauthenticated -> 401
        resp_unauth = await client.get("/v1/models/providers")
        assert resp_unauth.status_code == 401

        # Staff -> 403
        client.cookies.set("session_token", "tok-staff")
        resp_staff = await client.get("/v1/models/providers")
        assert resp_staff.status_code == 403

        # Client Admin -> 200
        client.cookies.set("session_token", "tok-cadmin")
        resp_cadmin = await client.get("/v1/models/providers")
        assert resp_cadmin.status_code == 200
        assert secret_key not in resp_cadmin.text
        data = resp_cadmin.json()
        by_id = {p["provider"]: p for p in data["providers"]}
        assert by_id["gemini"]["status"] == "connected"
        assert by_id["gemini"]["missing_settings"] == []
        assert by_id["nvidia"]["status"] == "not_connected"
        assert "NVIDIA_API_KEY" in by_id["nvidia"]["missing_settings"]
        assert by_id["anthropic"]["status"] == "coming_later"

        # Platform Admin -> 200
        client.cookies.set("session_token", "tok-padmin")
        resp_padmin = await client.get("/v1/models/providers")
        assert resp_padmin.status_code == 200
        assert secret_key not in resp_padmin.text


@pytest.mark.asyncio
async def test_provider_test_connection_rbac_csrf_and_error_codes(tmp_path: Path) -> None:
    """POST /v1/models/providers/{provider}/test-connection is platform_admin + CSRF only."""
    secret_key = "secret-gemini-xyz-999"  # pragma: allowlist secret
    app, _settings = _build_test_app_with_roles(tmp_path, gemini_key=secret_key)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # CSRF missing -> 403
        client.cookies.set("session_token", "tok-padmin")
        resp_no_csrf = await client.post("/v1/models/providers/gemini/test-connection")
        assert resp_no_csrf.status_code == 403

        csrf_headers = {"X-CSRF-Token": "csrf-ok"}
        client.cookies.set("csrf_token", "csrf-ok")

        # Staff -> 403
        client.cookies.set("session_token", "tok-staff")
        resp_staff = await client.post(
            "/v1/models/providers/gemini/test-connection",
            headers=csrf_headers,
        )
        assert resp_staff.status_code == 403

        # Client admin -> 403 (Prompt Decision 6: platform_admin only)
        client.cookies.set("session_token", "tok-cadmin")
        resp_cadmin = await client.post(
            "/v1/models/providers/gemini/test-connection",
            headers=csrf_headers,
        )
        assert resp_cadmin.status_code == 403

        # Platform admin + valid CSRF + connected upstream -> ok=True
        client.cookies.set("session_token", "tok-padmin")

        def ok_handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/chat/completions")
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}]},
            )

        with patch(
            "app.adapters.models.catalog._create_probe_http_client",
            return_value=httpx.AsyncClient(transport=httpx.MockTransport(ok_handler)),
        ):
            resp_ok = await client.post(
                "/v1/models/providers/gemini/test-connection",
                headers=csrf_headers,
            )
        assert resp_ok.status_code == 200
        assert resp_ok.json()["ok"] is True
        assert resp_ok.json()["error_code"] is None
        assert secret_key not in resp_ok.text

        # Upstream 401 -> ok=False, error_code="PROVIDER_AUTH_FAILED", never reveals key
        def unauth_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "bad key"}})

        with patch(
            "app.adapters.models.catalog._create_probe_http_client",
            return_value=httpx.AsyncClient(transport=httpx.MockTransport(unauth_handler)),
        ):
            resp_401 = await client.post(
                "/v1/models/providers/gemini/test-connection",
                headers=csrf_headers,
            )
        assert resp_401.status_code == 200
        assert resp_401.json()["ok"] is False
        assert resp_401.json()["error_code"] == "PROVIDER_AUTH_FAILED"
        assert secret_key not in resp_401.text

        # Not connected provider -> ok=False, PROVIDER_NOT_CONNECTED
        resp_missing = await client.post(
            "/v1/models/providers/nvidia/test-connection",
            headers=csrf_headers,
        )
        assert resp_missing.status_code == 200
        assert resp_missing.json()["ok"] is False
        assert resp_missing.json()["error_code"] == "PROVIDER_NOT_CONNECTED"

        # Coming later -> ok=False, PROVIDER_COMING_LATER
        resp_later = await client.post(
            "/v1/models/providers/anthropic/test-connection",
            headers=csrf_headers,
        )
        assert resp_later.status_code == 200
        assert resp_later.json()["ok"] is False
        assert resp_later.json()["error_code"] == "PROVIDER_COMING_LATER"

        # Unknown provider -> 404
        resp_unknown = await client.post(
            "/v1/models/providers/nonexistent/test-connection",
            headers=csrf_headers,
        )
        assert resp_unknown.status_code == 404


@asynccontextmanager
async def _throwaway_rls_db():
    """Spin up an isolated throwaway Postgres DB running as app_user (RLS enforced)."""
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
        try:
            yield
        finally:
            if old_db_url is not None:
                os.environ["DATABASE_URL"] = old_db_url
            else:
                os.environ.pop("DATABASE_URL", None)
            reset_session_engines()


@pytest.mark.asyncio
async def test_get_model_providers_builtin_auth_under_postgres_rls(
    tmp_path: Path, runtime_test_password: str
) -> None:
    """BuiltinAuthProvider under Postgres RLS permits client_admin and denies staff."""
    async with _throwaway_rls_db():
        hasher = BuiltinAuthProvider()
        hashed = hasher.hash_password(runtime_test_password)
        tenant_id = uuid4()
        staff_id = uuid4()
        admin_id = uuid4()
        padmin_id = uuid4()

        staff_email = f"staff-{uuid4().hex[:8]}@example.com"
        admin_email = f"admin-{uuid4().hex[:8]}@example.com"
        padmin_email = f"padmin-{uuid4().hex[:8]}@example.com"

        async with unscoped_session() as db:
            db.add(Tenant(id=tenant_id, name="Clinic Model Test"))
            db.add_all(
                [
                    User(
                        id=staff_id,
                        email=staff_email,
                        hashed_password=hashed,
                        is_platform_admin=False,
                    ),
                    User(
                        id=admin_id,
                        email=admin_email,
                        hashed_password=hashed,
                        is_platform_admin=False,
                    ),
                    User(
                        id=padmin_id,
                        email=padmin_email,
                        hashed_password=hashed,
                        is_platform_admin=True,
                    ),
                ]
            )
            await db.flush()

        seed_ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id=str(admin_id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed-rls",
        )
        async with tenant_session(seed_ctx) as tdb:
            tdb.add_all(
                [
                    Membership(tenant_id=tenant_id, user_id=staff_id, role="staff"),
                    Membership(tenant_id=tenant_id, user_id=admin_id, role="client_admin"),
                ]
            )
            await tdb.flush()

        settings = ApiSettings(
            profile="mock",
            storage_dir=str(tmp_path),
            session_upload_retention_hours=24,
            cookie_secure=False,
            cors_origins=["http://localhost:3000"],
        )
        app = create_app(settings=settings)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Staff blocked from GET /v1/models/providers
            r_login_staff = await client.post(
                "/v1/auth/login",
                json={"email": staff_email, "password": runtime_test_password},
            )
            assert r_login_staff.status_code == 200
            r_staff_prov = await client.get("/v1/models/providers")
            assert r_staff_prov.status_code == 403

            # 2. Client Admin allowed GET /v1/models/providers, denied test-connection
            r_login_admin = await client.post(
                "/v1/auth/login",
                json={"email": admin_email, "password": runtime_test_password},
            )
            assert r_login_admin.status_code == 200
            r_admin_prov = await client.get("/v1/models/providers")
            assert r_admin_prov.status_code == 200
            admin_csrf = r_login_admin.cookies.get("csrf_token")
            assert admin_csrf is not None
            r_admin_test = await client.post(
                "/v1/models/providers/nvidia/test-connection",
                headers={"X-CSRF-Token": admin_csrf},
            )
            assert r_admin_test.status_code == 403

            # 3. Platform Admin allowed both GET and test-connection
            r_login_padmin = await client.post(
                "/v1/auth/login",
                json={"email": padmin_email, "password": runtime_test_password},
            )
            assert r_login_padmin.status_code == 200
            padmin_csrf = r_login_padmin.cookies.get("csrf_token")
            assert padmin_csrf is not None
            r_padmin_prov = await client.get("/v1/models/providers")
            assert r_padmin_prov.status_code == 200
            r_padmin_test = await client.post(
                "/v1/models/providers/nvidia/test-connection",
                headers={"X-CSRF-Token": padmin_csrf},
            )
            assert r_padmin_test.status_code == 200


@pytest.mark.asyncio
async def test_questions_route_desktop_api_refuses_non_golden_doc_even_when_chat_model_empty(
    tmp_path: Path,
) -> None:
    """Addition (a): desktop-api + non-golden doc refuses 403 even when chat_model is empty."""
    tenant_uuid = uuid4()
    asst_uuid = uuid4()
    user_uuid = uuid4()
    ver_uuid = uuid4()

    settings = ApiSettings(
        profile="desktop-api",
        storage_dir=str(tmp_path),
        session_upload_retention_hours=24,
        cookie_secure=False,
        cors_origins=["http://localhost:3000"],
        nvidia_base_url="https://api.example/v1",
        nvidia_api_key=SecretStr("nv-dummy-test-key"),
    )
    # Normal desktop-api state: chat_model and vision_model are empty strings
    assert settings.chat_model == ""
    assert settings.vision_model == ""

    # Put real non-golden document in storage at tenant_uuid/documents/ver_uuid/original.xlsx
    storage = create_storage_backend(profile="desktop-api", base_dir=str(tmp_path))
    await storage.put(
        StorageKey(tenant_id=tenant_uuid, path=f"documents/{ver_uuid}/original.xlsx"),
        b"real-non-golden-spreadsheet-bytes",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    app = create_app(settings=settings)
    questions_route._ports_cache.clear()
    app.state.auth_provider = _StubAuthProvider(
        {
            "tok-staff": SessionContext(
                user_id=str(user_uuid),
                tenant_id=str(tenant_uuid),
                roles=[UserRole.STAFF],
                session_id="sess-lock",
            )
        },
        {str(user_uuid): [UserRole.STAFF]},
    )

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    res_count = MagicMock()
    res_count.scalar.return_value = 1
    res_docs = MagicMock()
    res_docs.all.return_value = [("Work_Tracker_FINAL.xlsx", ver_uuid, "xlsx")]
    res_visual = MagicMock()
    res_visual.all.return_value = []

    @asynccontextmanager
    async def _fake_tenant_session(_ctx):
        yield mock_db

    mock_db.execute.side_effect = [res_count, res_docs, res_visual]
    mock_retriever = MagicMock()

    with (
        patch.object(
            questions_route,
            "get_tenant_context_for_assistant",
            new=AsyncMock(
                return_value=TenantContext(
                    tenant_id=str(tenant_uuid),
                    user_id=str(user_uuid),
                    role=UserRole.STAFF,
                    session_id="sess-lock",
                )
            ),
        ),
        patch.object(questions_route, "check_question_rate_limit", new=AsyncMock()),
        patch.object(questions_route, "tenant_session", _fake_tenant_session),
        patch.object(questions_route, "create_retriever", return_value=mock_retriever),
        patch.object(questions_route, "get_compiled_graph") as mock_graph,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("session_token", "tok-staff")
            client.cookies.set("csrf_token", "c1")
            resp = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(asst_uuid),
                    "question_text": "What is in the tracker?",
                    "conversation_id": None,
                },
                headers={"X-CSRF-Token": "c1"},
            )

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "SAFETY LOCK REFUSAL" in detail
    assert "Work_Tracker_FINAL.xlsx" in detail
    mock_graph.assert_not_called()
    questions_route._ports_cache.clear()


@pytest.mark.asyncio
async def test_verify_active_documents_for_cloud_models_enforces_lock_on_cloud_judge_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 4: safety lock checks every active role including judge."""
    tenant_uuid = uuid4()
    ver_uuid = uuid4()

    ports = GraphPorts(
        capabilities_map=MappingProxyType(
            {
                ("ollama", "qwen3-vl:4b"): ModelCapabilities(
                    supports_vision=True, remote=False, baa_covered=False
                ),
                ("gemini", "gemini-3.5-flash-lite"): ModelCapabilities(
                    supports_vision=False, remote=True, baa_covered=False
                ),
            }
        ),
        chat_model="ollama:qwen3-vl:4b",
        vision_model="ollama:qwen3-vl:4b",
    )
    monkeypatch.setattr(questions_route, "get_engine_ports", lambda _s: ports)

    settings = ApiSettings(
        profile="mock",
        storage_dir=str(tmp_path),
        session_upload_retention_hours=24,
    )
    storage = create_storage_backend(profile="mock", base_dir=str(tmp_path))
    await storage.put(
        StorageKey(tenant_id=tenant_uuid, path=f"documents/{ver_uuid}/original.pdf"),
        b"non-golden-pdf-bytes",
        "application/pdf",
    )

    with pytest.raises(CloudModelSafetyLockError) as exc_info:
        await questions_route._verify_active_documents_for_cloud_models(
            settings,
            tenant_uuid,
            [("real_client_file.pdf", ver_uuid, "pdf")],
        )

    assert "real_client_file.pdf" in str(exc_info.value)
    assert "gemini:gemini-3.5-flash-lite" in str(exc_info.value)
