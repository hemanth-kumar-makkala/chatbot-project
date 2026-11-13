"""Regression tests for table validation with colliding sheet and row indices (Phase 5 fix).

Verifies:
1. When multiple ingested spreadsheets share sheet_name (e.g. 'Sheet1') and row_index (1, 2, ...),
   node_validate accurately matches the candidate row by checking stored cell values against
   the cited row without false refusals.
2. Cell tampering / hallucinated values are still strictly rejected byte-for-byte (Invariant 1).
3. Invented row IDs are still strictly rejected.
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
    node_validate,
)
from app.ports.types import (
    TableCandidate,
    TenantContext,
    UserRole,
)


def _make_context() -> GraphRuntimeContext:
    return GraphRuntimeContext(
        ports=GraphPorts(is_mock_profile=True),
        tenant=TenantContext(
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="session-1",
        ),
    )


class MockRuntime:
    def __init__(self, context: GraphRuntimeContext) -> None:
        self.context = context


def _make_runtime(ctx: GraphRuntimeContext) -> Runtime[GraphRuntimeContext]:
    return cast(Runtime[GraphRuntimeContext], MockRuntime(ctx))


@pytest.mark.asyncio
async def test_colliding_sheet_and_row_indices_validate_cleanly() -> None:
    """Shared sheet/row indices still validate the selected candidate without refusal."""
    ctx = _make_context()

    # Candidate 1 from pa_turnaround_times.xlsx (Sheet1, Row 1)
    tc_xlsx = TableCandidate(
        id="xlsx-row-1",
        document_title="pa_turnaround_times.xlsx",
        document_version_id=str(uuid4()),
        sheet_name="Sheet1",
        row_index=1,
        cells={
            "Payer Name": "Apex Health Insurance",
            "Service Category": "Diagnostic Imaging",
            "Urgency": "Routine",
            "Standard SLA": "48 Hours",
            "Liaison Contact": "555-0120",
        },
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    # Candidate 2 from accepted_plans.csv (Sheet1, Row 1)
    tc_csv = TableCandidate(
        id="csv-row-1",
        document_title="accepted_plans.csv",
        document_version_id=str(uuid4()),
        sheet_name="Sheet1",
        row_index=1,
        cells={
            "Payer Name": "Apex Health Insurance",
            "Plan Code": "APX-HMO-01",
            "Network Tier": "In-Network Tier 1",
            "Standard Copay": "$20.00",
            "Deductible": "$250.00",
            "Pre-Auth": "Yes",
        },
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    # Note the order: tc_xlsx is inserted first in dictionary
    ctx.table_candidates = {
        "xlsx-row-1": tc_xlsx,
        "csv-row-1": tc_csv,
    }
    ctx.evidence_text = {
        "xlsx-row-1": (
            "[Document: pa_turnaround_times.xlsx, Sheet: Sheet1, Row: 1] "
            "Payer Name: Apex Health Insurance, Service Category: Diagnostic Imaging, "
            "Standard SLA: 48 Hours"
        ),
        "csv-row-1": (
            "[Document: accepted_plans.csv, Sheet: Sheet1, Row: 1] "
            "Payer Name: Apex Health Insurance, Plan Code: APX-HMO-01, Deductible: $250.00"
        ),
    }

    # Case A: Answering about accepted_plans.csv citing csv-row-1
    ctx.generated_answer = {
        "mode": "table",
        "answer": (
            "[Sheet: Sheet1, Row: 1] Payer Name: Apex Health Insurance, "
            "Plan Code: APX-HMO-01, Network Tier: In-Network Tier 1, "
            "Standard Copay: $20.00, Deductible: $250.00, Pre-Auth: Yes"
        ),
        "evidence_ids": ["csv-row-1"],
    }
    state: GraphState = {"answer_mode": "table", "evidence_ids": ["csv-row-1"]}

    res = await node_validate(state, _make_runtime(ctx))
    assert res["status"] == "completed", (
        f"CSV row with colliding Sheet1 Row 1 must validate cleanly, got: {res}"
    )

    # Case B: Answering about pa_turnaround_times.xlsx citing xlsx-row-1
    ctx.generated_answer = {
        "mode": "table",
        "answer": (
            "[Sheet: Sheet1, Row: 1] Payer Name: Apex Health Insurance, "
            "Service Category: Diagnostic Imaging, Urgency: Routine, "
            "Standard SLA: 48 Hours, Liaison Contact: 555-0120"
        ),
        "evidence_ids": ["xlsx-row-1"],
    }
    state_b: GraphState = {"answer_mode": "table", "evidence_ids": ["xlsx-row-1"]}

    res_b = await node_validate(state_b, _make_runtime(ctx))
    assert res_b["status"] == "completed", (
        f"XLSX row with colliding Sheet1 Row 1 must validate cleanly, got: {res_b}"
    )


@pytest.mark.asyncio
async def test_colliding_sheet_and_row_cell_tampering_still_rejected() -> None:
    """Tampering with a cell value even with colliding candidates is strictly rejected."""
    ctx = _make_context()

    tc_xlsx = TableCandidate(
        id="xlsx-row-1",
        document_title="pa_turnaround_times.xlsx",
        document_version_id=str(uuid4()),
        sheet_name="Sheet1",
        row_index=1,
        cells={
            "Payer Name": "Apex Health Insurance",
            "Service Category": "Diagnostic Imaging",
            "Standard SLA": "48 Hours",
        },
        tenant_id="00000000-0000-0000-0000-000000000001",
    )
    tc_csv = TableCandidate(
        id="csv-row-1",
        document_title="accepted_plans.csv",
        document_version_id=str(uuid4()),
        sheet_name="Sheet1",
        row_index=1,
        cells={
            "Payer Name": "Apex Health Insurance",
            "Plan Code": "APX-HMO-01",
            "Deductible": "$250.00",
        },
        tenant_id="00000000-0000-0000-0000-000000000001",
    )
    ctx.table_candidates = {
        "xlsx-row-1": tc_xlsx,
        "csv-row-1": tc_csv,
    }
    ctx.evidence_text = {
        "xlsx-row-1": (
            "[Document: pa_turnaround_times.xlsx, Sheet: Sheet1, Row: 1] Standard SLA: 48 Hours"
        ),
        "csv-row-1": "[Document: accepted_plans.csv, Sheet: Sheet1, Row: 1] Deductible: $250.00",
    }

    # Hallucinate Standard SLA: 12 Hours (neither candidate has this)
    ctx.generated_answer = {
        "mode": "table",
        "answer": (
            "[Sheet: Sheet1, Row: 1] Payer Name: Apex Health Insurance, "
            "Service Category: Diagnostic Imaging, Standard SLA: 12 Hours"
        ),
        "evidence_ids": ["xlsx-row-1"],
    }
    state: GraphState = {"answer_mode": "table", "evidence_ids": ["xlsx-row-1"]}

    res = await node_validate(state, _make_runtime(ctx))
    assert res["status"] in ("retry", "refusal"), "Tampered SLA must be rejected"


@pytest.mark.asyncio
async def test_invented_row_id_with_multi_tables_still_rejected() -> None:
    """An invented row ID is rejected even if text mentions stored sheet and row."""
    ctx = _make_context()

    tc_csv = TableCandidate(
        id="csv-row-1",
        document_title="accepted_plans.csv",
        document_version_id=str(uuid4()),
        sheet_name="Sheet1",
        row_index=1,
        cells={"Plan Code": "APX-HMO-01"},
        tenant_id="00000000-0000-0000-0000-000000000001",
    )
    ctx.table_candidates = {"csv-row-1": tc_csv}
    ctx.evidence_text = {
        "csv-row-1": "[Document: accepted_plans.csv, Sheet: Sheet1, Row: 1] Plan Code: APX-HMO-01"
    }

    ctx.generated_answer = {
        "mode": "table",
        "answer": "[Sheet: Sheet1, Row: 1] Plan Code: APX-HMO-01",
        "evidence_ids": ["invented-row-999"],
    }
    state: GraphState = {"answer_mode": "table", "evidence_ids": ["invented-row-999"]}

    res = await node_validate(state, _make_runtime(ctx))
    assert res["status"] in ("retry", "refusal"), "Invented row ID must be rejected"
