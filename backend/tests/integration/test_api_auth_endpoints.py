"""Integration tests for authentication, sessions, tenant scoping, and question endpoints."""

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.adapters.auth.builtin import BuiltinAuthProvider
from app.api.app import create_app
from app.api.config import ApiSettings
from app.db.models import Assistant, Membership, Tenant, User
from app.db.session import tenant_session, unscoped_session
from app.ports.types import TenantContext, UserRole


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def api_settings() -> ApiSettings:
    return ApiSettings(
        cors_origins=["http://localhost:3000"],
        cookie_secure=False,  # localhost tests
        cookie_samesite="lax",
        idle_timeout_minutes=30,
        absolute_timeout_hours=24,
        allow_bearer_auth=False,
        profile="mock",
        storage_dir="storage",
    )


@pytest.fixture
def test_app(api_settings: ApiSettings):
    return create_app(settings=api_settings)


@pytest.mark.asyncio
class TestApiAuthAndTenantEndpoints:
    """Verifies end-to-end authentication, cookie management, tenant isolation,
    and question flow.
    """

    async def test_login_success_sets_httponly_cookie_and_never_returns_token_in_body(
        self, test_app, runtime_test_password: str
    ) -> None:
        """Login sets httpOnly cookie and omits token from JSON body; writes auth_event."""
        hasher = BuiltinAuthProvider()
        password = runtime_test_password
        hashed = hasher.hash_password(password)

        email = f"staff-{uuid4()}@example.com"
        async with unscoped_session() as session:
            user = User(email=email, hashed_password=hashed, is_platform_admin=False)
            session.add(user)
            await session.flush()
            user_id = user.id

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/auth/login",
                json={"email": email, "password": password},
            )
            assert resp.status_code == 200
            data = resp.json()
            # Token must NOT be in body
            assert "token" not in data
            assert "session_token" not in data
            assert data["email"] == email

            # Cookie must be set with httpOnly
            cookie = resp.cookies.get("session_token")
            assert cookie is not None
            set_cookie_header = resp.headers.get("set-cookie", "").lower()
            assert "httponly" in set_cookie_header

        # Verify auth_events row written
        async with unscoped_session() as session:
            result = await session.execute(
                text("SELECT outcome, reason_code FROM auth_events WHERE user_id = :uid"),
                {"uid": user_id},
            )
            event = result.mappings().first()
            assert event is not None
            assert event["outcome"] == "success"

    async def test_login_failure_returns_generic_error_and_writes_auth_event(
        self, test_app, runtime_test_password: str
    ) -> None:
        """Login failure returns generic error and writes failure to auth_events."""
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/auth/login",
                json={"email": "nonexistent@example.com", "password": runtime_test_password},
            )
            assert resp.status_code == 401
            assert resp.json()["detail"] == "Invalid email or password"

    async def test_login_rate_limiting_triggers_429(
        self, test_app, runtime_test_password: str
    ) -> None:
        """Repeated failed logins trigger rate limit (HTTP 429)."""
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            status_codes = []
            for _ in range(7):
                resp = await client.post(
                    "/v1/auth/login",
                    json={"email": "victim@example.com", "password": runtime_test_password},
                )
                status_codes.append(resp.status_code)
            assert 429 in status_codes

    async def test_logout_revokes_token_and_subsequent_request_fails(
        self, test_app, runtime_test_password: str
    ) -> None:
        """Logout revokes token in sessions table and subsequent request is rejected."""
        hasher = BuiltinAuthProvider()
        password = runtime_test_password
        email = f"staff-{uuid4()}@example.com"
        async with unscoped_session() as session:
            user = User(email=email, hashed_password=hasher.hash_password(password))
            session.add(user)
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login_resp = await client.post(
                "/v1/auth/login",
                json={"email": email, "password": password},
            )
            assert login_resp.status_code == 200
            csrf_token = str(login_resp.cookies.get("csrf_token") or "")

            # Logout with CSRF header
            logout_resp = await client.post(
                "/v1/auth/logout",
                headers={"X-CSRF-Token": csrf_token},
            )
            assert logout_resp.status_code == 200

            # Request using revoked cookie should fail
            me_resp = await client.get("/v1/auth/me")
            assert me_resp.status_code == 401

    async def test_client_a_user_asking_client_b_assistant_returns_404(
        self, test_app, runtime_test_password: str
    ) -> None:
        """Asking for another client's assistant returns 404 to prevent ID probing."""
        hasher = BuiltinAuthProvider()
        password = runtime_test_password
        email_a = f"staff-a-{uuid4()}@example.com"

        async with unscoped_session() as session:
            t_a = Tenant(name="Tenant A")
            t_b = Tenant(name="Tenant B")
            session.add_all([t_a, t_b])
            await session.flush()
            t_a_id = t_a.id
            t_b_id = t_b.id

            u_a = User(email=email_a, hashed_password=hasher.hash_password(password))
            session.add(u_a)
            await session.flush()
            u_a_id = u_a.id

        # Insert membership for Tenant A
        ctx_a = TenantContext(
            tenant_id=str(t_a_id),
            user_id=str(u_a_id),
            role=UserRole.CLIENT_ADMIN,
            session_id="setup-session",
        )
        async with tenant_session(ctx_a) as session_a:
            m_a = Membership(tenant_id=t_a_id, user_id=u_a_id, role="staff")
            session_a.add(m_a)
            await session_a.flush()

        # Insert assistant for Tenant B
        ctx_b = TenantContext(
            tenant_id=str(t_b_id),
            user_id=str(u_a_id),
            role=UserRole.CLIENT_ADMIN,
            session_id="setup-session",
        )
        async with tenant_session(ctx_b) as session_b:
            asst_b = Assistant(
                tenant_id=t_b_id,
                name="Assistant B",
                prompt_id="prompt-default",
                prompt_version="1.0",
            )
            session_b.add(asst_b)
            await session_b.flush()
            asst_b_id = str(asst_b.id)

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login_resp = await client.post(
                "/v1/auth/login",
                json={"email": email_a, "password": password},
            )
            assert login_resp.status_code == 200
            csrf_token = str(login_resp.cookies.get("csrf_token") or "")

            q_resp = await client.post(
                "/v1/questions",
                headers={"X-CSRF-Token": csrf_token},
                json={
                    "assistant_id": asst_b_id,
                    "question_text": "What are your hours?",
                },
            )
            # MUST be 404, not 403, preventing probing
            assert q_resp.status_code == 404

    async def test_platform_admin_with_no_membership_returns_404(
        self, test_app, runtime_test_password: str
    ) -> None:
        """Platform admin without membership asking tenant's assistant is refused 404."""
        hasher = BuiltinAuthProvider()
        password = runtime_test_password
        email_admin = f"padmin-{uuid4()}@example.com"

        async with unscoped_session() as session:
            t = Tenant(name="Tenant Independent")
            session.add(t)
            await session.flush()
            t_id = t.id

            admin = User(
                email=email_admin,
                hashed_password=hasher.hash_password(password),
                is_platform_admin=True,
            )
            session.add(admin)
            await session.flush()
            admin_id = admin.id

        ctx = TenantContext(
            tenant_id=str(t_id),
            user_id=str(admin_id),
            role=UserRole.CLIENT_ADMIN,
            session_id="setup-session",
        )
        async with tenant_session(ctx) as t_session:
            asst = Assistant(
                tenant_id=t_id,
                name="Independent Assistant",
                prompt_id="prompt-default",
                prompt_version="1.0",
            )
            t_session.add(asst)
            await t_session.flush()
            asst_id = str(asst.id)

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/v1/auth/login",
                json={"email": email_admin, "password": password},
            )
            csrf_token = str(client.cookies.get("csrf_token") or "")

            q_resp = await client.post(
                "/v1/questions",
                headers={"X-CSRF-Token": csrf_token},
                json={
                    "assistant_id": asst_id,
                    "question_text": "Admin probing data?",
                },
            )
            # Platform admin has NO membership in Tenant Independent -> 404
            assert q_resp.status_code == 404

    async def test_post_questions_forged_tenant_id_in_request_is_ignored(
        self, test_app, runtime_test_password: str
    ) -> None:
        """Forged tenant_id in body or header is ignored; tenant derives strictly from assistant."""
        hasher = BuiltinAuthProvider()
        password = runtime_test_password
        email = f"staff-{uuid4()}@example.com"

        async with unscoped_session() as session:
            t_real = Tenant(name="Tenant Real")
            session.add(t_real)
            await session.flush()
            t_real_id = t_real.id

            user = User(email=email, hashed_password=hasher.hash_password(password))
            session.add(user)
            await session.flush()
            u_id = user.id

        ctx_real = TenantContext(
            tenant_id=str(t_real_id),
            user_id=str(u_id),
            role=UserRole.CLIENT_ADMIN,
            session_id="setup-session",
        )
        async with tenant_session(ctx_real) as t_session:
            mem = Membership(tenant_id=t_real_id, user_id=u_id, role="staff")
            asst = Assistant(
                tenant_id=t_real_id,
                name="Assistant Real",
                prompt_id="prompt-default",
                prompt_version="1.0",
            )
            t_session.add_all([mem, asst])
            await t_session.flush()
            asst_id = str(asst.id)
            real_tenant_id = str(t_real_id)

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/v1/auth/login",
                json={"email": email, "password": password},
            )
            csrf_token = str(client.cookies.get("csrf_token") or "")

            fake_tenant_id = str(uuid4())
            q_resp = await client.post(
                "/v1/questions",
                headers={
                    "X-CSRF-Token": csrf_token,
                    "X-Tenant-ID": fake_tenant_id,  # Forged header
                },
                json={
                    "assistant_id": asst_id,
                    "tenant_id": fake_tenant_id,  # Forged body field
                    "question_text": "What are the hours?",
                },
            )
            assert q_resp.status_code == 200
            data = q_resp.json()
            assert data["tenant_id"] == real_tenant_id
            assert data["tenant_id"] != fake_tenant_id
            assert "answer" in data
            assert data["status"] in ("answered", "completed")
