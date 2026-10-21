# ruff: noqa: E501, ARG002
"""Targeted tests for critical bug fixes:
1. Table answers: model never writes cell values; code builds from tables_data;
   invented row ID rejected; every value shown equals the stored cell.
2. Conflict quotes: every quoted span must be an exact substring of stored chunk text;
   paraphrase replaced with stored sentence; tested with real v1/v2 text.
3. Citations: only evidence actually used is cited; unrelated sources removed.
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
    _find_exact_quote_in_chunk,
    node_assess_evidence,
    node_generate,
    node_validate,
)
from app.ports.types import (
    ModelResponse,
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


# --- 1. TABLE ANSWERS TESTS ---


@pytest.mark.asyncio
async def test_table_answer_invented_row_id_rejected() -> None:
    """An invented row ID that does not exist in tables_data is rejected by validation."""
    ctx = _make_context()
    real_tc = TableCandidate(
        id="row-1",
        document_title="accepted_plans.csv",
        document_version_id=str(uuid4()),
        sheet_name="Sheet1",
        row_index=1,
        cells={
            "Plan Code": "APX-HMO-01",
            "Payer Name": "Apex Health Insurance",
            "Deductible": "$250.00",
        },
    )
    ctx.table_candidates = {"row-1": real_tc}
    ctx.evidence_text = {
        "row-1": "[Document: accepted_plans.csv, Sheet: Sheet1, Row: 1] Plan Code: APX-HMO-01, Deductible: $250.00"
    }

    # Model hallucinated / invented "row-999"
    ctx.generated_answer = {
        "mode": "table",
        "answer": "[Sheet: Sheet1, Row: 999] Plan Code: FAKE-01, Deductible: $999.00",
        "evidence_ids": ["row-999"],
    }
    state: GraphState = {"answer_mode": "table", "evidence_ids": ["row-1"]}

    res = await node_validate(state, _make_runtime(ctx))
    assert res["status"] in ("retry", "refusal"), "Invented row id must be rejected"


@pytest.mark.asyncio
async def test_table_answer_value_mismatch_rejected() -> None:
    """A row answer where a cell value does not equal the stored cell is rejected."""
    ctx = _make_context()
    real_tc = TableCandidate(
        id="row-1",
        document_title="accepted_plans.csv",
        document_version_id=str(uuid4()),
        sheet_name="Sheet1",
        row_index=1,
        cells={
            "Plan Code": "APX-HMO-01",
            "Payer Name": "Apex Health Insurance",
            "Deductible": "$250.00",
        },
    )
    ctx.table_candidates = {"row-1": real_tc}
    ctx.evidence_text = {
        "row-1": "[Document: accepted_plans.csv, Sheet: Sheet1, Row: 1] Plan Code: APX-HMO-01, Deductible: $250.00"
    }

    # Hallucinated deductible $500.00 instead of real $250.00
    ctx.generated_answer = {
        "mode": "table",
        "answer": "[Sheet: Sheet1, Row: 1] Plan Code: APX-HMO-01, Payer Name: Apex Health Insurance, Deductible: $500.00",
        "evidence_ids": ["row-1"],
    }
    state: GraphState = {"answer_mode": "table", "evidence_ids": ["row-1"]}

    res = await node_validate(state, _make_runtime(ctx))
    assert res["status"] in ("retry", "refusal"), (
        "Value mismatch ($500.00 vs $250.00) must be rejected"
    )


@pytest.mark.asyncio
async def test_table_answer_exact_stored_cells_accepted() -> None:
    """Every value shown equals the stored cell -> validation passes."""
    ctx = _make_context()
    plans = [
        ("row-1", 1, "Apex Health Insurance", "APX-HMO-01", "$250.00"),
        ("row-2", 2, "Apex Health Insurance", "APX-PPO-02", "$750.00"),
        ("row-3", 3, "Beacon Mutual Healthcare", "BCN-EPO-10", "$300.00"),
        ("row-4", 4, "Crestview Health", "CRST-GLD-05", "$500.00"),
        ("row-5", 5, "Summit Care Plan", "SMT-SLV-08", "$1000.00"),
    ]
    formatted_rows = []
    for rid, idx, payer, code, ded in plans:
        tc = TableCandidate(
            id=rid,
            document_title="accepted_plans.csv",
            document_version_id=str(uuid4()),
            sheet_name="Sheet1",
            row_index=idx,
            cells={"Payer Name": payer, "Plan Code": code, "Deductible": ded},
        )
        ctx.table_candidates[rid] = tc
        ctx.evidence_text[rid] = (
            f"[Document: accepted_plans.csv, Sheet: Sheet1, Row: {idx}] Payer Name: {payer}, Plan Code: {code}, Deductible: {ded}"
        )
        formatted_rows.append(
            f"[Sheet: Sheet1, Row: {idx}] Payer Name: {payer}, Plan Code: {code}, Deductible: {ded}"
        )

    ans_str = "; ".join(formatted_rows)
    ctx.generated_answer = {
        "mode": "table",
        "answer": ans_str,
        "evidence_ids": [p[0] for p in plans],
    }
    state: GraphState = {"answer_mode": "table", "evidence_ids": [p[0] for p in plans]}

    res = await node_validate(state, _make_runtime(ctx))
    assert res["status"] == "completed", "Exact stored cells must pass validation"


@pytest.mark.asyncio
async def test_render_table_answer_compares_every_cell_to_stored_row_byte_for_byte() -> None:
    """Table answer must be built only by code from stored cells and match byte for byte."""
    import re

    ctx = _make_context()
    stored_rows = [
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
    tcs = {}
    for idx, r_data in enumerate(stored_rows, start=1):
        rid = f"row-{idx}"
        tc = TableCandidate(
            id=rid,
            document_title="accepted_plans.csv",
            document_version_id=str(uuid4()),
            sheet_name="Sheet1",
            row_index=idx,
            cells=r_data,
        )
        tcs[rid] = tc
        ctx.evidence_text[rid] = (
            f"[Document: accepted_plans.csv, Sheet: Sheet1, Row: {idx}] "
            + ", ".join(f"{k}: {v}" for k, v in r_data.items())
        )
    ctx.table_candidates = tcs

    state: GraphState = {"answer_mode": "table", "evidence_ids": list(tcs.keys())}
    res = await node_generate(state, _make_runtime(ctx))
    assert res["status"] == "in_progress"

    rendered_answer = ctx.response_text
    row_blocks = re.findall(r"\[Sheet:\s*([^,]+),\s*Row:\s*(\d+)\]\s*([^;]+)", rendered_answer)
    assert len(row_blocks) == len(stored_rows), "All rows must be rendered"

    for _, row_idx_str, row_content in row_blocks:
        r_idx = int(row_idx_str)
        stored_cells = stored_rows[r_idx - 1]
        for pair in row_content.split(", "):
            col_name, col_val = pair.split(": ", 1)
            stored_val = stored_cells[col_name.strip()]
            # Byte for byte exact comparison
            assert col_val.strip().encode("utf-8") == stored_val.encode("utf-8")

    # Invariant: validation passes
    val_res = await node_validate(state, _make_runtime(ctx))
    assert val_res["status"] == "completed"


# --- 2. CONFLICT QUOTES TESTS ---


def test_find_exact_quote_in_chunk_replaces_paraphrase_with_stored_sentence() -> None:
    """If model paraphrases, code extracts the exact stored sentence containing the value."""
    v2_chunk = (
        "*** SYNTHETIC TEST DOCUMENT ***\n"
        "HORIZON VALLEY HEALTH CLINIC\n"
        "Front-Desk Operational Standard Operating Procedure (Version 2.0 Revised)\n"
        "Issued: March 1, 2026 (Supersedes Version 1.0)\n"
        "1. Patient Check-In Protocol\n"
        "Routine appointment arrival window is 20 minutes prior to scheduled visit.\n"
        "Patients arriving later than 20 minutes will be rescheduled.\n"
        "2. Referral & Document Transmission\n"
        "Front desk intake fax number is 555-0102."
    )

    # Paraphrased quote from model:
    paraphrased_quote = "Patients must arrive 20 minutes prior to scheduled routine appointments"
    val = "20 minutes"

    res = _find_exact_quote_in_chunk(paraphrased_quote, val, v2_chunk)
    assert res != paraphrased_quote, "Paraphrase must not be returned"
    assert res == "Routine appointment arrival window is 20 minutes prior to scheduled visit."
    assert res in v2_chunk, "Returned span must be an exact substring of stored chunk"


def test_find_exact_quote_in_chunk_preserves_exact_match() -> None:
    """If model provides exact quote, it is preserved."""
    v1_chunk = (
        "1. Patient Check-In Protocol\n"
        "Routine appointment arrival window is 15 minutes prior to scheduled visit.\n"
        "2. Referral & Document Transmission\n"
        "Front desk intake fax number is 555-0101."
    )
    exact_quote = "Front desk intake fax number is 555-0101."
    res = _find_exact_quote_in_chunk(exact_quote, "555-0101", v1_chunk)
    assert res == exact_quote
    assert res in v1_chunk


def test_conflict_quotes_v2_quote_contains_revised_and_v1_does_not() -> None:
    """Conflict quotes must come from each version's stored text: v2 contains 'revised', v1 does not."""
    v1_chunk = (
        "*** SYNTHETIC TEST DOCUMENT ***\n"
        "HORIZON VALLEY HEALTH CLINIC\n"
        "Front-Desk Operational Standard Operating Procedure (Version 1.0)\n"
        "Issued: January 15, 2026\n"
        "1. Patient Check-In Protocol\n"
        "Routine appointment arrival window is 15 minutes prior to scheduled visit.\n"
        "Patients arriving later than 15 minutes will be rescheduled.\n"
        "2. Referral & Document Transmission\n"
        "Front desk intake fax number is 555-0101.\n"
        "Outside referrals must be sent directly to this intake fax line.\n"
        "3. Identity Verification\n"
        "Front desk staff must verify photo ID and insurance card."
    )
    v2_chunk = (
        "*** SYNTHETIC TEST DOCUMENT ***\n"
        "HORIZON VALLEY HEALTH CLINIC\n"
        "Front-Desk Operational Standard Operating Procedure (Version 2.0 Revised)\n"
        "Issued: March 1, 2026 (Supersedes Version 1.0)\n"
        "1. Patient Check-In Protocol\n"
        "Routine appointment arrival window is 20 minutes prior to scheduled visit.\n"
        "Patients arriving later than 20 minutes will be rescheduled.\n"
        "2. Referral & Document Transmission\n"
        "Front desk intake fax number is 555-0102.\n"
        "Outside referrals must be sent directly to this revised intake fax line.\n"
        "3. Identity Verification\n"
        "Front desk staff must verify photo ID and insurance card."
    )

    # When quotes for the intake fax line are extracted:
    v1_quote = _find_exact_quote_in_chunk(
        "Outside referrals must be sent directly to this intake fax line.", "555-0101", v1_chunk
    )
    v2_quote = _find_exact_quote_in_chunk(
        "Outside referrals must be sent directly to this revised intake fax line.",
        "555-0102",
        v2_chunk,
    )

    assert "revised" in v2_quote, "v2 quote must contain 'revised'"
    assert "revised" not in v1_quote, "v1 quote must not contain 'revised'"
    assert v1_quote in v1_chunk, "v1 quote must be exact substring of stored v1 chunk"
    assert v2_quote in v2_chunk, "v2 quote must be exact substring of stored v2 chunk"

    # Even if model's candidate quote omitted 'revised', code recovers the stored v2 sentence:
    v2_recovered = _find_exact_quote_in_chunk(
        "Outside referrals must be sent directly to this intake fax line.", "555-0102", v2_chunk
    )
    assert "revised" in v2_recovered, "Recovered v2 quote must contain 'revised' from stored text"
    assert v2_recovered in v2_chunk


# --- 3. CITATIONS TESTS ---


@pytest.mark.asyncio
async def test_conflict_node_cites_only_conflicting_documents() -> None:
    """Conflict detection cites only the 2 conflicting chunks, ignoring unrelated retrieved chunks."""
    ctx = _make_context()
    ctx.question_text = "What is the front desk intake fax number?"

    c1_text = (
        "[Document: front_desk_policy_v1.pdf, Page: 1] Front desk intake fax number is 555-0101."
    )
    c2_text = (
        "[Document: front_desk_policy_v2.pdf, Page: 1] Front desk intake fax number is 555-0102."
    )
    c3_text = "[Document: fee_schedule.pdf, Page: 1] Billing office phone is 555-0150."

    ctx.evidence_text = {"c1": c1_text, "c2": c2_text, "c3": c3_text}
    ctx.candidate_scores = {"c1": 0.95, "c2": 0.94, "c3": 0.30}

    class MockConflictModel:
        async def generate(self, **kwargs: Any) -> ModelResponse:
            return ModelResponse(
                content="",
                model_id="test",
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

    ctx.ports.model_provider = MockConflictModel()  # type: ignore[assignment]
    state: GraphState = {"evidence_ids": ["c1", "c2", "c3"]}

    res = await node_assess_evidence(state, _make_runtime(ctx))
    assert res["status"] == "conflicting"
    assert res["evidence_ids"] == ["c1", "c2"]
    assert "c3" not in res["evidence_ids"]


@pytest.mark.asyncio
async def test_generate_cites_only_used_evidence_dropping_unrelated_sources() -> None:
    """node_generate must cite only the chunk actually used, dropping unrelated chunks."""
    from app.core.graph import node_generate

    ctx = _make_context()
    ctx.question_text = "What is the effective date for our outpatient prior authorization policy?"
    c1_text = (
        "[Document: prior_auth_guide.pdf, Page: 1] Outpatient Prior Authorization Guide. "
        "Policy Reference: 2026-PA-01. Effective Date: January 1, 2026."
    )
    c2_text = (
        "[Document: front_desk_policy_v1.pdf, Page: 1] Routine appointment arrival window "
        "is 15 minutes prior to scheduled visit."
    )
    ctx.evidence_text = {"c1": c1_text, "c2": c2_text}
    ctx.candidate_scores = {"c1": 0.90, "c2": 0.60}
    ctx.ports.is_mock_profile = False

    class MockAnswerModel:
        async def generate(self, **kwargs: Any) -> ModelResponse:
            return ModelResponse(
                content="January 1, 2026",
                model_id="test",
                model_version="1.0",
                structured_output={
                    "mode": "short",
                    "answer": "The effective date for our outpatient prior authorization policy is January 1, 2026.",
                    "evidence_ids": ["c1", "c2"],  # Model lazily cited both
                    "quoted_span": "January 1, 2026",
                    "evidence_type": "text",
                },
            )

    ctx.ports.model_provider = MockAnswerModel()  # type: ignore[assignment]
    state: GraphState = {"answer_mode": "short", "evidence_ids": ["c1", "c2"]}

    res = await node_generate(state, _make_runtime(ctx))
    assert res["status"] == "in_progress"
    assert res["evidence_ids"] == ["c1"], "Must drop unrelated c2 (front_desk_policy_v1.pdf)"
    assert "c2" not in res["evidence_ids"]
