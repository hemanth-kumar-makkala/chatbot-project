"""Integration regression tests for assistant discovery, tenant membership, and /v1/questions.

Verifies:
1. Built-in cookie auth sessions (tenant_id="") resolve accessible assistants via
   GET /v1/assistants (and POST /v1/assistants for client_admin) despite RLS on memberships.
2. Platform admins without explicit tenant membership still receive anti-probing 404
   {"detail": "Assistant not found"} on POST /v1/questions (ADR-0007).
3. Clinic members (staff / client_admin) retrieve their assistant, submit genuine
   questions through POST /v1/questions, get grounded answers with citations/sources,
   persist conversations, and get 404 for genuinely missing resources.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.adapters.auth.builtin import BuiltinAuthProvider
from app.api.app import create_app
from app.api.config import ApiSettings
from app.db.models import Assistant, Chunk, Document, DocumentVersion, Page, Tenant, User
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


@pytest.mark.asyncio
async def test_builtin_cookie_assistant_discovery_and_question_e2e(
    api_settings: ApiSettings,
) -> None:
    """Cookie sessions discover authorized assistants, honor ADR-0007 404, and answer questions."""
    async with throwaway_step2_environment():
        tenant_a_id = UUID("00000000-0000-0000-0000-000000000001")
        assistant_a_id = UUID("00000000-0000-0000-0002-000000000001")
        auth = BuiltinAuthProvider()
        password = "SyntheticDevPassword123!"  # pragma: allowlist secret

        async with unscoped_session() as db:
            db.add(Tenant(id=tenant_a_id, name="Synthetic Client Clinic A"))
            db.add(
                User(
                    id=uuid4(),
                    email="platform-admin@dev.local",
                    hashed_password=auth.hash_password(password),
                    is_platform_admin=True,
                )
            )
            await db.flush()

        clinic_admin, admin_email, _ = await _create_user_with_role(
            tenant_a_id,
            "client_admin",
            email="admin@clinic-a.local",
            password=password,
        )
        _staff_user, staff_email, _ = await _create_user_with_role(
            tenant_a_id,
            "staff",
            email="staff@clinic-a.local",
            password=password,
        )

        ctx = TenantContext(
            tenant_id=str(tenant_a_id),
            user_id=str(clinic_admin.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed-setup",
        )
        async with tenant_session(ctx) as db:
            db.add(
                Assistant(
                    id=assistant_a_id,
                    tenant_id=tenant_a_id,
                    name="Clinic A Medical Assistant",
                    prompt_id="default",
                    prompt_version="1.0",
                )
            )
            doc_id, ver_id, page_id, chunk_id = uuid4(), uuid4(), uuid4(), uuid4()
            db.add(
                Document(
                    id=doc_id,
                    tenant_id=tenant_a_id,
                    title="prior_auth_guide.pdf",
                    is_retired=False,
                )
            )
            db.add(
                DocumentVersion(
                    id=ver_id,
                    tenant_id=tenant_a_id,
                    document_id=doc_id,
                    sha256="a" * 64,
                    uploaded_by=clinic_admin.id,
                    file_extension="pdf",
                    status="ready",
                )
            )
            await db.flush()
            doc = await db.get(Document, doc_id)
            assert doc is not None
            doc.active_version_id = ver_id
            db.add(
                Page(
                    id=page_id,
                    tenant_id=tenant_a_id,
                    document_version_id=ver_id,
                    page_number=1,
                    page_sha256="b" * 64,
                    page_type="digital_text",
                    image_key=f"{tenant_a_id}/page_1.png",
                )
            )
            await db.flush()
            db.add(
                Chunk(
                    id=chunk_id,
                    tenant_id=tenant_a_id,
                    document_version_id=ver_id,
                    page_id=page_id,
                    text=(
                        "Outpatient surgery prior authorization requires submission "
                        "at least 5 business days in advance."
                    ),
                    language="english",
                    ts_config="english",
                    evidence_type="text",
                    chunker_version="1.0",
                    embedding_model="bge-m3",
                    embedding_version="1.0",
                    parser_version="1.0",
                    is_active=True,
                )
            )

        app = create_app(settings=api_settings)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Platform admin without tenant membership sees [] and gets anti-probing 404
            plat_cookies, plat_csrf = await _login(client, "platform-admin@dev.local", password)
            r_plat_list = await client.get("/v1/assistants", cookies=plat_cookies)
            assert r_plat_list.status_code == 200
            assert r_plat_list.json() == []

            r_plat_q = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(assistant_a_id),
                    "question_text": "What are prior auth timelines?",
                    "conversation_id": None,
                },
                cookies=plat_cookies,
                headers={"X-CSRF-Token": plat_csrf},
            )
            assert r_plat_q.status_code == 404
            assert r_plat_q.json() == {"detail": "Assistant not found"}

            # 2. Clinic staff discovers Clinic A assistant and gets grounded answer + citations
            staff_cookies, staff_csrf = await _login(client, staff_email, password)
            r_staff_list = await client.get("/v1/assistants", cookies=staff_cookies)
            assert r_staff_list.status_code == 200
            staff_assistants = r_staff_list.json()
            assert len(staff_assistants) == 1
            assert staff_assistants[0]["id"] == str(assistant_a_id)
            assert staff_assistants[0]["name"] == "Clinic A Medical Assistant"
            assert staff_assistants[0]["document_count"] == 1

            r_staff_q = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(assistant_a_id),
                    "question_text": (
                        "How many business days in advance must outpatient surgery prior auths "
                        "be submitted?"
                    ),
                    "conversation_id": None,
                },
                cookies=staff_cookies,
                headers={"X-CSRF-Token": staff_csrf},
            )
            assert r_staff_q.status_code == 200
            q_data = r_staff_q.json()
            assert q_data["status"] == "answered"
            assert q_data["answer"]
            assert len(q_data["citations"]) >= 1
            assert q_data["sources"][0]["document_title"] == "prior_auth_guide.pdf"
            conv_id = q_data["conversation_id"]

            # 3. Conversation persisted and readable
            r_conv = await client.get(
                f"/v1/assistants/{assistant_a_id}/conversations",
                cookies=staff_cookies,
            )
            assert r_conv.status_code == 200
            conv_ids = [item["id"] for item in r_conv.json()]
            assert conv_id in conv_ids

            # 4. Genuinely missing assistant or conversation returns 404
            r_missing_asst = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(uuid4()),
                    "question_text": "Hello?",
                    "conversation_id": None,
                },
                cookies=staff_cookies,
                headers={"X-CSRF-Token": staff_csrf},
            )
            assert r_missing_asst.status_code == 404
            assert r_missing_asst.json() == {"detail": "Assistant not found"}

            r_missing_conv = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(assistant_a_id),
                    "question_text": "Hello?",
                    "conversation_id": str(uuid4()),
                },
                cookies=staff_cookies,
                headers={"X-CSRF-Token": staff_csrf},
            )
            assert r_missing_conv.status_code == 404
            assert r_missing_conv.json() == {"detail": "CONVERSATION_NOT_FOUND"}

            # 5. Client admin with cookie session can also create an assistant in their tenant
            admin_cookies, admin_csrf = await _login(client, admin_email, password)
            r_create = await client.post(
                "/v1/assistants",
                json={"name": "Clinic A Billing Assistant"},
                cookies=admin_cookies,
                headers={"X-CSRF-Token": admin_csrf},
            )
            assert r_create.status_code == 201
            assert r_create.json()["tenant_id"] == str(tenant_a_id)
            assert r_create.json()["name"] == "Clinic A Billing Assistant"
