"""LangGraph Studio local visual debugging entrypoint.

This module is strictly for Studio and developer-facing visual debugging.
Production code and app wiring live separately in app/wiring.py and do not
contain any Studio-specific overrides or subclasses.

Why a Studio-specific CompiledStateGraph subclass is used:
In LangGraph 0.6+ / 1.0+, when a StateGraph declares context_schema=GraphRuntimeContext,
the runtime expects `context` to be passed at invocation time (`ainvoke(input, context=...)`).
However, LangGraph Studio UI only exposes an input panel for state channels (input) and config
(thread_id); it does not provide an interface to instantiate or inject Python dataclasses as
runtime context. When invoked from Studio UI, LangGraph server passes `context=None`, causing
nodes accessing `runtime.context.ports` to raise an AttributeError. StudioCompiledGraph
intercepts execution to supply a default mock GraphRuntimeContext with synthetic healthcare
data when context is omitted, while preserving user-supplied context when explicitly provided.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from app.adapters.models.mock import MockModelProvider
from app.adapters.retrieval.mock import MockRetriever
from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    build_knowledge_graph,
)


def get_default_studio_context() -> GraphRuntimeContext:
    """Create a default mock runtime context for Studio visual debugging.

    Contains synthetic healthcare questions and evidence chunks, wired with
    MockModelProvider and MockRetriever.
    """
    mock_model = MockModelProvider()
    mock_retriever = MockRetriever()
    mock_ports = GraphPorts(
        model_provider=mock_model,
        retriever=mock_retriever,
        is_mock_profile=True,
    )
    return GraphRuntimeContext(
        ports=mock_ports,
        question_text="What are the standard hours of operation for the clinic?",
        evidence_text={
            "chunk-syn-001": (
                "Standard clinic hours of operation are 9:00 AM to 5:00 PM "
                "Monday through Friday. Urgent care is open Saturday 10:00 AM to 2:00 PM."
            ),
        },
    )


class StudioCompiledGraph(CompiledStateGraph[GraphState, GraphRuntimeContext, Any, Any]):
    """CompiledStateGraph subclass that supplies default mock context for Studio UI runs.

    Leaves production graph execution completely untouched while allowing Studio
    users to run the graph directly from the web interface without custom context injection.
    """

    def __init__(
        self,
        raw: CompiledStateGraph[GraphState, GraphRuntimeContext, Any, Any],
    ) -> None:
        self._raw_graph = raw
        self.__dict__.update(raw.__dict__)

    def copy(self, update: dict[str, Any] | None = None) -> StudioCompiledGraph:
        copied = self._raw_graph.copy(update=update)
        return StudioCompiledGraph(copied)

    @staticmethod
    def _ensure_config(config: RunnableConfig | None) -> RunnableConfig:
        cfg: dict[str, Any] = dict(config) if config else {}
        configurable_val = cfg.get("configurable")
        configurable: dict[str, Any] = (
            dict(configurable_val) if isinstance(configurable_val, dict) else {}
        )
        if (
            "thread_id" not in configurable
            and "checkpoint_id" not in configurable
            and "checkpoint_ns" not in configurable
        ):
            configurable["thread_id"] = str(uuid4())
        cfg["configurable"] = configurable
        return cfg  # type: ignore[return-value]

    async def ainvoke(  # type: ignore[override]
        self,
        input: Any,
        config: RunnableConfig | None = None,
        *,
        context: GraphRuntimeContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | Any:
        ctx = context or get_default_studio_context()
        resolved_config = self._ensure_config(config)
        return await super().ainvoke(input, config=resolved_config, context=ctx, **kwargs)

    async def astream(  # type: ignore[override]
        self,
        input: Any,
        config: RunnableConfig | None = None,
        *,
        context: GraphRuntimeContext | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any] | Any]:
        ctx = context or get_default_studio_context()
        resolved_config = self._ensure_config(config)
        async for chunk in super().astream(input, config=resolved_config, context=ctx, **kwargs):
            yield chunk

    def invoke(  # type: ignore[override]
        self,
        input: Any,
        config: RunnableConfig | None = None,
        *,
        context: GraphRuntimeContext | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | Any:
        ctx = context or get_default_studio_context()
        resolved_config = self._ensure_config(config)
        return super().invoke(input, config=resolved_config, context=ctx, **kwargs)

    def stream(  # type: ignore[override]
        self,
        input: Any,
        config: RunnableConfig | None = None,
        *,
        context: GraphRuntimeContext | None = None,
        **kwargs: Any,
    ) -> Iterator[dict[str, Any] | Any]:
        ctx = context or get_default_studio_context()
        resolved_config = self._ensure_config(config)
        yield from super().stream(input, config=resolved_config, context=ctx, **kwargs)


def make_studio_graph() -> StudioCompiledGraph:
    """Factory function for LangGraph Studio declared in langgraph.json.

    Builds the 9-node knowledge StateGraph with in-memory checkpointer (MemorySaver)
    and StudioCompiledGraph wrapper for visual debugging.
    """
    checkpointer = MemorySaver()
    builder = build_knowledge_graph()
    compiled = builder.compile(checkpointer=checkpointer)
    return StudioCompiledGraph(compiled)


# Exported graph instance for tools expecting a direct variable reference
graph: StudioCompiledGraph = make_studio_graph()
