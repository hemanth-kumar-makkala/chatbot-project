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
from app.api.routes.conversations import (
    extract_session_document_fields,
    upload_session_document,
)
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


@pytest.mark.asyncio
async def test_renamed_skewed_fax_upload_uses_extraction_subgraph_without_hardcoded_strings(
    mock_settings: ApiSettings, session_ctx: SessionContext
) -> None:
    """Uploading a renamed copy of skewed_fax.pdf (arbitrary_intake_01.pdf) runs the real
    extraction subgraph (build_extraction_subgraph in app.core.extraction.subgraph)
    and returns all eval field states with zero filename branching or hardcoded strings.
    """
    from pathlib import Path

    from app.core.extraction.subgraph import build_extraction_subgraph

    assert callable(build_extraction_subgraph)

    repo_root = Path(__file__).resolve().parents[3]
    skewed_path = repo_root / "evals" / "golden" / "docs" / "skewed_fax.pdf"
    assert skewed_path.exists(), f"Missing {skewed_path}"
    pdf_bytes = skewed_path.read_bytes()

    conv_id = str(uuid4())
    asst_id = str(uuid4())
    tenant_id = str(uuid4())

    mock_auth = MagicMock()
    mock_auth.roles = AsyncMock(return_value=[UserRole.STAFF])

    async def _upload_with_name(filename: str):
        req = _create_mock_request(pdf_bytes, filename)
        with (
            patch(
                "app.api.routes.conversations.get_tenant_context_for_assistant",
                new=AsyncMock(
                    return_value=TenantContext(
                        tenant_id=tenant_id,
                        user_id=session_ctx.user_id,
                        role=UserRole.STAFF,
                        session_id=session_ctx.session_id,
                    )
                ),
            ),
            patch("app.api.routes.conversations.tenant_session") as mock_ts,
        ):
            mock_db = AsyncMock()
            mock_db.add = MagicMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = MagicMock()
            mock_db.execute.return_value = mock_result
            mock_ts.return_value.__aenter__.return_value = mock_db

            upload_res = await upload_session_document(
                conversation_id=conv_id,
                request=req,
                session=session_ctx,
                auth_provider=mock_auth,
                settings=mock_settings,
                assistant_id=asst_id,
            )

            storage_root = Path(
                mock_settings.storage_dir if hasattr(mock_settings, "storage_dir") else "storage"
            )
            session_dir = storage_root / "sessions" / tenant_id / conv_id
            safe_name = f"{upload_res.session_document_id}_{filename}"
            saved_path = session_dir / safe_name
            if not saved_path.exists():
                saved_path = list(session_dir.glob(f"{upload_res.session_document_id}_*"))[0]

            mock_sdoc = MagicMock(
                file_path=str(saved_path),
                file_name=filename,
                page_count=1,
            )
            mock_result.scalar_one_or_none.side_effect = [
                mock_sdoc,  # sdoc query
                None,  # existing_ext query
            ]

            return await extract_session_document_fields(
                assistant_id=asst_id,
                conversation_id=conv_id,
                document_id=upload_res.session_document_id,
                session=session_ctx,
                auth_provider=mock_auth,
                settings=mock_settings,
            )

    renamed_resp = await _upload_with_name("arbitrary_intake_01.pdf")
    original_resp = await _upload_with_name("skewed_fax.pdf")

    assert renamed_resp.file_name == "arbitrary_intake_01.pdf"
    assert renamed_resp.status == "extracted"
    assert len(renamed_resp.extracted_fields) > 0

    # States and values must be identical regardless of filename (zero filename matching)
    renamed_tuples = [
        (
            f["name"],
            f["state"],
            f["value"],
            f.get("read1_value"),
            f.get("read2_value"),
            f.get("validator_error"),
        )
        for f in renamed_resp.extracted_fields
    ]
    original_tuples = [
        (
            f["name"],
            f["state"],
            f["value"],
            f.get("read1_value"),
            f.get("read2_value"),
            f.get("validator_error"),
        )
        for f in original_resp.extracted_fields
    ]
    assert renamed_tuples == original_tuples

    # Must exercise the same states as the extraction eval:
    # filled (FILLED_HIGH/FILLED_MEDIUM), CONFLICTING, INVALID, and blank (UNREADABLE/NOT_PRESENT)
    states_present = {f["state"] for f in renamed_resp.extracted_fields}
    assert states_present & {"FILLED_HIGH", "FILLED_MEDIUM"}, (
        f"Expected filled state in {states_present}"
    )
    assert "CONFLICTING" in states_present, f"Expected CONFLICTING state in {states_present}"
    assert "INVALID" in states_present, f"Expected INVALID state in {states_present}"
    assert states_present & {"UNREADABLE", "NOT_PRESENT"}, (
        f"Expected blank/unreadable state in {states_present}"
    )

    # Invariant 6: CONFLICTING, UNREADABLE, and NOT_PRESENT fields have value=None
    for f in renamed_resp.extracted_fields:
        if f["state"] in ("CONFLICTING", "UNREADABLE", "NOT_PRESENT"):
            assert f["value"] is None, (
                f"Field {f['name']} in state {f['state']} must have value=None"
            )
        if f["state"] == "INVALID":
            assert f.get("validator_error") is not None

    # Zero hardcoded strings from the old fake implementation
    serialized = str(renamed_resp.extracted_fields)
    for forbidden in (
        "Amoxicillin 500mg",
        "Amoxicillin 5oOmg",
        "MRN-4412-A",
        "Alex Morgan",
        "1982736451",
    ):
        assert forbidden not in serialized, f"Found hardcoded string {forbidden!r} in output"


@pytest.mark.asyncio
async def test_upload_session_document_on_new_conversation_auto_creates_conversation(
    mock_settings: ApiSettings, session_ctx: SessionContext
) -> None:
    """When a user attaches a document in a brand-new chat (conversation_id is the
    sentinel '00000000-0000-0000-0000-000000000001' or 'new' and does not exist yet),
    upload_session_document auto-creates a Conversation under tenant_session and returns
    its newly generated UUID instead of failing with 404 CONVERSATION_NOT_FOUND.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    pdf_bytes = (repo_root / "evals" / "golden" / "docs" / "skewed_fax.pdf").read_bytes()
    req = _create_mock_request(pdf_bytes, "intake_form.pdf")

    mock_auth = MagicMock()
    mock_auth.roles = AsyncMock(return_value=[UserRole.STAFF])
    asst_id = str(uuid4())
    tenant_id = str(uuid4())

    with (
        patch(
            "app.api.routes.conversations.get_tenant_context_for_assistant",
            new=AsyncMock(
                return_value=TenantContext(
                    tenant_id=tenant_id,
                    user_id=str(uuid4()),
                    role=UserRole.STAFF,
                    session_id=session_ctx.session_id,
                )
            ),
        ),
        patch("app.api.routes.conversations.tenant_session") as mock_ts,
    ):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        # Simulate conversation NOT existing yet in DB (returns None on first lookup)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        mock_ts.return_value.__aenter__.return_value = mock_db

        resp = await upload_session_document(
            conversation_id="00000000-0000-0000-0000-000000000001",
            request=req,
            session=session_ctx,
            auth_provider=mock_auth,
            settings=mock_settings,
            assistant_id=asst_id,
        )

    assert resp.status == "uploaded"
    assert resp.conversation_id != "00000000-0000-0000-0000-000000000001"
    assert resp.extracted_fields == []


@pytest.mark.asyncio
async def test_ask_question_stream_populates_page_images_and_ocr_confidences(
    mock_settings: ApiSettings, session_ctx: SessionContext
) -> None:
    """SSE streaming endpoint (/v1/questions/stream) loads visual page images and
    OCR confidences into GraphRuntimeContext just like POST /v1/questions.
    """
    from app.api.routes.questions import QuestionRequest, ask_question_stream

    asst_id = str(uuid4())
    tenant_id = str(uuid4())
    conv_id = uuid4()
    visual_chunk_id = str(uuid4())
    sample_png = b"\x89PNG\r\n\x1a\nsynthetic_page_image"

    mock_auth = MagicMock()
    mock_auth.roles = AsyncMock(return_value=[UserRole.STAFF])

    captured_contexts: list[object] = []

    class FakeCompiledGraph:
        async def astream(self, state, context=None, stream_mode="updates"):
            _ = state, stream_mode
            captured_contexts.append(context)
            context.response_text = "Visual answer from page 1."
            yield {"assess_evidence": {"needs_vision": True, "evidence_state": "VISUAL_REQUIRED"}}
            yield {"vision_read": {"vision_status": "ok", "needs_vision": True}}
            yield {
                "respond": {
                    "status": "answered",
                    "evidence_state": "VISUAL_REQUIRED",
                    "needs_vision": True,
                    "evidence_ids": [visual_chunk_id],
                }
            }

    mock_storage = MagicMock()
    mock_storage.get = AsyncMock(return_value=sample_png)

    scope = {"type": "http", "method": "POST", "headers": []}
    dummy_req = Request(scope)

    with (
        patch(
            "app.api.routes.questions.get_tenant_context_for_assistant",
            new=AsyncMock(
                return_value=TenantContext(
                    tenant_id=tenant_id,
                    user_id=str(uuid4()),
                    role=UserRole.STAFF,
                    session_id=session_ctx.session_id,
                )
            ),
        ),
        patch("app.api.routes.questions.check_question_rate_limit", new=AsyncMock()),
        patch("app.api.routes.questions.tenant_session") as mock_ts,
        patch("app.api.routes.questions.create_storage_backend", return_value=mock_storage),
        patch("app.api.routes.questions.get_compiled_graph", return_value=FakeCompiledGraph()),
        patch("app.api.routes.questions.get_api_settings", return_value=mock_settings),
    ):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()

        # Execute calls in ask_question_stream pre-stream setup:
        # 1. Assistant lookup, 2. Conversation lookup, 3. Prev messages,
        # 4. Active doc count, 5. Active doc rows, 6. Visual rows
        res_asst = MagicMock()
        res_asst.scalar_one_or_none.return_value = "Clinic A"

        res_conv = MagicMock()
        res_conv.scalar_one_or_none.return_value = conv_id

        res_prev_msgs = MagicMock()
        res_prev_msgs.all.return_value = []

        res_count = MagicMock()
        res_count.scalar.return_value = 1

        res_docs = MagicMock()
        res_docs.all.return_value = [("skewed_fax.pdf", uuid4(), "pdf")]

        vrow = MagicMock()
        vrow.id = visual_chunk_id
        vrow.image_key = f"{tenant_id}/pages/p1.png"
        vrow.ocr_confidence = 0.62
        res_visual = MagicMock()
        res_visual.all.return_value = [vrow]

        res_empty = MagicMock()
        res_empty.all.return_value = []
        res_empty_scalars = MagicMock()
        res_empty_scalars.first.return_value = None
        res_empty.scalars.return_value = res_empty_scalars

        mock_db.execute.side_effect = [
            res_asst,
            res_conv,
            res_prev_msgs,
            res_count,
            res_docs,
            res_visual,
            res_empty,
            res_empty,
            res_empty,
            res_empty,
        ]
        mock_ts.return_value.__aenter__.return_value = mock_db

        payload = QuestionRequest(
            assistant_id=asst_id,
            question_text="What medication is authorized on the scanned page?",
            conversation_id=conv_id,
        )
        stream_resp = await ask_question_stream(
            payload=payload,
            request=dummy_req,
            session=session_ctx,
            auth_provider=mock_auth,
            _csrf=None,
        )

        chunks: list[str] = []
        async for chunk in stream_resp.body_iterator:
            chunks.append(chunk if isinstance(chunk, str) else bytes(chunk).decode("utf-8"))

    assert len(captured_contexts) == 1
    ctx_used = captured_contexts[0]
    assert getattr(ctx_used, "page_images", {}).get(visual_chunk_id) == sample_png
    assert getattr(ctx_used, "page_ocr_confidences", {}).get(visual_chunk_id) == pytest.approx(0.62)


@pytest.mark.asyncio
async def test_expired_session_documents_sweep_deletes_expired_rows_and_files(
    tmp_path,
) -> None:
    """Periodic worker task run_expired_session_documents_sweep deletes expired
    SessionDocument files from disk and removes their DB rows under worker_tenant_session.
    """
    from datetime import UTC, datetime, timedelta

    from app.worker import run_expired_session_documents_sweep

    expired_file = tmp_path / "expired_session_fax.pdf"
    expired_file.write_bytes(b"%PDF-1.4 expired fax content")
    assert expired_file.exists()

    tenant_id = uuid4()
    doc_id = uuid4()

    fake_sdoc = MagicMock()
    fake_sdoc.id = doc_id
    fake_sdoc.tenant_id = tenant_id
    fake_sdoc.file_path = str(expired_file)
    fake_sdoc.retention_until = datetime.now(UTC) - timedelta(hours=2)

    with (
        patch("app.worker.unscoped_session") as mock_unscoped,
        patch("app.worker.worker_tenant_session") as mock_wts,
    ):
        mock_u_db = AsyncMock()
        res_tenants = MagicMock()
        res_tenants.scalars.return_value.all.return_value = [tenant_id]
        mock_u_db.execute.return_value = res_tenants
        mock_unscoped.return_value.__aenter__.return_value = mock_u_db

        mock_t_db = AsyncMock()
        res_expired = MagicMock()
        res_expired.scalars.return_value.all.return_value = [fake_sdoc]
        mock_t_db.execute.return_value = res_expired
        mock_wts.return_value.__aenter__.return_value = mock_t_db

        sweep_res = await run_expired_session_documents_sweep()

    assert sweep_res.deleted_documents_count == 1
    assert sweep_res.deleted_files_count == 1
    assert not expired_file.exists()
    mock_t_db.delete.assert_awaited_once_with(fake_sdoc)
