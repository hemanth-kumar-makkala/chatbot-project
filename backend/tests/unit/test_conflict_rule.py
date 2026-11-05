"""Unit tests for document version conflict resolution (Task 2).

Enforces:
- Answer from the newer version by effective date or version number.
- Add one short line noting that an older version said something different, naming both files.
- If version order cannot be determined, keep current CONFLICTING behavior.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.core.conflict import (
    format_version_conflict_answer,
    resolve_document_version_order,
)
from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    node_assess_evidence,
)
from app.ports.model_provider import ModelResponse
from app.ports.types import TenantContext, UserRole


def test_version_ordering_by_filename():
    """front_desk_policy_v2.pdf is strictly newer than front_desk_policy_v1.pdf."""
    order = resolve_document_version_order(
        doc1_name="front_desk_policy_v1.pdf",
        chunk1_text="Front desk intake fax is 555-0101.",
        doc2_name="front_desk_policy_v2.pdf",
        chunk2_text="Front desk intake fax is 555-0102.",
    )
    assert order == (2, 1)


def test_version_ordering_by_effective_date():
    """Document with newer effective date is strictly newer even without filename version."""
    order = resolve_document_version_order(
        doc1_name="clinical_guidelines_2025.pdf",
        chunk1_text="Effective Date: January 15, 2025\nArrival window is 15 minutes.",
        doc2_name="clinical_guidelines_2026.pdf",
        chunk2_text="Effective Date: March 1, 2026\nArrival window is 20 minutes.",
    )
    assert order == (2, 1)


def test_version_ordering_by_supersedes_clause():
    """Document containing supersedes clause is identified as newer."""
    order = resolve_document_version_order(
        doc1_name="intake_policy.pdf",
        chunk1_text="Front Desk Operational SOP (Version 1.0)\nFax is 555-0101.",
        doc2_name="intake_policy_revised.pdf",
        chunk2_text="Front Desk Operational SOP (Version 2.0)\nSupersedes Version 1.0\nFax is 555-0102.",
    )
    assert order == (2, 1)


def test_different_documents_cannot_determine_version_order():
    """Completely different documents return None (retaining CONFLICTING behavior)."""
    order = resolve_document_version_order(
        doc1_name="prior_auth_guide.pdf",
        chunk1_text="Copay is $20.",
        doc2_name="fee_schedule.pdf",
        chunk2_text="Copay is $30.",
    )
    assert order is None


def test_format_version_conflict_answer():
    """Formatting answers from newer version and notes older version, naming both files."""
    answer = format_version_conflict_answer(
        newer_doc="front_desk_policy_v2.pdf",
        newer_quote="Front desk intake fax number is 555-0102.",
        newer_val="555-0102",
        older_doc="front_desk_policy_v1.pdf",
        older_quote="Front desk intake fax number is 555-0101.",
        older_val="555-0101",
    )
    assert "Front desk intake fax number is 555-0102." in answer
    assert "front_desk_policy_v1.pdf" in answer
    assert "front_desk_policy_v2.pdf" in answer
    assert "555-0101" in answer
    assert "Note: An older version" in answer


@pytest.mark.asyncio
async def test_node_assess_evidence_resolves_version_conflict():
    """node_assess_evidence answers from newer version when version order is determined."""
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="session-1",
        ),
        question_text="What is the front desk intake fax number?",
        active_document_count=2,
    )
    c1_text = "[Document: front_desk_policy_v1.pdf, Page: 1] Front desk intake fax number is 555-0101."
    c2_text = "[Document: front_desk_policy_v2.pdf, Page: 1] Front desk intake fax number is 555-0102."
    ctx.evidence_text = {"c1": c1_text, "c2": c2_text}
    ctx.candidate_scores = {"c1": 0.95, "c2": 0.94}

    class MockConflictModel:
        async def generate(self, **kwargs: Any) -> ModelResponse:
            return ModelResponse(
                content="",
                model_id="mock",
                model_version="1.0",
                structured_output={
                    "has_conflict": True,
                    "value1": "555-0101",
                    "quote1": "Front desk intake fax number is 555-0101.",
                    "doc1_id": "c1",
                    "value2": "555-0102",
                    "quote2": "Front desk intake fax number is 555-0102.",
                    "doc2_id": "c2",
                },
            )

    ctx.ports.model_provider = MockConflictModel()
    runtime = MagicMock()
    runtime.context = ctx

    state: GraphState = {"evidence_ids": ["c1", "c2"]}
    res = await node_assess_evidence(state, runtime)

    assert res["status"] == "answered"
    assert res["evidence_state"] == "RESOLVED_CONFLICT"
    assert res["evidence_ids"] == ["c2", "c1"]
    assert "555-0102" in ctx.response_text
    assert "front_desk_policy_v1.pdf" in ctx.response_text
    assert "front_desk_policy_v2.pdf" in ctx.response_text


@pytest.mark.asyncio
async def test_node_assess_evidence_keeps_conflicting_when_order_undetermined():
    """node_assess_evidence retains CONFLICTING behavior when versions cannot be ordered."""
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="session-1",
        ),
        question_text="What is the copay?",
        active_document_count=2,
    )
    c1_text = "[Document: clinic_policy_a.pdf, Page: 1] In-network copay is $20."
    c2_text = "[Document: general_faq.pdf, Page: 1] In-network copay is $30."
    ctx.evidence_text = {"c1": c1_text, "c2": c2_text}
    ctx.candidate_scores = {"c1": 0.95, "c2": 0.94}

    class MockConflictModel:
        async def generate(self, **kwargs: Any) -> ModelResponse:
            return ModelResponse(
                content="",
                model_id="mock",
                model_version="1.0",
                structured_output={
                    "has_conflict": True,
                    "value1": "$20",
                    "quote1": "In-network copay is $20.",
                    "doc1_id": "c1",
                    "value2": "$30",
                    "quote2": "In-network copay is $30.",
                    "doc2_id": "c2",
                },
            )

    ctx.ports.model_provider = MockConflictModel()
    runtime = MagicMock()
    runtime.context = ctx

    state: GraphState = {"evidence_ids": ["c1", "c2"]}
    res = await node_assess_evidence(state, runtime)

    assert res["status"] == "conflicting"
    assert res["evidence_state"] == "CONFLICTING"
    assert "Two documents disagree" in ctx.response_text

