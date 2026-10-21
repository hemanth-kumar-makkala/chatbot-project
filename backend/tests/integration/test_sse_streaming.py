"""Integration tests for Task 3.6 SSE Streaming API and Conversations routes."""

import json
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.api.config import ApiSettings
from app.db.models import Assistant, Tenant
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


@pytest.fixture
def test_app(api_settings: ApiSettings):
    return create_app(settings=api_settings)


def parse_sse_events(raw_body: str) -> list[dict[str, str]]:
    """Parse raw SSE payload into structured events."""
    events = []
    blocks = raw_body.strip().split("\n\n")
    for block in blocks:
        if not block.strip():
            continue
        event_type = "message"
        data_lines = []
        for line in block.split("\n"):
            line = line.strip()
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        data_str = "\n".join(data_lines)
        events.append({"event": event_type, "data": data_str})
    return events


@pytest.mark.asyncio
async def test_sse_streaming_returns_event_stream_and_status_lines(
    test_app, api_settings, tmp_path
) -> None:
    """POST /v1/questions/stream streams status lines, answer, citations, and done event."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)

    async with throwaway_step2_environment():
        tenant_a = uuid4()
        asst_a_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_a, name="Clinic A"))
            await session.flush()

        staff_a, staff_email, password = await _create_user_with_role(tenant_a, "staff")
        ctx_a = TenantContext(
            tenant_id=str(tenant_a),
            user_id=str(staff_a.id),
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
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=True
        ) as client:
            cookies, csrf_token = await _login(client, staff_email, password)

            payload = {
                "assistant_id": str(asst_a_id),
                "question_text": "What are your business hours?",
            }
            resp = await client.post(
                "/v1/questions/stream",
                json=payload,
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

            events = parse_sse_events(resp.text)
            event_types = [e["event"] for e in events]
            assert "status" in event_types, f"Expected status events, got {event_types}"
            assert "done" in event_types, f"Expected done event, got {event_types}"

            # Check status event payload structure
            status_events = [e for e in events if e["event"] == "status"]
            first_status = json.loads(status_events[0]["data"])
            assert "status_line" in first_status

            # Check done event payload structure
            done_event = [e for e in events if e["event"] == "done"][0]
            done_data = json.loads(done_event["data"])
            assert "request_id" in done_data
            assert "conversation_id" in done_data
            assert "message_id" in done_data


@pytest.mark.asyncio
async def test_sse_streaming_error_emits_error_event(
    test_app, api_settings, tmp_path, monkeypatch
) -> None:
    """When an unhandled exception occurs in graph, emit SSE error event with public error ref."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)

    async with throwaway_step2_environment():
        tenant_a = uuid4()
        asst_a_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_a, name="Clinic A"))
            await session.flush()

        staff_a, staff_email, password = await _create_user_with_role(tenant_a, "staff")
        ctx_a = TenantContext(
            tenant_id=str(tenant_a),
            user_id=str(staff_a.id),
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
            await session.flush()

        from app.api.routes import questions

        class FailingGraph:
            async def astream(self, *args: Any, **kwargs: Any):  # noqa: ARG002
                raise RuntimeError("Simulated internal worker fault")
                yield {}

        monkeypatch.setattr(questions, "get_compiled_graph", lambda: FailingGraph())

        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=True
        ) as client:
            cookies, csrf_token = await _login(client, staff_email, password)

            payload = {
                "assistant_id": str(asst_a_id),
                "question_text": "Trigger error test",
            }
            resp = await client.post(
                "/v1/questions/stream",
                json=payload,
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert resp.status_code == 200

            events = parse_sse_events(resp.text)
            event_types = [e["event"] for e in events]
            assert "error" in event_types, f"Expected error event, got {event_types}"

            error_event = [e for e in events if e["event"] == "error"][0]
            err_data = json.loads(error_event["data"])
            assert "public_error_ref" in err_data
            assert err_data["public_error_ref"].startswith("ERR-")
            assert "Simulated internal worker fault" not in resp.text


@pytest.mark.asyncio
async def test_sse_streaming_cross_tenant_isolation(test_app, api_settings, tmp_path) -> None:
    """User in Tenant B cannot stream questions for Assistant in Tenant A."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)

    async with throwaway_step2_environment():
        tenant_a = uuid4()
        tenant_b = uuid4()
        asst_a_id = uuid4()

        async with unscoped_session() as session:
            session.add_all(
                [
                    Tenant(id=tenant_a, name="Clinic A"),
                    Tenant(id=tenant_b, name="Clinic B"),
                ]
            )
            await session.flush()

        _, email_b, pass_b = await _create_user_with_role(tenant_b, "staff")
        ctx_a = TenantContext(
            tenant_id=str(tenant_a),
            user_id="seed",
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
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=True
        ) as client:
            cookies, csrf_token = await _login(client, email_b, pass_b)

            payload = {
                "assistant_id": str(asst_a_id),
                "question_text": "What are your business hours?",
            }
            resp = await client.post(
                "/v1/questions/stream",
                json=payload,
                cookies=cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert resp.status_code in (403, 404)


@pytest.mark.asyncio
async def test_conversations_routes_crud_and_tenant_isolation(
    test_app, api_settings, tmp_path
) -> None:
    """Test conversation list, transcript messages, deletion, and cross-tenant isolation."""
    api_settings.profile = "mock"
    api_settings.storage_dir = str(tmp_path)

    async with throwaway_step2_environment():
        tenant_a = uuid4()
        tenant_b = uuid4()
        asst_a_id = uuid4()

        async with unscoped_session() as session:
            session.add_all(
                [
                    Tenant(id=tenant_a, name="Clinic A"),
                    Tenant(id=tenant_b, name="Clinic B"),
                ]
            )
            await session.flush()

        _, email_a, pass_a = await _create_user_with_role(tenant_a, "staff")
        _, email_b, pass_b = await _create_user_with_role(tenant_b, "staff")

        ctx_a = TenantContext(
            tenant_id=str(tenant_a),
            user_id="seed",
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
            await session.flush()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=True
        ) as client:
            cookies_a, csrf_a = await _login(client, email_a, pass_a)

            # 1. Ask a question via synchronous endpoint to create a conversation
            q_resp = await client.post(
                "/v1/questions",
                json={"assistant_id": str(asst_a_id), "question_text": "Hello assistant!"},
                cookies=cookies_a,
                headers={"X-CSRF-Token": csrf_a},
            )
            assert q_resp.status_code == 200
            q_data = q_resp.json()
            conv_id = q_data["conversation_id"]

            # 2. GET /v1/assistants/{id}/conversations lists this conversation
            list_resp = await client.get(
                f"/v1/assistants/{asst_a_id}/conversations",
                cookies=cookies_a,
            )
            assert list_resp.status_code == 200
            conversations = list_resp.json()
            assert len(conversations) >= 1
            conv_match = [c for c in conversations if c["id"] == conv_id]
            assert len(conv_match) == 1
            assert conv_match[0]["title"] == "Hello assistant!"

            # 3. GET /v1/conversations/{id}/messages returns the transcript
            msg_resp = await client.get(
                f"/v1/conversations/{conv_id}/messages",
                cookies=cookies_a,
            )
            assert msg_resp.status_code == 200
            messages = msg_resp.json()
            assert len(messages) >= 2  # user message and assistant message
            assert messages[0]["role"] == "user"
            assert messages[0]["content"] == "Hello assistant!"
            assert messages[1]["role"] == "assistant"

            # 4. Cross-tenant isolation: User B in Tenant B cannot view Tenant A conversation
            cookies_b, _ = await _login(client, email_b, pass_b)
            bad_msg_resp = await client.get(
                f"/v1/conversations/{conv_id}/messages",
                cookies=cookies_b,
            )
            assert bad_msg_resp.status_code in (403, 404)

            # 5. DELETE /v1/assistants/{id}/conversations clears conversations for assistant
            del_resp = await client.delete(
                f"/v1/assistants/{asst_a_id}/conversations",
                cookies=cookies_a,
                headers={"X-CSRF-Token": csrf_a},
            )
            assert del_resp.status_code == 200
            del_data = del_resp.json()
            assert del_data["ok"] is True
            assert del_data["deleted_count"] >= 1

            # Verify conversations list is now empty
            empty_list = await client.get(
                f"/v1/assistants/{asst_a_id}/conversations",
                cookies=cookies_a,
            )
            assert empty_list.status_code == 200
            assert len(empty_list.json()) == 0
