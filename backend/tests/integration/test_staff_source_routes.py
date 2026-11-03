"""Integration tests for staff-allowed document file streaming
and page thumbnail routes (Part F).
"""

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.api.config import ApiSettings
from app.db.models import Assistant, Document, DocumentVersion, Page, Tenant
from app.db.session import tenant_session, unscoped_session
from app.ports.storage import StorageKey
from app.ports.types import TenantContext, UserRole
from app.wiring import create_storage_backend
from tests.integration.test_api_knowledge_step2 import (
    _create_user_with_role,
    _login,
    throwaway_step2_environment,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def api_settings(tmp_path) -> ApiSettings:
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


@pytest.mark.asyncio
async def test_staff_document_file_and_thumbnail_routes(test_app, api_settings, tmp_path) -> None:
    """Verify staff can access original document file and page thumbnail
    with strict tenant isolation.
    """
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)
    storage = create_storage_backend(profile="mock", base_dir=str(tmp_path))

    async with throwaway_step2_environment():
        tenant_a = uuid4()
        tenant_b = uuid4()

        async with unscoped_session() as session:
            session.add_all(
                [
                    Tenant(id=tenant_a, name="Clinic A"),
                    Tenant(id=tenant_b, name="Clinic B"),
                ]
            )
            await session.flush()

        # Create staff user in Tenant A and client_admin in Tenant B
        staff_a, staff_a_email, password_a = await _create_user_with_role(tenant_a, "staff")
        admin_b, admin_b_email, password_b = await _create_user_with_role(tenant_b, "client_admin")

        asst_a_id = uuid4()
        asst_b_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()
        page_id = uuid4()

        ctx_a = TenantContext(
            tenant_id=str(tenant_a),
            user_id="seed",
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        ctx_b = TenantContext(
            tenant_id=str(tenant_b),
            user_id="seed",
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )

        async with tenant_session(ctx_a) as session:
            session.add_all(
                [
                    Assistant(
                        id=asst_a_id,
                        tenant_id=tenant_a,
                        name="Clinic A Assistant",
                        prompt_id="answer_synthesis",
                        prompt_version="1.0.0",
                    ),
                    Document(
                        id=doc_id, tenant_id=tenant_a, title="Protocols.pdf", is_retired=False
                    ),
                    DocumentVersion(
                        id=ver_id,
                        tenant_id=tenant_a,
                        document_id=doc_id,
                        sha256="dummy-sha",
                        uploaded_by=staff_a.id,
                        status="ready",
                        file_extension="pdf",
                    ),
                    Page(
                        id=page_id,
                        tenant_id=tenant_a,
                        document_version_id=ver_id,
                        page_number=1,
                        page_sha256="page1sha",
                        page_type="digital_text",
                        image_key=f"{tenant_a}/pages/{ver_id}/page_1.png",
                        needs_review=False,
                        is_excluded=False,
                    ),
                ]
            )
            await session.flush()

        async with tenant_session(ctx_b) as session:
            session.add(
                Assistant(
                    id=asst_b_id,
                    tenant_id=tenant_b,
                    name="Clinic B Assistant",
                    prompt_id="answer_synthesis",
                    prompt_version="1.0.0",
                )
            )
            await session.flush()

        # Seed files into storage
        dummy_pdf = b"%PDF-1.4 test document content"
        dummy_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        pdf_key = StorageKey(tenant_id=tenant_a, path=f"documents/{ver_id}/original.pdf")
        img_key = StorageKey(tenant_id=tenant_a, path=f"pages/{ver_id}/page_1.png")
        await storage.put(pdf_key, dummy_pdf, content_type="application/pdf")
        await storage.put(img_key, dummy_png, content_type="image/png")

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Login as Tenant A Staff
            cookies_a, _ = await _login(client, staff_a_email, password_a)

            # 2. Staff streams document PDF -> 200
            file_resp = await client.get(
                f"/v1/assistants/{asst_a_id}/knowledge/documents/{ver_id}/file",
                cookies=cookies_a,
            )
            assert file_resp.status_code == 200
            assert file_resp.content == dummy_pdf
            assert file_resp.headers["content-type"] == "application/pdf"
            assert file_resp.headers["cache-control"] == "no-store"

            # 3. Staff views page thumbnail -> 200
            thumb_resp = await client.get(
                f"/v1/assistants/{asst_a_id}/knowledge/documents/{ver_id}/pages/1/thumbnail",
                cookies=cookies_a,
            )
            assert thumb_resp.status_code == 200
            assert thumb_resp.content == dummy_png
            assert thumb_resp.headers["content-type"] == "image/png"
            assert thumb_resp.headers["cache-control"] == "no-store"

            # 4. Login as Tenant B Admin
            cookies_b, _ = await _login(client, admin_b_email, password_b)

            # 5. Tenant B user gets 404 when requesting Tenant A's document file
            b_file = await client.get(
                f"/v1/assistants/{asst_a_id}/knowledge/documents/{ver_id}/file",
                cookies=cookies_b,
            )
            assert b_file.status_code == 404

            # 6. Tenant B user gets 404 when requesting Tenant A's thumbnail
            b_thumb = await client.get(
                f"/v1/assistants/{asst_a_id}/knowledge/documents/{ver_id}/pages/1/thumbnail",
                cookies=cookies_b,
            )
            assert b_thumb.status_code == 404
