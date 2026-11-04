"""Integration tests for Task 5.5 Feedback API.

Tests:
1. Staff user can submit thumbs up/down rating with optional reason, persisting to feedback table.
2. Second rating by the same user on the same message updates the existing record.
3. Reason exceeding MAX_FEEDBACK_REASON_LENGTH returns 422.
4. Accessing another tenant's message returns 404.
5. Staff of another assistant/tenant cannot rate the message (returns 404).
6. CSRF token is strictly required for state-changing feedback POST requests.
7. Reason text never goes to operational logs or audit events (Invariant 9, 10).
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.app import create_app
from app.api.config import ApiSettings
from app.api.routes.feedback import MAX_FEEDBACK_REASON_LENGTH
from app.db.models import Assistant, AuditEvent, Conversation, Feedback, Message, Tenant
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
async def test_feedback_submission_persistence_and_update(
    test_app, api_settings, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Test thumbs up/down persists, links to message_id, and second rating updates first."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)

    async with throwaway_step2_environment():
        tenant_a = uuid4()
        asst_a_id = uuid4()
        conv_a_id = uuid4()
        msg_a_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_a, name="Clinic A"))
            await session.flush()

        user_a, email_a, pass_a = await _create_user_with_role(tenant_a, "staff")

        ctx_a = TenantContext(
            tenant_id=str(tenant_a),
            user_id=str(user_a.id),
            role=UserRole.STAFF,
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
                    id=conv_a_id,
                    tenant_id=tenant_a,
                    user_id=user_a.id,
                    assistant_id=asst_a_id,
                    title="Test Chat",
                )
            )
            session.add(
                Message(
                    id=msg_a_id,
                    tenant_id=tenant_a,
                    conversation_id=conv_a_id,
                    role="assistant",
                    content="Validated evidence-based answer.",
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Origin": "http://localhost:3000"},
        ) as client:
            cookies, csrf_token = await _login(client, email_a, pass_a)

            # 1. First submission: Thumbs up without reason
            caplog.set_level(logging.INFO)
            resp1 = await client.post(
                f"/v1/assistants/{asst_a_id}/messages/{msg_a_id}/feedback",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
                json={"rating": "up"},
            )
            assert resp1.status_code == 200, resp1.text
            data1 = resp1.json()
            assert data1["message_id"] == str(msg_a_id)
            assert data1["rating"] == "up"
            assert data1["reason"] is None
            feedback_id = UUID(data1["id"])

            # Verify in database
            async with tenant_session(ctx_a) as session:
                fb_row = (
                    await session.execute(
                        select(Feedback).where(
                            Feedback.tenant_id == tenant_a,
                            Feedback.message_id == msg_a_id,
                            Feedback.user_id == user_a.id,
                        )
                    )
                ).scalar_one()
                assert fb_row.id == feedback_id
                assert fb_row.rating == "up"
                assert fb_row.reason is None

            # 2. Second submission: Update same message to thumbs down with reason
            secret_reason = (  # pragma: allowlist secret
                "Confidential Reason: Policy page 4 is newer than page 2."
            )
            resp2 = await client.post(
                f"/v1/assistants/{asst_a_id}/messages/{msg_a_id}/feedback",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
                json={"rating": "down", "reason": secret_reason},
            )
            assert resp2.status_code == 200, resp2.text
            data2 = resp2.json()
            assert data2["id"] == str(feedback_id), "Second rating must update the existing record"
            assert data2["rating"] == "down"
            assert data2["reason"] == secret_reason

            # Verify in database: still exactly 1 record, updated
            async with tenant_session(ctx_a) as session:
                all_fb = (
                    (
                        await session.execute(
                            select(Feedback).where(
                                Feedback.tenant_id == tenant_a,
                                Feedback.message_id == msg_a_id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(all_fb) == 1
                assert all_fb[0].rating == "down"
                assert all_fb[0].reason == secret_reason

            # 3. Verify reason text NEVER appears in logs or audit events
            for record in caplog.records:
                assert secret_reason not in record.getMessage()

            async with tenant_session(ctx_a) as session:
                audit_rows = (
                    (
                        await session.execute(
                            select(AuditEvent).where(
                                AuditEvent.tenant_id == tenant_a,
                                AuditEvent.event_type == "feedback",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for audit in audit_rows:
                    assert secret_reason not in (audit.resource_id or "")
                    assert secret_reason not in audit.action


@pytest.mark.asyncio
async def test_feedback_cross_tenant_isolation_returns_404(
    test_app, api_settings, tmp_path: Path
) -> None:
    """Test accessing another tenant's message returns 404, preventing probing."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)

    async with throwaway_step2_environment():
        tenant_a = uuid4()
        tenant_b = uuid4()
        asst_a_id = uuid4()
        asst_b_id = uuid4()
        conv_a_id = uuid4()
        msg_a_id = uuid4()

        async with unscoped_session() as session:
            session.add_all(
                [
                    Tenant(id=tenant_a, name="Clinic A"),
                    Tenant(id=tenant_b, name="Clinic B"),
                ]
            )
            await session.flush()

        user_a, _, _ = await _create_user_with_role(tenant_a, "staff")
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
                    id=conv_a_id,
                    tenant_id=tenant_a,
                    user_id=user_a.id,
                    assistant_id=asst_a_id,
                    title="Clinic A Chat",
                )
            )
            session.add(
                Message(
                    id=msg_a_id,
                    tenant_id=tenant_a,
                    conversation_id=conv_a_id,
                    role="assistant",
                    content="Clinic A confidential answer.",
                )
            )
            await session.flush()

        ctx_b = TenantContext(
            tenant_id=str(tenant_b),
            user_id=str(user_b.id),
            role=UserRole.STAFF,
            session_id="seed-b",
        )
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

        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Origin": "http://localhost:3000"},
        ) as client:
            cookies_b, csrf_b = await _login(client, email_b, pass_b)

            # Probe 1: Staff B targets Assistant A with Message A -> returns 404 (not in Tenant A)
            resp1 = await client.post(
                f"/v1/assistants/{asst_a_id}/messages/{msg_a_id}/feedback",
                cookies=cookies_b,
                headers={"X-CSRF-Token": csrf_b},
                json={"rating": "up"},
            )
            assert resp1.status_code == 404

            # Probe 2: Staff B targets Assistant B with Message A -> returns 404
            resp2 = await client.post(
                f"/v1/assistants/{asst_b_id}/messages/{msg_a_id}/feedback",
                cookies=cookies_b,
                headers={"X-CSRF-Token": csrf_b},
                json={"rating": "down", "reason": "Attempting cross tenant rating"},
            )
            assert resp2.status_code == 404

            # Probe 3: Direct route without assistant_id for another tenant's message -> returns 404
            resp3 = await client.post(
                f"/v1/messages/{msg_a_id}/feedback",
                cookies=cookies_b,
                headers={"X-CSRF-Token": csrf_b},
                json={"rating": "up"},
            )
            assert resp3.status_code == 404


@pytest.mark.asyncio
async def test_feedback_validation_and_csrf_enforcement(
    test_app, api_settings, tmp_path: Path
) -> None:
    """Test reason length limit and CSRF token requirement."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)

    async with throwaway_step2_environment():
        tenant_a = uuid4()
        asst_a_id = uuid4()
        conv_a_id = uuid4()
        msg_a_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_a, name="Clinic A"))
            await session.flush()

        user_a, email_a, pass_a = await _create_user_with_role(tenant_a, "staff")

        ctx_a = TenantContext(
            tenant_id=str(tenant_a),
            user_id=str(user_a.id),
            role=UserRole.STAFF,
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
                    id=conv_a_id,
                    tenant_id=tenant_a,
                    user_id=user_a.id,
                    assistant_id=asst_a_id,
                    title="Test Chat",
                )
            )
            session.add(
                Message(
                    id=msg_a_id,
                    tenant_id=tenant_a,
                    conversation_id=conv_a_id,
                    role="assistant",
                    content="Sample message",
                )
            )
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Origin": "http://localhost:3000"},
        ) as client:
            cookies, csrf_token = await _login(client, email_a, pass_a)

            # 1. CSRF missing -> 403 Forbidden
            resp_no_csrf = await client.post(
                f"/v1/assistants/{asst_a_id}/messages/{msg_a_id}/feedback",
                cookies=cookies,
                json={"rating": "up"},
            )
            assert resp_no_csrf.status_code == 403

            # 2. Reason exceeds MAX_FEEDBACK_REASON_LENGTH -> 422
            overlong_reason = "X" * (MAX_FEEDBACK_REASON_LENGTH + 1)
            resp_too_long = await client.post(
                f"/v1/assistants/{asst_a_id}/messages/{msg_a_id}/feedback",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
                json={"rating": "down", "reason": overlong_reason},
            )
            assert resp_too_long.status_code == 422

            # 3. Invalid rating enum -> 422
            resp_invalid_rating = await client.post(
                f"/v1/assistants/{asst_a_id}/messages/{msg_a_id}/feedback",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
                json={"rating": "neutral"},
            )
            assert resp_invalid_rating.status_code == 422
