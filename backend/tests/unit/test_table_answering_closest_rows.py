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
                doc_cap: "Apex Health Insurance",
                "Column_2": "Diagnostic Imaging",
                "Column_3": "Routine",
                "Column_4": "48 Hours",
                "Column_5": "555-0120",
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
                doc_cap: "Beacon Mutual Healthcare",
                "Column_2": "Outpatient Therapy",
                "Column_3": "Routine",
                "Column_4": "72 Hours",
                "Column_5": "555-0121",
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
                doc_cap: "Summit Care Plan",
                "Column_2": "Standard Clinical Review",
                "Column_3": "Routine",
                "Column_4": "72 Hours",
                "Column_5": "555-0123",
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

    # Matching rows must use real column names, even when stored with legacy keys
    assert "Payer Name: Apex Health Insurance" in content
    assert "Service Category: Diagnostic Imaging" in content
    assert "Urgency: Routine" in content
    assert "Standard SLA: 48 Hours" in content
    assert "Liaison Contact: 555-0120" in content

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

    assert "Payer Name: Apex Health Insurance" in content
    assert "Service Category: Diagnostic Imaging" in content
    assert "Urgency: Routine" in content
    assert "Standard SLA: 48 Hours" in content
    assert "Liaison Contact: 555-0120" in content
