"""End-to-End integration tests for all 8 graph execution paths on Mock profile (Task 1.6).

Verifies the 8 required paths:
1. reliable text answer
2. visual answer
3. general task
4. clarify
5. extraction
6. conflicting evidence
7. refusal
8. safe_failure
"""

import pytest

from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    build_knowledge_graph,
    invoke_graph,
)


class TestGraphEndToEndPaths:
    """Verifies all 8 paths through the LangGraph StateGraph on mock profile."""

    @pytest.fixture
    def base_state(self) -> GraphState:
        return {
            "request_id": "req-e2e-1",
            "tenant_id": "ten-e2e-1",
            "user_id": "user-e2e-1",
            "conversation_id": "conv-e2e-1",
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

    async def test_e2e_reliable_text_answer(self, base_state: GraphState) -> None:
        """Path 1: Standard knowledge route with reliable text evidence -> answered."""
        ports = GraphPorts()
        ctx = GraphRuntimeContext(
            ports=ports,
            question_text="What are office hours?",
            evidence_text={"c1": "Office hours are 9am to 5pm Monday through Friday."},
        )

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(base_state, context=ctx)

        assert result["status"] in ("answered", "completed")
        assert result["route"] == "knowledge"
        assert result["evidence_state"] == "RELIABLE_TEXT"
        assert len(result["evidence_ids"]) > 0

    async def test_e2e_visual_answer(self, base_state: GraphState) -> None:
        """Path 2: Visual required evidence -> vision read -> generate -> validated."""
        ports = GraphPorts(mock_vision_mode="ok")
        ctx = GraphRuntimeContext(
            ports=ports,
            question_text="What is the fax number in the scan?",
            evidence_text={"v1": "Fax: 555-0199"},
        )
        state = dict(base_state)
        state["route"] = "knowledge"
        state["evidence_state"] = "VISUAL_REQUIRED"
        state["needs_vision"] = True

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(state, context=ctx)

        assert result["status"] in ("answered", "completed")
        assert result["needs_vision"] is True

    async def test_e2e_general_task(self, base_state: GraphState) -> None:
        """Path 3: General conversational or greeting task -> generate without retrieval."""
        ports = GraphPorts()
        ctx = GraphRuntimeContext(
            ports=ports,
            question_text="Hello, how can you help me today?",
        )
        state = dict(base_state)
        state["route"] = "general"

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(state, context=ctx)

        assert result["status"] in ("answered", "completed")
        assert result["route"] == "general"
        assert len(result["evidence_ids"]) == 0

    async def test_e2e_clarify(self, base_state: GraphState) -> None:
        """Path 4: Vague question -> clarify with top 3 grounded titles."""
        ports = GraphPorts(mock_titles=["Prior Authorization", "Intake Workflow", "Referrals"])
        ctx = GraphRuntimeContext(ports=ports, question_text="Help with form")
        state = dict(base_state)
        state["route"] = "clarify"

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(state, context=ctx)

        assert result["status"] == "clarify"
        assert result["route"] == "clarify"

    async def test_e2e_extraction(self, base_state: GraphState) -> None:
        """Path 5: Extraction route -> runs extraction subgraph -> shared respond."""
        ports = GraphPorts()
        ctx = GraphRuntimeContext(
            ports=ports,
            question_text="Extract fields from intake form",
            raw_document_bytes=b"\x89PNG\r\n\x1a\nfakeimage",
        )
        state = dict(base_state)
        state["route"] = "extraction"

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(state, context=ctx)

        assert result["status"] in ("extracted", "completed")
        assert result["route"] == "extraction"
        assert "extracted_fields" in result

    async def test_e2e_conflicting_evidence(self, base_state: GraphState) -> None:
        """Path 6: Conflicting evidence detected at gate -> respond with both sources."""
        ports = GraphPorts()
        ctx = GraphRuntimeContext(
            ports=ports,
            question_text="Copay rate",
            evidence_text={
                "c1": "Copay is $20.",
                "c2": "Copay is $30.",
            },
        )
        state = dict(base_state)
        state["evidence_state"] = "CONFLICTING"

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(state, context=ctx)

        assert result["status"] == "conflicting"
        assert result["evidence_state"] == "CONFLICTING"

    async def test_e2e_refusal(self, base_state: GraphState) -> None:
        """Path 7: Weak or no evidence -> refuse to answer."""
        ports = GraphPorts()
        ctx = GraphRuntimeContext(
            ports=ports,
            question_text="What is the CEO personal phone number?",
        )
        state = dict(base_state)
        state["evidence_state"] = "NO_EVIDENCE"

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(state, context=ctx)

        assert result["status"] == "refusal"

    async def test_e2e_safe_failure(self, base_state: GraphState) -> None:
        """Path 8: Non-retryable error during execution -> safe_failure with ERR reference."""
        ports = GraphPorts(force_non_retryable_error=True)
        ctx = GraphRuntimeContext(ports=ports, question_text="Trigger failure")

        result = await invoke_graph(base_state, context=ctx)

        assert result["status"] == "safe_failure"
        assert result["public_error_ref"] is not None
        assert result["public_error_ref"].startswith("ERR-")
