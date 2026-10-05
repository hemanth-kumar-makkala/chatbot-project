"""Composition root for the Client Knowledge Assistant application.

This is the ONLY module permitted by architecture boundary enforcement rules
to import from both app.core.* and app.adapters.*.

Provides clean factory functions for constructing the application graph
with specified ports and checkpointer without monkeypatching or test-only overrides.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.core.graph import (
    GraphRuntimeContext,
    GraphState,
    build_knowledge_graph,
)


def build_app_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph[GraphState, GraphRuntimeContext, Any, Any]:
    """Build and compile the core knowledge graph for the application."""
    builder = build_knowledge_graph()
    return builder.compile(checkpointer=checkpointer)
