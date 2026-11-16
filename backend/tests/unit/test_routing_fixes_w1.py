"""Unit tests for Stage W1: Routing fixes and generic table answer checking.

Enforces:
1. Check on U4:
   - Table answer formatting, matching, and validation work generically for any table
     (e.g. accepted_plans.csv shape), labeling each row by its first column,
     then "Header: value" for the rest, with no file-specific column names or synonyms.
   - Validation checks every shown value against candidate rows.
2. Stage W1 Routing fixes:
   - Written guidance queries (TF-003, TF-006, TF-008) are answered from text documents,
     never forced into table mode.
   - Possessives like "our check-in protocol" (CONF-007) and "their outstanding balance"
     (NOA-008) do not trigger clarify when conversation history is empty.
   - Real unresolved references (e.g. "what about that one?") ask for clarification.
"""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest
from langgraph.runtime import Runtime

from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    _format_table_answer_text,
    _select_matching_table_rows,
    node_retrieve,
    node_rewrite,
    node_route,
    node_validate,
)
from app.core.rewrite import (
    heuristic_rewrite,
    is_followup_question,
)
from app.ports.types import TableCandidate, TenantContext, UserRole


def _make_runtime(ctx: GraphRuntimeContext) -> Runtime[GraphRuntimeContext]:
    class _MockRuntime:
        def __init__(self, context: GraphRuntimeContext) -> None:
            self.context = context

    return cast(Runtime[GraphRuntimeContext], _MockRuntime(ctx))


# ==============================================================================
# Part 1: Check on U4 - Generic Table Answer Formatting, Matching & Validation
# ==============================================================================


def _create_accepted_plans_candidates() -> dict[str, TableCandidate]:
    """Differently shaped table matching accepted_plans.csv columns."""
    rows = [
        {
            "Payer Name": "Apex Health Insurance",
            "Plan Code": "APX-HMO-01",
            "Network Tier": "In-Network Tier 1",
            "Standard Copay": "$20.00",
            "Deductible": "$250.00",
            "Pre-Auth": "Yes",
        },
        {
            "Payer Name": "Apex Health Insurance",
            "Plan Code": "APX-PPO-02",
            "Network Tier": "In-Network Tier 2",
            "Standard Copay": "$35.00",
            "Deductible": "$750.00",
            "Pre-Auth": "No",
        },
        {
            "Payer Name": "Beacon Mutual Healthcare",
            "Plan Code": "BCN-EPO-10",
            "Network Tier": "In-Network Tier 1",
            "Standard Copay": "$25.00",
            "Deductible": "$300.00",
            "Pre-Auth": "Yes",
        },
        {
            "Payer Name": "Crestview Health",
            "Plan Code": "CRST-GLD-05",
            "Network Tier": "In-Network Gold Tier",
            "Standard Copay": "$15.00",
            "Deductible": "$500.00",
            "Pre-Auth": "No",
        },
        {
            "Payer Name": "Summit Care Plan",
            "Plan Code": "SMT-SLV-08",
            "Network Tier": "In-Network Silver Tier",
            "Standard Copay": "$30.00",
            "Deductible": "$1000.00",
            "Pre-Auth": "Yes",
        },
    ]
    tcs: dict[str, TableCandidate] = {}
    for idx, r in enumerate(rows, start=2):
        rid = f"plan-row-{idx}"
        tcs[rid] = TableCandidate(
            id=rid,
            document_title="accepted_plans.csv",
            document_version_id=str(uuid4()),
            sheet_name="Sheet1",
            row_index=idx,
            caption="SYNTHETIC TEST DOCUMENT - ACCEPTED PLANS",
            cells=r,
            tenant_id="00000000-0000-0000-0000-000000000001",
        )
    return tcs


def test_generic_table_formatting_and_matching_on_differently_shaped_table() -> None:
    """Proves generic table formatting labels by first column, then 'Header: value' for rest."""
    tcs = _create_accepted_plans_candidates()
    question = "What is the annual deductible for Crestview Health under the Gold Tier plan?"
    selected_ids, missing_terms = _select_matching_table_rows(
        question,
        tcs,
        list(tcs.keys()),
    )
    assert "plan-row-5" in selected_ids  # Crestview Health row
    content = _format_table_answer_text(tcs, selected_ids, missing_terms, question=question)

    lines = content.splitlines()
    assert not any("[Sheet:" in line for line in lines)
    # Labeled by first column, then "Header: value" for the rest
    crestview_line = next(item_line for item_line in lines if "Crestview Health" in item_line)
    assert crestview_line.startswith("Crestview Health:")
    assert "Plan Code: CRST-GLD-05" in crestview_line
    assert "Network Tier: In-Network Gold Tier" in crestview_line
    assert "Deductible: $500.00" in crestview_line
    assert "Pre-Auth: No" in crestview_line


@pytest.mark.asyncio
async def test_generic_table_validation_accepts_faithful_and_rejects_hallucinated_values() -> None:
    """Proves generic table validation validates any table columns and fails on hallucination."""
    tcs = _create_accepted_plans_candidates()
    q_deductible = "What is the annual deductible for Crestview Health under the Gold Tier plan?"
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q_deductible,
        table_candidates=tcs,
    )
    # Faithful answer passes validation
    ctx.generated_answer = {
        "mode": "table",
        "answer": (
            "The matching rows from accepted_plans.csv are listed below.\n"
            "Crestview Health: Plan Code: CRST-GLD-05, Network Tier: In-Network Gold Tier, "
            "Standard Copay: $15.00, Deductible: $500.00, Pre-Auth: No\n"
        ),
        "evidence_ids": ["plan-row-5"],
    }
    val_pass = await node_validate(
        {"answer_mode": "table", "evidence_ids": ["plan-row-5"]},
        _make_runtime(ctx),
    )
    assert val_pass["status"] == "completed"

    # Hallucinated deductible ($9,999.00) fails validation
    ctx.generated_answer = {
        "mode": "table",
        "answer": (
            "The matching rows from accepted_plans.csv are listed below.\n"
            "Crestview Health: Plan Code: CRST-GLD-05, Network Tier: In-Network Gold Tier, "
            "Standard Copay: $15.00, Deductible: $9,999.00, Pre-Auth: No\n"
        ),
        "evidence_ids": ["plan-row-5"],
    }
    val_fail = await node_validate(
        {"answer_mode": "table", "evidence_ids": ["plan-row-5"]},
        _make_runtime(ctx),
    )
    assert val_fail["status"] in ("retry", "refusal")


# ==============================================================================
# Part 2: Stage W1 - Possessives Do Not Trigger Clarify Without History
# ==============================================================================


def test_w1_conf_007_possessives_do_not_trigger_followup_detection() -> None:
    """CONF-007: 'our check-in protocol' and 'their appointment time' are self-contained."""
    q = (
        "According to our check-in protocol, how far in advance of their appointment time "
        "must routine patients arrive?"
    )
    assert is_followup_question(q) is False

    res = heuristic_rewrite(q, history=[])
    assert res.is_followup is False
    assert res.needs_clarification is False


def test_w1_noa_008_possessive_their_does_not_trigger_followup_detection() -> None:
    """NOA-008: 'Can patients pay their outstanding balance using Bitcoin or cryptocurrency?'"""
    q = "Can patients pay their outstanding balance using Bitcoin or cryptocurrency?"
    assert is_followup_question(q) is False

    res = heuristic_rewrite(q, history=[])
    assert res.is_followup is False
    assert res.needs_clarification is False


def test_w1_real_unresolved_reference_asks_for_clarification() -> None:
    """Real dangling references like 'what about that one?' ask for clarification."""
    q = "what about that one?"
    assert is_followup_question(q) is True

    res = heuristic_rewrite(q, history=[])
    assert res.is_followup is True
    assert res.needs_clarification is True
    assert res.clarifying_question is not None


@pytest.mark.asyncio
async def test_w1_node_rewrite_routes_conf_007_and_noa_008_to_knowledge() -> None:
    """CONF-007 and NOA-008 proceed to knowledge retrieval, never clarify."""
    q_conf = (
        "According to our check-in protocol, "
        "how far in advance of their appointment time must routine patients arrive?"
    )
    for q in (
        q_conf,
        "Can patients pay their outstanding balance using Bitcoin or cryptocurrency?",
    ):
        ctx = GraphRuntimeContext(
            ports=GraphPorts(is_mock_profile=True),
            tenant=TenantContext(
                tenant_id="00000000-0000-0000-0000-000000000001",
                user_id="user-1",
                role=UserRole.STAFF,
                session_id="sess-1",
            ),
            question_text=q,
            conversation_history=[],
        )
        state: GraphState = {"request_id": "r-1", "status": "in_progress"}
        res = await node_rewrite(state, _make_runtime(ctx))
        assert res.get("status") != "clarify"
        assert res.get("route") != "clarify"


# ==============================================================================
# Part 3: Stage W1 - Written Guidance Questions Never Forced into Table Mode
# ==============================================================================


@pytest.mark.asyncio
async def test_w1_tf_003_outpatient_advance_submission_routes_to_knowledge_short() -> None:
    """TF-003: Written guidance on advance submission days is short mode, not table."""
    q = "How many business days in advance must outpatient surgery prior auths be submitted?"
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
    )
    state: GraphState = {"request_id": "r-1", "status": "in_progress"}
    route_res = await node_route(state, _make_runtime(ctx))
    assert route_res["route"] == "knowledge"
    assert route_res["answer_mode"] != "table"


@pytest.mark.asyncio
async def test_w1_tf_006_urgent_appeal_timeframe_not_forced_into_table_mode() -> None:
    """TF-006: Appeal timeframes are written guidance, not table."""
    q = "Within what timeframe must an urgent authorization appeal be filed after a denial?"
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
    )
    state: GraphState = {"request_id": "r-1", "status": "in_progress"}
    route_res = await node_route(state, _make_runtime(ctx))
    assert route_res["route"] == "knowledge"
    assert route_res["answer_mode"] != "table"


@pytest.mark.asyncio
async def test_w1_tf_008_standard_appeal_days_not_forced_into_table_mode() -> None:
    """TF-008: Standard appeal filing days is written guidance, not table."""
    q = (
        "How many calendar days do we have to file a standard appeal "
        "following an auth denial letter?"
    )
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
    )
    state: GraphState = {"request_id": "r-1", "status": "in_progress"}
    route_res = await node_route(state, _make_runtime(ctx))
    assert route_res["route"] == "knowledge"
    assert route_res["answer_mode"] != "table"


@pytest.mark.asyncio
async def test_w1_node_retrieve_resets_table_mode_for_written_guidance() -> None:
    """In node_retrieve, incoming answer_mode='table' is reset to 'short' for written guidance."""
    q = "Within what timeframe must an urgent authorization appeal be filed after a denial?"
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
        evidence_text={"c1": "Appeals for urgent authorizations must be filed within 72 hours."},
    )
    state: GraphState = {
        "request_id": "r-1",
        "route": "knowledge",
        "answer_mode": "table",  # e.g. incorrectly chosen by an external router
        "status": "in_progress",
    }
    ret_res = await node_retrieve(state, _make_runtime(ctx))
    assert ret_res.get("answer_mode") == "short"


@pytest.mark.asyncio
async def test_w1_true_table_query_retains_table_mode() -> None:
    """A genuine turnaround time query retains table mode."""
    q = "what is the turnaround time for a routine prior authorization?"
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
    )
    state: GraphState = {"request_id": "r-1", "status": "in_progress"}
    route_res = await node_route(state, _make_runtime(ctx))
    assert route_res["route"] == "knowledge"
    assert route_res["answer_mode"] == "table"
