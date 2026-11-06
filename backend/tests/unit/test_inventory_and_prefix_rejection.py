"""Unit tests for Knowledge-Base Inventory Reply and Prefix Rejection (Problems 3c and 3d).

Verifies:
1. "What data do you have?" returns active titles and headings without model call.
2. Empty assistant returns exact refusal.
3. Validation rejects an answer that is empty or only repeats a context prefix.
4. Exact refusal messages for Part E.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.core.graph import (
    WEAK_EVIDENCE_THRESHOLD,
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    node_assess_evidence,
    node_route,
    node_validate,
)
from app.ports.retriever import Retriever
from app.ports.types import (
    EvidenceId,
    TenantContext,
    UserRole,
)


class StubInventoryRetriever(Retriever):
    """Retriever returning deterministic inventory data."""

    def __init__(self, titles: list[str] | None = None, headings: list[str] | None = None) -> None:
        self.titles = (
            titles
            if titles is not None
            else [
                "clean_fax.pdf",
                "accepted_plans.csv",
                "prior_auth_guide.pdf",
            ]
        )
        self.headings = (
            headings
            if headings is not None
            else [
                "Section 1",
                "Accepted Insurance Plans",
                "Prior Authorization Protocols",
            ]
        )

    async def search(self, tenant_ctx: Any, query: str, filters: Any = None) -> list[EvidenceId]:
        _ = (tenant_ctx, query, filters)
        return []

    async def search_titles(self, tenant_ctx: Any, query: str, limit: int = 3) -> list[str]:
        _ = (tenant_ctx, query)
        return self.titles[:limit]

    async def get_evidence_text(self, tenant_ctx: Any, evidence_ids: list[str]) -> dict[str, str]:
        _ = (tenant_ctx, evidence_ids)
        return {}

    async def search_tables(self, tenant_ctx: Any, query: str, limit: int = 15) -> list[Any]:
        _ = (tenant_ctx, query, limit)
        return []

    async def get_inventory(self, tenant_ctx: Any, heading_limit: int = 5) -> dict[str, list[str]]:
        _ = tenant_ctx
        return {"titles": self.titles, "headings": self.headings[:heading_limit]}

    async def get_neighbouring_chunks(
        self, tenant_ctx: Any, chunk_ids: list[str]
    ) -> list[EvidenceId]:
        _ = (tenant_ctx, chunk_ids)
        return []


class TrackingModelProvider:
    """Model provider stub that tracks invocation count."""

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self.call_count += 1
        raise AssertionError("ModelProvider was called but should have been bypassed!")


@pytest.fixture
def inventory_context() -> GraphRuntimeContext:
    tenant = TenantContext(
        tenant_id=str(uuid4()),
        user_id="inv-user",
        role=UserRole.STAFF,
        session_id="inv-sess",
    )
    retriever = StubInventoryRetriever()
    ports = GraphPorts(
        retriever=retriever,
        model_provider=TrackingModelProvider(),  # type: ignore
        is_mock_profile=False,
    )
    return GraphRuntimeContext(
        ports=ports,
        tenant=tenant,
        question_text="What data do you have?",
        active_document_count=3,
    )


@pytest.mark.asyncio
async def test_inventory_query_returns_titles_and_headings_without_model(
    inventory_context: GraphRuntimeContext,
) -> None:
    """When asked 'What data do you have?', list active titles and up to 5 headings
    with NO model call.
    """
    state: GraphState = {"request_id": "r1"}

    class MockRuntime:
        context = inventory_context

    res = await node_route(state, MockRuntime())  # type: ignore
    assert res["status"] == "completed"
    assert "clean_fax.pdf" in inventory_context.response_text
    assert "accepted_plans.csv" in inventory_context.response_text
    assert "Section 1" in inventory_context.response_text
    assert "Prior Authorization Protocols" in inventory_context.response_text
    # Model provider call count must remain 0
    assert inventory_context.ports.model_provider.call_count == 0  # type: ignore

    # Inventory intent must explicitly ask which documents are possessed.
    inventory_context.question_text = "what documents do you have"
    res2 = await node_route(state, MockRuntime())  # type: ignore
    assert res2["status"] == "completed"
    assert "clean_fax.pdf" in inventory_context.response_text
    assert inventory_context.ports.model_provider.call_count == 0  # type: ignore

    assert inventory_context.ports.model_provider.call_count == 0  # type: ignore


@pytest.mark.asyncio
async def test_required_documents_followup_does_not_trigger_inventory(
    inventory_context: GraphRuntimeContext,
) -> None:
    inventory_context.question_text = "and what documents are required with it?"
    state: GraphState = {"request_id": "r-followup"}

    class MockRuntime:
        context = inventory_context

    result = await node_route(state, MockRuntime())  # type: ignore

    assert result.get("status") != "completed"
    assert "This assistant has information" not in inventory_context.response_text
    assert inventory_context.ports.model_provider.call_count == 1  # type: ignore


@pytest.mark.asyncio
async def test_inventory_query_empty_assistant_returns_exact_refusal(
    inventory_context: GraphRuntimeContext,
) -> None:
    """When assistant has 0 documents, inventory query returns exact refusal."""
    inventory_context.active_document_count = 0
    inventory_context.ports.retriever = StubInventoryRetriever(titles=[], headings=[])
    state: GraphState = {"request_id": "r1"}

    class MockRuntime:
        context = inventory_context

    res = await node_route(state, MockRuntime())  # type: ignore
    assert res["status"] == "refusal"
    assert (
        inventory_context.response_text
        == "This assistant has no documents yet. Add files in Data sources to get started."
    )


@pytest.mark.asyncio
async def test_validation_rejects_echoed_context_prefix(
    inventory_context: GraphRuntimeContext,
) -> None:
    """Validation must reject an answer that only repeats a context prefix."""
    inventory_context.evidence_text = {
        "chunk-1": (
            "clean_fax.pdf - Page 1 - Section 1\n\nPatient presented for follow-up evaluation."
        )
    }
    inventory_context.generated_answer = {
        "mode": "short",
        "answer": "clean_fax.pdf - Page 1 - Section 1",  # Echoed context prefix
        "evidence_ids": ["chunk-1"],
        "quoted_span": "clean_fax.pdf - Page 1 - Section 1",
    }
    state: GraphState = {
        "request_id": "r1",
        "evidence_ids": ["chunk-1"],
        "validation_attempts": 0,
    }

    class MockRuntime:
        context = inventory_context

    val_res = await node_validate(state, MockRuntime())  # type: ignore
    # Must reject and request retry
    assert val_res["status"] == "retry"
    assert val_res["validation_attempts"] == 1


@pytest.mark.asyncio
async def test_validation_rejects_empty_answer(
    inventory_context: GraphRuntimeContext,
) -> None:
    """Validation must reject an empty or whitespace answer."""
    inventory_context.evidence_text = {"chunk-1": "Some valid content."}
    inventory_context.generated_answer = {
        "mode": "short",
        "answer": "   ",
        "evidence_ids": ["chunk-1"],
        "quoted_span": None,
    }
    state: GraphState = {
        "request_id": "r1",
        "evidence_ids": ["chunk-1"],
        "validation_attempts": 0,
    }

    class MockRuntime:
        context = inventory_context

    val_res = await node_validate(state, MockRuntime())  # type: ignore
    assert val_res["status"] == "retry"


@pytest.mark.asyncio
async def test_exact_refusal_strings_part_e(
    inventory_context: GraphRuntimeContext,
) -> None:
    """Verify all 3 exact refusal messages required by Part E."""

    class MockRuntime:
        context = inventory_context

    # 1. No documents
    inventory_context.active_document_count = 0
    state_no_docs: GraphState = {"request_id": "r1", "evidence_ids": []}
    res_no_docs = await node_assess_evidence(state_no_docs, MockRuntime())  # type: ignore
    assert res_no_docs["status"] == "refusal"
    assert (
        inventory_context.response_text
        == "This assistant has no documents yet. Add files in Data sources to get started."
    )

    # 2. No or weak evidence with up to 3 titles
    inventory_context.active_document_count = 2
    inventory_context.candidate_scores = {"chunk-1": WEAK_EVIDENCE_THRESHOLD - 1.0}
    state_weak: GraphState = {"request_id": "r2", "evidence_ids": ["chunk-1"]}
    res_weak = await node_assess_evidence(state_weak, MockRuntime())  # type: ignore
    assert res_weak["status"] == "refusal"
    assert "I couldn't find that in this assistant's documents." in inventory_context.response_text
    assert "I do have information on:" in inventory_context.response_text
    assert "clean_fax.pdf" in inventory_context.response_text

    # 3. Validation failed twice
    inventory_context.evidence_text = {"chunk-1": "Valid passage."}
    inventory_context.generated_answer = {
        "mode": "short",
        "answer": "clean_fax.pdf - Page 1 - Section 1",  # Invalid prefix
        "evidence_ids": ["chunk-1"],
        "quoted_span": None,
    }
    state_val_twice: GraphState = {
        "request_id": "r3",
        "evidence_ids": ["chunk-1"],
        "validation_attempts": 1,  # Second attempt failed
    }
    res_val = await node_validate(state_val_twice, MockRuntime())  # type: ignore
    assert res_val["status"] == "refusal"
    assert inventory_context.response_text == "I couldn't confirm that from the documents."
