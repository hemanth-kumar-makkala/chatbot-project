"""Unit tests for Stage H: Table answering when question term is missing from table.

Verifies:
1. When query term is not in table, answer starts with 1-sentence direct answer
   (e.g., "There is no 'urgent' category; Expedited requests are 24 hours.").
2. Only closest rows for that column are listed with sheet and row citations.
3. Detected column headers are used; never "Column_N" or caption as a column name.
"""

from __future__ import annotations

from typing import Any, cast
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
)
from app.ports.types import ModelResponse, TableCandidate, TenantContext, UserRole


def _create_pa_table_candidates() -> dict[str, TableCandidate]:
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
        rid = f"row-{idx}"
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


def test_select_matching_table_rows_identifies_missing_term_and_closest_rows() -> None:
    """When query asks for 'urgent', detects missing term and selects closest rows."""
    tcs = _create_pa_table_candidates()
    question = "what is the turnaround time for an urgent prior authorization?"
    selected_ids, missing_terms = _select_matching_table_rows(
        question,
        tcs,
        list(tcs.keys()),
    )
    assert "urgent" in missing_terms
    assert len(selected_ids) <= 3
    assert len(selected_ids) > 0


def test_format_table_answer_starts_with_direct_answer_sentence() -> None:
    """Answer starts with 1-sentence direct answer and lists closest rows with citations."""
    tcs = _create_pa_table_candidates()
    question = "what is the turnaround time for an urgent prior authorization?"
    selected_ids, missing_terms = _select_matching_table_rows(
        question,
        tcs,
        list(tcs.keys()),
    )
    content = _format_table_answer_text(tcs, selected_ids, missing_terms)
    first_line = content.split("\n")[0].strip()

    assert "urgent" in first_line.lower()
    assert "no 'urgent' category" in first_line.lower()
    assert "requests take" in first_line.lower()

    # Generic readable rows without "[Sheet: Sheet1, Row: N]" prefix
    lines = content.split("\n")
    assert not any("[Sheet:" in line for line in lines[1:])
    assert "Apex Health Insurance" in lines[1]
    assert "Standard SLA:" in lines[1]


def test_format_table_answer_never_uses_column_n_or_caption() -> None:
    """Never uses caption or metadata strings as a column name;
    uses clean stored headers directly.
    """
    tcs: dict[str, TableCandidate] = {
        "row-1": TableCandidate(
            id="row-1",
            document_title="pa_turnaround_times.xlsx",
            document_version_id=str(uuid4()),
            sheet_name="Sheet1",
            row_index=2,
            caption="*** SYNTHETIC DOCUMENT ***",
            cells={
                "Payer Name": "Apex Health",
                "Service Category": "Inpatient Surgery",
                "Urgency": "Expedited",
                "Standard SLA": "24 Hours",
                "caption": "*** SYNTHETIC DOCUMENT ***",
            },
            tenant_id="00000000-0000-0000-0000-000000000001",
        )
    }
    content = _format_table_answer_text(tcs, ["row-1"], [])
    assert "caption:" not in content
    assert "SYNTHETIC DOCUMENT" not in content
    exp_apex_h = (
        "Apex Health: Service Category: Inpatient Surgery, "
        "Urgency: Expedited, Standard SLA: 24 Hours"
    )
    assert exp_apex_h in content


def test_format_table_answer_multiple_matching_rows_includes_sla_values() -> None:
    """Stage Q: When multiple rows match (routine SLA), each row is listed with its SLA value."""
    # Synthetic PA turnaround times rows where headers were Column_N
    doc_cap = "*** SYNTHETIC TEST DOCUMENT - PRIOR AUTHORIZATION TURNAROUND TIMES ***"
    tcs: dict[str, TableCandidate] = {
        "row-1": TableCandidate(
            id="row-1",
            document_title="pa_turnaround_times.xlsx",
            document_version_id=str(uuid4()),
            sheet_name="Sheet1",
            row_index=1,
            caption=doc_cap,
            cells={
                doc_cap: "Payer Name",
                "Column_2": "Service Category",
                "Column_3": "Urgency",
                "Column_4": "Standard SLA",
                "Column_5": "Liaison Contact",
            },
            tenant_id="00000000-0000-0000-0000-000000000001",
        ),
        "row-2": TableCandidate(
            id="row-2",
            document_title="pa_turnaround_times.xlsx",
            document_version_id=str(uuid4()),
            sheet_name="Sheet1",
            row_index=2,
            caption=doc_cap,
            cells={
                "Payer Name": "Apex Health Insurance",
                "Service Category": "Diagnostic Imaging",
                "Urgency": "Routine",
                "Standard SLA": "48 Hours",
                "Liaison Contact": "555-0120",
            },
            tenant_id="00000000-0000-0000-0000-000000000001",
        ),
        "row-4": TableCandidate(
            id="row-4",
            document_title="pa_turnaround_times.xlsx",
            document_version_id=str(uuid4()),
            sheet_name="Sheet1",
            row_index=4,
            caption=doc_cap,
            cells={
                "Payer Name": "Beacon Mutual Healthcare",
                "Service Category": "Outpatient Therapy",
                "Urgency": "Routine",
                "Standard SLA": "72 Hours",
                "Liaison Contact": "555-0121",
            },
            tenant_id="00000000-0000-0000-0000-000000000001",
        ),
        "row-6": TableCandidate(
            id="row-6",
            document_title="pa_turnaround_times.xlsx",
            document_version_id=str(uuid4()),
            sheet_name="Sheet1",
            row_index=6,
            caption=doc_cap,
            cells={
                "Payer Name": "Summit Care Plan",
                "Service Category": "Standard Clinical Review",
                "Urgency": "Routine",
                "Standard SLA": "72 Hours",
                "Liaison Contact": "555-0123",
            },
            tenant_id="00000000-0000-0000-0000-000000000001",
        ),
    }

    selected_ids = ["row-2", "row-4", "row-6"]
    content = _format_table_answer_text(tcs, selected_ids, [])

    # Lead sentence must start with direct plain sentence
    first_line = content.split("\n")[0].strip()
    assert first_line == "Routine requests take 48 to 72 hours depending on payer and service."

    # Every matching row must be listed with its SLA value and identifying concepts
    assert "48 Hours" in content
    assert "72 Hours" in content
    assert "Apex Health Insurance" in content
    assert "Beacon Mutual Healthcare" in content
    assert "Summit Care Plan" in content
    assert "Diagnostic Imaging" in content

    # Generic row line format: label each row by its first column, then "Header: value" for the rest
    exp_apex = (
        "Apex Health Insurance: Service Category: Diagnostic Imaging, "
        "Urgency: Routine, Standard SLA: 48 Hours, Liaison Contact: 555-0120"
    )
    exp_beacon = (
        "Beacon Mutual Healthcare: Service Category: Outpatient Therapy, "
        "Urgency: Routine, Standard SLA: 72 Hours, Liaison Contact: 555-0121"
    )
    exp_summit = (
        "Summit Care Plan: Service Category: Standard Clinical Review, "
        "Urgency: Routine, Standard SLA: 72 Hours, Liaison Contact: 555-0123"
    )
    assert exp_apex in content
    assert exp_beacon in content
    assert exp_summit in content

    # Must never use Column_N or caption as a column name
    assert "Column_2" not in content
    assert "Column_4" not in content
    assert "SYNTHETIC TEST DOCUMENT - PRIOR AUTHORIZATION TURNAROUND TIMES ***:" not in content


def test_format_table_answer_with_real_column_headers() -> None:
    """When candidates already have real column names, format preserves them and adds lead."""
    tcs = _create_pa_table_candidates()
    # routine rows are row-2 (48h), row-4 (72h), row-6 (72h)
    selected_ids = ["row-2", "row-4", "row-6"]
    content = _format_table_answer_text(tcs, selected_ids, [])

    first_line = content.split("\n")[0].strip()
    assert first_line == "Routine requests take 48 to 72 hours depending on payer and service."

    # Generic row lines
    exp_r1 = (
        "Apex Health Insurance: Service Category: Diagnostic Imaging, "
        "Urgency: Routine, Standard SLA: 48 Hours, Liaison Contact: 555-0120"
    )
    exp_r2 = (
        "Beacon Mutual Healthcare: Service Category: Outpatient Therapy, "
        "Urgency: Routine, Standard SLA: 72 Hours, Liaison Contact: 555-0121"
    )
    exp_r3 = (
        "Summit Care Plan: Service Category: Standard Clinical Review, "
        "Urgency: Routine, Standard SLA: 72 Hours, Liaison Contact: 555-0123"
    )
    assert exp_r1 in content
    assert exp_r2 in content
    assert exp_r3 in content


def test_format_table_answer_uses_stored_headers_only_never_infers_from_first_data_row() -> None:
    """Stage S3: Table answers use stored headers directly and never infer from row 1 values."""
    doc_cap = "*** SYNTHETIC TEST DOCUMENT ***"
    tcs: dict[str, TableCandidate] = {
        "row-1": TableCandidate(
            id="row-1",
            document_title="custom_data.xlsx",
            document_version_id=str(uuid4()),
            sheet_name="Sheet1",
            row_index=1,
            caption=doc_cap,
            cells={
                "Column_1": "Payer Name",
                "Column_2": "Service Category",
            },
            tenant_id="00000000-0000-0000-0000-000000000001",
        ),
        "row-2": TableCandidate(
            id="row-2",
            document_title="custom_data.xlsx",
            document_version_id=str(uuid4()),
            sheet_name="Sheet1",
            row_index=2,
            caption=doc_cap,
            cells={
                "Column_1": "Acme Plan",
                "Column_2": "Outpatient",
            },
            tenant_id="00000000-0000-0000-0000-000000000001",
        ),
    }
    content = _format_table_answer_text(tcs, ["row-2"], [])
    # Uses stored headers directly
    assert "Acme Plan: Column_2: Outpatient" in content
    # Never infers "Payer Name" or "Service Category" from row 1 values
    assert "Payer Name:" not in content
    assert "Service Category:" not in content


@pytest.mark.asyncio
async def test_node_generate_table_returns_all_three_routine_rows_when_model_cites_one() -> None:
    """Stage T3: Asking 'what is the turnaround time for a routine prior authorization?'
    must return all 3 matching routine rows, start with the plain lead sentence from Stage R2,
    and show real column names including Payer Name even when the LLM cites only 1 row.
    """

    class _SingleRowModel:
        async def generate(self, **kwargs: Any) -> ModelResponse:
            _ = kwargs
            return ModelResponse(
                content='{"answer": "72 hours", "evidence_ids": ["row-6"]}',
                model_id="mock-model",
                model_version="1.0",
                structured_output={
                    "answer": "72 hours",
                    "evidence_ids": ["row-6"],
                    "quoted_span": None,
                },
            )

    class _MockRuntime:
        def __init__(self, context: GraphRuntimeContext) -> None:
            self.context = context

    tcs = _create_pa_table_candidates()
    ctx = GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=False, model_provider=cast(Any, _SingleRowModel())),
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
    state: GraphState = {
        "route": "knowledge",
        "answer_mode": "table",
        "evidence_ids": list(tcs.keys()),
    }

    res = await node_generate(state, cast(Runtime[GraphRuntimeContext], _MockRuntime(ctx)))
    assert res["evidence_ids"] == ["row-2", "row-4", "row-6"]

    lines = ctx.response_text.splitlines()
    assert lines[0] == "Routine requests take 48 to 72 hours depending on payer and service."
    assert len(lines) == 4
    assert lines[1] == (
        "Apex Health Insurance: Service Category: Diagnostic Imaging, "
        "Urgency: Routine, Standard SLA: 48 Hours, Liaison Contact: 555-0120"
    )
    assert lines[2] == (
        "Beacon Mutual Healthcare: Service Category: Outpatient Therapy, "
        "Urgency: Routine, Standard SLA: 72 Hours, Liaison Contact: 555-0121"
    )
    assert lines[3] == (
        "Summit Care Plan: Service Category: Standard Clinical Review, "
        "Urgency: Routine, Standard SLA: 72 Hours, Liaison Contact: 555-0123"
    )
