"""Unit tests for Evidence Assessment Gate (Node 5) and Validation (Node 8)."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.graph import (
    RELIABLE_TEXT_THRESHOLD,
    WEAK_EVIDENCE_THRESHOLD,
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    node_assess_evidence,
    node_validate,
)
from app.ports.retriever import Retriever
from app.ports.types import ChatMessage, ModelResponse, ModelRole, TenantContext, UserRole


class DummyRetriever(Retriever):
    """Stub retriever for testing gate and search_titles refusals."""

    def __init__(self, titles: list[str] | None = None, doc_count: int = 1) -> None:
        self.titles = titles or ["Clinic Operations Manual", "Billing Guide"]
        self.doc_count = doc_count

    async def search(self, _tenant_ctx: Any, _query: str, _filters: Any = None) -> list[Any]:
        return []

    async def search_titles(self, _tenant_ctx: Any, _query: str, limit: int = 3) -> list[str]:
        return self.titles[:limit]

    async def get_evidence_text(self, _tenant_ctx: Any, _evidence_ids: list[str]) -> dict[str, str]:
        return {}

    async def search_tables(self, _tenant_ctx: Any, _query: str, _limit: int = 15) -> list[Any]:
        return []

    async def get_inventory(
        self, _tenant_ctx: Any, _heading_limit: int = 5
    ) -> dict[str, list[str]]:
        return {"titles": self.titles, "headings": ["Topic A", "Topic B"]}


class DummyModelProvider:
    """Stub model provider for tracking generation invocations."""

    def __init__(
        self, response_text: str = "Test answer", structured: dict[str, Any] | None = None
    ) -> None:
        self.call_count = 0
        self.response_text = response_text
        self.structured = structured or {
            "mode": "short",
            "answer": response_text,
            "evidence_ids": ["chunk-1"],
            "quoted_span": "exact quote",
            "evidence_type": "text",
        }

    async def generate(
        self,
        _role: ModelRole,
        _messages: list[ChatMessage],
        _schema: Any = None,
        _max_tokens: int | None = None,
    ) -> ModelResponse:
        self.call_count += 1
        return ModelResponse(
            content=self.response_text,
            model_id="test-model",
            model_version="v1",
            structured_output=self.structured,
        )


@pytest.fixture
def base_context() -> GraphRuntimeContext:
    tenant = TenantContext(
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="user-test-1",
        role=UserRole.STAFF,
        session_id="sess-test-1",
    )
    return GraphRuntimeContext(
        ports=GraphPorts(retriever=DummyRetriever()),
        tenant=tenant,
        question_text="What are office hours?",
        evidence_text={"chunk-1": "The clinic hours are 9am to 5pm Monday to Friday."},
    )


@pytest.mark.asyncio
async def test_assess_evidence_no_candidates(base_context: GraphRuntimeContext) -> None:
    """Empty candidate pool sets NO_EVIDENCE and status refusal."""
    base_context.evidence_text = {}
    state: GraphState = {"request_id": "r1", "evidence_ids": []}

    class MockRuntime:
        context = base_context

    res = await node_assess_evidence(state, MockRuntime())  # type: ignore
    assert res["evidence_state"] == "NO_EVIDENCE"
    assert res["status"] == "refusal"
    assert res["needs_vision"] is False


@pytest.mark.asyncio
async def test_assess_evidence_weak_score(base_context: GraphRuntimeContext) -> None:
    """Best candidate score below RELIABLE_TEXT_THRESHOLD sets WEAK and status refusal."""
    state: GraphState = {
        "request_id": "r1",
        "evidence_ids": ["chunk-1"],
    }
    base_context.candidate_scores = {"chunk-1": RELIABLE_TEXT_THRESHOLD - 0.5}

    class MockRuntime:
        context = base_context

    res = await node_assess_evidence(state, MockRuntime())  # type: ignore
    assert res["evidence_state"] == "WEAK"
    assert res["status"] == "refusal"


@pytest.mark.asyncio
async def test_assess_evidence_reliable_text(base_context: GraphRuntimeContext) -> None:
    """Strong score with text evidence sets RELIABLE_TEXT."""
    state: GraphState = {
        "request_id": "r1",
        "evidence_ids": ["chunk-1"],
    }
    base_context.candidate_scores = {"chunk-1": RELIABLE_TEXT_THRESHOLD + 1.0}

    class MockRuntime:
        context = base_context

    res = await node_assess_evidence(state, MockRuntime())  # type: ignore
    assert res["evidence_state"] == "RELIABLE_TEXT"
    assert res["needs_vision"] is False


@pytest.mark.asyncio
async def test_assess_evidence_visual_required(base_context: GraphRuntimeContext) -> None:
    """Candidate with visual evidence_type sets VISUAL_REQUIRED."""
    state: GraphState = {
        "request_id": "r1",
        "evidence_ids": ["chunk-v1"],
    }
    base_context.evidence_text = {"chunk-v1": "Visual transcription of fax header"}
    base_context.candidate_scores = {"chunk-v1": RELIABLE_TEXT_THRESHOLD + 1.0}
    base_context.candidate_evidence_types = {"chunk-v1": "visual"}

    class MockRuntime:
        context = base_context

    res = await node_assess_evidence(state, MockRuntime())  # type: ignore
    assert res["evidence_state"] == "VISUAL_REQUIRED"
    assert res["needs_vision"] is True


@pytest.mark.asyncio
async def test_validate_exact_quote_match_passes(base_context: GraphRuntimeContext) -> None:
    """Validation succeeds when quoted_span is an exact substring of stored text."""
    state: GraphState = {
        "request_id": "r1",
        "evidence_ids": ["chunk-1"],
        "validation_attempts": 0,
    }
    base_context.generated_answer = {
        "mode": "short",
        "answer": "Open 9am to 5pm.",
        "evidence_ids": ["chunk-1"],
        "quoted_span": "9am to 5pm",
    }

    class MockRuntime:
        context = base_context

    res = await node_validate(state, MockRuntime())  # type: ignore
    assert res["status"] == "completed"


@pytest.mark.asyncio
async def test_validate_hallucinated_quote_retries_once_then_refuses(
    base_context: GraphRuntimeContext,
) -> None:
    """Hallucinated quote triggers retry on attempt 0, then refusal on attempt 1."""

    class MockRuntime:
        context = base_context

    base_context.generated_answer = {
        "mode": "short",
        "answer": "Open 24/7.",
        "evidence_ids": ["chunk-1"],
        "quoted_span": "Open 24/7 all day",  # NOT in "9am to 5pm Monday to Friday"
    }

    # Attempt 0 -> retry
    state_0: GraphState = {
        "request_id": "r1",
        "evidence_ids": ["chunk-1"],
        "validation_attempts": 0,
    }
    res_0 = await node_validate(state_0, MockRuntime())  # type: ignore
    assert res_0["status"] == "retry"
    assert res_0["validation_attempts"] == 1

    # Attempt 1 -> refusal
    state_1: GraphState = {
        "request_id": "r1",
        "evidence_ids": ["chunk-1"],
        "validation_attempts": 1,
    }
    res_1 = await node_validate(state_1, MockRuntime())  # type: ignore
    assert res_1["status"] == "refusal"
    assert base_context.response_text == "I couldn't confirm that from the documents."


@pytest.mark.asyncio
async def test_validate_unknown_citation_id_fails(base_context: GraphRuntimeContext) -> None:
    """Cited evidence ID not in retrieved evidence triggers retry/refusal."""

    class MockRuntime:
        context = base_context

    base_context.generated_answer = {
        "mode": "short",
        "answer": "Some answer",
        "evidence_ids": ["chunk-unknown-999"],  # NOT in ["chunk-1"]
        "quoted_span": None,
    }

    state_0: GraphState = {
        "request_id": "r1",
        "evidence_ids": ["chunk-1"],
        "validation_attempts": 0,
    }
    res_0 = await node_validate(state_0, MockRuntime())  # type: ignore
    assert res_0["status"] == "retry"


@pytest.mark.asyncio
async def test_empty_assistant_refuses_without_model_call(
    base_context: GraphRuntimeContext,
) -> None:
    """Empty assistant with 0 documents refuses immediately without model call (Rule E.1)."""
    base_context.active_document_count = 0
    state: GraphState = {"request_id": "r1", "evidence_ids": []}

    class MockRuntime:
        context = base_context

    res = await node_assess_evidence(state, MockRuntime())  # type: ignore
    assert res["status"] == "refusal"
    assert res["evidence_state"] == "NO_EVIDENCE"
    assert (
        base_context.response_text
        == "This assistant has no documents yet. Add files in Data sources to get started."
    )


@pytest.mark.asyncio
async def test_weak_evidence_includes_search_titles_topics(
    base_context: GraphRuntimeContext,
) -> None:
    """Documents exist but WEAK evidence includes search_titles topics (Rule E.2)."""
    base_context.active_document_count = 2
    base_context.candidate_scores = {"chunk-1": WEAK_EVIDENCE_THRESHOLD - 1.0}
    state: GraphState = {"request_id": "r1", "evidence_ids": ["chunk-1"]}

    class MockRuntime:
        context = base_context

    res = await node_assess_evidence(state, MockRuntime())  # type: ignore
    assert res["status"] == "refusal"
    assert res["evidence_state"] == "WEAK"
    assert "I couldn't find that in this assistant's documents." in base_context.response_text
    assert "I do have information on:" in base_context.response_text
    assert "Clinic Operations Manual" in base_context.response_text
