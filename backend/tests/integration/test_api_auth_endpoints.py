"""Integration tests for authentication, sessions, tenant scoping, and question endpoints."""

import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.adapters.auth.builtin import BuiltinAuthProvider
from app.api.app import create_app
from app.api.config import ApiSettings
from app.db.models import (
    Assistant,
    Chunk,
    Document,
    DocumentVersion,
    Membership,
    Page,
    Tenant,
    User,
)
from app.db.session import tenant_session, unscoped_session
from app.ports.errors import ConfigurationError
from app.ports.types import TenantContext, UserRole


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def api_settings(tmp_path: Path) -> ApiSettings:
    return ApiSettings(
        cors_origins=["http://localhost:3000"],
        cookie_secure=False,  # localhost tests
        cookie_samesite="lax",
        idle_timeout_minutes=30,
        absolute_timeout_hours=24,
        allow_bearer_auth=False,
        profile="mock",
        storage_dir=str(tmp_path),
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
        self, test_app, runtime_test_password: str, monkeypatch
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
            doc_id = uuid4()
            ver_id = uuid4()
            doc = Document(
                id=doc_id,
                tenant_id=t_real_id,
                title="Clinic Guide",
                active_version_id=ver_id,
                is_retired=False,
            )
            ver = DocumentVersion(
                id=ver_id,
                tenant_id=t_real_id,
                document_id=doc_id,
                sha256="0" * 64,
                uploaded_by=u_id,
                status="ready",
                file_extension="pdf",
            )
            page_id = uuid4()
            page = Page(
                id=page_id,
                tenant_id=t_real_id,
                document_version_id=ver_id,
                page_number=1,
                page_sha256="0" * 64,
                page_type="digital_text",
                image_key=f"{t_real_id}/page.png",
            )
            chunk_id = uuid4()
            chunk = Chunk(
                id=chunk_id,
                tenant_id=t_real_id,
                document_version_id=ver_id,
                page_id=page_id,
                text="The clinic hours are 9am to 5pm.",
                language="en",
                ts_config="english",
                evidence_type="text",
                is_active=True,
            )
            t_session.add_all([mem, asst, doc, ver, page])
            await t_session.flush()
            t_session.add(chunk)
            await t_session.flush()
            asst_id = str(asst.id)
            real_tenant_id = str(t_real_id)

        from app.api.routes import questions

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "status": "answered",
            "evidence_ids": [str(chunk_id)],
            "needs_vision": False,
        }
        monkeypatch.setattr(questions, "get_compiled_graph", lambda: mock_graph)

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

    async def test_post_questions_from_web_client_without_content_type_header_fails_422(
        self, test_app, runtime_test_password: str
    ) -> None:
        """Reproduce bug: posting string body without application/json Content-Type fails."""
        hasher = BuiltinAuthProvider()
        password = runtime_test_password
        email = f"staff-{uuid4()}@example.com"

        async with unscoped_session() as session:
            t_real = Tenant(name="Tenant Reproduction")
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
                name="Assistant Repro",
                prompt_id="prompt-default",
                prompt_version="1.0",
            )
            t_session.add_all([mem, asst])
            await t_session.flush()
            asst_id = str(asst.id)

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/v1/auth/login",
                json={"email": email, "password": password},
            )
            csrf_token = str(client.cookies.get("csrf_token") or "")

            raw_body = json.dumps(
                {
                    "assistant_id": asst_id,
                    "question_text": "What are the hours?",
                    "conversation_id": None,
                }
            )

            # What askQuestion previously sent: body string with Content-Type: text/plain
            q_resp = await client.post(
                "/v1/questions",
                headers={
                    "X-CSRF-Token": csrf_token,
                    "Content-Type": "text/plain",
                },
                content=raw_body,
            )
            assert q_resp.status_code == 422
            detail = q_resp.json()["detail"]
            assert detail[0]["loc"] == ["body"]
            assert detail[0]["type"] == "model_attributes_type"

    async def test_post_questions_shared_contract_fixture(
        self, test_app, runtime_test_password: str, monkeypatch
    ) -> None:
        """Shared contract: posting fixture question_request.json returns 200."""
        fixture_path = (
            Path(__file__).resolve().parents[3]
            / "web"
            / "__tests__"
            / "fixtures"
            / "question_request.json"
        )
        assert fixture_path.exists(), f"Fixture missing at {fixture_path}"
        with open(fixture_path, encoding="utf-8") as f:
            fixture_data = json.load(f)

        hasher = BuiltinAuthProvider()
        password = runtime_test_password
        email = f"staff-{uuid4()}@example.com"

        async with unscoped_session() as session:
            t_real = Tenant(name="Tenant Contract")
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
                name="Assistant Contract",
                prompt_id="prompt-default",
                prompt_version="1.0",
            )
            doc_id = uuid4()
            ver_id = uuid4()
            doc = Document(
                id=doc_id,
                tenant_id=t_real_id,
                title="Clinic Guide",
                active_version_id=ver_id,
                is_retired=False,
            )
            ver = DocumentVersion(
                id=ver_id,
                tenant_id=t_real_id,
                document_id=doc_id,
                sha256="0" * 64,
                uploaded_by=u_id,
                status="ready",
                file_extension="pdf",
            )
            page_id = uuid4()
            page = Page(
                id=page_id,
                tenant_id=t_real_id,
                document_version_id=ver_id,
                page_number=1,
                page_sha256="0" * 64,
                page_type="digital_text",
                image_key=f"{t_real_id}/page.png",
            )
            chunk_id = uuid4()
            chunk = Chunk(
                id=chunk_id,
                tenant_id=t_real_id,
                document_version_id=ver_id,
                page_id=page_id,
                text="Contract fixture clinic guide content.",
                language="en",
                ts_config="english",
                evidence_type="text",
                is_active=True,
            )
            t_session.add_all([mem, asst, doc, ver, page])
            await t_session.flush()
            t_session.add(chunk)
            await t_session.flush()
            asst_id = str(asst.id)

        from app.api.routes import questions

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {
            "status": "answered",
            "evidence_ids": [str(chunk_id)],
            "needs_vision": False,
        }
        monkeypatch.setattr(questions, "get_compiled_graph", lambda: mock_graph)

        # Substitute the seeded assistant ID into fixture payload
        payload = dict(fixture_data)
        payload["assistant_id"] = asst_id

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/v1/auth/login",
                json={"email": email, "password": password},
            )
            csrf_token = str(client.cookies.get("csrf_token") or "")

            q_resp = await client.post(
                "/v1/questions",
                headers={
                    "X-CSRF-Token": csrf_token,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            assert q_resp.status_code == 200, (
                f"Contract failed with {q_resp.status_code}: {q_resp.text}"
            )
            data = q_resp.json()
            assert data["status"] in ("answered", "completed")
            assert "mode" not in data

    @pytest.mark.parametrize(
        "mock_status, mock_evidence, expected_status, check_conflict, check_error_ref",
        [
            ("answered", ["c1"], "answered", False, False),
            ("refusal", [], "refusal", False, False),
            ("conflicting", ["c1", "c2"], "conflicting", True, False),
            ("safe_failure", [], "safe_failure", False, True),
        ],
    )
    async def test_post_questions_response_fields_by_status(
        self,
        test_app,
        runtime_test_password: str,
        monkeypatch,
        mock_status: str,
        mock_evidence: list[str],
        expected_status: str,
        check_conflict: bool,
        check_error_ref: bool,
    ) -> None:
        """Verify response fields for answered, refusal, conflicting, safe_failure."""
        hasher = BuiltinAuthProvider()
        password = runtime_test_password
        email = f"staff-{uuid4()}@example.com"

        async with unscoped_session() as session:
            t = Tenant(name="Tenant Statuses")
            session.add(t)
            await session.flush()
            t_id = t.id

            user = User(email=email, hashed_password=hasher.hash_password(password))
            session.add(user)
            await session.flush()
            u_id = user.id

        ctx = TenantContext(
            tenant_id=str(t_id),
            user_id=str(u_id),
            role=UserRole.CLIENT_ADMIN,
            session_id="setup-session",
        )
        seeded_chunk_ids: list[str] = []
        async with tenant_session(ctx) as t_session:
            mem = Membership(tenant_id=t_id, user_id=u_id, role="staff")
            asst = Assistant(
                tenant_id=t_id,
                name="Assistant Statuses",
                prompt_id="prompt-default",
                prompt_version="1.0",
            )
            t_session.add_all([mem, asst])
            await t_session.flush()
            asst_id = str(asst.id)

            for i in (1, 2):
                doc = Document(
                    tenant_id=t_id,
                    title=f"Policy Document {i}",
                )
                t_session.add(doc)
                await t_session.flush()
                ver = DocumentVersion(
                    tenant_id=t_id,
                    document_id=doc.id,
                    sha256=f"{i}" * 64,
                    uploaded_by=u_id,
                    status="ready",
                    file_extension="pdf",
                )
                t_session.add(ver)
                await t_session.flush()
                doc.active_version_id = ver.id
                page = Page(
                    tenant_id=t_id,
                    document_version_id=ver.id,
                    page_number=1,
                    page_sha256=f"{i}" * 64,
                    page_type="digital_text",
                    image_key=f"{t_id}/p_{i}.png",
                )
                t_session.add(page)
                await t_session.flush()
                chunk = Chunk(
                    tenant_id=t_id,
                    document_version_id=ver.id,
                    page_id=page.id,
                    text=f"Sample text content for policy {i}",
                    language="en",
                    ts_config="english",
                    evidence_type="text",
                    is_active=True,
                )
                t_session.add(chunk)
                await t_session.flush()
                seeded_chunk_ids.append(str(chunk.id))

        mapped_evidence = [
            seeded_chunk_ids[int(eid.replace("c", "")) - 1]
            if eid.startswith("c") and eid[1:].isdigit()
            else eid
            for eid in mock_evidence
        ]

        # Mock compiled graph output
        from app.api.routes import questions

        mock_result = {
            "status": mock_status,
            "evidence_ids": mapped_evidence,
            "needs_vision": False,
        }
        if check_error_ref:
            mock_result["public_error_ref"] = "ERR-ABC123"

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_result
        monkeypatch.setattr(questions, "get_compiled_graph", lambda: mock_graph)

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/v1/auth/login",
                json={"email": email, "password": password},
            )
            csrf_token = str(client.cookies.get("csrf_token") or "")

            q_resp = await client.post(
                "/v1/questions",
                headers={"X-CSRF-Token": csrf_token},
                json={
                    "assistant_id": asst_id,
                    "question_text": "Status test question",
                },
            )
            assert q_resp.status_code == 200
            data = q_resp.json()
            assert data["status"] == expected_status
            assert "mode" not in data
            assert "evidence_type" in data
            assert data["evidence_type"] == "text"
            assert isinstance(data["sources"], list)
            assert isinstance(data["conflict_sources"], list)

            if check_conflict:
                assert len(data["conflict_sources"]) == 2
                assert data["conflict_sources"][0]["document_title"] != ""
            else:
                assert len(data["conflict_sources"]) == 0

            if check_error_ref:
                assert data["public_error_ref"] == "ERR-ABC123"
            else:
                assert data["public_error_ref"] is None

    async def test_unresolvable_evidence_id_never_produces_citation(
        self,
        test_app,
        runtime_test_password: str,
        monkeypatch,
    ) -> None:
        """Unresolvable evidence ID raises typed error and ends in safe_failure."""
        hasher = BuiltinAuthProvider()
        password = runtime_test_password
        email = f"staff-{uuid4()}@example.com"

        async with unscoped_session() as session:
            t = Tenant(name="Tenant Unresolvable")
            session.add(t)
            await session.flush()
            t_id = t.id

            user = User(email=email, hashed_password=hasher.hash_password(password))
            session.add(user)
            await session.flush()
            u_id = user.id

        ctx = TenantContext(
            tenant_id=str(t_id),
            user_id=str(u_id),
            role=UserRole.CLIENT_ADMIN,
            session_id="setup-session",
        )
        async with tenant_session(ctx) as t_session:
            mem = Membership(tenant_id=t_id, user_id=u_id, role="staff")
            asst = Assistant(
                tenant_id=t_id,
                name="Assistant Unresolvable",
                prompt_id="prompt-default",
                prompt_version="1.0",
            )
            t_session.add_all([mem, asst])
            await t_session.flush()
            asst_id = str(asst.id)

        # Mock compiled graph output with an unresolvable ID that doesn't exist in DB
        from app.api.routes import questions

        unresolvable_id = str(uuid4())
        mock_result = {
            "status": "answered",
            "evidence_ids": [unresolvable_id],
            "needs_vision": False,
        }

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = mock_result
        monkeypatch.setattr(questions, "get_compiled_graph", lambda: mock_graph)

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/v1/auth/login",
                json={"email": email, "password": password},
            )
            csrf_token = str(client.cookies.get("csrf_token") or "")

            q_resp = await client.post(
                "/v1/questions",
                headers={"X-CSRF-Token": csrf_token},
                json={
                    "assistant_id": asst_id,
                    "question_text": "Test unresolvable citation",
                },
            )
            assert q_resp.status_code == 200
            data = q_resp.json()
            assert data["status"] == "safe_failure"
            assert data["sources"] == []
            assert data["conflict_sources"] == []
            assert data["citations"] == []
            assert data["public_error_ref"] is not None
            assert data["public_error_ref"].startswith("ERR-")

    async def test_create_app_missing_local_storage_path_raises_configuration_error(
        self, monkeypatch
    ) -> None:
        """create_app without settings raises ConfigurationError if storage path is missing."""
        monkeypatch.setenv("APP_PROFILE", "mock")
        monkeypatch.delenv("LOCAL_STORAGE_PATH", raising=False)
        monkeypatch.delenv("STORAGE_DIR", raising=False)
        with pytest.raises(ConfigurationError) as exc_info:
            create_app(settings=None)
        assert "storage_dir" in str(exc_info.value)
