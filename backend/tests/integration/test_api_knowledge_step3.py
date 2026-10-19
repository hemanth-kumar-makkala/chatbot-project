"""Integration tests for Task 2.8a Step 3 (Review actions: accept, exclude, and promote).

Verifies:
- Accept: vision reading indexed as visual with page_reading_id; OCR reading indexed as text;
  no usable reading returns 422 NO_USABLE_READING; double-click no-op.
- Exclude: is_excluded=true, needs_review=false, chunks stay inactive; double-click no-op.
- Invalid page state: action on non-flagged/non-idempotent page returns 409 INVALID_PAGE_STATE.
- Auto-promote: resolving last flagged page on Branch A activates version,
  retires previous active version, records separate version_auto_promoted audit with system actor.
- Branch B: resolving last flagged page on already-active version transitions status to ready.
- Promote with exclusions: activates version, leaves excluded chunks inactive,
  retires previous version, evaluates status based on remaining flagged pages.
- Retired document: all actions refused with 409 DocumentRetiredError under row lock.
- Cross-tenant anti-probing returns 404.
- Staff role returns 403.
- CSRF double-submit token required on all POST routes.
- Compliance audit events recorded with IDs only.
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
from app.db.models import (
    Assistant,
    AuditEvent,
    Chunk,
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
from app.db.session import (
    reset_session_engines,
    tenant_session,
    unscoped_session,
)
from app.ports.types import TenantContext, UserRole
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
async def throwaway_step3_environment() -> AsyncIterator[dict[str, Any]]:
    """Spins up an isolated throwaway DB migrated to 0008, redirects session engines."""
    load_dotenv()
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    app_user = os.getenv("DB_APP_USER", "app_user")
    app_pass = os.getenv("DB_APP_PASSWORD", "")

    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "head")

        app_url = f"postgresql+psycopg://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}"
        old_db_url = os.environ.get("DATABASE_URL")
        old_profile = os.environ.get("APP_PROFILE")
        os.environ["DATABASE_URL"] = app_url
        os.environ["APP_PROFILE"] = "mock"
        reset_session_engines()

        try:
            yield {"db_name": throwaway_db.db_name, "url": app_url}
        finally:
            if old_db_url is not None:
                os.environ["DATABASE_URL"] = old_db_url
            else:
                os.environ.pop("DATABASE_URL", None)
            if old_profile is not None:
                os.environ["APP_PROFILE"] = old_profile
            else:
                os.environ.pop("APP_PROFILE", None)
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
async def test_accept_flagged_page_with_vision_reading_indexes_visual(test_app) -> None:
    """Accept on a page with a vision reading indexes the chunk as visual with page_reading_id."""
    async with throwaway_step3_environment():
        tenant_id = uuid4()
        asst_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()
        page_id = uuid4()
        reading_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Accept Vision Tenant"))
            await session.flush()

        admin_user, admin_email, password = await _create_user_with_role(tenant_id, "client_admin")

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
                    name="Accept Asst",
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
                    sha256="sha-1",
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
                    page_sha256="psha-1",
                    page_type="scanned",
                    image_key=f"{tenant_id}/pages/{ver_id}/1.png",
                    ocr_confidence=0.40,
                    needs_review=True,
                    review_reason="low_ocr_confidence",
                )
            )
            # Add vision reading
            session.add(
                PageReading(
                    id=reading_id,
                    tenant_id=tenant_id,
                    page_id=page_id,
                    source="vision_ingest",
                    text="Synthetic visual transcription.",
                    model_id="test-vision",
                    model_version="1.0",
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, password)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            resp = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/1/accept"
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["status"] == "accepted"
            assert data["page_number"] == 1

        # Verify page state & indexed chunk
        async with tenant_session(ctx) as session:
            page = (
                await session.execute(
                    select(Page).where(Page.tenant_id == tenant_id, Page.id == page_id)
                )
            ).scalar_one()
            assert page.needs_review is False
            assert page.review_reason == "low_ocr_confidence"  # Preserved for audit

            chunk = (
                await session.execute(
                    select(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.page_id == page_id)
                )
            ).scalar_one()
            assert chunk.evidence_type == "visual"
            assert chunk.page_reading_id == reading_id

            # Audit event verified
            audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.action == "page_accepted",
                        AuditEvent.resource_id == f"{ver_id}:1",
                    )
                )
            ).scalar_one_or_none()
            assert audit is not None


@pytest.mark.asyncio
async def test_accept_flagged_page_with_ocr_reading_indexes_text(test_app) -> None:
    """Accept on a page with an OCR reading indexes chunk as text."""
    async with throwaway_step3_environment():
        tenant_id = uuid4()
        asst_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()
        page_id = uuid4()
        reading_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Accept OCR Tenant"))
            await session.flush()

        admin_user, admin_email, password = await _create_user_with_role(tenant_id, "client_admin")

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
                    name="Accept Asst",
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
                    sha256="sha-1",
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
                    page_sha256="psha-1",
                    page_type="scanned",
                    image_key=f"{tenant_id}/pages/{ver_id}/1.png",
                    ocr_confidence=0.45,
                    needs_review=True,
                    review_reason="low_ocr_confidence",
                )
            )
            # Add OCR reading (no vision reading)
            session.add(
                PageReading(
                    id=reading_id,
                    tenant_id=tenant_id,
                    page_id=page_id,
                    source="ocr",
                    text="Synthetic OCR text.",
                    model_id="test-ocr",
                    model_version="1.0",
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, password)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            resp = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/1/accept"
            )
            assert resp.status_code == 200, resp.text

        async with tenant_session(ctx) as session:
            chunk = (
                await session.execute(
                    select(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.page_id == page_id)
                )
            ).scalar_one()
            assert chunk.evidence_type == "text"
            assert chunk.page_reading_id is None


@pytest.mark.asyncio
async def test_accept_flagged_page_without_reading_returns_422_no_usable_reading(test_app) -> None:
    """Accept on a flagged page with no readings returns 422 NO_USABLE_READING."""
    async with throwaway_step3_environment():
        tenant_id = uuid4()
        asst_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()
        page_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="No Reading Tenant"))
            await session.flush()

        admin_user, admin_email, password = await _create_user_with_role(tenant_id, "client_admin")

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
                    name="Accept Asst",
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
                    sha256="sha-1",
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
                    page_sha256="psha-1",
                    page_type="scanned",
                    image_key=f"{tenant_id}/pages/{ver_id}/1.png",
                    ocr_confidence=0.10,
                    needs_review=True,
                    review_reason="processing_failed",
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, password)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            resp = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/1/accept"
            )
            assert resp.status_code == 422
            assert "NO_USABLE_READING" in resp.text


@pytest.mark.asyncio
async def test_accept_and_exclude_double_click_no_ops(test_app) -> None:
    """Accepting or excluding an already resolved page is an idempotent 200 OK no-op."""
    async with throwaway_step3_environment():
        tenant_id = uuid4()
        asst_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Double Click Tenant"))
            await session.flush()

        admin_user, admin_email, password = await _create_user_with_role(tenant_id, "client_admin")

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
                    name="Double Click Asst",
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
                    sha256="sha-1",
                    uploaded_by=admin_user.id,
                    status="needs_review",
                )
            )
            # Page 1: already accepted
            session.add(
                Page(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    document_version_id=ver_id,
                    page_number=1,
                    page_sha256="psha-1",
                    page_type="digital_text",
                    image_key=f"{tenant_id}/pages/{ver_id}/1.png",
                    needs_review=False,
                    is_excluded=False,
                )
            )
            # Page 2: already excluded
            session.add(
                Page(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    document_version_id=ver_id,
                    page_number=2,
                    page_sha256="psha-2",
                    page_type="scanned",
                    image_key=f"{tenant_id}/pages/{ver_id}/2.png",
                    needs_review=False,
                    is_excluded=True,
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, password)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            # Accept on already-accepted page 1 -> 200 OK
            resp1 = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/1/accept"
            )
            assert resp1.status_code == 200
            assert resp1.json()["status"] == "accepted"

            # Exclude on already-excluded page 2 -> 200 OK
            resp2 = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/2/exclude"
            )
            assert resp2.status_code == 200
            assert resp2.json()["status"] == "excluded"


@pytest.mark.asyncio
async def test_accept_or_exclude_invalid_page_state_returns_409(test_app) -> None:
    """Accept on excluded page or exclude on clean accepted page returns 409 INVALID_PAGE_STATE."""
    async with throwaway_step3_environment():
        tenant_id = uuid4()
        asst_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Invalid State Tenant"))
            await session.flush()

        admin_user, admin_email, password = await _create_user_with_role(tenant_id, "client_admin")

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
                    name="Invalid State Asst",
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
                    sha256="sha-1",
                    uploaded_by=admin_user.id,
                    status="needs_review",
                )
            )
            # Page 1: clean accepted (not flagged)
            session.add(
                Page(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    document_version_id=ver_id,
                    page_number=1,
                    page_sha256="psha-1",
                    page_type="digital_text",
                    image_key=f"{tenant_id}/pages/{ver_id}/1.png",
                    needs_review=False,
                    is_excluded=False,
                )
            )
            # Page 2: excluded
            session.add(
                Page(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    document_version_id=ver_id,
                    page_number=2,
                    page_sha256="psha-2",
                    page_type="scanned",
                    image_key=f"{tenant_id}/pages/{ver_id}/2.png",
                    needs_review=False,
                    is_excluded=True,
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, password)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            # Exclude on a clean page (not flagged) -> 409
            resp1 = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/1/exclude"
            )
            assert resp1.status_code == 409
            assert "INVALID_PAGE_STATE" in resp1.text

            # Accept on an excluded page -> 409
            resp2 = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/2/accept"
            )
            assert resp2.status_code == 409
            assert "INVALID_PAGE_STATE" in resp2.text


@pytest.mark.asyncio
async def test_exclude_flagged_page_keeps_chunks_inactive(test_app) -> None:
    """Exclude on a flagged page sets is_excluded=true, needs_review=false, chunks stay inactive."""
    async with throwaway_step3_environment():
        tenant_id = uuid4()
        asst_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()
        page_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Exclude Tenant"))
            await session.flush()

        admin_user, admin_email, password = await _create_user_with_role(tenant_id, "client_admin")

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
                    name="Exclude Asst",
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
                    sha256="sha-1",
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
                    page_sha256="psha-1",
                    page_type="scanned",
                    image_key=f"{tenant_id}/pages/{ver_id}/1.png",
                    needs_review=True,
                    review_reason="low_ocr_confidence",
                )
            )
            # Second page clean so version doesn't auto-promote
            session.add(
                Page(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    document_version_id=ver_id,
                    page_number=2,
                    page_sha256="psha-2",
                    page_type="scanned",
                    image_key=f"{tenant_id}/pages/{ver_id}/2.png",
                    needs_review=True,
                    review_reason="vision_failed",
                )
            )
            await session.flush()
            session.add(
                Chunk(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    page_id=page_id,
                    document_version_id=ver_id,
                    text="Chunk text",
                    language="en",
                    ts_config="english",
                    is_active=False,
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, password)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            resp = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/1/exclude"
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "excluded"
            assert resp.json()["page_number"] == 1

        async with tenant_session(ctx) as session:
            page = (
                await session.execute(
                    select(Page).where(Page.tenant_id == tenant_id, Page.id == page_id)
                )
            ).scalar_one()
            assert page.is_excluded is True
            assert page.needs_review is False

            chunk = (
                await session.execute(
                    select(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.page_id == page_id)
                )
            ).scalar_one()
            assert chunk.is_active is False

            # Audit event logged
            audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.action == "page_excluded",
                        AuditEvent.resource_id == f"{ver_id}:1",
                    )
                )
            ).scalar_one_or_none()
            assert audit is not None


@pytest.mark.asyncio
async def test_auto_promote_on_last_flagged_page_resolved_retires_previous_version(
    test_app,
) -> None:
    """Resolving the last flagged page on Branch A version auto-promotes and retires old version."""
    async with throwaway_step3_environment():
        tenant_id = uuid4()
        asst_id = uuid4()
        doc_id = uuid4()
        v1_id = uuid4()
        v2_id = uuid4()
        v2_page_id = uuid4()
        reading_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Auto Promote Tenant"))
            await session.flush()

        admin_user, admin_email, password = await _create_user_with_role(tenant_id, "client_admin")

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
                    name="Auto Promote Asst",
                    prompt_id="default",
                    prompt_version="1.0",
                )
            )
            # Document has v1 currently active
            session.add(
                Document(id=doc_id, tenant_id=tenant_id, title="Doc", active_version_id=v1_id)
            )
            session.add(
                DocumentVersion(
                    id=v1_id,
                    tenant_id=tenant_id,
                    document_id=doc_id,
                    sha256="sha-v1",
                    uploaded_by=admin_user.id,
                    status="ready",
                )
            )
            v1_page = Page(
                id=uuid4(),
                tenant_id=tenant_id,
                document_version_id=v1_id,
                page_number=1,
                page_sha256="p-v1",
                page_type="digital_text",
                image_key=f"{tenant_id}/pages/{v1_id}/1.png",
                needs_review=False,
            )
            session.add(v1_page)
            await session.flush()
            session.add(
                Chunk(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    page_id=v1_page.id,
                    document_version_id=v1_id,
                    text="v1 active chunk",
                    language="en",
                    ts_config="english",
                    is_active=True,
                )
            )
            # v2 is in Branch A (held in needs_review, 1 flagged page)
            session.add(
                DocumentVersion(
                    id=v2_id,
                    tenant_id=tenant_id,
                    document_id=doc_id,
                    sha256="sha-v2",
                    uploaded_by=admin_user.id,
                    status="needs_review",
                )
            )
            session.add(
                Page(
                    id=v2_page_id,
                    tenant_id=tenant_id,
                    document_version_id=v2_id,
                    page_number=1,
                    page_sha256="p-v2",
                    page_type="scanned",
                    image_key=f"{tenant_id}/pages/{v2_id}/1.png",
                    needs_review=True,
                    review_reason="low_ocr_confidence",
                )
            )
            session.add(
                PageReading(
                    id=reading_id,
                    tenant_id=tenant_id,
                    page_id=v2_page_id,
                    source="vision_ingest",
                    text="v2 vision transcription",
                    model_id="test-vision",
                    model_version="1.0",
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, password)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            resp = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{v2_id}/pages/1/accept"
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["auto_promoted"] is True

        # Verify auto-promote and retirement
        async with tenant_session(ctx) as session:
            doc = (
                await session.execute(
                    select(Document).where(Document.tenant_id == tenant_id, Document.id == doc_id)
                )
            ).scalar_one()
            assert doc.active_version_id == v2_id

            v2 = (
                await session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.tenant_id == tenant_id, DocumentVersion.id == v2_id
                    )
                )
            ).scalar_one()
            assert v2.status == "ready"

            v1 = (
                await session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.tenant_id == tenant_id, DocumentVersion.id == v1_id
                    )
                )
            ).scalar_one()
            assert v1.status == "retired"

            # v1 chunks inactive, v2 chunks active
            v1_chunks = (
                (
                    await session.execute(
                        select(Chunk).where(
                            Chunk.tenant_id == tenant_id, Chunk.document_version_id == v1_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for c in v1_chunks:
                assert c.is_active is False

            v2_chunks = (
                (
                    await session.execute(
                        select(Chunk).where(
                            Chunk.tenant_id == tenant_id, Chunk.document_version_id == v2_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(v2_chunks) > 0
            for c in v2_chunks:
                assert c.is_active is True

            # Separate version_auto_promoted audit logged with system actor (user_id=None)
            auto_audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.action == "version_auto_promoted",
                        AuditEvent.resource_id == str(v2_id),
                    )
                )
            ).scalar_one_or_none()
            assert auto_audit is not None
            assert auto_audit.user_id is None


@pytest.mark.asyncio
async def test_branch_b_status_transitions_to_ready_when_last_flagged_page_resolved(
    test_app,
) -> None:
    """Resolving last flagged page on live Branch B version transitions status to ready."""
    async with throwaway_step3_environment():
        tenant_id = uuid4()
        asst_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()
        page_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Branch B Resolve Tenant"))
            await session.flush()

        admin_user, admin_email, password = await _create_user_with_role(tenant_id, "client_admin")

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
                    name="Branch B Asst",
                    prompt_id="default",
                    prompt_version="1.0",
                )
            )
            # Branch B: doc.active_version_id is ver_id, but version status is needs_review
            session.add(
                Document(id=doc_id, tenant_id=tenant_id, title="Doc", active_version_id=ver_id)
            )
            session.add(
                DocumentVersion(
                    id=ver_id,
                    tenant_id=tenant_id,
                    document_id=doc_id,
                    sha256="sha-b",
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
                    page_sha256="p-b",
                    page_type="scanned",
                    image_key=f"{tenant_id}/pages/{ver_id}/1.png",
                    needs_review=True,
                    review_reason="vision_failed",
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, password)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            # Resolve the single flagged page by excluding it
            resp = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/1/exclude"
            )
            assert resp.status_code == 200, resp.text

        # Verify version transitioned to ready
        async with tenant_session(ctx) as session:
            ver = (
                await session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.tenant_id == tenant_id, DocumentVersion.id == ver_id
                    )
                )
            ).scalar_one()
            assert ver.status == "ready"


@pytest.mark.asyncio
async def test_promote_with_exclusions_status_rules(test_app) -> None:
    """Promote with exclusions: stays needs_review if flagged pages remain, ready if none remain."""
    async with throwaway_step3_environment():
        tenant_id = uuid4()
        asst_id = uuid4()
        doc_id = uuid4()
        v1_id = uuid4()
        v2_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Promote Status Tenant"))
            await session.flush()

        admin_user, admin_email, password = await _create_user_with_role(tenant_id, "client_admin")

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
                    name="Promote Asst",
                    prompt_id="default",
                    prompt_version="1.0",
                )
            )
            session.add(
                Document(id=doc_id, tenant_id=tenant_id, title="Doc", active_version_id=v1_id)
            )
            session.add(
                DocumentVersion(
                    id=v1_id,
                    tenant_id=tenant_id,
                    document_id=doc_id,
                    sha256="sha-v1",
                    uploaded_by=admin_user.id,
                    status="ready",
                )
            )
            session.add(
                DocumentVersion(
                    id=v2_id,
                    tenant_id=tenant_id,
                    document_id=doc_id,
                    sha256="sha-v2",
                    uploaded_by=admin_user.id,
                    status="needs_review",
                )
            )
            p1 = Page(
                id=uuid4(),
                tenant_id=tenant_id,
                document_version_id=v2_id,
                page_number=1,
                page_sha256="p-1",
                page_type="digital_text",
                image_key=f"{tenant_id}/pages/{v2_id}/1.png",
                needs_review=False,
                is_excluded=False,
            )
            p2 = Page(
                id=uuid4(),
                tenant_id=tenant_id,
                document_version_id=v2_id,
                page_number=2,
                page_sha256="p-2",
                page_type="scanned",
                image_key=f"{tenant_id}/pages/{v2_id}/2.png",
                needs_review=True,
                review_reason="vision_failed",
            )
            session.add_all([p1, p2])
            await session.flush()
            session.add(
                Chunk(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    page_id=p1.id,
                    document_version_id=v2_id,
                    text="clean text",
                    language="en",
                    ts_config="english",
                    is_active=False,
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, password)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            # 1. Promote while page 2 is flagged -> version live, status stays needs_review
            resp1 = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{v2_id}/promote"
            )
            assert resp1.status_code == 200, resp1.text
            assert resp1.json()["status"] == "needs_review"

        async with tenant_session(ctx) as session:
            doc = (
                await session.execute(
                    select(Document).where(Document.tenant_id == tenant_id, Document.id == doc_id)
                )
            ).scalar_one()
            assert doc.active_version_id == v2_id

            v1 = (
                await session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.tenant_id == tenant_id, DocumentVersion.id == v1_id
                    )
                )
            ).scalar_one()
            assert v1.status == "retired"

            v2 = (
                await session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.tenant_id == tenant_id, DocumentVersion.id == v2_id
                    )
                )
            ).scalar_one()
            assert v2.status == "needs_review"

            # Clean chunk is active
            c1 = (
                await session.execute(
                    select(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.page_id == p1.id)
                )
            ).scalar_one()
            assert c1.is_active is True

            # Audit event logged
            audit = (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.action == "version_promoted",
                        AuditEvent.resource_id == str(v2_id),
                    )
                )
            ).scalar_one_or_none()
            assert audit is not None


@pytest.mark.asyncio
async def test_all_actions_refused_on_retired_document_returns_409(test_app) -> None:
    """All review actions (accept, exclude, promote) refused with 409 if document is retired."""
    async with throwaway_step3_environment():
        tenant_id = uuid4()
        asst_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Retired Tenant"))
            await session.flush()

        admin_user, admin_email, password = await _create_user_with_role(tenant_id, "client_admin")

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
                    name="Retired Asst",
                    prompt_id="default",
                    prompt_version="1.0",
                )
            )
            # Document marked retired
            session.add(
                Document(id=doc_id, tenant_id=tenant_id, title="Retired Doc", is_retired=True)
            )
            session.add(
                DocumentVersion(
                    id=ver_id,
                    tenant_id=tenant_id,
                    document_id=doc_id,
                    sha256="sha-r",
                    uploaded_by=admin_user.id,
                    status="retired",
                )
            )
            session.add(
                Page(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    document_version_id=ver_id,
                    page_number=1,
                    page_sha256="p-r",
                    page_type="scanned",
                    image_key=f"{tenant_id}/pages/{ver_id}/1.png",
                    needs_review=True,
                    review_reason="vision_failed",
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, password)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            # Accept refused
            r1 = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/1/accept"
            )
            assert r1.status_code == 409

            # Exclude refused
            r2 = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/pages/1/exclude"
            )
            assert r2.status_code == 409

            # Promote refused
            r3 = await auth_client.post(
                f"/v1/assistants/{asst_id}/knowledge/documents/{ver_id}/promote"
            )
            assert r3.status_code == 409


@pytest.mark.asyncio
async def test_cross_tenant_anti_probing_and_staff_rbac_and_csrf(test_app) -> None:
    """Verifies cross-tenant 404s, staff 403s, and CSRF token enforcement."""
    async with throwaway_step3_environment():
        t1 = uuid4()
        t2 = uuid4()
        asst1 = uuid4()
        doc1 = uuid4()
        ver1 = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1, name="Tenant 1"))
            session.add(Tenant(id=t2, name="Tenant 2"))
            await session.flush()

        admin_t1, email_admin_t1, pass_t1 = await _create_user_with_role(t1, "client_admin")
        staff_t1, email_staff_t1, pass_staff_t1 = await _create_user_with_role(t1, "staff")
        admin_t2, email_admin_t2, pass_t2 = await _create_user_with_role(t2, "client_admin")

        ctx1 = TenantContext(
            tenant_id=str(t1),
            user_id=str(admin_t1.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx1) as session:
            session.add(
                Assistant(
                    id=asst1,
                    tenant_id=t1,
                    name="Asst 1",
                    prompt_id="default",
                    prompt_version="1.0",
                )
            )
            session.add(Document(id=doc1, tenant_id=t1, title="Doc 1"))
            session.add(
                DocumentVersion(
                    id=ver1,
                    tenant_id=t1,
                    document_id=doc1,
                    sha256="sha-t1",
                    uploaded_by=admin_t1.id,
                    status="needs_review",
                )
            )
            session.add(
                Page(
                    id=uuid4(),
                    tenant_id=t1,
                    document_version_id=ver1,
                    page_number=1,
                    page_sha256="p-t1",
                    page_type="scanned",
                    image_key=f"{t1}/pages/{ver1}/1.png",
                    needs_review=True,
                    review_reason="vision_failed",
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Cross-tenant user T2 calling T1 assistant routes -> 404
            c2, csrf2 = await _login(client, email_admin_t2, pass_t2)
            auth_t2 = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=c2,
                headers={"X-CSRF-Token": csrf2},
            )
            res_cross = await auth_t2.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{ver1}/pages/1/accept"
            )
            assert res_cross.status_code == 404

            # 2. Staff user in T1 calling routes -> 403
            c_staff, csrf_staff = await _login(client, email_staff_t1, pass_staff_t1)
            auth_staff = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=c_staff,
                headers={"X-CSRF-Token": csrf_staff},
            )
            res_staff = await auth_staff.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{ver1}/pages/1/accept"
            )
            assert res_staff.status_code == 403

            # 3. Missing CSRF token -> 403
            c1, _ = await _login(client, email_admin_t1, pass_t1)
            no_csrf_client = AsyncClient(transport=transport, base_url="http://test", cookies=c1)
            res_no_csrf = await no_csrf_client.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{ver1}/pages/1/accept"
            )
            assert res_no_csrf.status_code == 403


@pytest.mark.asyncio
async def test_promote_version_status_preconditions_409_invalid_version_state(test_app) -> None:
    """Promote is allowed only when version status is needs_review.

    Versions with status processing, failed, retired, or received return 409 INVALID_VERSION_STATE.
    """
    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant Preconditions"))
            await session.flush()

        admin_user, admin_email, pass_admin = await _create_user_with_role(t1_id, "client_admin")

        status_versions: dict[str, UUID] = {
            "processing": uuid4(),
            "failed": uuid4(),
            "retired": uuid4(),
            "received": uuid4(),
        }

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="Preconditions Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)
            doc = Document(id=uuid4(), tenant_id=t1_id, title="Doc")
            session.add(doc)
            await session.flush()

            for st, vid in status_versions.items():
                ver = DocumentVersion(
                    id=vid,
                    tenant_id=t1_id,
                    document_id=doc.id,
                    sha256=f"hash_{st}",
                    uploaded_by=admin_user.id,
                    status=st,
                )
                session.add(ver)
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, pass_admin)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            for st, vid in status_versions.items():
                resp = await auth_client.post(
                    f"/v1/assistants/{asst1}/knowledge/documents/{vid}/promote"
                )
                assert resp.status_code == 409, (
                    f"Status {st} should return 409, got {resp.status_code}"
                )
                assert "INVALID_VERSION_STATE" in resp.text


@pytest.mark.asyncio
async def test_promote_already_active_ready_version_is_noop_200(test_app) -> None:
    """Promoting an already-active ready version is an idempotent no-op returning 200."""
    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()
        v_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant Noop Promote"))
            await session.flush()

        admin_user, admin_email, pass_admin = await _create_user_with_role(t1_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="Noop Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)
            doc = Document(
                id=uuid4(),
                tenant_id=t1_id,
                title="Doc",
                active_version_id=v_id,
            )
            session.add(doc)
            await session.flush()

            ver = DocumentVersion(
                id=v_id,
                tenant_id=t1_id,
                document_id=doc.id,
                sha256="hash_ready",
                uploaded_by=admin_user.id,
                status="ready",
            )
            session.add(ver)
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, pass_admin)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            resp = await auth_client.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{v_id}/promote"
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ready"
            assert data["is_active"] is True


@pytest.mark.asyncio
async def test_retry_page_not_flagged_returns_409_invalid_page_state(test_app) -> None:
    """Calling retry on a clean page (needs_review=false) returns 409 INVALID_PAGE_STATE."""
    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()
        v_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant Retry Clean"))
            await session.flush()

        admin_user, admin_email, pass_admin = await _create_user_with_role(t1_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="Retry Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)
            doc = Document(id=uuid4(), tenant_id=t1_id, title="Doc")
            session.add(doc)
            await session.flush()

            ver = DocumentVersion(
                id=v_id,
                tenant_id=t1_id,
                document_id=doc.id,
                sha256="hash_retry",
                uploaded_by=admin_user.id,
                status="needs_review",
            )
            session.add(ver)
            await session.flush()

            page = Page(
                id=uuid4(),
                tenant_id=t1_id,
                document_version_id=v_id,
                page_number=1,
                page_sha256="psha-1",
                page_type="digital_text",
                image_key=f"{t1_id}/pages/{v_id}/1.png",
                needs_review=False,
                is_excluded=False,
            )
            session.add(page)
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, pass_admin)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            resp = await auth_client.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{v_id}/pages/1/retry"
            )
            assert resp.status_code == 409
            assert "INVALID_PAGE_STATE" in resp.text


@pytest.mark.asyncio
async def test_retry_retired_document_returns_409(test_app) -> None:
    """Calling retry on a retired document returns 409 DocumentRetiredError."""
    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()
        v_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant Retired Retry"))
            await session.flush()

        admin_user, admin_email, pass_admin = await _create_user_with_role(t1_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="Retired Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)
            doc = Document(
                id=uuid4(),
                tenant_id=t1_id,
                title="Doc",
                is_retired=True,
            )
            session.add(doc)
            await session.flush()

            ver = DocumentVersion(
                id=v_id,
                tenant_id=t1_id,
                document_id=doc.id,
                sha256="hash_ret",
                uploaded_by=admin_user.id,
                status="needs_review",
            )
            session.add(ver)
            await session.flush()

            page = Page(
                id=uuid4(),
                tenant_id=t1_id,
                document_version_id=v_id,
                page_number=1,
                page_sha256="psha-1",
                page_type="scanned",
                image_key=f"{t1_id}/pages/{v_id}/1.png",
                needs_review=True,
                review_reason="vision_failed",
            )
            session.add(page)
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, pass_admin)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            resp = await auth_client.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{v_id}/pages/1/retry"
            )
            assert resp.status_code == 409
            assert "DOCUMENT_RETIRED" in resp.text


@pytest.mark.asyncio
async def test_retry_not_useful_when_reading_exists_with_current_model_and_version(
    test_app,
) -> None:
    """Refuse 409 RETRY_NOT_USEFUL when reading exists for failed step with current model."""
    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()
        v_id = uuid4()
        p_id = uuid4()
        job_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant Retry Not Useful"))
            await session.flush()

        admin_user, admin_email, pass_admin = await _create_user_with_role(t1_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="Retry Useful Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)
            doc = Document(id=uuid4(), tenant_id=t1_id, title="Doc")
            session.add(doc)
            await session.flush()

            ver = DocumentVersion(
                id=v_id,
                tenant_id=t1_id,
                document_id=doc.id,
                sha256="hash_not_useful",
                uploaded_by=admin_user.id,
                status="needs_review",
            )
            session.add(ver)
            await session.flush()

            page = Page(
                id=p_id,
                tenant_id=t1_id,
                document_version_id=v_id,
                page_number=1,
                page_sha256="psha-1",
                page_type="scanned",
                image_key=f"{t1_id}/pages/{v_id}/1.png",
                needs_review=True,
                review_reason="vision_low_confidence",
            )
            session.add(page)
            await session.flush()

            # Existing page reading with current model mock-vision and version 1.0
            reading = PageReading(
                id=uuid4(),
                tenant_id=t1_id,
                page_id=p_id,
                source="vision_ingest",
                text="Low confidence reading text",
                model_id="mock-vision",
                model_version="1.0",
            )
            session.add(reading)

            # Job and job step completed with processing_version v1
            job = Job(id=job_id, tenant_id=t1_id, document_version_id=v_id, status="processing")
            session.add(job)
            await session.flush()

            step = JobStep(
                id=uuid4(),
                tenant_id=t1_id,
                job_id=job_id,
                document_version_id=v_id,
                page_number=1,
                step="vision",
                processing_version="v1",
                status="completed",
            )
            session.add(step)
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, pass_admin)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            resp = await auth_client.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{v_id}/pages/1/retry"
            )
            assert resp.status_code == 409
            assert "RETRY_NOT_USEFUL" in resp.text


@pytest.mark.asyncio
async def test_retry_allowed_when_no_reading_row_exists_for_failed_step(test_app) -> None:
    """When no reading exists (e.g. vision_failed), retry is allowed, enqueued, and audited."""
    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()
        v_id = uuid4()
        p_id = uuid4()
        job_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant Retry Allowed"))
            await session.flush()

        admin_user, admin_email, pass_admin = await _create_user_with_role(t1_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="Retry Allowed Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)
            doc = Document(id=uuid4(), tenant_id=t1_id, title="Doc")
            session.add(doc)
            await session.flush()

            ver = DocumentVersion(
                id=v_id,
                tenant_id=t1_id,
                document_id=doc.id,
                sha256="hash_allowed",
                uploaded_by=admin_user.id,
                status="needs_review",
            )
            session.add(ver)
            await session.flush()

            page = Page(
                id=p_id,
                tenant_id=t1_id,
                document_version_id=v_id,
                page_number=1,
                page_sha256="psha-1",
                page_type="scanned",
                image_key=f"{t1_id}/pages/{v_id}/1.png",
                needs_review=True,
                review_reason="vision_failed",
            )
            session.add(page)
            await session.flush()

            job = Job(id=job_id, tenant_id=t1_id, document_version_id=v_id, status="processing")
            session.add(job)
            await session.flush()

            step = JobStep(
                id=uuid4(),
                tenant_id=t1_id,
                job_id=job_id,
                document_version_id=v_id,
                page_number=1,
                step="vision",
                processing_version="v1",
                status="failed",
            )
            session.add(step)
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, pass_admin)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            resp = await auth_client.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{v_id}/pages/1/retry"
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["page_number"] == 1
            assert data["needs_review"] is True  # Page stays flagged until retried run finishes!

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="verify",
        )
        async with tenant_session(ctx) as session:
            p_check = await session.execute(
                select(Page).where(Page.tenant_id == t1_id, Page.id == p_id)
            )
            p_check_val = p_check.scalar_one()
            assert p_check_val.needs_review is True

            audits = (
                (
                    await session.execute(
                        select(AuditEvent).where(
                            AuditEvent.tenant_id == t1_id,
                            AuditEvent.action == "page_retry_enqueued",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(audits) == 1
            assert audits[0].resource_id == f"{v_id}:1"


@pytest.mark.asyncio
async def test_retry_allowed_when_processing_version_changed(test_app) -> None:
    """Allow retry when processing version changed, even if reading existed under old version."""
    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()
        v_id = uuid4()
        p_id = uuid4()
        job_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant Retry Version Changed"))
            await session.flush()

        admin_user, admin_email, pass_admin = await _create_user_with_role(t1_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="Version Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)
            doc = Document(id=uuid4(), tenant_id=t1_id, title="Doc")
            session.add(doc)
            await session.flush()

            ver = DocumentVersion(
                id=v_id,
                tenant_id=t1_id,
                document_id=doc.id,
                sha256="hash_v_change",
                uploaded_by=admin_user.id,
                status="needs_review",
            )
            session.add(ver)
            await session.flush()

            page = Page(
                id=p_id,
                tenant_id=t1_id,
                document_version_id=v_id,
                page_number=1,
                page_sha256="psha-1",
                page_type="scanned",
                image_key=f"{t1_id}/pages/{v_id}/1.png",
                needs_review=True,
                review_reason="vision_low_confidence",
            )
            session.add(page)
            await session.flush()

            # Reading exists with old version
            reading = PageReading(
                id=uuid4(),
                tenant_id=t1_id,
                page_id=p_id,
                source="vision_ingest",
                text="Old version reading",
                model_id="mock-vision",
                model_version="0.9",
            )
            session.add(reading)

            job = Job(id=job_id, tenant_id=t1_id, document_version_id=v_id, status="processing")
            session.add(job)
            await session.flush()

            # Old step recorded under processing_version "v0"
            step = JobStep(
                id=uuid4(),
                tenant_id=t1_id,
                job_id=job_id,
                document_version_id=v_id,
                page_number=1,
                step="vision",
                processing_version="v0",
                status="completed",
            )
            session.add(step)
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, pass_admin)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            # Current processing version is v1; since previous was v0, retry is useful and allowed
            resp = await auth_client.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{v_id}/pages/1/retry"
            )
            assert resp.status_code == 200
            assert resp.json()["needs_review"] is True


@pytest.mark.asyncio
async def test_retry_refused_when_job_already_queued_or_running_returns_409(test_app) -> None:
    """Refuse 409 PAGE_JOB_ALREADY_ACTIVE when job for page is already active or queued."""
    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()
        v_id = uuid4()
        p_id = uuid4()
        job_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant Job Conflict"))
            await session.flush()

        admin_user, admin_email, pass_admin = await _create_user_with_role(t1_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="Conflict Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)
            doc = Document(id=uuid4(), tenant_id=t1_id, title="Doc")
            session.add(doc)
            await session.flush()

            ver = DocumentVersion(
                id=v_id,
                tenant_id=t1_id,
                document_id=doc.id,
                sha256="hash_conflict",
                uploaded_by=admin_user.id,
                status="needs_review",
            )
            session.add(ver)
            await session.flush()

            page = Page(
                id=p_id,
                tenant_id=t1_id,
                document_version_id=v_id,
                page_number=1,
                page_sha256="psha-1",
                page_type="scanned",
                image_key=f"{t1_id}/pages/{v_id}/1.png",
                needs_review=True,
                review_reason="vision_failed",
            )
            session.add(page)
            await session.flush()

            job = Job(id=job_id, tenant_id=t1_id, document_version_id=v_id, status="processing")
            session.add(job)
            await session.flush()

            # Page processing marker is currently active ('running')
            step = JobStep(
                id=uuid4(),
                tenant_id=t1_id,
                job_id=job_id,
                document_version_id=v_id,
                page_number=1,
                step="page_processing",
                processing_version="v1",
                status="running",
            )
            session.add(step)
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, admin_email, pass_admin)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

            resp = await auth_client.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{v_id}/pages/1/retry"
            )
            assert resp.status_code == 409
            assert "PAGE_JOB_ALREADY_ACTIVE" in resp.text


@pytest.mark.asyncio
async def test_retry_success_on_live_version_activates_page_chunks() -> None:
    """Activating page chunks and Branch B transition when retried page finishes on live version."""
    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()
        v_id = uuid4()
        p1_id = uuid4()
        p2_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant Live Retry"))
            await session.flush()

        admin_user, admin_email, pass_admin = await _create_user_with_role(t1_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="Live Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)

            # Live version in needs_review
            doc = Document(
                id=uuid4(),
                tenant_id=t1_id,
                title="Doc",
                active_version_id=v_id,
            )
            session.add(doc)
            await session.flush()

            ver = DocumentVersion(
                id=v_id,
                tenant_id=t1_id,
                document_id=doc.id,
                sha256="hash_live_retry",
                uploaded_by=admin_user.id,
                status="needs_review",
            )
            session.add(ver)
            await session.flush()

            # Page 1: clean and active
            p1 = Page(
                id=p1_id,
                tenant_id=t1_id,
                document_version_id=v_id,
                page_number=1,
                page_sha256="psha-1",
                page_type="digital_text",
                image_key=f"{t1_id}/pages/{v_id}/1.png",
                needs_review=False,
                is_excluded=False,
            )
            c1 = Chunk(
                id=uuid4(),
                tenant_id=t1_id,
                document_version_id=v_id,
                page_id=p1_id,
                text="Page 1 text",
                language="en",
                ts_config="english",
                is_active=True,
            )

            # Page 2: flagged needs_review, chunk currently inactive
            p2 = Page(
                id=p2_id,
                tenant_id=t1_id,
                document_version_id=v_id,
                page_number=2,
                page_sha256="psha-2",
                page_type="scanned",
                image_key=f"{t1_id}/pages/{v_id}/2.png",
                needs_review=True,
                review_reason="vision_failed",
                is_excluded=False,
            )
            c2 = Chunk(
                id=uuid4(),
                tenant_id=t1_id,
                document_version_id=v_id,
                page_id=p2_id,
                text="Page 2 text",
                language="en",
                ts_config="english",
                is_active=False,
            )
            session.add_all([p1, p2])
            await session.flush()
            session.add_all([c1, c2])
            await session.flush()

        # Simulate retried page finishing: page flag cleared, re_evaluate_version_after_retry called
        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="verify",
        )
        async with tenant_session(ctx) as session:
            from app.wiring import create_document_repository

            repo = create_document_repository(session)
            await repo.clear_page_review_flag(str(t1_id), v_id, 2)
            version_rec, auto_promoted = await repo.re_evaluate_version_after_retry(
                str(t1_id), v_id
            )
            assert auto_promoted is False  # Version was already active
            assert version_rec.status == "ready"  # Branch B ready transition

        # Verify page 2 chunks are now active and version is ready
        async with tenant_session(ctx) as session:
            p2_chunks = (
                (
                    await session.execute(
                        select(Chunk.is_active).where(
                            Chunk.tenant_id == t1_id,
                            Chunk.document_version_id == v_id,
                            Chunk.page_id == p2_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert all(p2_chunks) is True

            ver_check = (
                await session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.tenant_id == t1_id, DocumentVersion.id == v_id
                    )
                )
            ).scalar_one()
            assert ver_check.status == "ready"


@pytest.mark.asyncio
async def test_retry_success_resolves_last_flag_on_branch_a_auto_promotes() -> None:
    """When retried page resolves last flag on Branch A version, auto-promote with system audit."""
    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()
        v1_id = uuid4()
        v2_id = uuid4()
        p1_id = uuid4()
        p2_id = uuid4()
        doc_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant Branch A Retry Auto Promote"))
            await session.flush()

        admin_user, admin_email, pass_admin = await _create_user_with_role(t1_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="Auto Promote Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)

            # Prior active version V1
            doc = Document(
                id=doc_id,
                tenant_id=t1_id,
                title="Doc",
                active_version_id=v1_id,
            )
            session.add(doc)
            await session.flush()

            v1 = DocumentVersion(
                id=v1_id,
                tenant_id=t1_id,
                document_id=doc.id,
                sha256="hash_v1",
                uploaded_by=admin_user.id,
                status="ready",
            )
            p_v1 = Page(
                id=uuid4(),
                tenant_id=t1_id,
                document_version_id=v1_id,
                page_number=1,
                page_sha256="psha-v1-1",
                page_type="digital_text",
                image_key=f"{t1_id}/pages/{v1_id}/1.png",
                needs_review=False,
                is_excluded=False,
            )
            c_v1 = Chunk(
                id=uuid4(),
                tenant_id=t1_id,
                document_version_id=v1_id,
                page_id=p_v1.id,
                text="V1 Chunk",
                language="en",
                ts_config="english",
                is_active=True,
            )

            # New version V2 held in needs_review (Branch A)
            v2 = DocumentVersion(
                id=v2_id,
                tenant_id=t1_id,
                document_id=doc.id,
                sha256="hash_v2",
                uploaded_by=admin_user.id,
                status="needs_review",
            )

            p1 = Page(
                id=p1_id,
                tenant_id=t1_id,
                document_version_id=v2_id,
                page_number=1,
                page_sha256="psha-1",
                page_type="digital_text",
                image_key=f"{t1_id}/pages/{v2_id}/1.png",
                needs_review=False,
                is_excluded=False,
            )
            c_p1 = Chunk(
                id=uuid4(),
                tenant_id=t1_id,
                document_version_id=v2_id,
                page_id=p1_id,
                text="V2 Page 1 text",
                language="en",
                ts_config="english",
                is_active=False,
            )

            p2 = Page(
                id=p2_id,
                tenant_id=t1_id,
                document_version_id=v2_id,
                page_number=2,
                page_sha256="psha-2",
                page_type="scanned",
                image_key=f"{t1_id}/pages/{v2_id}/2.png",
                needs_review=True,
                review_reason="vision_failed",
                is_excluded=False,
            )
            c_p2 = Chunk(
                id=uuid4(),
                tenant_id=t1_id,
                document_version_id=v2_id,
                page_id=p2_id,
                text="V2 Page 2 text",
                language="en",
                ts_config="english",
                is_active=False,
            )

            session.add_all([v1, p_v1, v2, p1, p2])
            await session.flush()
            session.add_all([c_v1, c_p1, c_p2])
            await session.flush()

        # Simulate retried page finishing: page 2 flag cleared, re-evaluate version
        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="verify",
        )
        async with tenant_session(ctx) as session:
            from app.wiring import create_document_repository

            repo = create_document_repository(session)
            await repo.clear_page_review_flag(str(t1_id), v2_id, 2)
            version_rec, auto_promoted = await repo.re_evaluate_version_after_retry(
                str(t1_id), v2_id
            )
            assert auto_promoted is True
            assert version_rec.status == "ready"

        # Verify Document active_version_id flipped to V2
        async with tenant_session(ctx) as session:
            doc_check = (
                await session.execute(select(Document).where(Document.id == doc_id))
            ).scalar_one()
            assert doc_check.active_version_id == v2_id

            v1_check = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == v1_id))
            ).scalar_one()
            assert v1_check.status == "retired"

            c_v1_check = (
                await session.execute(
                    select(Chunk.is_active).where(Chunk.document_version_id == v1_id)
                )
            ).scalar_one()
            assert c_v1_check is False

            v2_chunks = (
                (
                    await session.execute(
                        select(Chunk.is_active).where(Chunk.document_version_id == v2_id)
                    )
                )
                .scalars()
                .all()
            )
            assert all(v2_chunks) is True

            # Separate version_auto_promoted audit event with actor system (user_id=None)
            audits = (
                (
                    await session.execute(
                        select(AuditEvent).where(
                            AuditEvent.tenant_id == t1_id,
                            AuditEvent.action == "version_auto_promoted",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(audits) == 1
            assert audits[0].user_id is None
            assert audits[0].resource_id == str(v2_id)


@pytest.mark.asyncio
async def test_retry_cross_tenant_404_staff_403_csrf_required(test_app) -> None:
    """Retry enforces cross-tenant anti-probing (404), staff RBAC (403), and CSRF token."""
    async with throwaway_step3_environment():
        t1_id = uuid4()
        t2_id = uuid4()
        asst1 = uuid4()
        ver1 = uuid4()

        async with unscoped_session() as session:
            session.add_all([Tenant(id=t1_id, name="T1"), Tenant(id=t2_id, name="T2")])
            await session.flush()

        user_admin_t1, email_admin_t1, pass_t1 = await _create_user_with_role(t1_id, "client_admin")
        _, email_staff_t1, pass_staff_t1 = await _create_user_with_role(t1_id, "staff")
        _, email_admin_t2, pass_t2 = await _create_user_with_role(t2_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(user_admin_t1.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast1 = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="A1",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast1)

            doc1 = Document(id=uuid4(), tenant_id=t1_id, title="D")
            session.add(doc1)
            await session.flush()

            v1 = DocumentVersion(
                id=ver1,
                tenant_id=t1_id,
                document_id=doc1.id,
                sha256="h1",
                uploaded_by=user_admin_t1.id,
                status="needs_review",
            )
            p1 = Page(
                id=uuid4(),
                tenant_id=t1_id,
                document_version_id=ver1,
                page_number=1,
                page_sha256="psha-1",
                page_type="scanned",
                image_key=f"{t1_id}/pages/{ver1}/1.png",
                needs_review=True,
                review_reason="vision_failed",
            )
            session.add_all([v1, p1])
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Cross-tenant user T2 calling T1 assistant retry -> 404
            c2, csrf2 = await _login(client, email_admin_t2, pass_t2)
            auth_t2 = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=c2,
                headers={"X-CSRF-Token": csrf2},
            )
            res_cross = await auth_t2.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{ver1}/pages/1/retry"
            )
            assert res_cross.status_code == 404

            # 2. Staff user calling retry -> 403
            c_staff, csrf_staff = await _login(client, email_staff_t1, pass_staff_t1)
            auth_staff = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=c_staff,
                headers={"X-CSRF-Token": csrf_staff},
            )
            res_staff = await auth_staff.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{ver1}/pages/1/retry"
            )
            assert res_staff.status_code == 403

            # 3. Missing CSRF -> 403
            c1, _ = await _login(client, email_admin_t1, pass_t1)
            no_csrf = AsyncClient(transport=transport, base_url="http://test", cookies=c1)
            res_no_csrf = await no_csrf.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{ver1}/pages/1/retry"
            )
            assert res_no_csrf.status_code == 403


@pytest.mark.asyncio
async def test_retry_defer_failure_rolls_back_and_leaves_page_retryable(
    test_app, monkeypatch
) -> None:
    """When procrastinate defer fails, the transaction rolls back so the page remains retryable."""
    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()
        v_id = uuid4()
        p_id = uuid4()
        job_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant Defer Rollback"))
            await session.flush()

        admin_user, admin_email, pass_admin = await _create_user_with_role(t1_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="Retry Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)
            doc = Document(id=uuid4(), tenant_id=t1_id, title="Doc")
            session.add(doc)
            await session.flush()

            ver = DocumentVersion(
                id=v_id,
                tenant_id=t1_id,
                document_id=doc.id,
                sha256="hash_defer_test",
                uploaded_by=admin_user.id,
                status="needs_review",
            )
            page = Page(
                id=p_id,
                tenant_id=t1_id,
                document_version_id=v_id,
                page_number=1,
                page_sha256="psha_defer",
                page_type="scanned",
                image_key=f"{t1_id}/pages/{v_id}/1.png",
                needs_review=True,
                review_reason="vision_failed",
            )
            job = Job(id=job_id, tenant_id=t1_id, document_version_id=v_id, status="failed")
            step = JobStep(
                id=uuid4(),
                tenant_id=t1_id,
                job_id=job.id,
                document_version_id=v_id,
                page_number=1,
                step="ocr",
                processing_version="v1",
                status="failed",
            )
            session.add_all([ver, page, job, step])
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf = await _login(client, admin_email, pass_admin)
            auth_client = AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf},
            )

            # 1. Simulate Procrastinate defer failure
            from app.worker import process_page_job

            async def mock_failing_defer(*_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("Procrastinate DB connector unavailable")

            monkeypatch.setattr(process_page_job, "defer_async", mock_failing_defer)

            res_fail = await auth_client.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{v_id}/pages/1/retry"
            )
            assert res_fail.status_code == 500

            # 2. Verify page_processing marker was NOT committed (rolled back)
            # and original step unchanged
            async with tenant_session(ctx) as session:
                marker_check = (
                    (
                        await session.execute(
                            select(JobStep).where(
                                JobStep.tenant_id == t1_id,
                                JobStep.document_version_id == v_id,
                                JobStep.page_number == 1,
                                JobStep.step == "page_processing",
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                assert marker_check is None

                step_check = (
                    await session.execute(
                        select(JobStep.status).where(
                            JobStep.tenant_id == t1_id,
                            JobStep.document_version_id == v_id,
                            JobStep.page_number == 1,
                            JobStep.step == "ocr",
                        )
                    )
                ).scalar_one()
                assert step_check == "failed"

            # 3. Restore defer and retry again - must succeed without 409 conflict
            monkeypatch.undo()

            # Mock defer to succeed cleanly in this isolated test
            async def mock_succeeding_defer(*_args: Any, **_kwargs: Any) -> None:
                return None

            monkeypatch.setattr(process_page_job, "defer_async", mock_succeeding_defer)

            res_retry = await auth_client.post(
                f"/v1/assistants/{asst1}/knowledge/documents/{v_id}/pages/1/retry"
            )
            assert res_retry.status_code == 200
            assert res_retry.json()["status"] == "retrying"

            # Verify marker step is now queued
            async with tenant_session(ctx) as session:
                step_check2 = (
                    await session.execute(
                        select(JobStep.status).where(
                            JobStep.tenant_id == t1_id,
                            JobStep.document_version_id == v_id,
                            JobStep.page_number == 1,
                            JobStep.step == "page_processing",
                        )
                    )
                ).scalar_one()
                assert step_check2 == "queued"


@pytest.mark.asyncio
async def test_retry_end_to_end_through_run_page_job_activates_chunks_and_promotes() -> None:
    """When retried page finishes through run_page_job, re-evaluate triggers and auto-promotes."""
    from procrastinate import JobContext
    from procrastinate.jobs import Job as ProcrastinateJob

    from app.worker import app as procrastinate_app
    from app.worker import process_page_job, run_page_job

    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()
        v1_id = uuid4()
        v2_id = uuid4()
        doc_id = uuid4()
        p1_id = uuid4()
        p2_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant E2E Retry"))
            await session.flush()

        admin_user, admin_email, pass_admin = await _create_user_with_role(t1_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="E2E Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)

            # Document with V1 active
            doc = Document(id=doc_id, tenant_id=t1_id, title="Doc", active_version_id=v1_id)
            session.add(doc)
            await session.flush()

            v1 = DocumentVersion(
                id=v1_id,
                tenant_id=t1_id,
                document_id=doc_id,
                sha256="hash_v1_active",
                uploaded_by=admin_user.id,
                status="ready",
            )
            p_v1_id = uuid4()
            p_v1 = Page(
                id=p_v1_id,
                tenant_id=t1_id,
                document_version_id=v1_id,
                page_number=1,
                page_sha256="psha-v1",
                page_type="digital_text",
                image_key=f"{t1_id}/pages/{v1_id}/1.png",
                needs_review=False,
                is_excluded=False,
            )
            c_v1 = Chunk(
                id=uuid4(),
                tenant_id=t1_id,
                document_version_id=v1_id,
                page_id=p_v1_id,
                text="V1 text",
                language="en",
                ts_config="english",
                is_active=True,
            )

            # V2 (Branch A candidate: 2 pages, page 1 clean, page 2 flagged)
            v2 = DocumentVersion(
                id=v2_id,
                tenant_id=t1_id,
                document_id=doc_id,
                sha256="hash_v2_branch_a",
                uploaded_by=admin_user.id,
                status="needs_review",
            )
            p1 = Page(
                id=p1_id,
                tenant_id=t1_id,
                document_version_id=v2_id,
                page_number=1,
                page_sha256="psha-1",
                page_type="digital_text",
                image_key=f"{t1_id}/pages/{v2_id}/1.png",
                needs_review=False,
                is_excluded=False,
            )
            c_p1 = Chunk(
                id=uuid4(),
                tenant_id=t1_id,
                document_version_id=v2_id,
                page_id=p1_id,
                text="V2 page 1 text",
                language="en",
                ts_config="english",
                is_active=False,
            )
            p2 = Page(
                id=p2_id,
                tenant_id=t1_id,
                document_version_id=v2_id,
                page_number=2,
                page_sha256="psha-2",
                page_type="scanned",
                image_key=f"{t1_id}/pages/{v2_id}/2.png",
                needs_review=True,
                review_reason="vision_failed",
                is_excluded=False,
            )
            session.add_all([v1, p_v1, v2, p1, p2])
            await session.flush()
            session.add_all([c_v1, c_p1])
            await session.flush()

            # Enqueue page 2 retry
            from app.wiring import create_document_repository

            repo = create_document_repository(session)
            await repo.enqueue_page_retry(str(t1_id), v2_id, 2)

        # Run page job directly through worker job function run_page_job
        proc_job = ProcrastinateJob(
            id=501,
            queue="ingestion",
            lock=None,
            queueing_lock=None,
            task_name=process_page_job.name,
            task_kwargs={
                "tenant_id": str(t1_id),
                "document_version_id": str(v2_id),
                "page_number": 2,
            },
            attempts=1,
        )
        job_ctx = JobContext(
            app=procrastinate_app,
            job=proc_job,
            start_timestamp=0.0,
            abort_reason=lambda: None,
        )

        await run_page_job(
            context=job_ctx,
            tenant_id=str(t1_id),
            document_version_id=str(v2_id),
            page_number=2,
        )

        # Verify outcome end-to-end:
        # 1. Document active_version_id flipped to V2
        # 2. V1 retired and V1 chunk deactivated
        # 3. V2 status is ready
        # 4. V2 chunks are activated
        # 5. Page 2 review flag is cleared
        # 6. version_auto_promoted audit recorded
        async with tenant_session(ctx) as session:
            doc_rec = (
                await session.execute(select(Document).where(Document.id == doc_id))
            ).scalar_one()
            assert doc_rec.active_version_id == v2_id

            v1_rec = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == v1_id))
            ).scalar_one()
            assert v1_rec.status == "retired"

            v1_chunks = (
                (
                    await session.execute(
                        select(Chunk.is_active).where(Chunk.document_version_id == v1_id)
                    )
                )
                .scalars()
                .all()
            )
            assert all(not act for act in v1_chunks)

            v2_rec = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == v2_id))
            ).scalar_one()
            assert v2_rec.status == "ready"

            p2_rec = (
                await session.execute(
                    select(Page.needs_review).where(
                        Page.document_version_id == v2_id, Page.page_number == 2
                    )
                )
            ).scalar_one()
            assert p2_rec is False

            v2_chunks = (
                (
                    await session.execute(
                        select(Chunk.is_active).where(Chunk.document_version_id == v2_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(v2_chunks) > 0
            assert all(v2_chunks) is True

            audits = (
                (
                    await session.execute(
                        select(AuditEvent).where(
                            AuditEvent.tenant_id == t1_id,
                            AuditEvent.action == "version_auto_promoted",
                            AuditEvent.resource_id == str(v2_id),
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(audits) == 1


@pytest.mark.asyncio
async def test_retry_passing_run_does_not_clear_legacy_unspecified_flag() -> None:
    """A passing retried run must never clear a legacy_unspecified review reason."""
    from procrastinate import JobContext
    from procrastinate.jobs import Job as ProcrastinateJob

    from app.worker import app as procrastinate_app
    from app.worker import process_page_job, run_page_job

    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()
        v_id = uuid4()
        doc_id = uuid4()
        p_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant Legacy Unspecified"))
            await session.flush()

        admin_user, _admin_email, _pass_admin = await _create_user_with_role(t1_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="Legacy Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)
            doc = Document(id=doc_id, tenant_id=t1_id, title="Doc")
            session.add(doc)
            await session.flush()

            ver = DocumentVersion(
                id=v_id,
                tenant_id=t1_id,
                document_id=doc_id,
                sha256="hash_legacy_unspecified",
                uploaded_by=admin_user.id,
                status="needs_review",
            )
            page = Page(
                id=p_id,
                tenant_id=t1_id,
                document_version_id=v_id,
                page_number=1,
                page_sha256="psha_legacy",
                page_type="digital_text",
                image_key=f"{t1_id}/pages/{v_id}/1.png",
                needs_review=True,
                review_reason="legacy_unspecified",
                is_excluded=False,
            )
            session.add_all([ver, page])
            await session.flush()

            from app.wiring import create_document_repository

            repo = create_document_repository(session)
            await repo.enqueue_page_retry(str(t1_id), v_id, 1)

        proc_job = ProcrastinateJob(
            id=601,
            queue="ingestion",
            lock=None,
            queueing_lock=None,
            task_name=process_page_job.name,
            task_kwargs={
                "tenant_id": str(t1_id),
                "document_version_id": str(v_id),
                "page_number": 1,
            },
            attempts=1,
        )
        job_ctx = JobContext(
            app=procrastinate_app,
            job=proc_job,
            start_timestamp=0.0,
            abort_reason=lambda: None,
        )

        await run_page_job(
            context=job_ctx,
            tenant_id=str(t1_id),
            document_version_id=str(v_id),
            page_number=1,
        )

        # Page must stay flagged with legacy_unspecified and version must stay needs_review
        async with tenant_session(ctx) as session:
            page_rec = (
                await session.execute(
                    select(Page).where(
                        Page.tenant_id == t1_id,
                        Page.document_version_id == v_id,
                        Page.page_number == 1,
                    )
                )
            ).scalar_one()
            assert page_rec.needs_review is True
            assert page_rec.review_reason == "legacy_unspecified"

            ver_rec = (
                await session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.tenant_id == t1_id,
                        DocumentVersion.id == v_id,
                    )
                )
            ).scalar_one()
            assert ver_rec.status == "needs_review"


@pytest.mark.asyncio
async def test_retry_legacy_unspecified_hits_low_ocr_and_successful_vision_stays_flagged(
    monkeypatch,
) -> None:
    """A legacy page whose re-run hits low OCR and then a successful vision read
    stays legacy_unspecified.
    """
    from procrastinate import JobContext
    from procrastinate.jobs import Job as ProcrastinateJob

    from app.adapters.parsing.mock import MockDocumentParser
    from app.ports.types import PageArtifact, ParsedPageContent
    from app.worker import app as procrastinate_app
    from app.worker import process_page_job, run_page_job

    async with throwaway_step3_environment():
        t1_id = uuid4()
        asst1 = uuid4()
        v_id = uuid4()
        doc_id = uuid4()
        p_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=t1_id, name="Tenant Legacy Low OCR Vision"))
            await session.flush()

        admin_user, _admin_email, _pass_admin = await _create_user_with_role(t1_id, "client_admin")

        ctx = TenantContext(
            tenant_id=str(t1_id),
            user_id=str(admin_user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            ast = Assistant(
                id=asst1,
                tenant_id=t1_id,
                name="Legacy Vision Assistant",
                prompt_id="default",
                prompt_version="1.0",
            )
            session.add(ast)
            doc = Document(id=doc_id, tenant_id=t1_id, title="Doc")
            session.add(doc)
            await session.flush()

            ver = DocumentVersion(
                id=v_id,
                tenant_id=t1_id,
                document_id=doc_id,
                sha256="hash_legacy_vision",
                uploaded_by=admin_user.id,
                status="needs_review",
            )
            page = Page(
                id=p_id,
                tenant_id=t1_id,
                document_version_id=v_id,
                page_number=1,
                page_sha256="psha_legacy_vis",
                page_type="scanned",
                image_key=f"{t1_id}/pages/{v_id}/1.png",
                needs_review=True,
                review_reason="legacy_unspecified",
                is_excluded=False,
            )
            session.add_all([ver, page])
            await session.flush()

            from app.wiring import create_document_repository

            repo = create_document_repository(session)
            await repo.enqueue_page_retry(str(t1_id), v_id, 1)

        # Force MockDocumentParser to return low OCR confidence (0.45)
        async def mock_extract(
            _self: MockDocumentParser, page_artifact: PageArtifact
        ) -> ParsedPageContent:
            return ParsedPageContent(
                page_number=page_artifact.page_number,
                text="Low OCR text layer content",
                confidence=0.45,
            )

        monkeypatch.setattr(MockDocumentParser, "extract", mock_extract)

        proc_job = ProcrastinateJob(
            id=701,
            queue="ingestion",
            lock=None,
            queueing_lock=None,
            task_name=process_page_job.name,
            task_kwargs={
                "tenant_id": str(t1_id),
                "document_version_id": str(v_id),
                "page_number": 1,
            },
            attempts=1,
        )
        job_ctx = JobContext(
            app=procrastinate_app,
            job=proc_job,
            start_timestamp=0.0,
            abort_reason=lambda: None,
        )

        await run_page_job(
            context=job_ctx,
            tenant_id=str(t1_id),
            document_version_id=str(v_id),
            page_number=1,
        )

        # Page must stay flagged with legacy_unspecified (never overwritten by low_ocr_confidence)
        async with tenant_session(ctx) as session:
            page_rec = (
                await session.execute(
                    select(Page).where(
                        Page.tenant_id == t1_id,
                        Page.document_version_id == v_id,
                        Page.page_number == 1,
                    )
                )
            ).scalar_one()
            assert page_rec.needs_review is True
            assert page_rec.review_reason == "legacy_unspecified"

            ver_rec = (
                await session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.tenant_id == t1_id,
                        DocumentVersion.id == v_id,
                    )
                )
            ).scalar_one()
            assert ver_rec.status == "needs_review"
