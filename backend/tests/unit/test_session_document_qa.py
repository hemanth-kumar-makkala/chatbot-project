"""Unit tests for Stage J: Questions about an attached session document.

Verifies:
1. Router chooses "session_document" when a file is attached and question references it.
2. Router never chooses "session_document" when no file is attached.
3. Question answered correctly from attached fax with citation ("skewed_fax.pdf, page 1").
4. Question about a value not in the attached document is refused with fixed refusal text.
5. General question in the same conversation still uses general route.
6. Knowledge question in the same conversation still uses knowledge route.
7. Fabricated quote rejected by quote validation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    node_generate,
    node_route,
    node_validate,
)
from app.ports.model_provider import ModelCapabilities
from app.ports.types import ModelResponse, TenantContext, UserRole


def _mock_model_response(content: str, structured_output: dict) -> ModelResponse:
    return ModelResponse(
        content=content,
        structured_output=structured_output,
        model_id="mock-model",
        model_version="1.0.0",
    )


def _create_mock_context(
    question: str,
    session_doc_name: str | None = None,
    session_doc_pages: list[dict[str, str | int]] | None = None,
) -> GraphRuntimeContext:
    tenant_ctx = TenantContext(
        tenant_id=str(uuid4()),
        user_id=str(uuid4()),
        role=UserRole.STAFF,
        session_id=str(uuid4()),
    )
    capabilities = ModelCapabilities(supports_vision=True, remote=False, baa_covered=False)
    ports = GraphPorts(
        capabilities_map={("mock", "mock-model"): capabilities},
        is_mock_profile=False,
    )
    return GraphRuntimeContext(
        tenant=tenant_ctx,
        question_text=question,
        ports=ports,
        session_document_name=session_doc_name,
        session_document_pages=session_doc_pages,
    )


@pytest.mark.asyncio
async def test_router_chooses_session_document_when_file_attached_and_referenced():
    """1. Router chooses 'session_document' when file is attached and question references it."""
    ctx = _create_mock_context(
        question="what is the patient name on this document?",
        session_doc_name="skewed_fax.pdf",
    )
    mock_provider = MagicMock()
    mock_provider.generate = AsyncMock(
        return_value=_mock_model_response(
            content='{"route": "session_document", "answer_mode": "short", "language": "en"}',
            structured_output={
                "route": "session_document",
                "answer_mode": "short",
                "language": "en",
            },
        )
    )
    ctx.ports.model_provider = mock_provider

    state = {"request_id": str(uuid4()), "status": "pending"}
    res = await node_route(state, runtime=MagicMock(context=ctx))

    assert res["route"] == "session_document"


@pytest.mark.asyncio
async def test_router_never_chooses_session_document_when_no_file_attached():
    """2. Router never chooses 'session_document' when no file is attached."""
    # Context with NO attached document
    ctx = _create_mock_context(
        question="what is the patient name on the fax?",
        session_doc_name=None,
    )
    mock_provider = MagicMock()
    # Even if the LLM hallucinated returning "session_document"
    mock_provider.generate = AsyncMock(
        return_value=_mock_model_response(
            content='{"route": "session_document", "answer_mode": "short", "language": "en"}',
            structured_output={
                "route": "session_document",
                "answer_mode": "short",
                "language": "en",
            },
        )
    )
    ctx.ports.model_provider = mock_provider

    state = {"request_id": str(uuid4()), "status": "pending"}
    res = await node_route(state, runtime=MagicMock(context=ctx))

    # Guard forces fallback to knowledge
    assert res["route"] != "session_document"
    assert res["route"] == "knowledge"


@pytest.mark.asyncio
async def test_question_answered_from_attached_fax_with_citation():
    """3. Question answered correctly from attached fax with citation."""
    fax_page_text = (
        "CLINICAL INTAKE FORM\n"
        "Patient Name: Alex Morgan\n"
        "MRN: MRN-9842-A\n"
        "Authorized Medication: Amoxicillin 500mg\n"
        "Dosage: Take 1 capsule three times daily for 10 days\n"
        "Refill count: 0\n"
        "Clinic Phone: 555-019-2834\n"
    )
    ctx = _create_mock_context(
        question="what medication was prescribed in this document?",
        session_doc_name="skewed_fax.pdf",
        session_doc_pages=[{"page_number": 1, "text": fax_page_text}],
    )
    mock_provider = MagicMock()
    content_json = (
        '{"mode": "short", "answer": "The authorized medication is Amoxicillin 500mg.", '
        '"quoted_span": "Amoxicillin 500mg", "evidence_ids": ["page_1"], "evidence_type": "text"}'
    )
    mock_provider.generate = AsyncMock(
        return_value=_mock_model_response(
            content=content_json,
            structured_output={
                "mode": "short",
                "answer": "The authorized medication is Amoxicillin 500mg.",
                "quoted_span": "Amoxicillin 500mg",
                "evidence_ids": ["page_1"],
                "evidence_type": "text",
            },
        )
    )
    ctx.ports.model_provider = mock_provider

    state = {
        "request_id": str(uuid4()),
        "route": "session_document",
        "answer_mode": "short",
        "status": "in_progress",
    }
    gen_res = await node_generate(state, runtime=MagicMock(context=ctx))
    assert gen_res["status"] == "in_progress"

    val_res = await node_validate(state, runtime=MagicMock(context=ctx))
    assert val_res["status"] == "completed"
    assert "Amoxicillin 500mg" in ctx.response_text
    assert ctx.citations is not None
    assert len(ctx.citations) == 1
    assert ctx.citations[0].document_title == "skewed_fax.pdf"
    assert ctx.citations[0].page_number == 1


@pytest.mark.asyncio
async def test_question_unlisted_value_refused_with_required_text():
    """4. Question about a value not in the attached document is refused with required text."""
    fax_page_text = "CLINICAL INTAKE FORM\nPatient Name: Alex Morgan\nMRN: MRN-9842-A\n"
    ctx = _create_mock_context(
        question="what is the patient's parking policy permit number?",
        session_doc_name="skewed_fax.pdf",
        session_doc_pages=[{"page_number": 1, "text": fax_page_text}],
    )
    mock_provider = MagicMock()
    refusal_content = (
        '{"mode": "short", "answer": "I couldn\'t confirm that from the attached document.", '
        '"quoted_span": null, "evidence_ids": [], "evidence_type": "text"}'
    )
    mock_provider.generate = AsyncMock(
        return_value=_mock_model_response(
            content=refusal_content,
            structured_output={
                "mode": "short",
                "answer": "I couldn't confirm that from the attached document.",
                "quoted_span": None,
                "evidence_ids": [],
                "evidence_type": "text",
            },
        )
    )
    ctx.ports.model_provider = mock_provider

    state = {
        "request_id": str(uuid4()),
        "route": "session_document",
        "answer_mode": "short",
        "status": "in_progress",
    }
    await node_generate(state, runtime=MagicMock(context=ctx))
    val_res = await node_validate(state, runtime=MagicMock(context=ctx))

    assert val_res["status"] == "refusal"
    assert ctx.response_text == "I couldn't confirm that from the attached document."
    assert len(ctx.citations) == 0


@pytest.mark.asyncio
async def test_general_question_in_same_chat_uses_general_route():
    """5. General question in the same conversation still uses general route."""
    ctx = _create_mock_context(
        question="draft a short email to a clinic about a delayed prior authorization",
        session_doc_name="skewed_fax.pdf",
    )
    mock_provider = MagicMock()
    mock_provider.generate = AsyncMock(
        return_value=_mock_model_response(
            content='{"route": "general", "answer_mode": "short", "language": "en"}',
            structured_output={
                "route": "general",
                "answer_mode": "short",
                "language": "en",
            },
        )
    )
    ctx.ports.model_provider = mock_provider

    state = {"request_id": str(uuid4()), "status": "pending"}
    res = await node_route(state, runtime=MagicMock(context=ctx))

    assert res["route"] == "general"


@pytest.mark.asyncio
async def test_knowledge_question_in_same_chat_uses_knowledge_route():
    """6. Knowledge question in the same conversation still uses knowledge route."""
    ctx = _create_mock_context(
        question="what is the front desk policy for identity verification",
        session_doc_name="skewed_fax.pdf",
    )
    mock_provider = MagicMock()
    mock_provider.generate = AsyncMock(
        return_value=_mock_model_response(
            content='{"route": "knowledge", "answer_mode": "explain", "language": "en"}',
            structured_output={
                "route": "knowledge",
                "answer_mode": "explain",
                "language": "en",
            },
        )
    )
    ctx.ports.model_provider = mock_provider

    state = {"request_id": str(uuid4()), "status": "pending"}
    res = await node_route(state, runtime=MagicMock(context=ctx))

    assert res["route"] == "knowledge"


@pytest.mark.asyncio
async def test_quote_validation_rejects_fabricated_quote():
    """9. Fabricated quote not in the document is rejected and replaced with refusal."""
    fax_page_text = "CLINICAL INTAKE FORM\nPatient Name: Alex Morgan\n"
    ctx = _create_mock_context(
        question="what is the patient's allergy?",
        session_doc_name="skewed_fax.pdf",
        session_doc_pages=[{"page_number": 1, "text": fax_page_text}],
    )
    mock_provider = MagicMock()
    # Hallucinated answer claiming allergy with fake quote
    fake_content = (
        '{"mode": "short", "answer": "The patient is allergic to penicillin.", '
        '"quoted_span": "Allergic to penicillin", "evidence_ids": ["page_1"], '
        '"evidence_type": "text"}'
    )
    mock_provider.generate = AsyncMock(
        return_value=_mock_model_response(
            content=fake_content,
            structured_output={
                "mode": "short",
                "answer": "The patient is allergic to penicillin.",
                "quoted_span": "Allergic to penicillin",
                "evidence_ids": ["page_1"],
                "evidence_type": "text",
            },
        )
    )
    ctx.ports.model_provider = mock_provider

    state = {
        "request_id": str(uuid4()),
        "route": "session_document",
        "answer_mode": "short",
        "status": "in_progress",
    }
    await node_generate(state, runtime=MagicMock(context=ctx))
    val_res = await node_validate(state, runtime=MagicMock(context=ctx))

    assert val_res["status"] == "refusal"
    assert ctx.response_text == "I couldn't confirm that from the attached document."
    assert len(ctx.citations) == 0


@pytest.mark.asyncio
async def test_deterministic_session_doc_routing_for_reference_phrase_never_clarify() -> None:
    """Stage M: Questions referring to attached document ('this fax', 'this document')

    must deterministically route to 'session_document' before the model router, never to 'clarify'.
    """
    ctx = _create_mock_context(
        question="what is the dosage in this fax?",
        session_doc_name="skewed_fax.pdf",
    )
    mock_provider = MagicMock()
    mock_provider.generate = AsyncMock(
        return_value=_mock_model_response(
            content='{"route": "clarify", "answer_mode": "short", "language": "en"}',
            structured_output={"route": "clarify", "answer_mode": "short", "language": "en"},
        )
    )
    ctx.ports.model_provider = mock_provider
    ctx.ports.is_mock_profile = False

    state: GraphState = {"request_id": str(uuid4()), "status": "in_progress"}
    res = await node_route(state, runtime=MagicMock(context=ctx))

    assert res["route"] == "session_document"
    assert res.get("status") != "refusal"


@pytest.mark.asyncio
async def test_deterministic_session_doc_routing_for_contained_value_phrasing() -> None:
    """Stage M: Questions asking for a value the attached document may contain (e.g. date of birth)

    must deterministically route to 'session_document' before the model router, never to 'clarify'.
    """
    ctx = _create_mock_context(
        question="what is the patient's date of birth?",
        session_doc_name="skewed_fax.pdf",
    )
    mock_provider = MagicMock()
    mock_provider.generate = AsyncMock(
        return_value=_mock_model_response(
            content='{"route": "clarify", "answer_mode": "short", "language": "en"}',
            structured_output={"route": "clarify", "answer_mode": "short", "language": "en"},
        )
    )
    ctx.ports.model_provider = mock_provider
    ctx.ports.is_mock_profile = False

    state: GraphState = {"request_id": str(uuid4()), "status": "in_progress"}
    res = await node_route(state, runtime=MagicMock(context=ctx))

    assert res["route"] == "session_document"


@pytest.mark.asyncio
async def test_session_doc_summary_answers_with_points_and_validates_each_quote() -> None:
    """Stage R1: Session document summary answers as short points with quotes and page numbers."""
    page_text = (
        "Patient: Alex Morgan (Chart #MRN-4412-A)\n"
        "Prescription Refill Authorization for Amoxicillin 500mg\n"
        "Dosage: Take 1 capsule by mouth three times daily for 10 days\n"
        "Clinic Phone: 555-0145\n"
    )
    ctx = _create_mock_context(
        question="please summarize this fax",
        session_doc_name="skewed_fax.pdf",
        session_doc_pages=[{"page_number": 1, "text": page_text}],
    )
    mock_provider = MagicMock()
    points_data = [
        {
            "point": "Patient name is Alex Morgan",
            "quoted_span": "Patient: Alex Morgan",
            "evidence_ids": ["page_1"],
        },
        {
            "point": "Authorized medication is Amoxicillin 500mg",
            "quoted_span": "Amoxicillin 500mg",
            "evidence_ids": ["page_1"],
        },
        {
            "point": "Dosage is Take 1 capsule three times daily",
            "quoted_span": "Take 1 capsule by mouth three times daily for 10 days",
            "evidence_ids": ["page_1"],
        },
    ]
    mock_provider.generate = AsyncMock(
        return_value=_mock_model_response(
            content='{"mode": "explain", "answer": "", "points": []}',
            structured_output={
                "mode": "explain",
                "answer": "",
                "points": points_data,
                "evidence_ids": ["page_1"],
            },
        )
    )
    ctx.ports.model_provider = mock_provider
    ctx.ports.is_mock_profile = False

    state: GraphState = {
        "request_id": str(uuid4()),
        "route": "session_document",
        "answer_mode": "explain",
        "status": "in_progress",
    }
    await node_generate(state, runtime=MagicMock(context=ctx))
    val_res = await node_validate(state, runtime=MagicMock(context=ctx))

    assert val_res["status"] == "completed"
    assert "Alex Morgan" in ctx.response_text
    assert "Amoxicillin 500mg" in ctx.response_text
    assert len(ctx.citations) == 1
    assert ctx.citations[0].document_title == "skewed_fax.pdf"
    assert ctx.citations[0].page_number == 1


@pytest.mark.asyncio
async def test_session_doc_summary_drops_points_with_failing_quotes_and_keeps_valid() -> None:
    """Stage R1: Points with failing quotes are dropped; surviving points are displayed."""
    page_text = (
        "Patient: Alex Morgan (Chart #MRN-4412-A)\n"
        "Prescription Refill Authorization for Amoxicillin 500mg\n"
    )
    ctx = _create_mock_context(
        question="please summarize this fax",
        session_doc_name="skewed_fax.pdf",
        session_doc_pages=[{"page_number": 1, "text": page_text}],
    )
    mock_provider = MagicMock()
    points_data = [
        {
            "point": "Patient name is Alex Morgan",
            "quoted_span": "Alex Morgan",
            "evidence_ids": ["page_1"],
        },
        {
            "point": "Patient date of birth is 1985-05-12",
            "quoted_span": "DOB: 1985-05-12",  # NOT in page_text! Must be dropped!
            "evidence_ids": ["page_1"],
        },
        {
            "point": "Prescription is for Amoxicillin 500mg",
            "quoted_span": "Amoxicillin 500mg",
            "evidence_ids": ["page_1"],
        },
    ]
    mock_provider.generate = AsyncMock(
        return_value=_mock_model_response(
            content='{"mode": "explain", "answer": "", "points": []}',
            structured_output={
                "mode": "explain",
                "answer": "",
                "points": points_data,
                "evidence_ids": ["page_1"],
            },
        )
    )
    ctx.ports.model_provider = mock_provider
    ctx.ports.is_mock_profile = False

    state: GraphState = {
        "request_id": str(uuid4()),
        "route": "session_document",
        "answer_mode": "explain",
        "status": "in_progress",
    }
    await node_generate(state, runtime=MagicMock(context=ctx))
    val_res = await node_validate(state, runtime=MagicMock(context=ctx))

    assert val_res["status"] == "completed"
    assert "Alex Morgan" in ctx.response_text
    assert "Amoxicillin 500mg" in ctx.response_text
    assert "1985-05-12" not in ctx.response_text
    assert len(ctx.citations) == 1


@pytest.mark.asyncio
async def test_session_doc_summary_refuses_when_no_points_survive() -> None:
    """Stage R1: When all points have hallucinated quotes, refuses with fixed message."""
    page_text = "Attn: Pharmacy Dispensing Coordinator\nClinic Phone: 555-0145\n"
    ctx = _create_mock_context(
        question="please summarize this fax",
        session_doc_name="skewed_fax.pdf",
        session_doc_pages=[{"page_number": 1, "text": page_text}],
    )
    mock_provider = MagicMock()
    points_data = [
        {
            "point": "Patient name is John Doe",
            "quoted_span": "Patient: John Doe",  # NOT in text
            "evidence_ids": ["page_1"],
        },
        {
            "point": "Patient date of birth is 1990-01-01",
            "quoted_span": "DOB: 1990-01-01",  # NOT in text
            "evidence_ids": ["page_1"],
        },
    ]
    mock_provider.generate = AsyncMock(
        return_value=_mock_model_response(
            content='{"mode": "explain", "answer": "", "points": []}',
            structured_output={
                "mode": "explain",
                "answer": "",
                "points": points_data,
                "evidence_ids": ["page_1"],
            },
        )
    )
    ctx.ports.model_provider = mock_provider
    ctx.ports.is_mock_profile = False

    state: GraphState = {
        "request_id": str(uuid4()),
        "route": "session_document",
        "answer_mode": "explain",
        "status": "in_progress",
    }
    await node_generate(state, runtime=MagicMock(context=ctx))
    val_res = await node_validate(state, runtime=MagicMock(context=ctx))

    assert val_res["status"] == "refusal"
    assert ctx.response_text == "I couldn't confirm that from the attached document."
    assert len(ctx.citations) == 0


@pytest.mark.asyncio
async def test_session_doc_summary_mock_profile_on_skewed_fax() -> None:
    """Stage R1: On mock profile, summary of skewed_fax produces at least two cited points."""
    skewed_fax_ocr = (
        "* * * SYNTHETIC TEST DOCUMENT *** TRANSMISSION CONFIDENTIAL MEDICAL RECORD FACSIMILE\n\n"
        "NOTICE PEDIATRIC & ADULT URGENT CARE TRANSMISSION Date: 2026-02-14 "
        "Attn: Pharmacy Dispensing Coordinator Clinic Phone: 555-0145\n\n"
        "Prescription Refill Authorization: approved for Amoxicillin 5oOmg Dr. Evelyn Reed. "
        "Urgent prescription refill request Take 1 capsule by mouth three times daily for 10 days: "
        "Dosage: Patient: Alex Morgan (Chart #MRN-4412-A) Refill count: 0 (one-time course). by"
    )
    ctx = _create_mock_context(
        question="please summarize this fax",
        session_doc_name="skewed_fax.pdf",
        session_doc_pages=[{"page_number": 1, "text": skewed_fax_ocr}],
    )
    ctx.ports.is_mock_profile = True

    state: GraphState = {
        "request_id": str(uuid4()),
        "route": "session_document",
        "answer_mode": "explain",
        "status": "in_progress",
    }
    await node_generate(state, runtime=MagicMock(context=ctx))
    val_res = await node_validate(state, runtime=MagicMock(context=ctx))

    assert val_res["status"] == "completed"
    assert "Alex Morgan" in ctx.response_text
    assert "Amoxicillin" in ctx.response_text or "Take 1 capsule" in ctx.response_text
    assert len(ctx.citations) == 1
    assert ctx.citations[0].document_title == "skewed_fax.pdf"
    assert ctx.citations[0].page_number == 1
