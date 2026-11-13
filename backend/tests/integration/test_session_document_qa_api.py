"""Integration tests for Stage J: Cross-conversation and cross-tenant isolation.

Verifies:
7. Cross-conversation isolation: another conversation cannot query the attached document.
8. Cross-tenant isolation: another tenant cannot query or access the attached document.
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.api.config import ApiSettings
from app.db.models import Assistant, Conversation, Tenant
from app.db.session import tenant_session, unscoped_session
from app.ports.types import TenantContext, UserRole
from tests.integration.test_assistant_access_and_questions_api import (
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
async def test_cross_conversation_and_cross_tenant_session_document_isolation(
    test_app, api_settings, tmp_path: Path
) -> None:
    """Attached session document is queryable ONLY in its own conversation and tenant."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)
    api_settings.session_upload_retention_hours = 24

    async with throwaway_step2_environment():
        tenant_a = uuid4()
        tenant_b = uuid4()
        asst_a_id = uuid4()
        conv_1_id = uuid4()
        conv_2_id = uuid4()

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
            session_id="seed-a",
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
                    id=conv_1_id,
                    tenant_id=tenant_a,
                    user_id=user_a.id,
                    assistant_id=asst_a_id,
                    title="Conversation 1 With Attached File",
                )
            )
            session.add(
                Conversation(
                    id=conv_2_id,
                    tenant_id=tenant_a,
                    user_id=user_a.id,
                    assistant_id=asst_a_id,
                    title="Conversation 2 Without File",
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

            # 1. Tenant A attaches file to Conversation 1
            files = {
                "file": (
                    "skewed_fax.pdf",
                    io.BytesIO(pdf_bytes),
                    "application/pdf",
                )
            }
            client.cookies.clear()
            for k, v in cookies_a.items():
                client.cookies.set(k, v)

            upload_resp = await client.post(
                f"/v1/assistants/{asst_a_id}/conversations/{conv_1_id}/documents",
                headers={"X-CSRF-Token": csrf_a},
                files=files,
            )
            assert upload_resp.status_code == 200, upload_resp.text
            up_data = upload_resp.json()
            doc_id = up_data["session_document_id"]
            assert doc_id

            # 2. Cross-conversation: Conversation 2 in Tenant A asks question referencing the file
            # Conversation 2 has no attached file, so it cannot access Conversation 1's file
            q2_resp = await client.post(
                "/v1/questions",
                headers={"X-CSRF-Token": csrf_a},
                json={
                    "assistant_id": str(asst_a_id),
                    "conversation_id": str(conv_2_id),
                    "question_text": "what is the patient name on this document?",
                },
            )
            assert q2_resp.status_code == 200
            q2_data = q2_resp.json()
            # Conversation 2 must NOT route to session_document or cite Conversation 1's doc
            assert q2_data.get("route") != "session_document"
            for src in q2_data.get("sources", []):
                assert src.get("document_title") != "skewed_fax.pdf"

            # 3. Cross-tenant: Tenant B attempts to ask a question referencing Conversation 1
            client.cookies.clear()
            for k, v in cookies_b.items():
                client.cookies.set(k, v)

            b_q_resp = await client.post(
                "/v1/questions",
                headers={"X-CSRF-Token": csrf_b},
                json={
                    "assistant_id": str(asst_a_id),
                    "conversation_id": str(conv_1_id),
                    "question_text": "what is the patient name on this document?",
                },
            )
            # Under RLS / anti-probing, Tenant B cannot access Tenant A's conversation
            assert b_q_resp.status_code == 404


@pytest.mark.asyncio
async def test_upload_session_document_and_ask_question_in_same_conversation(
    test_app, api_settings, tmp_path: Path
) -> None:
    """Stage Q: Upload session document, ask in same conversation, verify route & citation."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)
    api_settings.session_upload_retention_hours = 24

    async with throwaway_step2_environment():
        tenant_id = uuid4()
        asst_id = uuid4()
        conv_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Clinic Session QA"))
            await session.flush()

        user, email, password = await _create_user_with_role(tenant_id, "staff")
        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id=str(user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed-qa",
        )
        async with tenant_session(ctx) as session:
            session.add(
                Assistant(
                    id=asst_id,
                    tenant_id=tenant_id,
                    name="Session QA Assistant",
                    prompt_id="answer_synthesis",
                    prompt_version="1.0.0",
                )
            )
            session.add(
                Conversation(
                    id=conv_id,
                    tenant_id=tenant_id,
                    user_id=user.id,
                    assistant_id=asst_id,
                    title="Session Document Chat",
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
            cookies, csrf = await _login(client, email, password)
            client.cookies.clear()
            for k, v in cookies.items():
                client.cookies.set(k, v)

            # 1. Upload document into conversation
            files = {
                "file": (
                    "skewed_fax.pdf",
                    io.BytesIO(pdf_bytes),
                    "application/pdf",
                )
            }
            up_resp = await client.post(
                f"/v1/assistants/{asst_id}/conversations/{conv_id}/documents",
                headers={"X-CSRF-Token": csrf},
                files=files,
            )
            assert up_resp.status_code == 200, up_resp.text
            up_data = up_resp.json()
            assert up_data["conversation_id"] == str(conv_id)

            # 2. Ask question in that same conversation
            q_resp = await client.post(
                "/v1/questions",
                headers={"X-CSRF-Token": csrf},
                json={
                    "assistant_id": str(asst_id),
                    "conversation_id": str(conv_id),
                    "question_text": "what is the dosage in this fax?",
                },
            )
            assert q_resp.status_code == 200, q_resp.text
            q_data = q_resp.json()
            assert q_data["route"] == "session_document"
            sources = q_data.get("sources", [])
            assert len(sources) >= 1
            assert sources[0]["document_title"] == "skewed_fax.pdf"
            assert sources[0]["page_number"] == 1
