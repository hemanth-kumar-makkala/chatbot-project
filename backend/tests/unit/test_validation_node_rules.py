"""Unit tests for Validation Node Rules (Node 8 validate) - Task 3.5.

PHASE-3 Requirements:
1. Every quoted span matches stored text character for character;
   table answers contain only rows that exist in tables_data;
   every citation id is one of the evidence ids given; tenant matches.
2. explain mode: validate each point separately; drop points that fail;
   show the validated points; if none validates, regenerate once (the 1-retry loop),
   then refuse with "I couldn't confirm that from the documents."
   Never show unvalidated text.
3. Reject answers that are only a document title, a file name, or a context prefix.
"""

from __future__ import annotations

import pytest

from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    node_validate,
)
from app.ports.types import TenantContext, UserRole


@pytest.fixture
def base_context() -> GraphRuntimeContext:
    tenant = TenantContext(
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="user-1",
        role=UserRole.STAFF,
        session_id="s1",
    )
    return GraphRuntimeContext(ports=GraphPorts(), tenant=tenant)


class TestValidationNodeRules:
    """Verifies Task 3.5 quote verification, table row containment,
    explain point filtering, and rejection of echoed titles.
    """

    @pytest.mark.asyncio
    async def test_quoted_span_exact_character_for_character_match_passes(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement 1: Exact character-for-character quote match passes validation."""
        chunk_text = "Standard prior authorization requires submission 5 business days in advance."
        base_context.evidence_text = {"c1": chunk_text}
        base_context.response_text = "5 business days in advance."
        base_context.generated_answer = {
            "mode": "short",
            "answer": "5 business days in advance.",
            "evidence_ids": ["c1"],
            "quoted_span": "5 business days in advance",
        }

        state: GraphState = {
            "request_id": "r1",
            "answer_mode": "short",
            "evidence_ids": ["c1"],
            "validation_attempts": 0,
        }
        res = await node_validate(state, type("MockRuntime", (), {"context": base_context})())
        assert res["status"] == "completed"

    @pytest.mark.asyncio
    async def test_hallucinated_quote_triggers_retry_then_refuses(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement 1: Hallucinated quote fails, retries on attempt 0,
        then refuses on attempt 1.
        """
        base_context.evidence_text = {"c1": "Prior auth is required for surgeries."}
        base_context.response_text = "Submitted 10 days early."
        base_context.generated_answer = {
            "mode": "short",
            "answer": "Submitted 10 days early.",
            "evidence_ids": ["c1"],
            "quoted_span": "submission 10 days early",  # Hallucinated quote
        }

        # Attempt 0 -> retry
        state_0: GraphState = {
            "request_id": "r1",
            "answer_mode": "short",
            "evidence_ids": ["c1"],
            "validation_attempts": 0,
        }
        res_0 = await node_validate(state_0, type("MockRuntime", (), {"context": base_context})())
        assert res_0["status"] == "retry"
        assert res_0["validation_attempts"] == 1

        # Attempt 1 -> refusal
        state_1: GraphState = {
            "request_id": "r1",
            "answer_mode": "short",
            "evidence_ids": ["c1"],
            "validation_attempts": 1,
        }
        res_1 = await node_validate(state_1, type("MockRuntime", (), {"context": base_context})())
        assert res_1["status"] == "refusal"
        assert base_context.response_text == "I couldn't confirm that from the documents."

    @pytest.mark.asyncio
    async def test_table_answers_contain_only_rows_in_tables_data(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement 1: Table answers contain only rows and values that exist in tables_data."""
        # Row 1 exists with Crestview Health Gold Tier $500
        table_row_text = (
            "[Document: accepted_plans.csv, Sheet: Sheet1, Row: 1] "
            "Plan: Crestview Health, Tier: Gold, Deductible: $500.00"
        )
        base_context.evidence_text = {"t1": table_row_text}

        # Valid table answer containing values from tables_data
        base_context.response_text = "Crestview Health Gold Tier plan has a deductible of $500.00."
        base_context.generated_answer = {
            "mode": "table",
            "answer": base_context.response_text,
            "evidence_ids": ["t1"],
        }
        state_valid: GraphState = {
            "request_id": "r1",
            "answer_mode": "table",
            "evidence_ids": ["t1"],
            "validation_attempts": 0,
        }
        res_valid = await node_validate(
            state_valid, type("MockRuntime", (), {"context": base_context})()
        )
        assert res_valid["status"] == "completed"

        # Invalid table answer inventing values not in tables_data
        base_context.response_text = "Apex Silver plan has a deductible of $9999.00."
        base_context.generated_answer = {
            "mode": "table",
            "answer": base_context.response_text,
            "evidence_ids": ["t1"],
        }
        state_invalid: GraphState = {
            "request_id": "r1",
            "answer_mode": "table",
            "evidence_ids": ["t1"],
            "validation_attempts": 0,
        }
        res_invalid = await node_validate(
            state_invalid, type("MockRuntime", (), {"context": base_context})()
        )
        assert res_invalid["status"] == "retry"

    @pytest.mark.asyncio
    async def test_citation_id_not_in_evidence_ids_fails(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement 1: Citation ID outside given evidence fails validation."""
        base_context.evidence_text = {"c1": "Some valid text."}
        base_context.response_text = "Valid text."
        base_context.generated_answer = {
            "mode": "short",
            "answer": "Valid text.",
            "evidence_ids": ["c1", "c999_fabricated"],  # Fabricated citation ID
            "quoted_span": "valid text",
        }

        state: GraphState = {
            "request_id": "r1",
            "answer_mode": "short",
            "evidence_ids": ["c1"],
            "validation_attempts": 0,
        }
        res = await node_validate(state, type("MockRuntime", (), {"context": base_context})())
        assert res["status"] == "retry"

    @pytest.mark.asyncio
    async def test_explain_mode_validates_each_point_separately_drops_failing_points(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement 2: explain mode validates each point separately; drops failing points;
        shows validated points.
        """
        base_context.evidence_text = {
            "c1": "Policy 2026-PA-01 is effective January 1, 2026.",
            "c2": "Submissions require 5 business days notice.",
        }

        # 3 points: point 1 and 2 valid; point 3 has hallucinated quote
        points = [
            {
                "point": "The policy code is 2026-PA-01.",
                "quoted_span": "Policy 2026-PA-01",
                "evidence_ids": ["c1"],
            },
            {
                "point": "Effective date is January 1, 2026.",
                "quoted_span": "January 1, 2026",
                "evidence_ids": ["c1"],
            },
            {
                "point": "Appeals must be submitted in 10 minutes.",
                "quoted_span": "appeals within 10 minutes",  # Hallucinated quote
                "evidence_ids": ["c2"],
            },
        ]

        base_context.generated_answer = {
            "mode": "explain",
            "answer": "Summary:",
            "points": points,
            "evidence_ids": ["c1", "c2"],
        }

        state: GraphState = {
            "request_id": "r1",
            "answer_mode": "explain",
            "evidence_ids": ["c1", "c2"],
            "validation_attempts": 0,
        }
        res = await node_validate(state, type("MockRuntime", (), {"context": base_context})())

        # Passing points kept, failing point dropped
        assert res["status"] == "completed"
        assert "points" in base_context.generated_answer
        validated = base_context.generated_answer["points"]
        assert len(validated) == 2
        assert all(p["point"] != "Appeals must be submitted in 10 minutes." for p in validated)
        # Never show unvalidated text
        assert "10 minutes" not in base_context.response_text
        assert "2026-PA-01" in base_context.response_text

    @pytest.mark.asyncio
    async def test_explain_mode_all_points_fail_retries_once_then_refuses(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement 2: explain mode where all points fail retries on attempt 0,
        then refuses on attempt 1.
        """
        base_context.evidence_text = {"c1": "Policy 2026-PA-01."}
        points = [
            {
                "point": "Hallucinated claim 1",
                "quoted_span": "non-existent quote 1",
                "evidence_ids": ["c1"],
            },
            {
                "point": "Hallucinated claim 2",
                "quoted_span": "non-existent quote 2",
                "evidence_ids": ["c1"],
            },
        ]
        base_context.generated_answer = {
            "mode": "explain",
            "answer": "Summary:",
            "points": points,
            "evidence_ids": ["c1"],
        }

        # Attempt 0 -> retry
        state_0: GraphState = {
            "request_id": "r1",
            "answer_mode": "explain",
            "evidence_ids": ["c1"],
            "validation_attempts": 0,
        }
        res_0 = await node_validate(state_0, type("MockRuntime", (), {"context": base_context})())
        assert res_0["status"] == "retry"

        # Attempt 1 -> refusal
        state_1: GraphState = {
            "request_id": "r1",
            "answer_mode": "explain",
            "evidence_ids": ["c1"],
            "validation_attempts": 1,
        }
        res_1 = await node_validate(state_1, type("MockRuntime", (), {"context": base_context})())
        assert res_1["status"] == "refusal"
        assert base_context.response_text == "I couldn't confirm that from the documents."

    @pytest.mark.asyncio
    async def test_reject_answer_that_is_only_document_title_or_filename(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement 3: Reject answers that are only a document title or a file name."""
        base_context.evidence_text = {
            "c1": "[Document: front_desk_policy_v1.pdf, Page: 1] Horizon Valley Health Clinic SOP."
        }

        # Answer echoing just filename
        base_context.response_text = "front_desk_policy_v1.pdf"
        base_context.generated_answer = {
            "mode": "short",
            "answer": "front_desk_policy_v1.pdf",
            "evidence_ids": ["c1"],
            "quoted_span": "front_desk_policy_v1.pdf",
        }

        state: GraphState = {
            "request_id": "r1",
            "answer_mode": "short",
            "evidence_ids": ["c1"],
            "validation_attempts": 0,
        }
        res = await node_validate(state, type("MockRuntime", (), {"context": base_context})())
        assert res["status"] == "retry"

    @pytest.mark.asyncio
    async def test_reject_answer_that_is_only_context_prefix(
        self, base_context: GraphRuntimeContext
    ) -> None:
        """Requirement 3: Reject answers that only repeat a context prefix."""
        base_context.evidence_text = {
            "c1": "front_desk_policy_v1.pdf - Page 1 - Section 1\n\nIntake fax is 555-0101."
        }

        base_context.response_text = "front_desk_policy_v1.pdf - Page 1 - Section 1"
        base_context.generated_answer = {
            "mode": "short",
            "answer": "front_desk_policy_v1.pdf - Page 1 - Section 1",
            "evidence_ids": ["c1"],
            "quoted_span": "front_desk_policy_v1.pdf - Page 1 - Section 1",
        }

        state: GraphState = {
            "request_id": "r1",
            "answer_mode": "short",
            "evidence_ids": ["c1"],
            "validation_attempts": 0,
        }
        res = await node_validate(state, type("MockRuntime", (), {"context": base_context})())
        assert res["status"] == "retry"
