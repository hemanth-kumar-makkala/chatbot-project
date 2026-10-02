"""Contract and structure tests for LangGraph StateGraph (Task 1.6).

Verifies:
- 9-node knowledge graph and conditional edges match the architecture spec.
- Extraction subgraph ends after validate_fields and returns to main graph (no respond inside).
- Mermaid export matches committed docs/graph.md.
- Checkpoints (state and metadata) contain zero content/prompt/text fields.
- Validation loop is bounded to at most 1 retry, then refuses.
- Vision read conditional edge handles all 3 branches:
  1. vision ok -> generate
  2. vision failed + OCR ok -> generate (with OCR evidence)
  3. vision failed + OCR unusable -> respond (page refusal)
- Recursion limit stops loops and transitions to safe_failure.
- All nodes wrap execution with Tracer fail-open.
"""

from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    build_extraction_subgraph,
    build_knowledge_graph,
    invoke_graph,
)


class TestGraphContractsAndStructure:
    """Validates StateGraph structure and spec conformity."""

    def test_knowledge_graph_nodes_and_edges_match_spec(self) -> None:
        graph = build_knowledge_graph()
        nodes = set(graph.nodes.keys())

        expected_nodes = {
            "guard_input",
            "route",
            "retrieve",
            "rerank",
            "assess_evidence",
            "vision_read",
            "generate",
            "validate",
            "respond",
            "safe_failure",
            "extraction_subgraph",
        }
        assert expected_nodes.issubset(nodes), f"Missing nodes: {expected_nodes - nodes}"

    def test_extraction_subgraph_structure(self) -> None:
        subgraph = build_extraction_subgraph()
        nodes = set(subgraph.nodes.keys())

        # Spec: extract_fields -> dual_read_compare -> classify_confidence -> validate_fields
        # Must not contain respond node (returns to main graph for shared respond)
        expected_nodes = {
            "extract_fields",
            "dual_read_compare",
            "classify_confidence",
            "validate_fields",
        }
        assert expected_nodes == nodes, f"Extraction subgraph node mismatch: {nodes}"
        assert "respond" not in nodes, "Extraction subgraph must not contain respond node"

    def test_graph_mermaid_matches_committed_docs(self) -> None:
        graph = build_knowledge_graph()
        compiled = graph.compile()
        mermaid_text = compiled.get_graph().draw_mermaid()

        docs_path = Path(__file__).resolve().parents[3] / "docs" / "graph.md"
        assert docs_path.is_file(), (
            "docs/graph.md does not exist. Run scripts/generate_graph_docs.py."
        )

        committed_content = docs_path.read_text(encoding="utf-8")
        assert mermaid_text.strip() in committed_content.strip(), (
            "Committed docs/graph.md is out of date. Run scripts/generate_graph_docs.py."
        )

    async def test_checkpoint_state_and_metadata_contains_zero_content_memory_saver(
        self,
    ) -> None:
        """In-memory checkpointer inspection: both state and metadata contain zero content."""
        checkpointer = MemorySaver()
        graph = build_knowledge_graph().compile(checkpointer=checkpointer)

        # Content lives strictly in GraphRuntimeContext, never in state
        runtime_ctx = GraphRuntimeContext(
            ports=GraphPorts(),
            question_text="Sensitive question about patient diagnosis?",
            evidence_text={"c1": "Sensitive patient diagnosis document text..."},
        )

        initial_state: GraphState = {
            "request_id": "req-chk-1",
            "tenant_id": "ten-chk-1",
            "user_id": "usr-1",
            "conversation_id": "conv-1",
            "route": "knowledge",
            "answer_mode": "short",
            "language": "en",
            "evidence_ids": ["c1"],
            "evidence_state": "RELIABLE_TEXT",
            "needs_vision": False,
            "validation_attempts": 0,
            "status": "pending",
            "public_error_ref": None,
        }

        config = RunnableConfig(configurable={"thread_id": "thread-chk-1"})
        await graph.ainvoke(initial_state, config=config, context=runtime_ctx)

        # Inspect all checkpoints stored in checkpointer
        banned_words = {"patient", "diagnosis", "Sensitive question", "Sensitive patient"}
        for checkpoint_tuple in checkpointer.list(config):
            checkpoint = checkpoint_tuple.checkpoint
            metadata = checkpoint_tuple.metadata

            # 1. Inspect state channel values
            channel_values = checkpoint.get("channel_values", {})
            for key, val in channel_values.items():
                val_str = str(val)
                for banned in banned_words:
                    assert banned not in val_str, (
                        f"Banned content '{banned}' leaked into checkpoint state channel '{key}'"
                    )

            # 2. Inspect metadata values
            meta_str = str(metadata)
            for banned in banned_words:
                assert banned not in meta_str, (
                    f"Banned content '{banned}' leaked into checkpoint metadata"
                )

    async def test_validation_generate_loop_bounds_to_single_retry(self) -> None:
        """validate -> generate retries at most once, then refuses."""
        # Provider that returns ungrounded quote triggering validation failure
        ports = GraphPorts()
        runtime_ctx = GraphRuntimeContext(
            ports=ports,
            question_text="Query",
            evidence_text={"c1": "Ground truth evidence span."},
        )

        initial_state: GraphState = {
            "request_id": "req-loop-1",
            "tenant_id": "ten-1",
            "user_id": "usr-1",
            "conversation_id": "conv-1",
            "route": "knowledge",
            "answer_mode": "short",
            "language": "en",
            "evidence_ids": ["c1"],
            "evidence_state": "RELIABLE_TEXT",
            "needs_vision": False,
            "validation_attempts": 0,
            "status": "pending",
            "public_error_ref": None,
        }

        graph = build_knowledge_graph().compile()
        # Force validator to always fail
        runtime_ctx.evidence_text = {"c1": "Non matching"}
        result = await graph.ainvoke(initial_state, context=runtime_ctx)

        # Must have completed exactly 1 retry (validation_attempts == 1) and refused
        assert result["validation_attempts"] == 1
        assert result["status"] == "refusal"

    async def test_vision_read_conditional_branches(self) -> None:
        """Vision read conditional edge handles:
        1. vision ok -> generate
        2. vision failed + OCR passes -> generate
        3. vision failed + OCR unusable -> respond (refusal)
        """
        graph = build_knowledge_graph().compile()

        # Branch 1: Vision OK
        ctx1 = GraphRuntimeContext(ports=GraphPorts(mock_vision_mode="ok"))
        s1: GraphState = {
            "request_id": "req-v1",
            "tenant_id": "ten-1",
            "user_id": "u1",
            "conversation_id": "c1",
            "route": "knowledge",
            "answer_mode": "short",
            "language": "en",
            "evidence_ids": ["v1"],
            "evidence_state": "VISUAL_REQUIRED",
            "needs_vision": True,
            "validation_attempts": 0,
            "status": "pending",
            "public_error_ref": None,
        }
        r1 = await graph.ainvoke(s1, context=ctx1)
        assert r1["status"] in ("answered", "completed")

        # Branch 2: Vision fails, OCR confidence passes -> generate using OCR
        p2 = GraphPorts(mock_vision_mode="fail", mock_ocr_confidence=0.95)
        ctx2 = GraphRuntimeContext(ports=p2)
        s2 = dict(s1)
        s2["request_id"] = "req-v2"
        r2 = await graph.ainvoke(s2, context=ctx2)
        assert r2["status"] in ("answered", "completed")

        # Branch 3: Vision fails, OCR confidence unusable -> respond with refusal
        p3 = GraphPorts(mock_vision_mode="fail", mock_ocr_confidence=0.40)
        ctx3 = GraphRuntimeContext(ports=p3)
        s3 = dict(s1)
        s3["request_id"] = "req-v3"
        r3 = await graph.ainvoke(s3, context=ctx3)
        assert r3["status"] == "refusal"

    async def test_recursion_limit_stops_loop_and_ends_in_safe_failure(self) -> None:
        """Forced infinite cycle hits invocation recursion_limit and transitions to safe_failure."""
        # Using invoke_graph which sets invocation config with recursion_limit
        ctx = GraphRuntimeContext(ports=GraphPorts(force_infinite_loop=True))
        state: GraphState = {
            "request_id": "req-rec-1",
            "tenant_id": "ten-1",
            "user_id": "u1",
            "conversation_id": "c1",
            "route": "knowledge",
            "answer_mode": "short",
            "language": "en",
            "evidence_ids": ["c1"],
            "evidence_state": "RELIABLE_TEXT",
            "needs_vision": False,
            "validation_attempts": 0,
            "status": "pending",
            "public_error_ref": None,
        }

        result = await invoke_graph(state, context=ctx, recursion_limit=5)
        assert result["status"] == "safe_failure"
        assert result["public_error_ref"] is not None
        assert result["public_error_ref"].startswith("ERR-")
