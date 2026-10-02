"""Unit tests for retrieval fallbacks and evidence gate enforcement (Task 1.6).

Verifies:
- Vector search failure falls back to keyword full-text search.
- Keyword search failure falls back to vector search.
- Reranker failure falls back to RRF retrieval order.
- Evidence gate (Node 5 assess_evidence) is never bypassed under any fallback.
- Clarify route fetches grounded titles from Retriever.search_titles, not model generation.
"""

import pytest

from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    build_knowledge_graph,
)


class TestRetrievalFallbacks:
    """Verifies internal node fallbacks and evidence gate invariant."""

    @pytest.fixture
    def base_state(self) -> GraphState:
        return {
            "request_id": "req-ret-1",
            "tenant_id": "ten-1",
            "user_id": "usr-1",
            "conversation_id": "conv-1",
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

    async def test_retrieve_vector_failure_falls_back_to_keyword(
        self, base_state: GraphState
    ) -> None:
        """When vector retrieval fails, node continues using keyword results."""
        ports = GraphPorts(mock_vector_down=True)
        ctx = GraphRuntimeContext(
            ports=ports,
            question_text="Sample query",
            evidence_text={"kw_chunk_1": "Keyword grounded content."},
        )

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(base_state, context=ctx)

        # Must have completed without crashing and retrieved keyword chunk
        assert result["status"] in ("answered", "completed")
        assert "kw_chunk_1" in result["evidence_ids"]

    async def test_retrieve_keyword_failure_falls_back_to_vector(
        self, base_state: GraphState
    ) -> None:
        """When keyword retrieval fails, node continues using vector results."""
        ports = GraphPorts(mock_keyword_down=True)
        ctx = GraphRuntimeContext(
            ports=ports,
            question_text="Sample query",
            evidence_text={"vec_chunk_1": "Vector grounded content."},
        )

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(base_state, context=ctx)

        assert result["status"] in ("answered", "completed")
        assert "vec_chunk_1" in result["evidence_ids"]

    async def test_reranker_failure_falls_back_to_rrf_ranking(self, base_state: GraphState) -> None:
        """When reranker service fails, execution proceeds using RRF ordering."""
        ports = GraphPorts(mock_reranker_down=True)
        ctx = GraphRuntimeContext(
            ports=ports,
            question_text="Sample query",
            evidence_text={
                "c1": "First RRF chunk content.",
                "c2": "Second RRF chunk content.",
            },
        )

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(base_state, context=ctx)

        assert result["status"] in ("answered", "completed")
        assert len(result["evidence_ids"]) > 0

    async def test_evidence_gate_never_bypassed_under_any_fallback(
        self, base_state: GraphState
    ) -> None:
        """When both vector and keyword fail, gate sets NO_EVIDENCE and refuses."""
        ports = GraphPorts(mock_vector_down=True, mock_keyword_down=True)
        ctx = GraphRuntimeContext(ports=ports, question_text="Query with zero retrieval")

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(base_state, context=ctx)

        # Gate must have caught the empty evidence and set refusal
        assert result["evidence_state"] == "NO_EVIDENCE"
        assert result["status"] == "refusal"

    async def test_clarify_route_fetches_grounded_titles_from_retriever(
        self, base_state: GraphState
    ) -> None:
        titles = ["Office Guidelines", "Billing Schedule", "Referral Protocol"]
        ports = GraphPorts(mock_titles=titles)
        ctx = GraphRuntimeContext(ports=ports, question_text="Vague question")

        clarify_state = dict(base_state)
        clarify_state["route"] = "clarify"

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(clarify_state, context=ctx)

        assert result["status"] == "clarify"
        assert result["route"] == "clarify"
        # Grounded titles must be present in response
        assert "Office Guidelines" in str(result.get("response_payload", ""))
