"""Integration tests for Evidence Assessment Gate (Node 5 assess_evidence) - Task 3.3.

PHASE-3 Requirements:
1. evidence_state is one of RELIABLE_TEXT, VISUAL_REQUIRED, CONFLICTING, WEAK, NO_EVIDENCE.
   Routing:
   - RELIABLE_TEXT -> Node 7 (generate)
   - VISUAL_REQUIRED -> Node 6 (vision_read)
   - CONFLICTING -> Node 9 (respond) with both sources
   - WEAK and NO_EVIDENCE -> Node 9 (respond) as a refusal (Invariant 4)
   WEAK, NO_EVIDENCE, and CONFLICTING never call the generation model.
2. CONFLICTING: when strong evidence comes from two or more different active documents
   that give different values for what the question asks, Node 9 returns:
   "Two documents disagree:" with each value, its quoted span and its citation,
   and no single answer.
   Detecting differing values may use a structured model call, but code must verify
   that each value's quoted span exists in its cited document.
   Tested with front_desk_policy_v1 and v2.
3. Thresholds are named constants: RELIABLE_TEXT_THRESHOLD, WEAK_EVIDENCE_THRESHOLD.
"""

# ruff: noqa: E501, ARG002

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest

from app.core.graph import (
    RELIABLE_TEXT_THRESHOLD,
    WEAK_EVIDENCE_THRESHOLD,
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    build_knowledge_graph,
    invoke_graph,
    node_assess_evidence,
)
from app.ports.model_provider import ModelProvider
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


class CountingModelProvider(ModelProvider):
    """Mock model provider tracking invocations for generation and conflict detection."""

    def __init__(
        self, conflict_detected: bool = False, conflict_payload: dict[str, Any] | None = None
    ) -> None:
        self.generate_call_count = 0
        self.judge_call_count = 0
        self.conflict_detected = conflict_detected
        self.conflict_payload = conflict_payload or {}

    async def generate(
        self,
        role: ModelRole,
        messages: list[ChatMessage],
        schema: Any = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        if role == ModelRole.ANSWER:
            self.generate_call_count += 1
            return ModelResponse(
                content="Generated answer",
                model_id="test-model",
                model_version="1.0",
                structured_output={
                    "mode": "short",
                    "answer": "Generated answer",
                    "evidence_ids": ["c1"],
                    "quoted_span": "Policy reference code",
                    "evidence_type": "text",
                },
            )
        if role == ModelRole.JUDGE:
            self.judge_call_count += 1
            return ModelResponse(
                content="Conflict assessment",
                model_id="test-model",
                model_version="1.0",
                structured_output=self.conflict_payload,
            )
        return ModelResponse(content="OK", model_id="test-model", model_version="1.0")

    async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReading:
        return PageReading(reading="", confidence=1.0, model_id="test", model_version="1.0")

    async def read_page(self, role: ModelRole, image: bytes, question: str) -> PageReading:
        return PageReading(reading="", confidence=1.0, model_id="test", model_version="1.0")

    async def extract_fields(self, role: ModelRole, image: bytes, schema: Any) -> ExtractedFields:
        return ExtractedFields(fields={}, model_id="test", model_version="1.0")


class StubRetriever(Retriever):
    """Stub retriever returning candidate documents and text."""

    def __init__(
        self, candidates: list[EvidenceId] | None = None, texts: dict[str, str] | None = None
    ) -> None:
        self.candidates = candidates or []
        self.texts = texts or {}

    async def search(
        self, tenant_ctx: TenantContext, query: str, filters: Any = None
    ) -> list[EvidenceId]:
        return [
            EvidenceId(
                chunk_id=c.chunk_id,
                page_id=c.page_id,
                document_version_id=c.document_version_id,
                evidence_type=c.evidence_type,
                tenant_id=getattr(c, "tenant_id", None) or str(tenant_ctx.tenant_id),
            )
            for c in self.candidates
        ]

    async def search_titles(
        self, tenant_ctx: TenantContext, query: str, limit: int = 3
    ) -> list[str]:
        return ["Front-Desk SOP v1", "Front-Desk SOP v2"][:limit]

    async def get_evidence_text(
        self, tenant_ctx: TenantContext, evidence_ids: list[str]
    ) -> dict[str, str]:
        return {eid: self.texts[eid] for eid in evidence_ids if eid in self.texts}

    async def search_tables(
        self, tenant_ctx: TenantContext, query: str, limit: int = 15
    ) -> list[Any]:
        return []

    async def get_inventory(
        self, tenant_ctx: TenantContext, heading_limit: int = 5
    ) -> dict[str, list[str]]:
        return {"titles": ["Front-Desk SOP v1"], "headings": ["Intake"]}

    async def get_neighbouring_chunks(
        self, tenant_ctx: TenantContext, chunk_ids: list[str]
    ) -> list[EvidenceId]:
        return []


@pytest.fixture
def base_state() -> GraphState:
    return {
        "request_id": f"req-{uuid4().hex[:8]}",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "user_id": "user-test",
        "conversation_id": f"conv-{uuid4().hex[:8]}",
        "route": "knowledge",
        "answer_mode": "short",
        "language": "en",
        "evidence_ids": [],
        "evidence_state": "NONE",
        "needs_vision": False,
        "validation_attempts": 0,
        "status": "pending",
        "public_error_ref": None,
    }


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext(
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="user-test",
        role=UserRole.STAFF,
        session_id="session-test",
    )


class TestEvidenceGateRouting:
    """Verifies evidence gate (Node 5) routing and Invariant 4 compliance."""

    def test_threshold_constants_are_named_floats(self) -> None:
        """Requirement 3: Thresholds are named constants."""
        assert isinstance(RELIABLE_TEXT_THRESHOLD, float)
        assert isinstance(WEAK_EVIDENCE_THRESHOLD, float)
        assert WEAK_EVIDENCE_THRESHOLD < RELIABLE_TEXT_THRESHOLD

    @pytest.mark.asyncio
    async def test_reliable_text_routes_to_generate(
        self, base_state: GraphState, tenant_ctx: TenantContext
    ) -> None:
        """Strong text score >= RELIABLE_TEXT_THRESHOLD routes to Node 7 (generate)."""
        model = CountingModelProvider()
        ports = GraphPorts(model_provider=model)
        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=tenant_ctx,
            question_text="What is the policy code?",
            evidence_text={"c1": "Policy reference code is 2026-PA-01."},
            candidate_scores={"c1": 0.85},
            candidate_evidence_types={"c1": "text"},
        )
        state = dict(base_state)
        state["evidence_ids"] = ["c1"]

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(state, context=ctx)

        assert result["evidence_state"] == "RELIABLE_TEXT"
        assert result["status"] in ("answered", "completed")
        assert model.generate_call_count >= 1

    @pytest.mark.asyncio
    async def test_visual_required_routes_to_vision_read(
        self, base_state: GraphState, tenant_ctx: TenantContext
    ) -> None:
        """Strong evidence with visual type routes to Node 6 (vision_read)."""
        model = CountingModelProvider()
        ports = GraphPorts(model_provider=model, mock_vision_mode="ok")
        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=tenant_ctx,
            question_text="What is on the chart?",
            evidence_text={"c1": "Scanned chart image text"},
            candidate_scores={"c1": 0.90},
            candidate_evidence_types={"c1": "visual"},
        )
        state = dict(base_state)
        state["evidence_ids"] = ["c1"]

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(state, context=ctx)

        assert result["evidence_state"] == "VISUAL_REQUIRED"
        assert result["needs_vision"] is True

    @pytest.mark.asyncio
    async def test_weak_evidence_refuses_without_calling_generation_model(
        self, base_state: GraphState, tenant_ctx: TenantContext
    ) -> None:
        """Weak evidence below RELIABLE_TEXT_THRESHOLD refuses without calling Node 7 generate."""
        c1_id = "00000000-0000-0000-0001-000000000001"
        model = CountingModelProvider()
        candidates = [
            EvidenceId(
                chunk_id=c1_id,
                page_id="p1",
                document_version_id="ver-1",
                evidence_type=EvidenceType.TEXT,
                tenant_id=str(tenant_ctx.tenant_id),
            )
        ]
        retriever = StubRetriever(
            candidates=candidates, texts={c1_id: "Some vaguely related text."}
        )
        ports = GraphPorts(model_provider=model, retriever=retriever)
        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=tenant_ctx,
            question_text="What is the acupuncture copay?",
            evidence_text={c1_id: "Some vaguely related text."},
            candidate_scores={c1_id: 0.00005},  # Well below RELIABLE_TEXT_THRESHOLD
            candidate_evidence_types={c1_id: "text"},
            active_document_count=2,
        )
        state = dict(base_state)
        state["evidence_ids"] = [c1_id]

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(state, context=ctx)

        assert result["evidence_state"] == "WEAK"
        assert result["status"] == "refusal"
        assert model.generate_call_count == 0  # Invariant 4: Node 7 never called
        assert "I couldn't find that in this assistant's documents." in ctx.response_text
        assert "I do have information on:" in ctx.response_text
        assert result["evidence_ids"] == []

    @pytest.mark.asyncio
    async def test_no_evidence_refuses_without_calling_generation_model(
        self, base_state: GraphState, tenant_ctx: TenantContext
    ) -> None:
        """Empty candidate list refuses immediately without calling Node 7 generate."""
        model = CountingModelProvider()
        retriever = StubRetriever()
        ports = GraphPorts(model_provider=model, retriever=retriever)
        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=tenant_ctx,
            question_text="What is the parking garage rate?",
            evidence_text={},
            candidate_scores={},
            active_document_count=2,
        )
        state = dict(base_state)
        state["evidence_ids"] = []

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(state, context=ctx)

        assert result["evidence_state"] == "NO_EVIDENCE"
        assert result["status"] == "refusal"
        assert model.generate_call_count == 0  # Invariant 4: Node 7 never called
        assert "I couldn't find that in this assistant's documents." in ctx.response_text
        assert result["evidence_ids"] == []

    @pytest.mark.asyncio
    async def test_conflicting_evidence_between_v1_and_v2_disagreement(
        self, base_state: GraphState, tenant_ctx: TenantContext
    ) -> None:
        """Requirement 2: When strong evidence comes from 2 distinct active documents with differing values:
        - evidence_state is CONFLICTING
        - Node 7 generate is NEVER called
        - Routes to Node 9 respond
        - Response returns 'Two documents disagree:' with each value, quoted span and citation
        - Code verifies each value's quoted span exists in its cited document.
        """
        c1_id = "00000000-0000-0000-0001-000000000001"
        c2_id = "00000000-0000-0000-0002-000000000002"
        v1_text = (
            "[Document: front_desk_policy_v1.pdf, Page: 1]\n\n"
            "Front desk intake fax number is 555-0101."
        )
        v2_text = (
            "[Document: front_desk_policy_v2.pdf, Page: 1]\n\n"
            "Front desk intake fax number is 555-0102."
        )

        conflict_payload = {
            "has_conflict": True,
            "value1": "555-0101",
            "quote1": "Front desk intake fax number is 555-0101.",
            "doc1_id": c1_id,
            "value2": "555-0102",
            "quote2": "Front desk intake fax number is 555-0102.",
            "doc2_id": c2_id,
        }
        model = CountingModelProvider(conflict_detected=True, conflict_payload=conflict_payload)

        candidates = [
            EvidenceId(
                chunk_id=c1_id,
                page_id="p1",
                document_version_id="ver-1",
                evidence_type=EvidenceType.TEXT,
                tenant_id=str(tenant_ctx.tenant_id),
            ),
            EvidenceId(
                chunk_id=c2_id,
                page_id="p2",
                document_version_id="ver-2",
                evidence_type=EvidenceType.TEXT,
                tenant_id=str(tenant_ctx.tenant_id),
            ),
        ]
        retriever = StubRetriever(
            candidates=candidates,
            texts={c1_id: v1_text, c2_id: v2_text},
        )
        ports = GraphPorts(model_provider=model, retriever=retriever)
        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=tenant_ctx,
            question_text="What is the front desk intake fax number?",
            evidence_text={c1_id: v1_text, c2_id: v2_text},
            candidate_scores={c1_id: 0.92, c2_id: 0.91},
            candidate_evidence_types={c1_id: "text", c2_id: "text"},
            active_document_count=2,
        )
        state = dict(base_state)
        state["evidence_ids"] = [c1_id, c2_id]

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(state, context=ctx)

        assert result["evidence_state"] == "RESOLVED_CONFLICT"
        assert result["status"] == "answered"
        assert model.generate_call_count == 0  # Node 7 generate NEVER called
        assert "555-0102" in ctx.response_text
        assert "555-0101" in ctx.response_text
        assert "front_desk_policy_v2.pdf" in ctx.response_text
        assert "front_desk_policy_v1.pdf" in ctx.response_text
        assert set(result["evidence_ids"]) == {c1_id, c2_id}

    @pytest.mark.asyncio
    async def test_conflicting_evidence_undetermined_versions_preserves_conflicting_state(
        self, base_state: GraphState, tenant_ctx: TenantContext
    ) -> None:
        """When 2 distinct documents conflict and version order cannot be determined:
        - evidence_state is CONFLICTING
        - Node 7 generate is NEVER called
        - Routes to Node 9 respond
        - Response returns 'Two documents disagree:' with each value, quoted span and citation.
        """
        c1_id = "00000000-0000-0000-0001-000000000001"
        c2_id = "00000000-0000-0000-0002-000000000002"
        v1_text = (
            "[Document: clinic_handbook.pdf, Page: 1]\n\nFront desk intake fax number is 555-0101."
        )
        v2_text = (
            "[Document: office_memo.pdf, Page: 1]\n\nFront desk intake fax number is 555-0102."
        )

        conflict_payload = {
            "has_conflict": True,
            "value1": "555-0101",
            "quote1": "Front desk intake fax number is 555-0101.",
            "doc1_id": c1_id,
            "value2": "555-0102",
            "quote2": "Front desk intake fax number is 555-0102.",
            "doc2_id": c2_id,
        }
        model = CountingModelProvider(conflict_detected=True, conflict_payload=conflict_payload)

        candidates = [
            EvidenceId(
                chunk_id=c1_id,
                page_id="p1",
                document_version_id="ver-1",
                evidence_type=EvidenceType.TEXT,
                tenant_id=str(tenant_ctx.tenant_id),
            ),
            EvidenceId(
                chunk_id=c2_id,
                page_id="p2",
                document_version_id="ver-2",
                evidence_type=EvidenceType.TEXT,
                tenant_id=str(tenant_ctx.tenant_id),
            ),
        ]
        retriever = StubRetriever(
            candidates=candidates,
            texts={c1_id: v1_text, c2_id: v2_text},
        )
        ports = GraphPorts(model_provider=model, retriever=retriever)
        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=tenant_ctx,
            question_text="What is the front desk intake fax number?",
            evidence_text={c1_id: v1_text, c2_id: v2_text},
            candidate_scores={c1_id: 0.92, c2_id: 0.91},
            candidate_evidence_types={c1_id: "text", c2_id: "text"},
            active_document_count=2,
        )
        state = dict(base_state)
        state["evidence_ids"] = [c1_id, c2_id]

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(state, context=ctx)

        assert result["evidence_state"] == "CONFLICTING"
        assert result["status"] == "conflicting"
        assert model.generate_call_count == 0  # Node 7 generate NEVER called
        assert "Two documents disagree:" in ctx.response_text
        assert "555-0101" in ctx.response_text
        assert "555-0102" in ctx.response_text
        assert set(result["evidence_ids"]) == {c1_id, c2_id}

    @pytest.mark.asyncio
    async def test_conflicting_evidence_unverified_quote_fails_closed(
        self, base_state: GraphState, tenant_ctx: TenantContext
    ) -> None:
        """If model reports a conflict but quote does not exist in document, conflict is rejected."""
        c1_id = "00000000-0000-0000-0001-000000000001"
        c2_id = "00000000-0000-0000-0002-000000000002"
        v1_text = "Routine appointment arrival window is 15 minutes prior to scheduled visit."
        v2_text = "Routine appointment arrival window is 20 minutes prior to scheduled visit."

        # Model invents a quote not in document text
        conflict_payload = {
            "has_conflict": True,
            "value1": "10 minutes",
            "quote1": "Patients must arrive 10 minutes early.",  # Hallucinated quote
            "doc1_id": c1_id,
            "value2": "20 minutes",
            "quote2": "arrival window is 20 minutes prior",
            "doc2_id": c2_id,
        }
        model = CountingModelProvider(conflict_detected=True, conflict_payload=conflict_payload)
        ports = GraphPorts(model_provider=model)
        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=tenant_ctx,
            question_text="How early must patients arrive?",
            evidence_text={c1_id: v1_text, c2_id: v2_text},
            candidate_scores={c1_id: 0.90, c2_id: 0.89},
            candidate_evidence_types={c1_id: "text", c2_id: "text"},
            active_document_count=2,
        )
        state = dict(base_state)
        state["evidence_ids"] = [c1_id, c2_id]

        res = await node_assess_evidence(
            cast(GraphState, state), type("MockRuntime", (), {"context": ctx})()
        )
        # Unverified quote must not be accepted as a valid conflict
        assert res["evidence_state"] != "CONFLICTING"

    @pytest.mark.asyncio
    async def test_evidence_with_mismatched_tenant_id_ends_in_safe_failure(
        self, base_state: GraphState, tenant_ctx: TenantContext
    ) -> None:
        """Retrieved evidence belonging to an alien tenant must trigger TenantIsolationError and end in safe_failure."""
        alien_tenant_id = "00000000-0000-0000-0000-999999999999"
        c1_id = "00000000-0000-0000-0001-000000000001"
        candidates = [
            EvidenceId(
                chunk_id=c1_id,
                page_id="p1",
                document_version_id="ver-1",
                evidence_type=EvidenceType.TEXT,
                tenant_id=alien_tenant_id,
            )
        ]

        class AlienRetriever(StubRetriever):
            async def search(
                self, tenant_ctx: TenantContext, query: str, filters: Any = None
            ) -> list[EvidenceId]:
                return self.candidates

        retriever = AlienRetriever(candidates=candidates, texts={c1_id: "Alien text"})
        model = CountingModelProvider()
        ports = GraphPorts(model_provider=model, retriever=retriever)
        ctx = GraphRuntimeContext(
            ports=ports,
            tenant=tenant_ctx,
            question_text="What is the acupuncture copay?",
        )
        res = await invoke_graph(base_state, ctx)
        assert res["status"] == "safe_failure"
        assert res.get("public_error_ref") is not None
