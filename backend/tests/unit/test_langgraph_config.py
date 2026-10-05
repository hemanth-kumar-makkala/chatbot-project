"""Unit tests for LangGraph Studio local debugging configuration (Task 1.7).

Verifies:
1. langgraph.json exists, is valid JSON, specifies python 3.12, dependencies,
   and valid graph entrypoint.
2. LANGSMITH_TRACING=false is set in .env.example and .env so no telemetry leaves the machine.
3. Studio entrypoint in studio.entry compiles the knowledge graph with in-memory checkpointer.
4. Production wiring in app.wiring contains no monkeypatching or test-only overrides.
5. Wired graph connects MockModelProvider and MockRetriever under the mock profile.
6. Invoking the studio graph with synthetic data executes cleanly to an answered state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph
from studio.entry import get_default_studio_context, graph, make_studio_graph

from app.adapters.models.mock import MockModelProvider
from app.adapters.retrieval.mock import MockRetriever
from app.core.graph import GraphPorts, GraphRuntimeContext, GraphState
from app.wiring import build_app_graph


class TestLangGraphStudioConfiguration:
    """Validates configuration files and environment for LangGraph Studio."""

    def test_langgraph_json_valid_and_complete(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        backend_dir = Path(__file__).resolve().parents[2]
        config_path = repo_root / "langgraph.json"
        duplicate_path = backend_dir / "langgraph.json"

        assert config_path.is_file(), f"Single langgraph.json must exist at {config_path}"
        assert not duplicate_path.is_file(), (
            f"Duplicate langgraph.json must not exist at {duplicate_path}"
        )

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert "graphs" in data, "langgraph.json missing 'graphs' key"
        assert "knowledge_assistant" in data["graphs"], (
            "langgraph.json missing 'knowledge_assistant' graph"
        )
        assert (
            data["graphs"]["knowledge_assistant"] == "./backend/studio/entry.py:make_studio_graph"
        )

        assert data.get("dependencies") == ["./backend"], "dependencies must specify ['./backend']"
        assert data.get("python_version") == "3.12", "python_version must be '3.12'"

    def test_langsmith_tracing_disabled_in_env_files(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        env_example = repo_root / ".env.example"
        env_file = repo_root / ".env"

        assert env_example.is_file(), ".env.example missing"
        assert env_file.is_file(), ".env missing"

        example_lines = env_example.read_text(encoding="utf-8").splitlines()
        assert any(line.strip() == "LANGSMITH_TRACING=false" for line in example_lines), (
            "LANGSMITH_TRACING=false must be present in .env.example"
        )

        env_lines = env_file.read_text(encoding="utf-8").splitlines()
        assert any(line.strip() == "LANGSMITH_TRACING=false" for line in env_lines), (
            "LANGSMITH_TRACING=false must be present in .env"
        )


class TestProductionWiringPurity:
    """Validates that production app.wiring is pure and does not monkeypatch graph methods."""

    def test_production_build_app_graph_pure(self) -> None:
        compiled = build_app_graph()
        assert isinstance(compiled, CompiledStateGraph)
        # Verify methods are not monkeypatched on the production instance
        assert not hasattr(compiled.ainvoke, "__wrapped__")
        assert not hasattr(compiled.astream, "__wrapped__")


class TestStudioEntrypointWiring:
    """Validates the compiled studio graph wired in studio.entry."""

    def test_studio_graph_is_compiled_with_in_memory_checkpointer(self) -> None:
        assert isinstance(graph, CompiledStateGraph), "Exported graph must be a CompiledStateGraph"
        assert graph.checkpointer is not None, "Studio graph must have a checkpointer configured"
        assert isinstance(graph.checkpointer, MemorySaver), (
            "Studio graph checkpointer must be an in-memory MemorySaver"
        )

    def test_make_studio_graph_returns_fresh_instance(self) -> None:
        fresh_graph = make_studio_graph()
        assert isinstance(fresh_graph, CompiledStateGraph)
        assert isinstance(fresh_graph.checkpointer, MemorySaver)
        assert fresh_graph is not graph, "make_studio_graph() must produce a new instance"

    def test_default_studio_context_wired_with_mock_profile(self) -> None:
        ctx = get_default_studio_context()
        assert isinstance(ctx, GraphRuntimeContext)
        assert isinstance(ctx.ports.model_provider, MockModelProvider)
        assert isinstance(ctx.ports.retriever, MockRetriever)
        assert ctx.question_text != "", "Default studio context must provide a sample question"
        assert len(ctx.evidence_text) > 0, "Default studio context must provide sample evidence"

    @pytest.mark.asyncio
    async def test_studio_graph_ainvoke_with_default_context(self) -> None:
        """Studio graph can be invoked without explicit context, using mock defaults."""
        initial_state: GraphState = {
            "request_id": "studio-req-001",
            "tenant_id": "tenant-studio-synthetic",
            "route": "knowledge",
            "answer_mode": "short",
            "evidence_state": "RELIABLE_TEXT",
            "status": "pending",
        }

        result = await graph.ainvoke(initial_state)
        assert result["status"] in ("answered", "completed")
        assert result.get("response_message_id") is not None
        assert "response_payload" not in result
        assert "error" not in result["status"].lower()

    @pytest.mark.asyncio
    async def test_studio_graph_ainvoke_with_explicit_context(self) -> None:
        """Studio graph respects explicitly supplied runtime context when provided."""
        custom_ports = GraphPorts(
            model_provider=MockModelProvider(),
            retriever=MockRetriever(),
        )
        custom_ctx = GraphRuntimeContext(
            ports=custom_ports,
            question_text="What are office hours for the clinic?",
            evidence_text={"c1": "Clinic office hours are Monday through Friday 9am to 5pm."},
        )

        initial_state: GraphState = {
            "request_id": "studio-req-002",
            "tenant_id": "tenant-studio-synthetic",
            "route": "knowledge",
            "answer_mode": "short",
            "evidence_state": "RELIABLE_TEXT",
            "status": "pending",
        }

        result = await graph.ainvoke(initial_state, context=custom_ctx)
        assert result["status"] in ("answered", "completed")
        assert result.get("response_message_id") is not None
        assert "response_payload" not in result
        assert custom_ctx.response_text != ""
