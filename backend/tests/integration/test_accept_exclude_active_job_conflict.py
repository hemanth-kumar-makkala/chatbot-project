"""Integration tests for active page job conflict guards on accept and exclude routes."""

from __future__ import annotations

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
    Document,
    DocumentVersion,
    Job,
    JobStep,
    Membership,
    Page,
    PageReading,
    Tenant,
    User,
)
from app.db.session import tenant_session, unscoped_session
from app.ports.types import TenantContext, UserRole


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
def test_app(api_settings: ApiSettings) -> Any:
    return create_app(settings=api_settings)


async def _create_test_client_admin(
    tenant_id: UUID, password: str = "AdminPass123!Aa"
) -> tuple[User, str, str]:
    hasher = BuiltinAuthProvider()
    hashed = hasher.hash_password(password)
    email = f"admin-{uuid4().hex[:8]}@example.com"
    user_id = uuid4()
    async with unscoped_session() as session:
        user = User(
            id=user_id,
            email=email,
            hashed_password=hashed,
            is_platform_admin=False,
        )
        session.add(user)
        await session.flush()

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
            role="client_admin",
        )
        t_session.add(membership)
        await t_session.flush()

    return user, email, password


async def _setup_test_page_hierarchy(
    with_active_job: bool = False,
) -> dict[str, Any]:
    tenant_id = uuid4()
    asst_id = uuid4()
    doc_id = uuid4()
    ver_id = uuid4()
    page_id = uuid4()
    reading_id = uuid4()

    async with unscoped_session() as session:
        session.add(Tenant(id=tenant_id, name=f"Test Tenant {tenant_id.hex[:6]}"))
        await session.flush()

    admin_user, admin_email, password = await _create_test_client_admin(tenant_id)

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
                name="Test Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
        )
        session.add(Document(id=doc_id, tenant_id=tenant_id, title="Doc"))
        session.add(
            DocumentVersion(
                id=ver_id,
                tenant_id=tenant_id,
                document_id=doc_id,
                sha256=f"sha-{uuid4().hex[:12]}",
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
                page_sha256=f"psha-{uuid4().hex[:8]}",
                page_type="scanned",
                image_key=f"{tenant_id}/pages/{ver_id}/1.png",
                needs_review=True,
                review_reason="low_ocr_confidence",
            )
        )
        session.add(
            PageReading(
                id=reading_id,
                tenant_id=tenant_id,
                page_id=page_id,
                source="ocr",
                text="Sample OCR reading text for test.",
                model_id="test-ocr",
                model_version="1.0",
            )
        )
        if with_active_job:
            job_id = uuid4()
            session.add(
                Job(
                    id=job_id,
                    tenant_id=tenant_id,
                    document_version_id=ver_id,
                    status="processing",
                )
            )
            session.add(
                JobStep(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    job_id=job_id,
                    document_version_id=ver_id,
                    page_number=1,
                    step="step_4",
                    processing_version="v1",
                    status="processing",
                )
            )
        await session.flush()

    return {
        "tenant_id": tenant_id,
        "asst_id": asst_id,
        "doc_id": doc_id,
        "ver_id": ver_id,
        "page_id": page_id,
        "admin_email": admin_email,
        "password": password,
        "ctx": ctx,
    }


async def _authenticated_client(test_app: Any, admin_email: str, password: str) -> AsyncClient:
    transport = ASGITransport(app=test_app)
    client = AsyncClient(transport=transport, base_url="http://test")
    login_resp = await client.post(
        "/v1/auth/login", json={"email": admin_email, "password": password}
    )
    assert login_resp.status_code == 200
    session_token = str(login_resp.cookies.get("session_token") or "")
    csrf_token = str(login_resp.cookies.get("csrf_token") or "")
    return AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"session_token": session_token, "csrf_token": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
    )


@pytest.mark.asyncio
async def test_accept_while_job_active_returns_409_page_unchanged(test_app: Any) -> None:
    """Accept while a job is active for that page: 409, page row unchanged."""
    data = await _setup_test_page_hierarchy(with_active_job=True)
    asst_id = data["asst_id"]
    ver_id = data["ver_id"]
    page_id = data["page_id"]
    tenant_id = data["tenant_id"]
    ctx = data["ctx"]

    async with await _authenticated_client(
        test_app, data["admin_email"], data["password"]
    ) as client:
        resp = await client.post(
            f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/1/accept"
        )
        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["detail"] == "PAGE_JOB_ALREADY_ACTIVE"

    # Verify page row is completely unchanged
    async with tenant_session(ctx) as session:
        page = (
            await session.execute(
                select(Page).where(Page.tenant_id == tenant_id, Page.id == page_id)
            )
        ).scalar_one()
        assert page.needs_review is True
        assert page.review_reason == "low_ocr_confidence"
        assert getattr(page, "is_excluded", False) is False


@pytest.mark.asyncio
async def test_exclude_while_job_active_returns_409_page_unchanged(test_app: Any) -> None:
    """Exclude while a job is active for that page: 409, page row unchanged."""
    data = await _setup_test_page_hierarchy(with_active_job=True)
    asst_id = data["asst_id"]
    ver_id = data["ver_id"]
    page_id = data["page_id"]
    tenant_id = data["tenant_id"]
    ctx = data["ctx"]

    async with await _authenticated_client(
        test_app, data["admin_email"], data["password"]
    ) as client:
        resp = await client.post(
            f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/1/exclude"
        )
        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["detail"] == "PAGE_JOB_ALREADY_ACTIVE"

    # Verify page row is completely unchanged
    async with tenant_session(ctx) as session:
        page = (
            await session.execute(
                select(Page).where(Page.tenant_id == tenant_id, Page.id == page_id)
            )
        ).scalar_one()
        assert page.needs_review is True
        assert page.review_reason == "low_ocr_confidence"
        assert getattr(page, "is_excluded", False) is False


@pytest.mark.asyncio
async def test_positive_controls_accept_and_exclude_succeed_without_active_job(
    test_app: Any,
) -> None:
    """Positive controls: accept and exclude with no active job still succeed."""
    # 1. Accept positive control
    data_accept = await _setup_test_page_hierarchy(with_active_job=False)
    async with await _authenticated_client(
        test_app, data_accept["admin_email"], data_accept["password"]
    ) as client:
        resp = await client.post(
            f"/v1/assistants/{data_accept['asst_id']}/knowledge/documents/{data_accept['ver_id']}/pages/1/accept"
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "accepted"

    async with tenant_session(data_accept["ctx"]) as session:
        page = (
            await session.execute(
                select(Page).where(
                    Page.tenant_id == data_accept["tenant_id"],
                    Page.id == data_accept["page_id"],
                )
            )
        ).scalar_one()
        assert page.needs_review is False

    # 2. Exclude positive control
    data_exclude = await _setup_test_page_hierarchy(with_active_job=False)
    async with await _authenticated_client(
        test_app, data_exclude["admin_email"], data_exclude["password"]
    ) as client:
        resp = await client.post(
            f"/v1/assistants/{data_exclude['asst_id']}/knowledge/documents/{data_exclude['ver_id']}/pages/1/exclude"
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "excluded"

    async with tenant_session(data_exclude["ctx"]) as session:
        page = (
            await session.execute(
                select(Page).where(
                    Page.tenant_id == data_exclude["tenant_id"],
                    Page.id == data_exclude["page_id"],
                )
            )
        ).scalar_one()
        assert page.needs_review is False
        assert getattr(page, "is_excluded", False) is True
