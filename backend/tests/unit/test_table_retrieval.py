"""Unit tests for Table retrieval and validation path (Problem 3b).

Verifies:
1. Table lookup queries route to answer_mode="table".
2. Tables_data rows are retrieved and formatted as [Sheet: X, Row: Y] evidence.
3. Node 8 validation passes when cited row values exist in tables_data.
4. Node 8 validation rejects answers containing hallucinated values not in tables_data.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    node_assess_evidence,
    node_rerank,
    node_retrieve,
    node_route,
    node_validate,
)
from app.ports.retriever import Retriever
from app.ports.types import (
    EvidenceId,
    TableCandidate,
    TenantContext,
    UserRole,
)


class StubTableRetriever(Retriever):
    """Retriever returning deterministic table candidates."""

    def __init__(self, candidates: list[TableCandidate] | None = None) -> None:
        self.candidates = candidates or [
            TableCandidate(
                id="tab-row-1",
                document_title="accepted_plans.csv",
                document_version_id="ver-tab-1",
                sheet_name="Sheet1",
                row_index=1,
                cells={"Plan Name": "Aetna Choice POS II", "Status": "In-Network", "Copay": "$25"},
            ),
            TableCandidate(
                id="tab-row-2",
                document_title="accepted_plans.csv",
                document_version_id="ver-tab-1",
                sheet_name="Sheet1",
                row_index=2,
                cells={
                    "Plan Name": "Blue Cross Blue Shield PPO",
                    "Status": "In-Network",
                    "Copay": "$20",
                },
            ),
        ]

    async def search(self, tenant_ctx: Any, query: str, filters: Any = None) -> list[EvidenceId]:
        _ = (tenant_ctx, query, filters)
        return []

    async def search_titles(self, tenant_ctx: Any, query: str, limit: int = 3) -> list[str]:
        _ = (tenant_ctx, query, limit)
        return ["accepted_plans.csv"]

    async def get_evidence_text(self, tenant_ctx: Any, evidence_ids: list[str]) -> dict[str, str]:
        _ = (tenant_ctx, evidence_ids)
        return {}

    async def search_tables(
        self, tenant_ctx: TenantContext, query: str, limit: int = 15
    ) -> list[TableCandidate]:
        _ = (tenant_ctx, query)
        return self.candidates[:limit]

    async def get_inventory(
        self, tenant_ctx: TenantContext, heading_limit: int = 5
    ) -> dict[str, list[str]]:
        _ = (tenant_ctx, heading_limit)
        return {"titles": ["accepted_plans.csv"], "headings": ["Accepted Plans"]}


@pytest.fixture
def table_context() -> GraphRuntimeContext:
    tenant = TenantContext(
        tenant_id=str(uuid4()),
        user_id="table-user",
        role=UserRole.STAFF,
        session_id="table-sess",
    )
    retriever = StubTableRetriever()
    ports = GraphPorts(
        retriever=retriever,
        is_mock_profile=True,
    )
    return GraphRuntimeContext(
        ports=ports,
        tenant=tenant,
        question_text="What insurance plans are accepted?",
        active_document_count=1,
    )


@pytest.mark.asyncio
async def test_table_route_sets_answer_mode_table(table_context: GraphRuntimeContext) -> None:
    """Queries about accepted insurance plans or spreadsheets route to table mode."""
    state: GraphState = {"request_id": "r1"}

    class MockRuntime:
        context = table_context

    route_res = await node_route(state, MockRuntime())  # type: ignore
    assert route_res["answer_mode"] == "table"


@pytest.mark.asyncio
async def test_table_retrieve_and_rerank_populates_evidence(
    table_context: GraphRuntimeContext,
) -> None:
    """Table retrieval extracts matching rows from tables_data and passes evidence assessment."""
    state: GraphState = {"request_id": "r1", "answer_mode": "table"}

    class MockRuntime:
        context = table_context

    # 1. Retrieve
    ret_res = await node_retrieve(state, MockRuntime())  # type: ignore
    assert "tab-row-1" in ret_res["evidence_ids"]
    assert "tab-row-2" in ret_res["evidence_ids"]
    assert table_context.candidate_evidence_types["tab-row-1"] == "table"
    assert (
        "[Document: accepted_plans.csv, Sheet: Sheet1, Row: 1]"
        in table_context.evidence_text["tab-row-1"]
    )
    assert "Aetna Choice POS II" in table_context.evidence_text["tab-row-1"]

    # 2. Rerank preserves score >= 0.0
    state["evidence_ids"] = ret_res["evidence_ids"]
    rerank_res = await node_rerank(state, MockRuntime())  # type: ignore
    assert table_context.candidate_scores["tab-row-1"] >= 0.0

    # 3. Assess evidence recognizes reliable text
    state["evidence_ids"] = rerank_res["evidence_ids"]
    assess_res = await node_assess_evidence(state, MockRuntime())  # type: ignore
    assert assess_res["evidence_state"] == "RELIABLE_TEXT"
    assert assess_res.get("status") != "refusal"


@pytest.mark.asyncio
async def test_table_validation_passes_with_real_row_values(
    table_context: GraphRuntimeContext,
) -> None:
    """Validation passes when cited row values exist in tables_data."""
    table_context.evidence_text = {
        "tab-row-1": (
            "[Document: accepted_plans.csv, Sheet: Sheet1, Row: 1] "
            "Plan Name: Aetna Choice POS II, Status: In-Network"
        ),
        "tab-row-2": (
            "[Document: accepted_plans.csv, Sheet: Sheet1, Row: 2] "
            "Plan Name: Blue Cross Blue Shield PPO, Status: In-Network"
        ),
    }
    table_context.generated_answer = {
        "mode": "table",
        "answer": (
            "Accepted plans: [Sheet: Sheet1, Row: 1] Aetna Choice POS II "
            "and [Sheet: Sheet1, Row: 2] Blue Cross Blue Shield PPO."
        ),
        "evidence_ids": ["tab-row-1", "tab-row-2"],
        "quoted_span": "Aetna Choice POS II",
    }
    state: GraphState = {
        "request_id": "r1",
        "evidence_ids": ["tab-row-1", "tab-row-2"],
        "answer_mode": "table",
        "validation_attempts": 0,
    }

    class MockRuntime:
        context = table_context

    val_res = await node_validate(state, MockRuntime())  # type: ignore
    assert val_res["status"] == "completed"


@pytest.mark.asyncio
async def test_table_validation_rejects_hallucinated_values(
    table_context: GraphRuntimeContext,
) -> None:
    """Validation fails and retries when answer claims values not found in tables_data."""
    table_context.evidence_text = {
        "tab-row-1": (
            "[Document: accepted_plans.csv, Sheet: Sheet1, Row: 1] Plan Name: Aetna Choice POS II"
        ),
    }
    table_context.generated_answer = {
        "mode": "table",
        "answer": "Accepted plans include UnitedHealth and Kaiser.",
        "evidence_ids": ["tab-row-1"],
        "quoted_span": None,
    }
    state_0: GraphState = {
        "request_id": "r1",
        "evidence_ids": ["tab-row-1"],
        "answer_mode": "table",
        "validation_attempts": 0,
    }

    class MockRuntime:
        context = table_context

    # Attempt 0 -> retry
    res_0 = await node_validate(state_0, MockRuntime())  # type: ignore
    assert res_0["status"] == "retry"

    # Attempt 1 -> refusal
    state_1: GraphState = {
        "request_id": "r1",
        "evidence_ids": ["tab-row-1"],
        "answer_mode": "table",
        "validation_attempts": 1,
    }
    res_1 = await node_validate(state_1, MockRuntime())  # type: ignore
    assert res_1["status"] == "refusal"
    assert table_context.response_text == "I couldn't confirm that from the documents."
