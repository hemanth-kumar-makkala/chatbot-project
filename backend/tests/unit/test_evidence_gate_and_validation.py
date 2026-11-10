"""Unit tests for Evidence Assessment Gate (Node 5) and Validation (Node 8)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from app.core.graph import (
    RELIABLE_TEXT_THRESHOLD,
    WEAK_EVIDENCE_THRESHOLD,
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    build_knowledge_graph,
    node_assess_evidence,
    node_generate,
    node_retrieve,
    node_validate,
)
from app.ports.errors import TenantIsolationError
from app.ports.model_provider import ModelCapabilities, ModelProvider
from app.ports.retriever import Retriever
from app.ports.types import (
    ChatMessage,
    EvidenceId,
    EvidenceType,
    ExtractedFields,
    ModelResponse,
    ModelRole,
    PageReading,
    TenantContext,
    UserRole,
)


class DummyRetriever(Retriever):
    """Stub retriever for testing gate and search_titles refusals."""

    def __init__(self, titles: list[str] | None = None, doc_count: int = 1) -> None:
        self.titles = titles or ["Clinic Operations Manual", "Billing Guide"]
        self.doc_count = doc_count

    async def search(
        self, tenant_ctx: Any, query: str = "", filters: Any = None, **kwargs: Any
    ) -> list[Any]:
        _ = (tenant_ctx, query, filters, kwargs)
        return []

    async def search_titles(self, tenant_ctx: Any, query: str = "", limit: int = 3) -> list[str]:
        _ = (tenant_ctx, query)
        return self.titles[:limit]

    async def get_evidence_text(self, tenant_ctx: Any, evidence_ids: list[str]) -> dict[str, str]:
        _ = (tenant_ctx, evidence_ids)
        return {}

    async def search_tables(self, tenant_ctx: Any, query: str = "", limit: int = 15) -> list[Any]:
        _ = (tenant_ctx, query, limit)
        return []

    async def get_inventory(self, tenant_ctx: Any, heading_limit: int = 5) -> dict[str, list[str]]:
        _ = (tenant_ctx, heading_limit)
        return {"titles": self.titles, "headings": ["Topic A", "Topic B"]}

    async def get_neighbouring_chunks(self, tenant_ctx: Any, chunk_ids: list[str]) -> list[Any]:
        _ = (tenant_ctx, chunk_ids)
        return []


class DummyModelProvider(ModelProvider):
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
        role: ModelRole,
        messages: list[ChatMessage],
        schema: Any = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        _ = (role, messages, schema, max_tokens)
        self.call_count += 1
        return ModelResponse(
            content=self.response_text,
            model_id="test-model",
            model_version="v1",
            structured_output=self.structured,
        )

    async def transcribe_page(
        self,
        role: ModelRole,
        image: bytes,
    ) -> PageReading:
        _ = (role, image)
        return PageReading(
            reading="dummy page text",
            confidence=0.98,
            model_id="test-model",
            model_version="v1",
        )

    async def read_page(
        self,
        role: ModelRole,
        image: bytes,
        question: str,
    ) -> PageReading:
        _ = (role, image, question)
        return PageReading(
            reading="dummy page text",
            confidence=0.98,
            model_id="test-model",
            model_version="v1",
        )

    async def extract_fields(
        self,
        role: ModelRole,
        image: bytes,
        schema: type[BaseModel],
    ) -> ExtractedFields:
        _ = (role, image, schema)
        return ExtractedFields(
            fields={},
            model_id="test-model",
            model_version="v1",
            raw_text="dummy raw text",
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


@pytest.mark.asyncio
async def test_node_assess_evidence_skips_judge_for_single_document(
    base_context: GraphRuntimeContext,
) -> None:
    """When all strong evidence belongs to 1 document, JUDGE call is skipped."""
    base_context.active_document_count = 2
    base_context.evidence_text = {
        "chunk-1": "[Document: Clinic Manual]\nParagraph 1 info.",
        "chunk-2": "[Document: Clinic Manual]\nParagraph 2 info.",
    }
    base_context.candidate_scores = {
        "chunk-1": RELIABLE_TEXT_THRESHOLD + 0.1,
        "chunk-2": RELIABLE_TEXT_THRESHOLD + 0.2,
    }
    base_context.candidate_document_ids = {
        "chunk-1": "doc-manual-1",
        "chunk-2": "doc-manual-1",
    }

    judge_calls: list[Any] = []

    class TrackingModelProvider(DummyModelProvider):
        async def generate(
            self,
            role: ModelRole,
            messages: list[ChatMessage],
            schema: Any = None,
            max_tokens: int | None = None,
        ) -> ModelResponse:
            if role == ModelRole.JUDGE:
                judge_calls.append((role, messages, schema, max_tokens))
            return await super().generate(role, messages, schema, max_tokens)

    base_context.ports = GraphPorts(
        retriever=DummyRetriever(),
        model_provider=TrackingModelProvider(),
        capabilities_map={
            ("mock", "test-model"): ModelCapabilities(
                supports_vision=False, remote=False, baa_covered=False
            )
        },
    )

    state: GraphState = {"request_id": "r1", "evidence_ids": ["chunk-1", "chunk-2"]}

    class MockRuntime:
        context = base_context

    res = await node_assess_evidence(state, MockRuntime())  # type: ignore[arg-type]
    assert res["evidence_state"] == "RELIABLE_TEXT"
    assert res.get("status") in (None, "in_progress")
    assert len(judge_calls) == 0


@pytest.mark.asyncio
async def test_node_assess_evidence_fails_closed_when_judge_fails(
    base_context: GraphRuntimeContext,
) -> None:
    """When strong evidence spans >= 2 documents and JUDGE fails, routes to safe_failure."""
    base_context.active_document_count = 2
    base_context.evidence_text = {
        "chunk-1": "[Document: Policy A]\nTurnaround is 24 hours.",
        "chunk-2": "[Document: Policy B]\nTurnaround is 48 hours.",
    }
    base_context.candidate_scores = {
        "chunk-1": RELIABLE_TEXT_THRESHOLD + 0.1,
        "chunk-2": RELIABLE_TEXT_THRESHOLD + 0.2,
    }
    base_context.candidate_document_ids = {
        "chunk-1": "doc-policy-a",
        "chunk-2": "doc-policy-b",
    }

    class FailingJudgeModelProvider(DummyModelProvider):
        async def generate(
            self,
            role: ModelRole,
            messages: list[ChatMessage],
            schema: Any = None,
            max_tokens: int | None = None,
        ) -> ModelResponse:
            if role == ModelRole.JUDGE:
                raise RuntimeError("Judge endpoint HTTP 504 Gateway Timeout")
            return await super().generate(role, messages, schema, max_tokens)

    base_context.ports = GraphPorts(
        retriever=DummyRetriever(),
        model_provider=FailingJudgeModelProvider(),
        capabilities_map={
            ("mock", "test-model"): ModelCapabilities(
                supports_vision=False, remote=False, baa_covered=False
            )
        },
    )

    state: GraphState = {"request_id": "r1", "evidence_ids": ["chunk-1", "chunk-2"]}

    class MockRuntime:
        context = base_context

    res = await node_assess_evidence(state, MockRuntime())  # type: ignore[arg-type]
    assert res["status"] == "safe_failure"
    assert res["evidence_state"] == "SAFE_FAILURE"
    assert res["public_error_ref"].startswith("ERR-JUDGE-")
    assert "ERR-JUDGE-" in base_context.response_text


class _JudgeTrackingRetriever(Retriever):
    """Retriever yielding specific candidate document versions and tenant ids."""

    def __init__(self, evidence: list[EvidenceId], evidence_text: dict[str, str]) -> None:
        self.evidence = evidence
        self.evidence_text = evidence_text

    async def search(
        self, tenant_ctx: Any, query: str = "", filters: Any = None, **kwargs: Any
    ) -> list[EvidenceId]:
        _ = (query, filters, kwargs)
        return [
            EvidenceId(
                chunk_id=e.chunk_id,
                page_id=e.page_id,
                document_version_id=e.document_version_id,
                evidence_type=e.evidence_type,
                tenant_id=e.tenant_id or str(tenant_ctx.tenant_id),
            )
            for e in self.evidence
        ]

    async def search_titles(self, tenant_ctx: Any, query: str = "", limit: int = 3) -> list[str]:
        _ = (tenant_ctx, query)
        return ["Clinic Guide"][:limit]

    async def get_evidence_text(self, tenant_ctx: Any, evidence_ids: list[str]) -> dict[str, str]:
        _ = tenant_ctx
        return {eid: self.evidence_text.get(eid, f"Text {eid}") for eid in evidence_ids}

    async def search_tables(self, tenant_ctx: Any, query: str = "", limit: int = 15) -> list[Any]:
        _ = (tenant_ctx, query, limit)
        return []

    async def get_inventory(self, tenant_ctx: Any, limit: int = 5) -> dict[str, list[str]]:
        _ = (tenant_ctx, limit)
        return {"titles": ["Clinic Guide"], "headings": ["Hours"]}

    async def get_neighbouring_chunks(self, tenant_ctx: Any, chunk_ids: list[str]) -> list[Any]:
        _ = (tenant_ctx, chunk_ids)
        return []


@pytest.mark.asyncio
async def test_real_graph_run_judge_called_for_two_different_documents() -> None:
    """Real graph run: 2 strong chunks from 2 different document IDs DO call the judge."""
    tenant = TenantContext(
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="user-1",
        role=UserRole.STAFF,
        session_id="sess-1",
    )
    judge_calls: list[ModelRole] = []

    class RealGraphTrackingProvider(DummyModelProvider):
        async def generate(
            self,
            role: ModelRole,
            messages: list[ChatMessage],
            schema: Any = None,
            max_tokens: int | None = None,
        ) -> ModelResponse:
            if role == ModelRole.JUDGE:
                judge_calls.append(role)
                return ModelResponse(
                    content='{"has_conflict": false}',
                    model_id="test-model",
                    model_version="v1",
                    structured_output={"has_conflict": False},
                )
            return await super().generate(role, messages, schema, max_tokens)

    retriever = _JudgeTrackingRetriever(
        evidence=[
            EvidenceId(
                chunk_id="c1",
                page_id="p1",
                document_version_id="ver-doc-A",
                evidence_type=EvidenceType.TEXT,
                tenant_id=tenant.tenant_id,
            ),
            EvidenceId(
                chunk_id="c2",
                page_id="p2",
                document_version_id="ver-doc-B",
                evidence_type=EvidenceType.TEXT,
                tenant_id=tenant.tenant_id,
            ),
        ],
        evidence_text={
            "c1": "[Document: Policy A]\nClinic hours are 9am to 5pm.",
            "c2": "[Document: Policy B]\nClinic hours are 9am to 5pm.",
        },
    )

    ports = GraphPorts(
        retriever=retriever,
        model_provider=RealGraphTrackingProvider(),
        is_mock_profile=True,
    )
    ctx = GraphRuntimeContext(
        ports=ports,
        tenant=tenant,
        question_text="What are clinic hours?",
    )
    state: GraphState = {"request_id": "req-judge-1"}
    graph = build_knowledge_graph().compile()
    res = await graph.ainvoke(state, context=ctx)
    assert res["status"] in ("answered", "completed")
    assert len(judge_calls) == 1


@pytest.mark.asyncio
async def test_real_graph_run_judge_skipped_for_single_document() -> None:
    """Real graph run: 2 strong chunks from 1 document do NOT call the judge."""
    tenant = TenantContext(
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="user-1",
        role=UserRole.STAFF,
        session_id="sess-1",
    )
    judge_calls: list[ModelRole] = []

    class RealGraphTrackingProvider(DummyModelProvider):
        async def generate(
            self,
            role: ModelRole,
            messages: list[ChatMessage],
            schema: Any = None,
            max_tokens: int | None = None,
        ) -> ModelResponse:
            if role == ModelRole.JUDGE:
                judge_calls.append(role)
            return await super().generate(role, messages, schema, max_tokens)

    retriever = _JudgeTrackingRetriever(
        evidence=[
            EvidenceId(
                chunk_id="c1",
                page_id="p1",
                document_version_id="ver-doc-SAME",
                evidence_type=EvidenceType.TEXT,
                tenant_id=tenant.tenant_id,
            ),
            EvidenceId(
                chunk_id="c2",
                page_id="p2",
                document_version_id="ver-doc-SAME",
                evidence_type=EvidenceType.TEXT,
                tenant_id=tenant.tenant_id,
            ),
        ],
        evidence_text={
            "c1": "[Document: Policy Same]\nClinic hours are 9am to 5pm.",
            "c2": "[Document: Policy Same]\nLunch break from 12pm to 1pm.",
        },
    )

    ports = GraphPorts(
        retriever=retriever,
        model_provider=RealGraphTrackingProvider(),
        is_mock_profile=True,
    )
    ctx = GraphRuntimeContext(
        ports=ports,
        tenant=tenant,
        question_text="What are clinic hours?",
    )
    state: GraphState = {"request_id": "req-judge-2"}
    graph = build_knowledge_graph().compile()
    res = await graph.ainvoke(state, context=ctx)
    assert res["status"] in ("answered", "completed")
    assert len(judge_calls) == 0


@pytest.mark.asyncio
async def test_real_graph_run_judge_called_for_two_documents_sharing_same_title() -> None:
    """Real graph run: 2 documents sharing same title with different IDs DO call the judge."""
    tenant = TenantContext(
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="user-1",
        role=UserRole.STAFF,
        session_id="sess-1",
    )
    judge_calls: list[ModelRole] = []

    class RealGraphTrackingProvider(DummyModelProvider):
        async def generate(
            self,
            role: ModelRole,
            messages: list[ChatMessage],
            schema: Any = None,
            max_tokens: int | None = None,
        ) -> ModelResponse:
            if role == ModelRole.JUDGE:
                judge_calls.append(role)
                return ModelResponse(
                    content='{"has_conflict": false}',
                    model_id="test-model",
                    model_version="v1",
                    structured_output={"has_conflict": False},
                )
            return await super().generate(role, messages, schema, max_tokens)

    # Identical title "[Document: Shared Title]" but different document version IDs
    retriever = _JudgeTrackingRetriever(
        evidence=[
            EvidenceId(
                chunk_id="c1",
                page_id="p1",
                document_version_id="ver-doc-UUID-1",
                evidence_type=EvidenceType.TEXT,
                tenant_id=tenant.tenant_id,
            ),
            EvidenceId(
                chunk_id="c2",
                page_id="p2",
                document_version_id="ver-doc-UUID-2",
                evidence_type=EvidenceType.TEXT,
                tenant_id=tenant.tenant_id,
            ),
        ],
        evidence_text={
            "c1": "[Document: Shared Title]\nArrival time is 15 minutes before.",
            "c2": "[Document: Shared Title]\nArrival time is 20 minutes before.",
        },
    )

    ports = GraphPorts(
        retriever=retriever,
        model_provider=RealGraphTrackingProvider(),
        is_mock_profile=True,
    )
    ctx = GraphRuntimeContext(
        ports=ports,
        tenant=tenant,
        question_text="When should I arrive?",
    )
    state: GraphState = {"request_id": "req-judge-3"}
    graph = build_knowledge_graph().compile()
    res = await graph.ainvoke(state, context=ctx)
    assert res["status"] in ("answered", "completed")
    assert len(judge_calls) == 1


@pytest.mark.asyncio
async def test_node_retrieve_fails_closed_when_evidence_lacks_tenant_id(
    base_context: GraphRuntimeContext,
) -> None:
    """An evidence item without a tenant_id raises TenantIsolationError."""
    retriever = DummyRetriever()

    async def _search_no_tenant(*args: Any, **kwargs: Any) -> list[EvidenceId]:
        _ = (args, kwargs)
        return [
            EvidenceId(
                chunk_id="chunk-no-tenant",
                page_id="page-1",
                document_version_id="ver-1",
                evidence_type=EvidenceType.TEXT,
                tenant_id=None,
            )
        ]

    retriever.search = _search_no_tenant  # type: ignore[method-assign]
    base_context.ports = GraphPorts(retriever=retriever)

    class MockRuntime:
        context = base_context

    with pytest.raises(TenantIsolationError):
        await node_retrieve({"request_id": "r1"}, MockRuntime())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_node_retrieve_fails_closed_when_evidence_has_mismatched_tenant_id(
    base_context: GraphRuntimeContext,
) -> None:
    """An evidence item with a mismatched tenant_id raises TenantIsolationError."""
    retriever = DummyRetriever()

    async def _search_wrong_tenant(*args: Any, **kwargs: Any) -> list[EvidenceId]:
        _ = (args, kwargs)
        return [
            EvidenceId(
                chunk_id="chunk-alien",
                page_id="page-1",
                document_version_id="ver-1",
                evidence_type=EvidenceType.TEXT,
                tenant_id="00000000-0000-0000-0000-999999999999",
            )
        ]

    retriever.search = _search_wrong_tenant  # type: ignore[method-assign]
    base_context.ports = GraphPorts(retriever=retriever)

    class MockRuntime:
        context = base_context

    with pytest.raises(TenantIsolationError):
        await node_retrieve({"request_id": "r1"}, MockRuntime())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_short_mode_budget_140_words_not_truncated(
    base_context: GraphRuntimeContext,
) -> None:
    """140-word short answer with quote and citations is not truncated with MAX_TOKENS_SHORT=512."""
    words_140 = "word " * 140
    answer_text = words_140.strip()

    generated_output = {
        "mode": "short",
        "answer": answer_text,
        "evidence_ids": ["chunk-1"],
        "quoted_span": "Clinic hours are 9am to 5pm.",
        "evidence_type": "text",
    }

    recorded_max_tokens: list[int | None] = []

    class TokenCheckingModelProvider(DummyModelProvider):
        async def generate(
            self,
            role: ModelRole,
            messages: list[ChatMessage],
            schema: Any = None,
            max_tokens: int | None = None,
        ) -> ModelResponse:
            recorded_max_tokens.append(max_tokens)
            return await super().generate(role, messages, schema, max_tokens)

    model = TokenCheckingModelProvider(response_text=answer_text, structured=generated_output)
    base_context.ports = GraphPorts(
        retriever=DummyRetriever(),
        model_provider=model,
    )
    base_context.evidence_text = {"chunk-1": "Clinic hours are 9am to 5pm. More info here."}

    class MockRuntime:
        context = base_context

    state: GraphState = {
        "request_id": "r1",
        "answer_mode": "short",
        "route": "knowledge",
        "evidence_ids": ["chunk-1"],
    }
    await node_generate(state, MockRuntime())  # type: ignore[arg-type]
    assert 512 in recorded_max_tokens
    # Validation passes on 140-word answer within 150-word ceiling
    val_res = await node_validate(state, MockRuntime())  # type: ignore[arg-type]
    assert val_res["status"] == "completed"
    assert state.get("validation_attempts", 0) == 0
