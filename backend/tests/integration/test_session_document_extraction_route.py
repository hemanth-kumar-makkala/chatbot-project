"""Integration tests for Task 4.6 session document chat upload and extraction.

Enforces:
- Attaching a session fax creates a SessionDocument in database under
  tenant and conversation scope (Task 4.3; Invariant 7).
- Chunks in knowledge index are strictly 0 (Invariant 7).
- Runs extraction subgraph and returns extracted fields with explicit field states (Invariant 6).
- Page image endpoint streams valid PNG image of the document page.
- Tenant isolation and typed error handling.
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.app import create_app
from app.api.config import ApiSettings
from app.db.models import Assistant, Chunk, Conversation, ExtractionResult, SessionDocument, Tenant
from app.db.session import tenant_session, unscoped_session
from app.ports.types import TenantContext, UserRole
from tests.integration.test_api_knowledge_step2 import (
    _create_user_with_role,
    _login,
    throwaway_step2_environment,
)


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
        session_upload_retention_hours=24,
    )


@pytest.fixture
def test_app(api_settings: ApiSettings):
    return create_app(settings=api_settings)


@pytest.mark.asyncio
async def test_session_document_upload_and_extraction_route(
    test_app, api_settings, tmp_path: Path
) -> None:
    """Test POST .../documents creates session document and runs extraction."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)
    api_settings.session_upload_retention_hours = 24

    async with throwaway_step2_environment():
        tenant_a = uuid4()
        tenant_b = uuid4()
        asst_a_id = uuid4()
        conv_id = uuid4()

        async with unscoped_session() as session:
            session.add_all(
                [
                    Tenant(id=tenant_a, name="Clinic A"),
                    Tenant(id=tenant_b, name="Clinic B"),
                ]
            )
            await session.flush()

        user_a, email_a, pass_a = await _create_user_with_role(tenant_a, "staff")
        user_b, email_b, pass_b = await _create_user_with_role(tenant_b, "staff")

        ctx_a = TenantContext(
            tenant_id=str(tenant_a),
            user_id=str(user_a.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx_a) as session:
            session.add(
                Assistant(
                    id=asst_a_id,
                    tenant_id=tenant_a,
                    name="Clinic A Assistant",
                    prompt_id="answer_synthesis",
                    prompt_version="1.0.0",
                )
            )
            session.add(
                Conversation(
                    id=conv_id,
                    tenant_id=tenant_a,
                    user_id=user_a.id,
                    assistant_id=asst_a_id,
                    title="Extraction Test Conversation",
                )
            )
            await session.flush()

        repo_root = Path(__file__).resolve().parents[3]
        sample_pdf_path = repo_root / "evals" / "golden" / "docs" / "skewed_fax.pdf"
        assert sample_pdf_path.exists(), f"{sample_pdf_path} must exist"
        pdf_bytes = sample_pdf_path.read_bytes()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=True
        ) as client:
            cookies_a, csrf_a = await _login(client, email_a, pass_a)
            cookies_b, csrf_b = await _login(client, email_b, pass_b)

            # 1. Upload valid fax document
            files = {"file": ("skewed_fax.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
            upload_resp = await client.post(
                f"/v1/assistants/{asst_a_id}/conversations/{conv_id}/documents",
                files=files,
                cookies=cookies_a,
                headers={"X-CSRF-Token": csrf_a},
            )

            assert upload_resp.status_code == 200, f"Upload failed: {upload_resp.text}"
            data = upload_resp.json()

            assert "session_document_id" in data
            assert data["conversation_id"] == str(conv_id)
            assert data["file_name"] == "skewed_fax.pdf"
            assert data["page_count"] >= 1
            assert data["status"] == "extracted"
            assert "extracted_fields" in data
            assert len(data["extracted_fields"]) > 0

            doc_id = data["session_document_id"]
            fields = {f["name"]: f for f in data["extracted_fields"]}
            assert "patient_name" in fields
            assert fields["patient_name"]["state"] in ("FILLED_HIGH", "FILLED_MEDIUM")

            # Invariant 6: UNREADABLE fields must have value=None / null
            for f in data["extracted_fields"]:
                if f["state"] == "UNREADABLE":
                    assert f["value"] is None

            # 2. Verify Invariant 7: Exists in session_documents, 0 chunks in index
            async with tenant_session(ctx_a) as db:
                s_doc_cnt = (
                    await db.execute(
                        select(func.count(SessionDocument.id)).where(
                            SessionDocument.id == UUID(doc_id)
                        )
                    )
                ).scalar()
                assert s_doc_cnt == 1

                ext_cnt = (
                    await db.execute(
                        select(func.count(ExtractionResult.id)).where(
                            ExtractionResult.session_document_id == UUID(doc_id)
                        )
                    )
                ).scalar()
                assert ext_cnt == 1

                # Invariant 7: Chunks table must have 0 chunks for session doc
                chunks_cnt = (await db.execute(select(func.count(Chunk.id)))).scalar()
                assert chunks_cnt == 0, (
                    f"Invariant 7 violation: session upload produced {chunks_cnt} chunks"
                )

            # 3. Stream page image
            img_url = (
                f"/v1/assistants/{asst_a_id}/conversations/{conv_id}"
                f"/documents/{doc_id}/pages/1/image"
            )
            img_resp = await client.get(img_url, cookies=cookies_a)
            assert img_resp.status_code == 200
            assert img_resp.headers["content-type"] == "image/png"
            assert img_resp.content.startswith(b"\x89PNG\r\n\x1a\n")

            # 4. Cross-tenant isolation: Tenant B cannot access Tenant A image
            b_img_resp = await client.get(img_url, cookies=cookies_b)
            assert b_img_resp.status_code in (403, 404)

            # 5. Invalid file type rejection
            bad_files = {
                "file": (
                    "malicious.exe",
                    io.BytesIO(b"MZ\x90\x00"),
                    "application/x-dosexec",
                )
            }
            bad_resp = await client.post(
                f"/v1/assistants/{asst_a_id}/conversations/{conv_id}/documents",
                files=bad_files,
                cookies=cookies_a,
                headers={"X-CSRF-Token": csrf_a},
            )
            assert bad_resp.status_code == 400
            assert "INVALID_FILE_TYPE" in bad_resp.text

            # 6. Content sniffing rejection: plain text disguised as .pdf
            sniff_fail_files = {
                "file": (
                    "disguised.pdf",
                    io.BytesIO(b"This is not a real PDF file at all, just plain text."),
                    "application/pdf",
                )
            }
            sniff_resp = await client.post(
                f"/v1/assistants/{asst_a_id}/conversations/{conv_id}/documents",
                files=sniff_fail_files,
                cookies=cookies_a,
                headers={"X-CSRF-Token": csrf_a},
            )
            assert sniff_resp.status_code == 400
            assert "INVALID_FILE_TYPE" in sniff_resp.text

            # 7. Oversize file rejection: exceeds max_upload_bytes
            oversize_content = b"%PDF-1.4\n" + b"A" * (api_settings.max_upload_bytes + 1024)
            oversize_files = {
                "file": (
                    "oversize.pdf",
                    io.BytesIO(oversize_content),
                    "application/pdf",
                )
            }
            oversize_resp = await client.post(
                f"/v1/assistants/{asst_a_id}/conversations/{conv_id}/documents",
                files=oversize_files,
                cookies=cookies_a,
                headers={"X-CSRF-Token": csrf_a},
            )
            assert oversize_resp.status_code == 413
            assert "FILE_TOO_LARGE" in oversize_resp.text

            # 8. Another tenant's conversation gets 404
            cross_tenant_resp = await client.post(
                f"/v1/assistants/{asst_a_id}/conversations/{conv_id}/documents",
                files=files,
                cookies=cookies_b,
                headers={"X-CSRF-Token": csrf_b},
            )
            assert cross_tenant_resp.status_code == 404
