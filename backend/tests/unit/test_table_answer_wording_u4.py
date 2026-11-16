"""Unit tests for Stage U4: Table answer wording and validation.

Enforces:
1. When a query term is not in the table, the lead sentence names the closest matching category
   from the matched column (e.g., "There is no 'urgent' category; Expedited requests take
   24 hours.").
2. Matched rows are shown as readable lines without the "[Sheet: Sheet1, Row: N]" prefix
   (e.g., "Apex Health Insurance, Inpatient Surgery: 24 Hours (Expedited)").
3. Validation checks every shown value against the cited rows:
   - Valid readable lines pass validation.
   - Hallucinated values (e.g., invented SLA '99 Hours' or invented payer) fail validation.
   - Quoted values not in cited rows fail validation.
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
    node_generate,
    node_validate,
)
from app.ports.types import TableCandidate, TenantContext, UserRole


def _create_pa_test_candidates() -> dict[str, TableCandidate]:
    pa_data = [
        {
            "Payer Name": "Apex Health Insurance",
            "Service Category": "Diagnostic Imaging",
            "Urgency": "Routine",
            "Standard SLA": "48 Hours",
            "Liaison Contact": "555-0120",
        },
        {
            "Payer Name": "Apex Health Insurance",
            "Service Category": "Inpatient Surgery",
            "Urgency": "Expedited",
            "Standard SLA": "24 Hours",
            "Liaison Contact": "555-0120",
        },
        {
            "Payer Name": "Beacon Mutual Healthcare",
            "Service Category": "Outpatient Therapy",
            "Urgency": "Routine",
            "Standard SLA": "72 Hours",
            "Liaison Contact": "555-0121",
        },
        {
            "Payer Name": "Crestview Health",
            "Service Category": "Specialty Medication",
            "Urgency": "Expedited",
            "Standard SLA": "24 Hours",
            "Liaison Contact": "555-0122",
        },
        {
            "Payer Name": "Summit Care Plan",
            "Service Category": "Standard Clinical Review",
            "Urgency": "Routine",
            "Standard SLA": "72 Hours",
            "Liaison Contact": "555-0123",
        },
    ]
    tcs: dict[str, TableCandidate] = {}
    for idx, r in enumerate(pa_data, start=2):
        rid = f"pa-row-{idx}"
        tcs[rid] = TableCandidate(
            id=rid,
            document_title="pa_turnaround_times.xlsx",
            document_version_id=str(uuid4()),
            sheet_name="Sheet1",
            row_index=idx,
            caption="*** SYNTHETIC TEST DOCUMENT - PRIOR AUTHORIZATION TURNAROUND TIMES ***",
            cells=r,
            tenant_id="00000000-0000-0000-0000-000000000001",
        )
    return tcs


def _make_runtime(ctx: GraphRuntimeContext) -> Runtime[GraphRuntimeContext]:
    class _MockRuntime:
        def __init__(self, context: GraphRuntimeContext) -> None:
            self.context = context

    return cast(Runtime[GraphRuntimeContext], _MockRuntime(ctx))


def test_u4_urgent_query_lead_sentence_names_closest_category_from_matched_column() -> None:
    """When query asks for 'urgent', lead sentence names closest category 'Expedited'
    from the matched column 'Urgency', not 'Inpatient Surgery'.
    """
    tcs = _create_pa_test_candidates()
    question = "what is the turnaround time for an urgent prior authorization?"
    selected_ids, missing_terms = _select_matching_table_rows(
        question,
        tcs,
        list(tcs.keys()),
    )
    assert "urgent" in missing_terms
    content = _format_table_answer_text(tcs, selected_ids, missing_terms)
    lines = content.splitlines()

    # Exact lead sentence wording from Stage U4 spec
    assert lines[0] == "There is no 'urgent' category; Expedited requests take 24 hours."
    assert "Inpatient Surgery requests are 24 hours" not in lines[0]

    # Matched rows are readable lines without "[Sheet: Sheet1, Row: N]" prefix
    assert not any("[Sheet:" in line for line in lines[1:])
    assert lines[1] == "Apex Health Insurance, Inpatient Surgery: 24 Hours (Expedited)"
    assert lines[2] == "Crestview Health, Specialty Medication: 24 Hours (Expedited)"


@pytest.mark.asyncio
async def test_u4_node_generate_and_node_validate_accept_urgent_table_answer() -> None:
    """End-to-end node_generate + node_validate pass for urgent turnaround query."""
    tcs = _create_pa_test_candidates()
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text="what is the turnaround time for an urgent prior authorization?",
        table_candidates=tcs,
        candidate_evidence_types=dict.fromkeys(tcs, "table"),
        evidence_text={
            rid: (
                f"[Document: {tc.document_title}, Sheet: {tc.sheet_name}, Row: {tc.row_index}] "
                + ", ".join(f"{k}: {v}" for k, v in tc.cells.items())
            )
            for rid, tc in tcs.items()
        },
    )
    state: GraphState = {"answer_mode": "table", "evidence_ids": list(tcs.keys())}
    gen_res = await node_generate(state, _make_runtime(ctx))

    lines = ctx.response_text.splitlines()
    assert lines[0] == "There is no 'urgent' category; Expedited requests take 24 hours."
    assert not any("[Sheet:" in line for line in lines[1:])
    assert lines[1] == "Apex Health Insurance, Inpatient Surgery: 24 Hours (Expedited)"
    assert lines[2] == "Crestview Health, Specialty Medication: 24 Hours (Expedited)"

    val_res = await node_validate(
        {"answer_mode": "table", "evidence_ids": gen_res["evidence_ids"]},
        _make_runtime(ctx),
    )
    assert val_res["status"] == "completed"
    assert val_res.get("refusal") is None


@pytest.mark.asyncio
async def test_u4_node_validate_rejects_hallucinated_sla_value_in_readable_line() -> None:
    """Validation checks every shown value against cited rows: hallucinated SLA fails."""
    tcs = _create_pa_test_candidates()
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text="what is the turnaround time for an urgent prior authorization?",
        table_candidates=tcs,
    )
    # 99 Hours is hallucinated (does not exist in pa-row-3 or pa-row-5)
    fabricated_answer = (
        "There is no 'urgent' category; Expedited requests take 24 hours.\n"
        "Apex Health Insurance, Inpatient Surgery: 99 Hours (Expedited)\n"
    )
    ctx.generated_answer = {
        "mode": "table",
        "answer": fabricated_answer,
        "evidence_ids": ["pa-row-3"],
    }
    val_res = await node_validate(
        {"answer_mode": "table", "evidence_ids": ["pa-row-3"]},
        _make_runtime(ctx),
    )
    assert val_res["status"] in ("retry", "refusal")


@pytest.mark.asyncio
async def test_u4_node_validate_rejects_hallucinated_payer_in_readable_line() -> None:
    """Validation checks every shown value against cited rows: invented payer fails."""
    tcs = _create_pa_test_candidates()
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text="what is the turnaround time for an urgent prior authorization?",
        table_candidates=tcs,
    )
    # Invented Payer does not exist in any cited candidate
    fabricated_answer = (
        "There is no 'urgent' category; Expedited requests take 24 hours.\n"
        "Invented Payer, Inpatient Surgery: 24 Hours (Expedited)\n"
    )
    ctx.generated_answer = {
        "mode": "table",
        "answer": fabricated_answer,
        "evidence_ids": ["pa-row-3"],
    }
    val_res = await node_validate(
        {"answer_mode": "table", "evidence_ids": ["pa-row-3"]},
        _make_runtime(ctx),
    )
    assert val_res["status"] in ("retry", "refusal")


@pytest.mark.asyncio
async def test_u4_routine_query_shows_all_routine_rows_as_readable_lines() -> None:
    """Routine turnaround query shows readable lines without [Sheet: ..., Row: ...]
    and validates.
    """
    tcs = _create_pa_test_candidates()
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="sess-1",
        ),
        question_text="what is the turnaround time for a routine prior authorization?",
        table_candidates=tcs,
        candidate_evidence_types=dict.fromkeys(tcs, "table"),
        evidence_text={
            rid: (
                f"[Document: {tc.document_title}, Sheet: {tc.sheet_name}, Row: {tc.row_index}] "
                + ", ".join(f"{k}: {v}" for k, v in tc.cells.items())
            )
            for rid, tc in tcs.items()
        },
    )
    state: GraphState = {"answer_mode": "table", "evidence_ids": list(tcs.keys())}
    gen_res = await node_generate(state, _make_runtime(ctx))

    lines = ctx.response_text.splitlines()
    assert lines[0] == "Routine requests take 48 to 72 hours depending on payer and service."
    assert not any("[Sheet:" in line for line in lines[1:])
    assert lines[1] == "Apex Health Insurance, Diagnostic Imaging: 48 Hours (Routine)"
    assert lines[2] == "Beacon Mutual Healthcare, Outpatient Therapy: 72 Hours (Routine)"
    assert lines[3] == "Summit Care Plan, Standard Clinical Review: 72 Hours (Routine)"

    val_res = await node_validate(
        {"answer_mode": "table", "evidence_ids": gen_res["evidence_ids"]},
        _make_runtime(ctx),
    )
    assert val_res["status"] == "completed"
