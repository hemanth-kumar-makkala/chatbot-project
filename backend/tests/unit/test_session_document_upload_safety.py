"""Unit tests for chat session document upload safety validation (Phase 4 Step 3).

Verifies:
1. Content-based type check: wrong file type or magic byte mismatch rejected with 400.
2. File size limit: oversized file rejected with 413.
3. Tenant scope: accessing another tenant's conversation returns 404.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.config import ApiSettings
from app.api.routes.conversations import upload_session_document
from app.ports.types import SessionContext, TenantContext, UserRole


def _create_mock_request(content: bytes, filename: str) -> Request:
    """Create a mock Starlette Request with binary body and filename headers."""
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [
            (b"content-type", b"application/pdf"),
            (b"x-filename", filename.encode("utf-8")),
        ],
    }

    async def receive():
        return {"type": "http.request", "body": content, "more_body": False}

    return Request(scope, receive)


@pytest.fixture
def mock_settings(tmp_path) -> ApiSettings:
    return ApiSettings(
        cors_origins=["http://localhost:3000"],
        cookie_secure=False,
        cookie_samesite="lax",
        idle_timeout_minutes=30,
        absolute_timeout_hours=24,
        allow_bearer_auth=False,
        max_upload_bytes=1024 * 1024,  # 1 MB
        profile="mock",
        storage_dir=str(tmp_path),
        session_upload_retention_hours=24,
    )


@pytest.fixture
def session_ctx() -> SessionContext:
    return SessionContext(
        session_id="test_session",
        user_id="user_123",
        tenant_id=str(uuid4()),
        roles=[UserRole.STAFF],
    )


@pytest.mark.asyncio
async def test_wrong_file_type_rejected_by_content_sniffing(
    mock_settings: ApiSettings, session_ctx: SessionContext
) -> None:
    """Disguised plain text claiming to be a PDF is rejected with 400 INVALID_FILE_TYPE."""
    conv_id = str(uuid4())
    fake_pdf_content = b"This is plain text with no PDF header at all."
    req = _create_mock_request(fake_pdf_content, "disguised.pdf")

    mock_auth = MagicMock()
    mock_auth.roles = AsyncMock(return_value=[UserRole.STAFF])

    with (
        patch(
            "app.api.routes.conversations.get_tenant_context_for_assistant",
            new=AsyncMock(
                return_value=TenantContext(
                    tenant_id=str(uuid4()),
                    user_id=session_ctx.user_id,
                    role=UserRole.STAFF,
                    session_id=session_ctx.session_id,
                )
            ),
        ),
        patch("app.api.routes.conversations.tenant_session") as mock_ts,
    ):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()
        mock_db.execute.return_value = mock_result
        mock_ts.return_value.__aenter__.return_value = mock_db

        with pytest.raises(HTTPException) as exc_info:
            await upload_session_document(
                conversation_id=conv_id,
                request=req,
                session=session_ctx,
                auth_provider=mock_auth,
                settings=mock_settings,
                assistant_id=str(uuid4()),
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "INVALID_FILE_TYPE"


@pytest.mark.asyncio
async def test_oversize_upload_rejected_with_413(
    mock_settings: ApiSettings, session_ctx: SessionContext
) -> None:
    """Uploads exceeding max_upload_bytes are rejected with 413 FILE_TOO_LARGE."""
    conv_id = str(uuid4())
    large_content = b"%PDF-1.4\n" + b"X" * (mock_settings.max_upload_bytes + 500)
    req = _create_mock_request(large_content, "large.pdf")

    mock_auth = MagicMock()
    mock_auth.roles = AsyncMock(return_value=[UserRole.STAFF])

    with (
        patch(
            "app.api.routes.conversations.get_tenant_context_for_assistant",
            new=AsyncMock(
                return_value=TenantContext(
                    tenant_id=str(uuid4()),
                    user_id=session_ctx.user_id,
                    role=UserRole.STAFF,
                    session_id=session_ctx.session_id,
                )
            ),
        ),
        patch("app.api.routes.conversations.tenant_session") as mock_ts,
    ):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()
        mock_db.execute.return_value = mock_result
        mock_ts.return_value.__aenter__.return_value = mock_db

        with pytest.raises(HTTPException) as exc_info:
            await upload_session_document(
                conversation_id=conv_id,
                request=req,
                session=session_ctx,
                auth_provider=mock_auth,
                settings=mock_settings,
                assistant_id=str(uuid4()),
            )
        assert exc_info.value.status_code == 413
        assert exc_info.value.detail == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_another_tenant_conversation_returns_404(
    mock_settings: ApiSettings, session_ctx: SessionContext
) -> None:
    """If user attempts to upload to a conversation in another tenant, returns 404."""
    conv_id = str(uuid4())
    pdf_content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    req = _create_mock_request(pdf_content, "doc.pdf")

    mock_auth = MagicMock()
    mock_auth.roles = AsyncMock(return_value=[UserRole.STAFF])

    # Assistant lookup fails with 404 (assistant belongs to another tenant)
    with patch(
        "app.api.routes.conversations.get_tenant_context_for_assistant",
        side_effect=HTTPException(status_code=404, detail="ASSISTANT_NOT_FOUND"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await upload_session_document(
                conversation_id=conv_id,
                request=req,
                session=session_ctx,
                auth_provider=mock_auth,
                settings=mock_settings,
                assistant_id=str(uuid4()),
            )
        assert exc_info.value.status_code == 404
