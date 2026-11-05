"""Integration tests for Task 1: Multi-turn Follow-up Support and Session History.

Tests:
1. Multi-turn follow-up question rewrite using conversation history (Step 3.j context).
2. Clarifying question triggered when follow-up has no antecedent in history.
3. Strict cross-tenant isolation: Tenant B cannot access or leak Tenant A's conversation history (returns 404).
4. Strict cross-session isolation: Session 2 does not leak or use Session 1's history.
5. Two additional 3-turn follow-up chains proving multi-turn continuity.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.api.config import ApiSettings
from app.db.models import Assistant, Chunk, Conversation, Document, DocumentVersion, Page, Tenant
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
async def test_multiturn_followup_chains_and_clarification(test_app, api_settings, tmp_path: Path) -> None:
    """Test 3-turn follow-up chains, pronoun rewriting, and clarification without history."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)

    async with throwaway_step2_environment():
        tenant_id = uuid4()
        asst_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()
        page_id = uuid4()
        chunk_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Horizon Clinic"))
            await session.flush()

        user, email, password = await _create_user_with_role(tenant_id, "staff")
        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id=str(user.id),
            role=UserRole.STAFF,
            session_id="seed",
        )

        async with tenant_session(ctx) as session:
            session.add(
                Assistant(
                    id=asst_id,
                    tenant_id=tenant_id,
                    name="Horizon Staff Assistant",
                    prompt_id="default",
                    prompt_version="1.0",
                )
            )
            doc = Document(
                id=doc_id,
                tenant_id=tenant_id,
                title="prior_auth_guide.pdf",
                is_retired=False,
            )
            session.add(doc)
            ver = DocumentVersion(
                id=ver_id,
                tenant_id=tenant_id,
                document_id=doc_id,
                sha256="a" * 64,
                uploaded_by=user.id,
                file_extension="pdf",
                status="ready",
            )
            session.add(ver)
            await session.flush()
            doc.active_version_id = ver_id
            await session.flush()

            session.add(
                Page(
                    id=page_id,
                    tenant_id=tenant_id,
                    document_version_id=ver_id,
                    page_number=1,
                    page_sha256="d" * 64,
                    page_type="digital_text",
                    image_key=f"{tenant_id}/page_1.png",
                )
            )
            await session.flush()
            session.add(
                Chunk(
                    id=chunk_id,
                    tenant_id=tenant_id,
                    document_version_id=ver_id,
                    page_id=page_id,
                    text=(
                        "Outpatient Prior Authorization Submission Guide (Policy 2026-PA-01). "
                        "Prior authorization requests for outpatient surgery require submission at least 5 business days in advance. "
                        "Documentation Checklist: Complete clinical consultation notes within past 30 days. "
                        "Diagnostic imaging reports and relevant laboratory panels. "
                        "Proposed CPT procedure codes and ICD-10 diagnostic indications."
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
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cookies, csrf_token = await _login(client, email, password)
            headers = {"X-CSRF-Token": csrf_token}

            # 1. Follow-up Chain 1 (Step 3.j context: 3 turns)
            # Turn 1
            t1_resp = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(asst_id),
                    "question_text": "How many business days in advance must outpatient surgery prior auths be submitted?",
                },
                cookies=cookies,
                headers=headers,
            )
            assert t1_resp.status_code == 200
            t1_data = t1_resp.json()
            conv_id = t1_data["conversation_id"]
            assert t1_data["status"] == "answered"

            # Turn 2: Follow-up using 'it' (Step 3.j)
            t2_resp = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(asst_id),
                    "question_text": "and what documents are required with it?",
                    "conversation_id": conv_id,
                },
                cookies=cookies,
                headers=headers,
            )
            assert t2_resp.status_code == 200
            t2_data = t2_resp.json()
            assert t2_data["status"] == "answered"
            assert "rewritten_question" in t2_data
            assert "outpatient surgery prior authorizations" in t2_data["rewritten_question"].lower()
            assert "with it" not in t2_data["rewritten_question"].lower()

            # Turn 3: Follow-up using 'they'
            t3_resp = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(asst_id),
                    "question_text": "How recent must they be?",
                    "conversation_id": conv_id,
                },
                cookies=cookies,
                headers=headers,
            )
            assert t3_resp.status_code == 200
            t3_data = t3_resp.json()
            assert t3_data["status"] == "answered"
            assert "rewritten_question" in t3_data

            # 2. Clarifying question on unclear follow-up without history
            unclear_resp = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(asst_id),
                    "question_text": "and what documents are required with it?",
                    # No conversation_id or history: ambiguous antecedent
                },
                cookies=cookies,
                headers=headers,
            )
            assert unclear_resp.status_code == 200
            unclear_data = unclear_resp.json()
            assert unclear_data["status"] == "clarify"
            assert "clarify" in unclear_data["answer"].lower()

            # 3. Follow-up Chain 2 (Apex SLA context: 3 turns)
            # Turn 1
            chain2_t1 = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(asst_id),
                    "question_text": "What is the standard SLA for Apex Health Insurance diagnostic imaging?",
                },
                cookies=cookies,
                headers=headers,
            )
            assert chain2_t1.status_code == 200
            conv2_id = chain2_t1.json()["conversation_id"]

            # Turn 2
            chain2_t2 = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(asst_id),
                    "question_text": "what documentation is needed for that?",
                    "conversation_id": conv2_id,
                },
                cookies=cookies,
                headers=headers,
            )
            assert chain2_t2.status_code == 200
            c2_t2_data = chain2_t2.json()
            assert "apex health insurance diagnostic imaging" in c2_t2_data["rewritten_question"].lower()

            # Turn 3
            chain2_t3 = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(asst_id),
                    "question_text": "Does it require prior auth?",
                    "conversation_id": conv2_id,
                },
                cookies=cookies,
                headers=headers,
            )
            assert chain2_t3.status_code == 200
            c2_t3_data = chain2_t3.json()
            assert "apex health insurance diagnostic imaging" in c2_t3_data["rewritten_question"].lower()


@pytest.mark.asyncio
async def test_multiturn_cross_tenant_and_cross_session_isolation(test_app, api_settings, tmp_path: Path) -> None:
    """Prove history cannot leak across tenants or across independent sessions."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)

    async with throwaway_step2_environment():
        tenant_a = uuid4()
        tenant_b = uuid4()
        asst_a = uuid4()
        asst_b = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_a, name="Clinic A"))
            session.add(Tenant(id=tenant_b, name="Clinic B"))
            await session.flush()

        user_a, email_a, pass_a = await _create_user_with_role(tenant_a, "staff")
        user_b, email_b, pass_b = await _create_user_with_role(tenant_b, "staff")

        ctx_a = TenantContext(tenant_id=str(tenant_a), user_id=str(user_a.id), role=UserRole.STAFF, session_id="s_a")
        ctx_b = TenantContext(tenant_id=str(tenant_b), user_id=str(user_b.id), role=UserRole.STAFF, session_id="s_b")

        async with tenant_session(ctx_a) as session:
            session.add(
                Assistant(id=asst_a, tenant_id=tenant_a, name="Asst A", prompt_id="p1", prompt_version="1.0")
            )
            doc_a = Document(
                id=uuid4(),
                tenant_id=tenant_a,
                title="doc_a.pdf",
                is_retired=False,
            )
            session.add(doc_a)
            ver_a = DocumentVersion(
                id=uuid4(),
                tenant_id=tenant_a,
                document_id=doc_a.id,
                sha256="b" * 64,
                uploaded_by=user_a.id,
                file_extension="pdf",
                status="ready",
            )
            session.add(ver_a)
            await session.flush()
            doc_a.active_version_id = ver_a.id
            await session.flush()

        async with tenant_session(ctx_b) as session:
            session.add(
                Assistant(id=asst_b, tenant_id=tenant_b, name="Asst B", prompt_id="p1", prompt_version="1.0")
            )
            doc_b = Document(
                id=uuid4(),
                tenant_id=tenant_b,
                title="doc_b.pdf",
                is_retired=False,
            )
            session.add(doc_b)
            ver_b = DocumentVersion(
                id=uuid4(),
                tenant_id=tenant_b,
                document_id=doc_b.id,
                sha256="c" * 64,
                uploaded_by=user_b.id,
                file_extension="pdf",
                status="ready",
            )
            session.add(ver_b)
            await session.flush()
            doc_b.active_version_id = ver_b.id
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # User A logs in and creates a conversation
            cookies_a, csrf_a = await _login(client, email_a, pass_a)
            resp_a = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(asst_a),
                    "question_text": "How many business days in advance must outpatient surgery prior auths be submitted?",
                },
                cookies=cookies_a,
                headers={"X-CSRF-Token": csrf_a},
            )
            assert resp_a.status_code == 200
            conv_a_id = resp_a.json()["conversation_id"]

            # User B logs in
            cookies_b, csrf_b = await _login(client, email_b, pass_b)

            # Attack 1: User B tries to use User A's conversation_id in Tenant B assistant
            probe_b = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(asst_b),
                    "question_text": "and what documents are required with it?",
                    "conversation_id": conv_a_id,
                },
                cookies=cookies_b,
                headers={"X-CSRF-Token": csrf_b},
            )
            # RLS ensures conversation_id from another tenant returns 404 (zero leakage)
            assert probe_b.status_code == 404
            assert probe_b.json()["detail"] == "CONVERSATION_NOT_FOUND"

            # Attack 2: Cross-session isolation within Tenant A
            # User A creates a fresh independent session (Session 2) and asks a follow-up
            fresh_session_resp = await client.post(
                "/v1/questions",
                json={
                    "assistant_id": str(asst_a),
                    "question_text": "and what documents are required with it?",
                    # No conversation_id: fresh session
                },
                cookies=cookies_a,
                headers={"X-CSRF-Token": csrf_a},
            )
            assert fresh_session_resp.status_code == 200
            # Session 2 does NOT inherit Session 1's history; triggers clarifying question
            assert fresh_session_resp.json()["status"] == "clarify"
            assert "clarify" in fresh_session_resp.json()["answer"].lower()
