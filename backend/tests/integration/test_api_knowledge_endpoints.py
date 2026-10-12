"""Integration tests for Knowledge page backend API (Task 2.8a Step 1).

Verifies:
1. Upload endpoint streams in memory (zero Starlette spooled temp files).
2. Upload size cap enforced during streaming (HTTP 413 CONTENT_TOO_LARGE).
3. CSRF token enforcement on POST.
4. Assistant-scoped routing (/v1/assistants/{assistant_id}/knowledge/...).
5. Multi-tenant permissions: user who is client_admin for tenant A and staff for tenant B
   can upload to A's assistant and gets 403 on B's assistant.
6. Unknown or cross-tenant assistant returns 404 (anti-probing).
7. Cross-tenant document_id on replace returns 404 (anti-probing).
8. Upload response carries IDs and status only (zero filenames or content).
9. Status list maps database fields to allowlist UI states:
   Queued, Reading, Scanning images, Ready, Ready (N pages need review), and Failed.
10. Status list raises error on unknown status (never falls back to Queued).
11. Status list polling produces zero audit_events records.
12. Zero cookies-in-request httpx deprecation warnings (cookies configured on client instance).
"""

from __future__ import annotations

import tempfile
from typing import Any
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
    Membership,
    Tenant,
    User,
)
from app.db.session import tenant_session, unscoped_session
from app.ports.types import TenantContext, UserRole
from tests.fixtures.database_seeding import (
    seed_document,
    seed_page,
    seed_version,
)


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
        max_upload_bytes=5 * 1024 * 1024,  # 5 MB for tests
    )


@pytest.fixture
def test_app(api_settings: ApiSettings):
    return create_app(settings=api_settings)


async def _create_test_user_with_membership(
    tenant_id: UUID,
    role: str,
    password: str = "CorrectTestPassword123!",
    is_platform_admin: bool = False,
    existing_user: User | None = None,
) -> tuple[User, str, str]:
    """Helper to seed user and membership."""
    hasher = BuiltinAuthProvider()
    hashed = hasher.hash_password(password)

    if existing_user is None:
        email = f"user-{uuid4()}@example.com"
        async with unscoped_session() as session:
            user = User(
                id=uuid4(),
                email=email,
                hashed_password=hashed,
                is_platform_admin=is_platform_admin,
            )
            session.add(user)
            await session.flush()
            user_id = user.id
    else:
        user = existing_user
        email = user.email
        user_id = user.id

    if role in ("client_admin", "staff"):
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


async def _seed_assistant(tenant_id: UUID, name: str = "Test Assistant") -> UUID:
    """Helper to seed an assistant row in a tenant."""
    ctx = TenantContext(
        tenant_id=str(tenant_id),
        user_id="seed-user",
        role=UserRole.CLIENT_ADMIN,
        session_id="seed-session",
    )
    async with tenant_session(ctx) as session:
        asst = Assistant(
            id=uuid4(),
            tenant_id=tenant_id,
            name=name,
            prompt_id="prompt-default",
            prompt_version="1.0",
        )
        session.add(asst)
        await session.flush()
        return asst.id


async def _login_and_get_cookies(
    client: AsyncClient,
    email: str,
    password: str,
) -> tuple[dict[str, str], str]:
    """Log in and retrieve session cookies plus CSRF token."""
    resp = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    session_token = str(resp.cookies.get("session_token") or "")
    csrf_token = str(resp.cookies.get("csrf_token") or "")
    cookies: dict[str, str] = {
        "session_token": session_token,
        "csrf_token": csrf_token,
    }
    return cookies, csrf_token


@pytest.mark.asyncio
class TestKnowledgeUploadAndStatusList:
    """Step 1 tests for assistant-scoped Knowledge page upload and status list APIs."""

    async def test_knowledge_upload_streaming_memory_no_tempfile(
        self, test_app, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Upload streams in memory up to cap and NEVER creates Starlette spooled temp files."""

        # Monkeypatch SpooledTemporaryFile to ensure it is never invoked
        def _forbidden_spooled_file(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("SpooledTemporaryFile was instantiated! Must stay in memory.")

        monkeypatch.setattr(tempfile, "SpooledTemporaryFile", _forbidden_spooled_file)

        # Seed tenant, assistant, and client_admin
        tenant_id = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Upload Test Tenant"))
            await session.flush()

        asst_id = await _seed_assistant(tenant_id, "Upload Assistant")
        user, email, password = await _create_test_user_with_membership(
            tenant_id=tenant_id, role="client_admin"
        )

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login_and_get_cookies(client, email, password)
            client.cookies.update(cookies)

            # Valid minimal PDF bytes
            pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<\n>>\nendobj\ntrailer\n<<\n>>\n%%EOF\n"

            resp = await client.post(
                f"/v1/assistants/{asst_id}/knowledge/upload",
                headers={
                    "X-CSRF-Token": csrf_token,
                    "X-Filename": "test_document.pdf",
                    "Content-Type": "application/pdf",
                },
                content=pdf_bytes,
            )

            assert resp.status_code == 202
            data = resp.json()

            # Invariant: Response holds IDs and status only, never filenames
            assert "document_id" in data
            assert "document_version_id" in data
            assert data["status"] == "queued"
            assert data["is_duplicate"] is False
            assert "filename" not in data
            assert "test_document.pdf" not in str(data)

            # Verify audit event written with resource_id = version_id
            ctx = TenantContext(
                tenant_id=str(tenant_id),
                user_id=str(user.id),
                role=UserRole.CLIENT_ADMIN,
                session_id="verify-audit",
            )
            async with tenant_session(ctx) as session:
                stmt = select(AuditEvent).where(
                    AuditEvent.tenant_id == tenant_id,
                    AuditEvent.action == "document_uploaded",
                )
                audit = (await session.execute(stmt)).scalars().first()
                assert audit is not None
                assert audit.resource_id == data["document_version_id"]
                assert "test_document.pdf" not in f"{audit.action} {audit.resource_id}"

    async def test_knowledge_upload_streaming_size_limit_413(self, test_app) -> None:
        """Upload stream exceeding max_upload_bytes raises 413 CONTENT_TOO_LARGE."""
        tenant_id = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Size Limit Tenant"))
            await session.flush()

        asst_id = await _seed_assistant(tenant_id, "Size Limit Assistant")
        user, email, password = await _create_test_user_with_membership(tenant_id, "client_admin")

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login_and_get_cookies(client, email, password)
            client.cookies.update(cookies)

            # 6 MB payload (cap is 5 MB)
            oversized_bytes = b"0" * (6 * 1024 * 1024)

            resp = await client.post(
                f"/v1/assistants/{asst_id}/knowledge/upload",
                headers={
                    "X-CSRF-Token": csrf_token,
                    "X-Filename": "oversized.pdf",
                    "Content-Type": "application/pdf",
                },
                content=oversized_bytes,
            )
            assert resp.status_code == 413

    async def test_knowledge_upload_csrf_required(self, test_app) -> None:
        """Upload requires valid X-CSRF-Token matching csrf_token cookie; rejects without it."""
        tenant_id = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="CSRF Test Tenant"))
            await session.flush()

        asst_id = await _seed_assistant(tenant_id, "CSRF Assistant")
        user, email, password = await _create_test_user_with_membership(tenant_id, "client_admin")

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, _ = await _login_and_get_cookies(client, email, password)
            client.cookies.update(cookies)

            pdf_bytes = b"%PDF-1.4\nsmall\n%%EOF"

            # 1. Missing CSRF header -> 403
            resp_missing = await client.post(
                f"/v1/assistants/{asst_id}/knowledge/upload",
                headers={"X-Filename": "test.pdf", "Content-Type": "application/pdf"},
                content=pdf_bytes,
            )
            assert resp_missing.status_code == 403

            # 2. Tampered CSRF header -> 403
            resp_tampered = await client.post(
                f"/v1/assistants/{asst_id}/knowledge/upload",
                headers={
                    "X-CSRF-Token": "invalid-tampered-token",
                    "X-Filename": "test.pdf",
                    "Content-Type": "application/pdf",
                },
                content=pdf_bytes,
            )
            assert resp_tampered.status_code == 403

    async def test_knowledge_upload_roles_staff_and_platform_admin_403(self, test_app) -> None:
        """Staff receives 403; platform admin without membership receives 404 (anti-probing)."""
        tenant_id = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Role Guard Tenant"))
            await session.flush()

        asst_id = await _seed_assistant(tenant_id, "Role Guard Assistant")
        _, staff_email, staff_pw = await _create_test_user_with_membership(tenant_id, "staff")
        _, padmin_email, padmin_pw = await _create_test_user_with_membership(
            tenant_id, "none", is_platform_admin=True
        )

        transport = ASGITransport(app=test_app)
        pdf_bytes = b"%PDF-1.4\ncontent\n%%EOF"

        # Staff user has tenant membership but role=staff -> 403 Forbidden
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            staff_cookies, staff_csrf = await _login_and_get_cookies(client, staff_email, staff_pw)
            client.cookies.update(staff_cookies)
            staff_resp = await client.post(
                f"/v1/assistants/{asst_id}/knowledge/upload",
                headers={
                    "X-CSRF-Token": staff_csrf,
                    "X-Filename": "staff.pdf",
                    "Content-Type": "application/pdf",
                },
                content=pdf_bytes,
            )
            assert staff_resp.status_code == 403

        # Platform admin without membership in this tenant -> 404 Not Found (anti-probing)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            padmin_cookies, padmin_csrf = await _login_and_get_cookies(
                client, padmin_email, padmin_pw
            )
            client.cookies.update(padmin_cookies)
            padmin_resp = await client.post(
                f"/v1/assistants/{asst_id}/knowledge/upload",
                headers={
                    "X-CSRF-Token": padmin_csrf,
                    "X-Filename": "padmin.pdf",
                    "Content-Type": "application/pdf",
                },
                content=pdf_bytes,
            )
            assert padmin_resp.status_code == 404

    async def test_knowledge_upload_cross_tenant_document_id_returns_404(self, test_app) -> None:
        """Replace upload with cross-tenant document_id returns 404 (anti-probing)."""
        tenant_a = uuid4()
        tenant_b = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_a, name="Tenant A"))
            session.add(Tenant(id=tenant_b, name="Tenant B"))
            await session.flush()

        asst_a = await _seed_assistant(tenant_a, "Assistant A")
        user_a, email_a, pw_a = await _create_test_user_with_membership(tenant_a, "client_admin")
        user_b, _, _ = await _create_test_user_with_membership(tenant_b, "client_admin")

        # Seed document in Tenant B
        ctx_b = TenantContext(
            tenant_id=str(tenant_b),
            user_id=str(user_b.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="setup-session",
        )
        async with tenant_session(ctx_b) as session:
            doc_b_id = await seed_document(session, tenant_id=tenant_b, title="Tenant B Document")

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies_a, csrf_a = await _login_and_get_cookies(client, email_a, pw_a)
            client.cookies.update(cookies_a)

            pdf_bytes = b"%PDF-1.4\nreplacement content\n%%EOF"
            resp = await client.post(
                f"/v1/assistants/{asst_a}/knowledge/upload?document_id={doc_b_id}",
                headers={
                    "X-CSRF-Token": csrf_a,
                    "X-Filename": "replace.pdf",
                    "Content-Type": "application/pdf",
                },
                content=pdf_bytes,
            )
            # MUST be 404, not 403, preventing probing existence of Tenant B document
            assert resp.status_code == 404

    async def test_knowledge_multi_tenant_admin_and_staff_roles(self, test_app) -> None:
        """User who is client_admin for A and staff for B can upload to A and gets 403 on B."""
        tenant_a = uuid4()
        tenant_b = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_a, name="Dual Tenant A"))
            session.add(Tenant(id=tenant_b, name="Dual Tenant B"))
            await session.flush()

        asst_a = await _seed_assistant(tenant_a, "Assistant Dual A")
        asst_b = await _seed_assistant(tenant_b, "Assistant Dual B")

        # Create one user with memberships in BOTH tenants:
        # Tenant A: client_admin
        user, email, password = await _create_test_user_with_membership(tenant_a, "client_admin")
        # Tenant B: staff (same user)
        await _create_test_user_with_membership(
            tenant_b, "staff", password=password, existing_user=user
        )

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf = await _login_and_get_cookies(client, email, password)
            client.cookies.update(cookies)

            pdf_bytes = b"%PDF-1.4\ncontent\n%%EOF"

            # 1. Upload to Tenant A assistant succeeds (user is client_admin)
            resp_a = await client.post(
                f"/v1/assistants/{asst_a}/knowledge/upload",
                headers={
                    "X-CSRF-Token": csrf,
                    "X-Filename": "doc_a.pdf",
                    "Content-Type": "application/pdf",
                },
                content=pdf_bytes,
            )
            assert resp_a.status_code == 202

            # 2. Upload to Tenant B assistant fails with 403 (user is only staff in Tenant B)
            resp_b = await client.post(
                f"/v1/assistants/{asst_b}/knowledge/upload",
                headers={
                    "X-CSRF-Token": csrf,
                    "X-Filename": "doc_b.pdf",
                    "Content-Type": "application/pdf",
                },
                content=pdf_bytes,
            )
            assert resp_b.status_code == 403

            # 3. Upload to random/non-existent assistant returns 404 (anti-probing)
            unknown_asst = uuid4()
            resp_unknown = await client.post(
                f"/v1/assistants/{unknown_asst}/knowledge/upload",
                headers={
                    "X-CSRF-Token": csrf,
                    "X-Filename": "doc_unknown.pdf",
                    "Content-Type": "application/pdf",
                },
                content=pdf_bytes,
            )
            assert resp_unknown.status_code == 404

    async def test_knowledge_documents_status_list_derived_states_all_five(self, test_app) -> None:
        """Status list accurately derives all 5 UI states from database rows."""
        tenant_id = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Status States Tenant"))
            await session.flush()

        asst_id = await _seed_assistant(tenant_id, "Status Assistant")
        user, email, password = await _create_test_user_with_membership(tenant_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id=str(user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="setup-session",
        )

        # Seed versions with different states
        async with tenant_session(ctx) as session:
            # 1. Queued: status = 'received'
            doc_1 = await seed_document(session, tenant_id=tenant_id, title="Doc 1 Queued")
            await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_1, user_id=user.id, status="received"
            )

            # 2. Reading: status = 'processing', split completed, no vision processing
            doc_2 = await seed_document(session, tenant_id=tenant_id, title="Doc 2 Reading")
            ver_2 = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_2, user_id=user.id, status="processing"
            )
            from app.adapters.db.postgres_document_repository import PostgresDocumentRepository

            repo = PostgresDocumentRepository(session)
            await repo.record_job_step(
                tenant_id=str(tenant_id),
                version_id=ver_2,
                page_number=None,
                step="split",
                status="completed",
            )

            # 3. Scanning images: status = 'processing', vision step in processing
            doc_3 = await seed_document(session, tenant_id=tenant_id, title="Doc 3 Scanning")
            ver_3 = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_3, user_id=user.id, status="processing"
            )
            await repo.record_job_step(
                tenant_id=str(tenant_id),
                version_id=ver_3,
                page_number=1,
                step="vision",
                status="processing",
            )

            # 4. Ready: status = 'ready'
            doc_4 = await seed_document(session, tenant_id=tenant_id, title="Doc 4 Ready")
            await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_4, user_id=user.id, status="ready"
            )

            # 5. Ready (2 pages need review): status = 'needs_review', 2 pages flagged
            doc_5 = await seed_document(session, tenant_id=tenant_id, title="Doc 5 Needs Review")
            ver_5 = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_5, user_id=user.id, status="needs_review"
            )
            await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_5,
                page_number=1,
                page_type="scanned",
                needs_review=True,
                review_reason="low_ocr_confidence",
            )
            await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_5,
                page_number=2,
                page_type="scanned",
                needs_review=True,
                review_reason="vision_failed",
            )
            await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_5,
                page_number=3,
                page_type="digital_text",
                needs_review=False,
            )
            await session.commit()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, _ = await _login_and_get_cookies(client, email, password)
            client.cookies.update(cookies)

            resp = await client.get(f"/v1/assistants/{asst_id}/knowledge/documents")
            assert resp.status_code == 200
            data = resp.json()
            assert "documents" in data

            doc_map = {d["document_id"]: d for d in data["documents"]}

            # Assert UI state derivations
            assert doc_map[str(doc_1)]["versions"][0]["ui_status"] == "Queued"
            assert doc_map[str(doc_2)]["versions"][0]["ui_status"] == "Reading"
            assert doc_map[str(doc_3)]["versions"][0]["ui_status"] == "Scanning images"
            assert doc_map[str(doc_4)]["versions"][0]["ui_status"] == "Ready"
            assert doc_map[str(doc_5)]["versions"][0]["ui_status"] == "Ready (2 pages need review)"
            assert doc_map[str(doc_5)]["versions"][0]["flagged_pages_count"] == 2

    async def test_knowledge_documents_status_list_failed_and_unknown_status(
        self, test_app
    ) -> None:
        """Failed maps to 'Failed'; unknown status raises ValueError and never 'Queued'."""
        tenant_id = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Failed Status Tenant"))
            await session.flush()

        asst_id = await _seed_assistant(tenant_id, "Failed Status Assistant")
        user, email, password = await _create_test_user_with_membership(tenant_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id=str(user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="setup-session",
        )

        async with tenant_session(ctx) as session:
            doc_failed = await seed_document(session, tenant_id=tenant_id, title="Doc Failed")
            await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_failed, user_id=user.id, status="failed"
            )

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, _ = await _login_and_get_cookies(client, email, password)
            client.cookies.update(cookies)

            resp = await client.get(f"/v1/assistants/{asst_id}/knowledge/documents")
            assert resp.status_code == 200
            data = resp.json()
            doc_map = {d["document_id"]: d for d in data["documents"]}
            assert doc_map[str(doc_failed)]["versions"][0]["ui_status"] == "Failed"

        # Direct repository test for unknown status raising ValueError
        async with tenant_session(ctx) as session:
            from app.adapters.db.postgres_document_repository import PostgresDocumentRepository

            repo = PostgresDocumentRepository(session)
            # Testing that invalid status is not mapped to 'Queued':
            with pytest.raises(ValueError, match="Unknown document version status"):
                repo._map_version_ui_status(
                    status="unexpected_corrupt_status",
                    flagged_count=0,
                    version_id=uuid4(),
                    completed_split_versions=set(),
                    processing_vision_versions=set(),
                )

    async def test_knowledge_documents_status_list_no_audit_logged(self, test_app) -> None:
        """Polling status list must NOT produce audit_events records."""
        tenant_id = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Audit Poll Tenant"))
            await session.flush()

        asst_id = await _seed_assistant(tenant_id, "Audit Poll Assistant")
        user, email, password = await _create_test_user_with_membership(tenant_id, "client_admin")

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, _ = await _login_and_get_cookies(client, email, password)
            client.cookies.update(cookies)

            ctx = TenantContext(
                tenant_id=str(tenant_id),
                user_id=str(user.id),
                role=UserRole.CLIENT_ADMIN,
                session_id="verify-no-audit",
            )
            # Record count before
            async with tenant_session(ctx) as session:
                before_count = len(
                    (
                        await session.execute(
                            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
                        )
                    )
                    .scalars()
                    .all()
                )

            # Poll status list twice
            resp1 = await client.get(f"/v1/assistants/{asst_id}/knowledge/documents")
            resp2 = await client.get(f"/v1/assistants/{asst_id}/knowledge/documents")
            assert resp1.status_code == 200
            assert resp2.status_code == 200

            # Count after polling must be identical (zero audit events written)
            async with tenant_session(ctx) as session:
                after_count = len(
                    (
                        await session.execute(
                            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert before_count == after_count

    async def test_knowledge_documents_roles_staff_and_platform_admin_403(self, test_app) -> None:
        """Staff receives 403 on status list; platform admin without membership receives 404."""
        tenant_id = uuid4()
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Status Role Guard Tenant"))
            await session.flush()

        asst_id = await _seed_assistant(tenant_id, "Status Role Guard Assistant")
        _, staff_email, staff_pw = await _create_test_user_with_membership(tenant_id, "staff")
        _, padmin_email, padmin_pw = await _create_test_user_with_membership(
            tenant_id, "none", is_platform_admin=True
        )

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            staff_cookies, _ = await _login_and_get_cookies(client, staff_email, staff_pw)
            client.cookies.update(staff_cookies)
            staff_resp = await client.get(f"/v1/assistants/{asst_id}/knowledge/documents")
            assert staff_resp.status_code == 403

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            padmin_cookies, _ = await _login_and_get_cookies(client, padmin_email, padmin_pw)
            client.cookies.update(padmin_cookies)
            padmin_resp = await client.get(f"/v1/assistants/{asst_id}/knowledge/documents")
            assert padmin_resp.status_code == 404
