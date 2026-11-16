"""Unit tests for Stage W2: Table refusals and CSV retrieval.

Enforces:
1. Out-of-scope questions that only loosely match a table row (NOA-012, NOA-013)
   are refused instead of answered with unrelated rows:
   a table answer requires the question's key terms to match the row values or headers.
2. The accepted_plans.csv lookups (TL-011, TL-013, TL-014) find the right rows and
   produce exact table answers.
3. Questions about written guidance (TF-003, TF-006, TF-008) are not forced into table mode
   because their key terms do not match the turnaround times spreadsheet; they use text evidence.
4. A spreadsheet containing an "Appeal Deadline" column is answered from the table, proving
   that table mode is governed by generic header/cell term matching rather than keyword lists.
"""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest
from langgraph.runtime import Runtime

from app.adapters.retrieval.mock import MockRetriever
from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    node_generate,
    node_retrieve,
    node_route,
    node_validate,
    score_table_match,
)
from app.ports.types import TableCandidate, TenantContext, UserRole


def _make_runtime(ctx: GraphRuntimeContext) -> Runtime[GraphRuntimeContext]:
    class _MockRuntime:
        def __init__(self, context: GraphRuntimeContext) -> None:
            self.context = context

    return cast(Runtime[GraphRuntimeContext], _MockRuntime(ctx))


def _create_pa_turnaround_candidates() -> dict[str, TableCandidate]:
    """Candidate rows corresponding to pa_turnaround_times.xlsx."""
    rows = [
        ("row-1", "Apex Health Insurance", "Inpatient Surgery", "24 Hours", "Expedited", "555-0120"),
        ("row-2", "Apex Health Insurance", "Diagnostic Imaging", "48 Hours", "Routine", "555-0120"),
        ("row-3", "Beacon Mutual Healthcare", "Inpatient Surgery", "24 Hours", "Expedited", "555-0121"),
        ("row-4", "Beacon Mutual Healthcare", "Outpatient Therapy", "72 Hours", "Routine", "555-0121"),
        ("row-5", "Summit Care Plan", "Inpatient Surgery", "24 Hours", "Expedited", "555-0123"),
        ("row-6", "Summit Care Plan", "Standard Clinical Review", "72 Hours", "Routine", "555-0123"),
    ]
    tcs: dict[str, TableCandidate] = {}
    for rid, payer, service, sla, urgency, contact in rows:
        tcs[rid] = TableCandidate(
            id=rid,
            document_title="pa_turnaround_times.xlsx",
            document_version_id="ver-pa-turnaround",
            sheet_name="Sheet1",
            row_index=int(rid.split("-")[1]),
            cells={
                "Payer Name": payer,
                "Service Category": service,
                "Standard SLA": sla,
                "Urgency": urgency,
                "Liaison Contact": contact,
            },
            tenant_id="00000000-0000-0000-0000-000000000001",
        )
    return tcs


def _create_accepted_plans_candidates() -> dict[str, TableCandidate]:
    """Candidate rows corresponding to accepted_plans.csv."""
    rows = [
        ("plan-row-1", "Apex Health Insurance", "APX-HMO-01", "In-Network Tier 1", "$20.00", "$250.00", "Yes"),
        ("plan-row-2", "Apex Health Insurance", "APX-PPO-02", "In-Network Tier 2", "$35.00", "$750.00", "No"),
        ("plan-row-3", "Beacon Mutual Healthcare", "BCN-EPO-10", "In-Network Tier 1", "$25.00", "$300.00", "Yes"),
        ("plan-row-4", "Beacon Mutual Healthcare", "BCN-POS-12", "In-Network Tier 2", "$40.00", "$800.00", "No"),
        ("plan-row-5", "Crestview Health", "CRST-GLD-05", "In-Network Gold Tier", "$15.00", "$500.00", "No"),
        ("plan-row-6", "Summit Care Plan", "SMT-SLV-08", "In-Network Silver Tier", "$30.00", "$1000.00", "Yes"),
    ]
    tcs: dict[str, TableCandidate] = {}
    for rid, payer, code, tier, copay, deductible, preauth in rows:
        tcs[rid] = TableCandidate(
            id=rid,
            document_title="accepted_plans.csv",
            document_version_id="ver-acc-plans",
            sheet_name="Sheet1",
            row_index=int(rid.split("-")[2]),
            cells={
                "Payer Name": payer,
                "Plan Code": code,
                "Network Tier": tier,
                "Standard Copay": copay,
                "Deductible": deductible,
                "Pre-Auth": preauth,
            },
            tenant_id="00000000-0000-0000-0000-000000000001",
        )
    return tcs


def _create_appeal_deadline_candidates() -> dict[str, TableCandidate]:
    """Candidate rows for a table with an Appeal Deadline column."""
    rows = [
        ("app-row-1", "Apex Health", "Inpatient Surgery", "60 Days", "Standard"),
        ("app-row-2", "Beacon Mutual", "Diagnostic Imaging", "45 Days", "Standard"),
        ("app-row-3", "Summit Care", "Outpatient Therapy", "30 Days", "Expedited"),
    ]
    tcs: dict[str, TableCandidate] = {}
    for rid, payer, service, deadline, urgency in rows:
        tcs[rid] = TableCandidate(
            id=rid,
            document_title="payer_appeal_deadlines.xlsx",
            document_version_id="ver-appeal-deadlines",
            sheet_name="Sheet1",
            row_index=int(rid.split("-")[2]),
            cells={
                "Payer Name": payer,
                "Service Category": service,
                "Appeal Deadline": deadline,
                "Urgency": urgency,
            },
            tenant_id="00000000-0000-0000-0000-000000000001",
        )
    return tcs


# ==============================================================================
# Part 1: Table Refusals for Out-of-Scope Questions (NOA-012, NOA-013)
# ==============================================================================


@pytest.mark.asyncio
async def test_w2_noa_012_reimbursement_fee_cpt_99205_refused() -> None:
    """NOA-012: Reimbursement fee for CPT 99205 is not in the table; must be refused."""
    q = "What is the standard reimbursement fee for new patient office visit CPT code 99205?"
    tcs = _create_pa_turnaround_candidates()

    # 1. Scoring function proves no match
    match_res = score_table_match(q, tcs)
    assert match_res.is_match is False
    assert "99205" in match_res.unmatched_tokens
    assert "reimbursement" in match_res.unmatched_tokens

    # 2. node_generate in table mode produces safe refusal rather than closest rows
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
        table_candidates=tcs,
    )
    state: GraphState = {"answer_mode": "table", "evidence_ids": list(tcs.keys())}
    gen_res = await node_generate(state, _make_runtime(ctx))
    assert ctx.response_text.startswith("I couldn't confirm that")
    assert "Inpatient Surgery" not in ctx.response_text

    # 3. node_validate cleanly validates safe refusal
    val_res = await node_validate(
        {"answer_mode": "table", "evidence_ids": gen_res["evidence_ids"]},
        _make_runtime(ctx),
    )
    assert val_res["status"] in ("completed", "refusal")


@pytest.mark.asyncio
async def test_w2_noa_013_dermatology_biopsy_refused() -> None:
    """NOA-013: Urgent dermatology biopsy pathology is not in the table; must be refused."""
    q = "What is the SLA turnaround time for processing urgent dermatology biopsy pathology?"
    tcs = _create_pa_turnaround_candidates()

    # 1. Scoring function proves no match (foreign service category)
    match_res = score_table_match(q, tcs)
    assert match_res.is_match is False
    assert "dermatology" in match_res.unmatched_tokens
    assert "biopsy" in match_res.unmatched_tokens
    assert "pathology" in match_res.unmatched_tokens

    # 2. node_generate refuses instead of returning Inpatient Surgery
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
        table_candidates=tcs,
    )
    state: GraphState = {"answer_mode": "table", "evidence_ids": list(tcs.keys())}
    gen_res = await node_generate(state, _make_runtime(ctx))
    assert ctx.response_text.startswith("I couldn't confirm that")
    assert "Inpatient Surgery" not in ctx.response_text

    # 3. node_validate validates refusal
    val_res = await node_validate(
        {"answer_mode": "table", "evidence_ids": gen_res["evidence_ids"]},
        _make_runtime(ctx),
    )
    assert val_res["status"] in ("completed", "refusal")


# ==============================================================================
# Part 2: CSV Retrieval & Table Answers for accepted_plans.csv (TL-011, TL-013, TL-014)
# ==============================================================================


@pytest.mark.asyncio
async def test_w2_tl_011_crestview_health_gold_tier_deductible() -> None:
    """TL-011: Annual deductible for Crestview Health Gold Tier is $500.00 from accepted_plans.csv."""
    q = "What is the annual deductible for Crestview Health under the Gold Tier plan?"
    tcs = _create_accepted_plans_candidates()

    # 1. Score match succeeds
    match_res = score_table_match(q, tcs)
    assert match_res.is_match is True
    assert "crestview" in match_res.cell_matches
    assert "deductible" in match_res.header_matches

    # 2. node_retrieve enables table mode with MockRetriever
    mock_retriever = MockRetriever(canned_table_candidates=list(tcs.values()))
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True, retriever=mock_retriever),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
    )
    state: GraphState = {"route": "knowledge", "answer_mode": "short"}
    ret_res = await node_retrieve(state, _make_runtime(ctx))
    assert ret_res["answer_mode"] == "table"
    assert "plan-row-5" in ret_res["evidence_ids"]

    # 3. node_generate outputs exact deductible
    gen_res = await node_generate(
        {"answer_mode": "table", "evidence_ids": ret_res["evidence_ids"]},
        _make_runtime(ctx),
    )
    assert "$500.00" in ctx.response_text
    assert "Crestview Health" in ctx.response_text
    assert "CRST-GLD-05" in ctx.response_text

    # 4. node_validate passes
    val_res = await node_validate(
        {"answer_mode": "table", "evidence_ids": gen_res["evidence_ids"]},
        _make_runtime(ctx),
    )
    assert val_res["status"] == "completed"


@pytest.mark.asyncio
async def test_w2_tl_013_summit_care_plan_smt_slv_08_deductible() -> None:
    """TL-013: Deductible for Summit Care Plan code SMT-SLV-08 is $1000.00 from accepted_plans.csv."""
    q = "What is the deductible amount listed for Summit Care Plan under code SMT-SLV-08?"
    tcs = _create_accepted_plans_candidates()

    match_res = score_table_match(q, tcs)
    assert match_res.is_match is True
    assert "summit" in match_res.cell_matches
    assert "smt-slv-08" in match_res.cell_matches
    assert "deductible" in match_res.header_matches

    mock_retriever = MockRetriever(canned_table_candidates=list(tcs.values()))
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True, retriever=mock_retriever),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
    )
    state: GraphState = {"route": "knowledge", "answer_mode": "short"}
    ret_res = await node_retrieve(state, _make_runtime(ctx))
    assert ret_res["answer_mode"] == "table"
    assert "plan-row-6" in ret_res["evidence_ids"]

    gen_res = await node_generate(
        {"answer_mode": "table", "evidence_ids": ret_res["evidence_ids"]},
        _make_runtime(ctx),
    )
    assert "$1000.00" in ctx.response_text
    assert "Summit Care Plan" in ctx.response_text
    assert "SMT-SLV-08" in ctx.response_text

    val_res = await node_validate(
        {"answer_mode": "table", "evidence_ids": gen_res["evidence_ids"]},
        _make_runtime(ctx),
    )
    assert val_res["status"] == "completed"


@pytest.mark.asyncio
async def test_w2_tl_014_crestview_health_gold_tier_preauth() -> None:
    """TL-014: Crestview Health Gold Tier prior authorization requirement is No."""
    q = "Does Crestview Health Gold Tier require prior authorization for services?"
    tcs = _create_accepted_plans_candidates()

    match_res = score_table_match(q, tcs)
    assert match_res.is_match is True
    assert "crestview" in match_res.cell_matches

    mock_retriever = MockRetriever(canned_table_candidates=list(tcs.values()))
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True, retriever=mock_retriever),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
    )
    state: GraphState = {"route": "knowledge", "answer_mode": "short"}
    ret_res = await node_retrieve(state, _make_runtime(ctx))
    assert ret_res["answer_mode"] == "table"
    assert "plan-row-5" in ret_res["evidence_ids"]

    gen_res = await node_generate(
        {"answer_mode": "table", "evidence_ids": ret_res["evidence_ids"]},
        _make_runtime(ctx),
    )
    assert "Pre-Auth: No" in ctx.response_text
    assert "Crestview Health" in ctx.response_text

    val_res = await node_validate(
        {"answer_mode": "table", "evidence_ids": gen_res["evidence_ids"]},
        _make_runtime(ctx),
    )
    assert val_res["status"] == "completed"


# ==============================================================================
# Part 3: Written Guidance Uses Text Evidence (TF-003, TF-006, TF-008)
# ==============================================================================


@pytest.mark.asyncio
async def test_w2_tf_003_outpatient_advance_submission_uses_text_evidence() -> None:
    """TF-003: Questions about advance submission rules use text evidence, not table mode."""
    q = "How many business days in advance must outpatient surgery prior auths be submitted?"
    tcs = _create_pa_turnaround_candidates()

    # Table candidate match fails because table has no advance submission columns/data
    match_res = score_table_match(q, tcs)
    assert match_res.is_match is False
    assert "advance" in match_res.unmatched_tokens
    assert "submitted" in match_res.unmatched_tokens

    # node_retrieve does not assign table mode
    mock_retriever = MockRetriever(
        canned_table_candidates=list(tcs.values()),
        canned_evidence_text={
            "c_text": "Outpatient surgery prior authorizations must be submitted at least 5 business days in advance."
        },
    )
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True, retriever=mock_retriever),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
    )
    state: GraphState = {"route": "knowledge", "answer_mode": "table"}  # incoming mode
    ret_res = await node_retrieve(state, _make_runtime(ctx))
    assert ret_res["answer_mode"] == "short"


@pytest.mark.asyncio
async def test_w2_tf_006_urgent_appeal_timeframe_uses_text_evidence() -> None:
    """TF-006: Urgent appeal timeframes use text evidence, not table mode."""
    q = "Within what timeframe must an urgent authorization appeal be filed after a denial?"
    tcs = _create_pa_turnaround_candidates()

    match_res = score_table_match(q, tcs)
    assert match_res.is_match is False
    assert "appeal" in match_res.unmatched_tokens
    assert "denial" in match_res.unmatched_tokens

    mock_retriever = MockRetriever(
        canned_table_candidates=list(tcs.values()),
        canned_evidence_text={
            "c_text": "Appeals for urgent authorizations must be filed within 72 hours of denial."
        },
    )
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True, retriever=mock_retriever),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
    )
    state: GraphState = {"route": "knowledge", "answer_mode": "table"}
    ret_res = await node_retrieve(state, _make_runtime(ctx))
    assert ret_res["answer_mode"] == "short"


@pytest.mark.asyncio
async def test_w2_tf_008_standard_appeal_days_uses_text_evidence() -> None:
    """TF-008: Standard appeal calendar days use text evidence, not table mode."""
    q = "How many calendar days do we have to file a standard appeal following an auth denial letter?"
    tcs = _create_pa_turnaround_candidates()

    match_res = score_table_match(q, tcs)
    assert match_res.is_match is False
    assert "appeal" in match_res.unmatched_tokens
    assert "denial" in match_res.unmatched_tokens

    mock_retriever = MockRetriever(
        canned_table_candidates=list(tcs.values()),
        canned_evidence_text={
            "c_text": "A standard appeal must be filed within 30 calendar days of an auth denial letter."
        },
    )
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True, retriever=mock_retriever),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
    )
    state: GraphState = {"route": "knowledge", "answer_mode": "table"}
    ret_res = await node_retrieve(state, _make_runtime(ctx))
    assert ret_res["answer_mode"] == "short"


# ==============================================================================
# Part 4: Spreadsheet with "Appeal Deadline" Column Answered from Table
# ==============================================================================


@pytest.mark.asyncio
async def test_w2_spreadsheet_with_appeal_deadline_column_answered_from_table() -> None:
    """A spreadsheet containing an 'Appeal Deadline' column is answered from the table."""
    q = "What is the appeal deadline for Apex Health?"
    tcs = _create_appeal_deadline_candidates()

    # 1. Scoring function confirms table match based on column and cell match
    match_res = score_table_match(q, tcs)
    assert match_res.is_match is True
    assert "apex" in match_res.cell_matches
    assert "health" in match_res.cell_matches
    assert "appeal" in match_res.header_matches
    assert "deadline" in match_res.header_matches

    # 2. node_retrieve enables table mode
    mock_retriever = MockRetriever(canned_table_candidates=list(tcs.values()))
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True, retriever=mock_retriever),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text=q,
    )
    state: GraphState = {"route": "knowledge", "answer_mode": "short"}
    ret_res = await node_retrieve(state, _make_runtime(ctx))
    assert ret_res["answer_mode"] == "table"
    assert "app-row-1" in ret_res["evidence_ids"]

    # 3. node_generate answers from table with 60 Days
    gen_res = await node_generate(
        {"answer_mode": "table", "evidence_ids": ret_res["evidence_ids"]},
        _make_runtime(ctx),
    )
    assert "Apex Health:" in ctx.response_text
    assert "Appeal Deadline: 60 Days" in ctx.response_text

    # 4. node_validate validates the table row
    val_res = await node_validate(
        {"answer_mode": "table", "evidence_ids": gen_res["evidence_ids"]},
        _make_runtime(ctx),
    )
    assert val_res["status"] == "completed"
