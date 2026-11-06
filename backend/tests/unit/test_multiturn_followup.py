"""Unit tests for multi-turn question rewriting and follow-up disambiguation (Task 1).

Enforces:
- Standalone question rewriting using conversation history (last N turns).
- If the rewrite is unclear, ask the user a short clarifying question instead of guessing.
- Log both original and rewritten question.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    node_rewrite,
)
from app.core.rewrite import (
    heuristic_rewrite,
    is_followup_question,
)
from app.ports.types import TenantContext, UserRole


def test_is_followup_question_detection():
    """Detects pronouns and follow-up conjunctions."""
    assert is_followup_question("and what documents are required with it?") is True
    assert (
        is_followup_question(
            "What is the standard SLA for Apex Health Insurance diagnostic imaging?"
        )
        is False
    )
    assert is_followup_question("How recent must they be?") is True
    assert is_followup_question("What documentation is needed for that?") is True
    assert is_followup_question("What insurance plans are accepted?") is False


def test_heuristic_rewrite_resolves_prior_auth_followup():
    """Follow-up 'and what documents are required with it?' resolves with history."""
    history = [
        {
            "role": "user",
            "content": (
                "How many business days in advance must outpatient surgery prior auths be "
                "submitted?"
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Outpatient surgery prior auths must be submitted at least 5 business days "
                "in advance."
            ),
        },
    ]
    res = heuristic_rewrite("and what documents are required with it?", history)
    assert res.is_followup is True
    assert res.needs_clarification is False
    assert "outpatient surgery prior authorizations" in res.rewritten_question.lower()
    assert "with it" not in res.rewritten_question.lower()


def test_heuristic_rewrite_resolves_apex_imaging_followup():
    """Follow-up 'what documentation is needed for that?' resolves with Apex SLA history."""
    history = [
        {
            "role": "user",
            "content": "What is the standard SLA for Apex Health Insurance diagnostic imaging?",
        },
        {"role": "assistant", "content": "The standard SLA is 5 days."},
    ]
    res = heuristic_rewrite("what documentation is needed for that?", history)
    assert res.is_followup is True
    assert res.needs_clarification is False
    assert "apex health insurance diagnostic imaging" in res.rewritten_question.lower()


def test_heuristic_rewrite_unclear_without_history_asks_clarifying_question():
    """When a follow-up is asked with no history, asks a clarifying question instead of guessing."""
    res = heuristic_rewrite("and what documents are required with it?", history=[])
    assert res.is_followup is True
    assert res.needs_clarification is True
    assert res.clarifying_question is not None
    assert "clarify" in res.clarifying_question.lower()


@pytest.mark.asyncio
async def test_node_rewrite_updates_question_and_context():
    """node_rewrite updates ctx.question_text and ctx.rewritten_question."""
    history = [
        {
            "role": "user",
            "content": (
                "How many business days in advance must outpatient surgery prior auths be "
                "submitted?"
            ),
        },
        {"role": "assistant", "content": "5 business days."},
    ]
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="session-1",
        ),
        question_text="and what documents are required with it?",
        conversation_history=history,
    )
    runtime = MagicMock()
    runtime.context = ctx

    state: GraphState = {"status": "in_progress"}
    res = await node_rewrite(state, runtime)

    assert res["status"] == "in_progress"
    assert "outpatient surgery prior authorizations" in ctx.question_text
    assert ctx.original_question == "and what documents are required with it?"
    assert ctx.rewritten_question == ctx.question_text


@pytest.mark.asyncio
async def test_node_rewrite_triggers_clarification_on_unclear_reference():
    """node_rewrite routes to clarify when reference has no antecedent."""
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="session-1",
        ),
        question_text="and what documents are required with it?",
        conversation_history=[],
    )
    runtime = MagicMock()
    runtime.context = ctx

    state: GraphState = {"status": "in_progress"}
    res = await node_rewrite(state, runtime)

    assert res["status"] == "clarify"
    assert res["route"] == "clarify"
    assert "clarify" in ctx.response_text.lower()
