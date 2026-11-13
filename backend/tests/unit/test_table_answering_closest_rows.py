"""Unit tests for Stage H: Table answering when question term is missing from table.

Verifies:
1. When query term is not in table, answer starts with 1-sentence direct answer
   (e.g., "There is no 'urgent' category; Expedited requests are 24 hours.").
2. Only closest rows for that column are listed with sheet and row citations.
3. Detected column headers are used; never "Column_N" or caption as a column name.
"""

from __future__ import annotations

from uuid import uuid4

from app.core.graph import (
    _format_table_answer_text,
    _select_matching_table_rows,
)
from app.ports.types import TableCandidate


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
    """When query asks for 'urgent', detects missing term and selects closest expedited rows."""
    tcs = _create_pa_table_candidates()
    question = "what is the turnaround time for an urgent prior authorization?"
    selected_ids, missing_terms = _select_matching_table_rows(
        question,
        tcs,
        list(tcs.keys()),
    )
    assert "urgent" in missing_terms
    assert len(selected_ids) <= 3
    # Both row-3 and row-5 are Expedited rows
    assert all(tcs[rid].cells.get("Urgency") == "Expedited" for rid in selected_ids)


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
    assert "no 'urgent' category" in first_line.lower() or "not listed" in first_line.lower()
    assert "Expedited" in first_line or "expedited" in first_line.lower()
    assert "24" in first_line

    # Next lines contain closest row citations
    lines = content.split("\n")
    assert any("[Sheet: Sheet1, Row:" in line for line in lines[1:])
    assert "Standard SLA: 24 Hours" in content


def test_format_table_answer_never_uses_column_n_or_caption() -> None:
    """Never uses 'Column_N' or the caption string as a column name."""
    tcs: dict[str, TableCandidate] = {
        "row-1": TableCandidate(
            id="row-1",
            document_title="pa_turnaround_times.xlsx",
            document_version_id=str(uuid4()),
            sheet_name="Sheet1",
            row_index=2,
            caption="*** SYNTHETIC DOCUMENT ***",
            cells={
                "Column_1": "Apex Health",
                "Column_2": "Inpatient Surgery",
                "Urgency": "Expedited",
                "Standard SLA": "24 Hours",
                "caption": "*** SYNTHETIC DOCUMENT ***",
            },
            tenant_id="00000000-0000-0000-0000-000000000001",
        )
    }
    content = _format_table_answer_text(tcs, ["row-1"], [])
    assert "Column_1:" not in content
    assert "Column_2:" not in content
    assert "caption:" not in content
    assert "SYNTHETIC DOCUMENT" not in content
    assert "Urgency: Expedited" in content
    assert "Standard SLA: 24 Hours" in content
