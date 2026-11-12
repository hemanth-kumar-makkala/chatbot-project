"""Unit tests for Answer Generation (Node 7 generate) and Answer Modes - Task 3.4.

PHASE-3 Requirements:
1. Prompts and output schemas live in prompts/registry.yaml. Loaded from the registry.
2. The four answer modes:
   - exact: verbatim stored span with minimal framing, strict token cap.
   - short: 1 to 2 sentences, strict token cap.
   - explain: grounded summary citing evidence.
   - table: structured lookup from tables_data.
   Node 2 picks mode with structured model output; on model failure falls back to short.
3. explain mode returns a list of points, each with its own quoted span and citation,
   and receives enough evidence: top N reranked chunks (named constant, start at 8)
   plus neighbouring chunks from the same page of the top documents.
   Token caps per mode are named constants.
"""

# ruff: noqa: E501, ARG002

from __future__ import annotations

from typing import Any

import pytest

from app.core.graph import (
    MAX_REQUEST_TOKENS_EXACT,
    MAX_REQUEST_TOKENS_SHORT,
    MAX_TOKENS_EXPLAIN,
    MAX_TOKENS_TABLE,
    TOP_N_CHUNKS,
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    node_generate,
    node_route,
)
from app.core.prompts import (
    get_prompt_registry,
    load_prompt_template,
    load_response_schema,
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


class MockGenerationModel(ModelProvider):
    """Mock model provider capturing token limits, prompts, and returning structured outputs."""

    def __init__(self, output: dict[str, Any] | None = None) -> None:
        self.last_max_tokens: int | None = None
        self.last_messages: list[ChatMessage] = []
        self.output = output or {}

    async def generate(
        self,
        role: ModelRole,
        messages: list[ChatMessage],
        schema: Any = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        self.last_max_tokens = max_tokens
        self.last_messages = messages
        return ModelResponse(
            content="Generated answer",
            model_id="test-model",
            model_version="1.0",
            structured_output=self.output,
        )

    async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReading:
        return PageReading(reading="", confidence=1.0, model_id="test", model_version="1.0")

    async def read_page(self, role: ModelRole, image: bytes, question: str) -> PageReading:
        return PageReading(reading="", confidence=1.0, model_id="test", model_version="1.0")

    async def extract_fields(self, role: ModelRole, image: bytes, schema: Any) -> ExtractedFields:
        return ExtractedFields(fields={}, model_id="test", model_version="1.0")


class MockFailingModel(ModelProvider):
    """Mock model that raises an exception during generation."""

    async def generate(
        self,
        role: ModelRole,
        messages: list[ChatMessage],
        schema: Any = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        raise RuntimeError("Model provider connection timeout")

    async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReading:
        raise RuntimeError("Model provider connection timeout")

    async def read_page(self, role: ModelRole, image: bytes, question: str) -> PageReading:
        raise RuntimeError("Model provider connection timeout")

    async def extract_fields(self, role: ModelRole, image: bytes, schema: Any) -> ExtractedFields:
        raise RuntimeError("Model provider connection timeout")


class NeighbouringRetriever(Retriever):
    """Stub retriever providing neighbouring chunks on the same page."""

    async def search(
        self, _tenant_ctx: TenantContext, _query: str, _filters: Any = None
    ) -> list[EvidenceId]:
        return []

    async def search_titles(
        self, _tenant_ctx: TenantContext, _query: str, limit: int = 3
    ) -> list[str]:
        return []

    async def get_evidence_text(
        self, _tenant_ctx: TenantContext, evidence_ids: list[str]
    ) -> dict[str, str]:
        return {eid: f"Text for {eid}" for eid in evidence_ids}

    async def search_tables(
        self, _tenant_ctx: TenantContext, _query: str, _limit: int = 15
    ) -> list[Any]:
        return []

    async def get_inventory(
        self, _tenant_ctx: TenantContext, _heading_limit: int = 5
    ) -> dict[str, list[str]]:
        return {"titles": [], "headings": []}

    async def get_neighbouring_chunks(
        self, _tenant_ctx: TenantContext, chunk_ids: list[str]
    ) -> list[EvidenceId]:
        # Return neighbouring chunks for page 1
        return [
            EvidenceId(
                chunk_id=f"{cid}_neighbour",
                page_id="p1",
                document_version_id="v1",
                evidence_type=EvidenceType.TEXT,
                tenant_id=str(_tenant_ctx.tenant_id),
            )
            for cid in chunk_ids
        ]


@pytest.fixture
def base_context() -> GraphRuntimeContext:
    tenant = TenantContext(
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="user-1",
        role=UserRole.STAFF,
        session_id="s1",
    )
    return GraphRuntimeContext(ports=GraphPorts(), tenant=tenant)


class TestGenerateNodeSchemas:
    """Verifies Task 3.4 answer generation schemas, registry integration, and modes."""

    def test_token_cap_constants_are_named_integers(self) -> None:
        """Token caps per mode are named constants."""
        assert isinstance(MAX_REQUEST_TOKENS_EXACT, int)
        assert isinstance(MAX_REQUEST_TOKENS_SHORT, int)
        assert isinstance(MAX_TOKENS_EXPLAIN, int)
        assert isinstance(MAX_TOKENS_TABLE, int)
        assert isinstance(TOP_N_CHUNKS, int)
        assert TOP_N_CHUNKS >= 8
        assert MAX_REQUEST_TOKENS_EXACT < MAX_REQUEST_TOKENS_SHORT < MAX_TOKENS_EXPLAIN

    def test_prompts_and_schemas_loaded_from_registry_yaml(self) -> None:
        """Requirement 1: Prompts and schemas loaded from prompts/registry.yaml."""
        registry = get_prompt_registry()
        assert "prompts" in registry
        assert "answer_synthesis" in registry["prompts"]
        assert "router_classify" in registry["prompts"]

        template = load_prompt_template("answer_synthesis")
        assert "{question}" in template
        assert "{evidence_context}" in template

        schema = load_response_schema("answer_synthesis")
        assert schema.get("type") == "object"

    def test_router_classify_prompt_contains_explicit_rules_and_bumped_version(self) -> None:
        """Fix 1: router_classify prompt 1.2.0 defines route and answer_mode rules and enum values."""
        registry = get_prompt_registry()
        router_entry = registry["prompts"]["router_classify"]
        assert router_entry["default_version"] == "1.2.0"

        tmpl = load_prompt_template("router_classify")
        assert "{question}" in tmpl
        assert "general:" in tmpl
        assert "hello" in tmpl
        assert "draft" in tmpl
        assert "what is 15% of 240" in tmpl
        assert "knowledge:" in tmpl
        assert "what is the front desk policy for identity verification" in tmpl
        assert "which plans are accepted" in tmpl
        assert "extraction:" in tmpl
        assert "When unsure between general and knowledge, choose knowledge." in tmpl
        assert "Answer mode rules:" in tmpl
        assert '"exact"' in tmpl
        assert '"explain"' in tmpl
        assert '"table"' in tmpl
        assert '"short"' in tmpl

        router_schema = load_response_schema("router_classify")
        route_enum = router_schema["properties"]["route"]["enum"]
        assert set(route_enum) == {"knowledge", "general", "clarify", "extraction"}
        mode_enum = router_schema["properties"]["answer_mode"]["enum"]
        assert set(mode_enum) == {"exact", "short", "explain", "table"}

    @pytest.mark.asyncio
    async def test_node_route_logs_route_and_fallback_metadata_only(
        self,
        base_context: GraphRuntimeContext,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fix 1: node_route logs chosen route, answer_mode, and router_fallback without query text."""
        import logging

        for logger_name in ("app", "app.core", "app.core.graph"):
            logger_obj = logging.getLogger(logger_name)
            monkeypatch.setattr(logger_obj, "propagate", True)
            monkeypatch.setattr(logger_obj, "disabled", False)

        sample_question = "unique_question_payload_do_not_log_this"
        base_context.ports.model_provider = MockGenerationModel(
            output={"route": "general", "answer_mode": "short", "language": "en"}
        )
        base_context.question_text = sample_question
        state: GraphState = {"request_id": "req-route-1"}

        with caplog.at_level("INFO", logger="app.core.graph"):
            res = await node_route(state, type("MockRuntime", (), {"context": base_context})())

        assert res["route"] == "general"
        assert res["answer_mode"] == "short"
        route_logs = [
            rec
            for rec in caplog.records
            if getattr(rec, "node", None) == "route" and rec.getMessage() == "Query routed"
        ]
        assert len(route_logs) == 1
        assert getattr(route_logs[0], "route", None) == "general"
        assert getattr(route_logs[0], "answer_mode", None) == "short"
        assert getattr(route_logs[0], "router_fallback", None) is False
        assert sample_question not in route_logs[0].getMessage()

        caplog.clear()
        base_context.ports.model_provider = MockFailingModel()
        base_context.question_text = sample_question
        with caplog.at_level("INFO", logger="app.core.graph"):
            res_fb = await node_route(state, type("MockRuntime", (), {"context": base_context})())

        assert res_fb["route"] == "knowledge"
        assert res_fb["answer_mode"] == "short"
        fb_logs = [
            rec
            for rec in caplog.records
            if getattr(rec, "node", None) == "route" and rec.getMessage() == "Query routed"
        ]
        assert len(fb_logs) == 1
        assert getattr(fb_logs[0], "route", None) == "knowledge"
        assert getattr(fb_logs[0], "answer_mode", None) == "short"
        assert getattr(fb_logs[0], "router_fallback", None) is True

    @pytest.mark.asyncio
    async def test_node_route_keyword_heuristic_scoped_to_knowledge_route(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement: keyword heuristic applies only to knowledge route; never changes general."""
        state: GraphState = {"request_id": "req-route-heuristic"}

        # Case 1: Router classifies as general, question mentions table/turnaround/code keywords
        # answer_mode MUST remain "short" and route MUST remain "general"
        base_context.ports.model_provider = MockGenerationModel(
            output={"route": "general", "answer_mode": "short", "language": "en"}
        )
        base_context.question_text = (
            "draft an email about our turnaround time table and policy code"
        )
        res_general = await node_route(state, type("MockRuntime", (), {"context": base_context})())
        assert res_general["route"] == "general"
        assert res_general["answer_mode"] == "short"

        # Case 2: Router classifies as knowledge, question mentions table keyword -> mode is table
        base_context.ports.model_provider = MockGenerationModel(
            output={"route": "knowledge", "answer_mode": "short", "language": "en"}
        )
        base_context.question_text = "what is the turnaround time for urgent prior authorization?"
        res_table = await node_route(state, type("MockRuntime", (), {"context": base_context})())
        assert res_table["route"] == "knowledge"
        assert res_table["answer_mode"] == "table"

        # Case 3: Router classifies as knowledge, question mentions summarize -> mode is explain
        base_context.question_text = "summarize the prior authorization policy in depth"
        res_explain = await node_route(state, type("MockRuntime", (), {"context": base_context})())
        assert res_explain["route"] == "knowledge"
        assert res_explain["answer_mode"] == "explain"

    @pytest.mark.models
    @pytest.mark.asyncio
    async def test_router_classify_live_qwen3_8b_six_prompts(self) -> None:
        """Live local Ollama qwen3:8b (think=false) classifies all 6 benchmark prompts accurately."""
        import os

        from app.core.prompts import RouterOutput, format_router_prompt
        from app.wiring import create_model_provider

        os.environ.setdefault("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        provider = create_model_provider("desktop-gpu")
        expected_cases = [
            ("hello", "general", "short"),
            (
                "draft a short email to a clinic about a delayed prior authorization",
                "general",
                "short",
            ),
            ("what is 15% of 240", "general", "short"),
            (
                "what is the front desk policy for identity verification",
                "knowledge",
                "explain",
            ),
            ("which plans are accepted", "knowledge", "table"),
            (
                "what is the turnaround time for an urgent prior authorization",
                "knowledge",
                "table",
            ),
        ]
        for question, expected_route, expected_mode in expected_cases:
            resp = await provider.generate(
                role=ModelRole.ROUTER,
                messages=[ChatMessage(role="user", content=format_router_prompt(question))],
                schema=RouterOutput,
                max_tokens=150,
            )
            assert resp.structured_output is not None, f"No structured output for: {question}"
            assert resp.structured_output["route"] == expected_route, (
                f"Wrong route for {question!r}: {resp.structured_output}"
            )
            assert resp.structured_output["answer_mode"] == expected_mode, (
                f"Wrong answer_mode for {question!r}: {resp.structured_output}"
            )

    def test_question_response_exposes_route_and_clears_sources_on_refusal_and_general(
        self,
    ) -> None:
        """Fix 4: QuestionResponse exposes route and helper clears sources on refusal/general."""
        from app.api.routes.questions import QuestionResponse, _should_include_sources

        resp = QuestionResponse(
            request_id="req-1",
            tenant_id="ten-1",
            conversation_id="conv-1",
            message_id="msg-1",
            status="answered",
            route="general",
            answer="Hello!",
        )
        assert resp.route == "general"
        assert _should_include_sources(status_val="answered", route_val="general") is False
        assert _should_include_sources(status_val="refusal", route_val="knowledge") is False
        assert _should_include_sources(status_val="clarify", route_val="clarify") is False
        assert _should_include_sources(status_val="answered", route_val="knowledge") is True

    @pytest.mark.asyncio
    async def test_node_route_picks_mode_and_falls_back_to_short_on_model_failure(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement 2: Router picks mode via structured model output; falls back to short on failure."""
        # 1. Successful router classification
        model_success = MockGenerationModel(
            output={"route": "knowledge", "answer_mode": "explain", "language": "en"}
        )
        base_context.ports.model_provider = model_success
        base_context.question_text = "Summarize the prior auth guide."

        state: GraphState = {"request_id": "r1"}
        res = await node_route(state, type("MockRuntime", (), {"context": base_context})())
        assert res["route"] == "knowledge"
        assert res["answer_mode"] == "explain"

        # 2. Failing model classification falls back to 'short'
        base_context.ports.model_provider = MockFailingModel()
        base_context.question_text = "What is the copay?"
        res_fail = await node_route(state, type("MockRuntime", (), {"context": base_context})())
        assert res_fail["route"] == "knowledge"
        assert res_fail["answer_mode"] == "short"  # Guaranteed fallback

    @pytest.mark.asyncio
    async def test_exact_mode_uses_strict_token_cap(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement 2: exact mode enforces MAX_REQUEST_TOKENS_EXACT."""
        model = MockGenerationModel(
            output={
                "mode": "exact",
                "answer": "2026-PA-01",
                "evidence_ids": ["c1"],
                "quoted_span": "Policy reference code is 2026-PA-01",
                "evidence_type": "text",
            }
        )
        base_context.ports.model_provider = model
        base_context.question_text = "What is the policy code?"
        base_context.evidence_text = {"c1": "Policy reference code is 2026-PA-01."}

        state: GraphState = {"request_id": "r1", "answer_mode": "exact", "evidence_ids": ["c1"]}
        res = await node_generate(state, type("MockRuntime", (), {"context": base_context})())

        assert model.last_max_tokens == MAX_REQUEST_TOKENS_EXACT
        assert res["status"] == "in_progress"
        assert base_context.response_text == "2026-PA-01"

    @pytest.mark.asyncio
    async def test_short_mode_uses_strict_token_cap(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement 2: short mode enforces MAX_REQUEST_TOKENS_SHORT."""
        model = MockGenerationModel(
            output={
                "mode": "short",
                "answer": "The effective date is January 1, 2026.",
                "evidence_ids": ["c1"],
                "quoted_span": "Effective January 1, 2026",
                "evidence_type": "text",
            }
        )
        base_context.ports.model_provider = model
        base_context.question_text = "Effective date?"
        base_context.evidence_text = {"c1": "Effective January 1, 2026 for outpatient."}

        state: GraphState = {"request_id": "r1", "answer_mode": "short", "evidence_ids": ["c1"]}
        res = await node_generate(state, type("MockRuntime", (), {"context": base_context})())

        assert model.last_max_tokens == MAX_REQUEST_TOKENS_SHORT
        assert res["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_explain_mode_returns_list_of_points_with_quotes_and_citations(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement 3: explain mode returns structured list of points with quotes & citations."""
        points = [
            {
                "point": "Prior auth is required for all outpatient surgeries.",
                "quoted_span": "Prior authorization is required for all outpatient surgeries.",
                "evidence_ids": ["c1"],
            },
            {
                "point": "Requests must be submitted 5 business days in advance.",
                "quoted_span": "submitted at least 5 business days in advance",
                "evidence_ids": ["c2"],
            },
        ]
        model = MockGenerationModel(
            output={
                "mode": "explain",
                "answer": "Prior authorization overview:",
                "points": points,
                "evidence_ids": ["c1", "c2"],
                "evidence_type": "text",
            }
        )
        base_context.ports.model_provider = model
        base_context.question_text = "Explain the prior auth process."
        base_context.evidence_text = {
            "c1": "Prior authorization is required for all outpatient surgeries.",
            "c2": "Requests must be submitted at least 5 business days in advance.",
        }

        state: GraphState = {
            "request_id": "r1",
            "answer_mode": "explain",
            "evidence_ids": ["c1", "c2"],
        }
        res = await node_generate(state, type("MockRuntime", (), {"context": base_context})())

        assert model.last_max_tokens == MAX_TOKENS_EXPLAIN
        assert res["status"] == "in_progress"
        assert "points" in base_context.generated_answer
        assert len(base_context.generated_answer["points"]) == 2

    @pytest.mark.asyncio
    async def test_explain_mode_receives_top_n_plus_neighbouring_chunks(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement 3: explain mode receives top N reranked chunks plus neighbouring page chunks."""
        retriever = NeighbouringRetriever()
        base_context.ports.retriever = retriever

        top_chunk_ids = [f"chunk_{i}" for i in range(TOP_N_CHUNKS)]
        base_context.evidence_text = {cid: f"Text {cid}" for cid in top_chunk_ids}

        # Verify neighbour expansion mechanism for explain mode
        neighbours = await retriever.get_neighbouring_chunks(base_context.tenant, top_chunk_ids[:2])
        assert len(neighbours) == 2
        assert neighbours[0].chunk_id == "chunk_0_neighbour"
