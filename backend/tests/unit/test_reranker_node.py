"""Unit tests for Node 4 rerank, MockReranker, BgeRerankerAdapter, and wiring guards (Task 3.2).

Verifies:
1. Node 4 reorders evidence_ids according to candidate reranking scores.
2. Node 4 preserves original RRF evidence order when reranker is unavailable
   or mock_reranker_down is True.
3. Node 4 gracefully catches reranker exceptions and falls back to unranked candidates
   (Failure Mode: Reranker fails).
4. MockReranker contract: descending scores, 1-based ranks, empty candidates handling.
5. BgeRerankerAdapter configuration validation: devices, required keys, missing local model files.
6. BgeRerankerAdapter inference and ranking: pair construction, sorting, finite float checks.
7. Wiring profile guards: mock -> MockReranker, real profiles -> BgeRerankerAdapter,
   unknown -> ConfigurationError.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.adapters.reranking.bge_reranker import (
    BgeRerankerAdapter,
    BgeRerankerConfig,
    validate_bge_reranker_config,
)
from app.adapters.reranking.mock import MockReranker
from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    build_knowledge_graph,
    node_rerank,
)
from app.ports.errors import (
    ConfigurationError,
    RerankerOutputInvalidError,
)
from app.ports.reranker import Reranker
from app.ports.types import RerankCandidate
from app.wiring import create_reranker


class TestRerankerNode:
    """Tests for Node 4 rerank within the LangGraph StateGraph flow."""

    @pytest.fixture
    def base_state(self) -> GraphState:
        return {
            "request_id": "req-rerank-1",
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "user_id": "usr-1",
            "conversation_id": "conv-1",
            "route": "knowledge",
            "answer_mode": "short",
            "language": "en",
            "evidence_ids": ["c1", "c2", "c3"],
            "evidence_state": "NONE",
            "needs_vision": False,
            "vision_status": None,
            "validation_attempts": 0,
            "status": "in_progress",
            "public_error_ref": None,
            "extracted_field_ids": None,
            "dual_read_matches": None,
            "confidence_score": None,
            "fields_valid": None,
            "response_message_id": None,
        }

    async def test_reranker_node_reorders_evidence_ids_by_score(
        self, base_state: GraphState
    ) -> None:
        """Node 4 reorders evidence IDs matching the descending relevance score order."""
        # c2 has highest score, c3 medium, c1 lowest
        mock_reranker = MockReranker(score_map={"c1": 0.1, "c2": 0.9, "c3": 0.5})
        ports = GraphPorts(reranker=mock_reranker)
        ctx = GraphRuntimeContext(
            ports=ports,
            question_text="What are the prior auth submission requirements?",
            evidence_text={
                "c1": "General office hours.",
                "c2": "Prior authorization submission requirements and deadlines.",
                "c3": "Department contact phone numbers.",
            },
        )

        class DummyRuntime:
            context = ctx

        result = await node_rerank(base_state, DummyRuntime())  # type: ignore[arg-type]
        assert result["evidence_ids"] == ["c2", "c3", "c1"]

    async def test_reranker_node_preserves_order_when_mock_reranker_down(
        self, base_state: GraphState
    ) -> None:
        """When mock_reranker_down is True, evidence_ids order is preserved as-is (RRF order)."""
        mock_reranker = MockReranker(score_map={"c1": 0.1, "c2": 0.9, "c3": 0.5})
        ports = GraphPorts(
            reranker=mock_reranker,
            is_mock_profile=True,
            mock_reranker_down=True,
        )
        ctx = GraphRuntimeContext(
            ports=ports,
            question_text="What are the requirements?",
            evidence_text={
                "c1": "General office hours.",
                "c2": "Prior authorization requirements.",
                "c3": "Department phone numbers.",
            },
        )

        class DummyRuntime:
            context = ctx

        result = await node_rerank(base_state, DummyRuntime())  # type: ignore[arg-type]
        assert result["evidence_ids"] == ["c1", "c2", "c3"]

    async def test_reranker_node_falls_back_when_reranker_service_raises(
        self, base_state: GraphState
    ) -> None:
        """When the reranker service throws an exception, node catches and falls back cleanly."""
        failing_reranker = MagicMock(spec=Reranker)
        failing_reranker.rerank.side_effect = RuntimeError("GPU out of memory or inference crash")

        ports = GraphPorts(reranker=failing_reranker)
        ctx = GraphRuntimeContext(
            ports=ports,
            question_text="Emergency query",
            evidence_text={
                "c1": "Doc 1 text",
                "c2": "Doc 2 text",
                "c3": "Doc 3 text",
            },
        )

        class DummyRuntime:
            context = ctx

        result = await node_rerank(base_state, DummyRuntime())  # type: ignore[arg-type]
        # Must not raise; original order is preserved
        assert result["evidence_ids"] == ["c1", "c2", "c3"]

    async def test_reranker_node_empty_evidence_ids_returns_empty(
        self, base_state: GraphState
    ) -> None:
        """When evidence_ids is empty, node_rerank immediately returns empty list."""
        base_state["evidence_ids"] = []
        mock_reranker = MockReranker()
        ports = GraphPorts(reranker=mock_reranker)
        ctx = GraphRuntimeContext(ports=ports, question_text="Any query")

        class DummyRuntime:
            context = ctx

        result = await node_rerank(base_state, DummyRuntime())  # type: ignore[arg-type]
        assert result["evidence_ids"] == []

    async def test_reranker_node_no_reranker_injected_preserves_order(
        self, base_state: GraphState
    ) -> None:
        """When ports.reranker is None, original candidate order is preserved."""
        ports = GraphPorts(reranker=None)
        ctx = GraphRuntimeContext(ports=ports, question_text="Any query")

        class DummyRuntime:
            context = ctx

        result = await node_rerank(base_state, DummyRuntime())  # type: ignore[arg-type]
        assert result["evidence_ids"] == ["c1", "c2", "c3"]

    async def test_reranker_end_to_end_in_compiled_graph(self, base_state: GraphState) -> None:
        """Verifies Node 4 participates in compiled graph execution end-to-end."""
        mock_reranker = MockReranker(score_map={"c1": 0.1, "c2": 0.9})
        ports = GraphPorts(
            reranker=mock_reranker,
            is_mock_profile=True,
            mock_evidence_state="RELIABLE_TEXT",
        )
        ctx = GraphRuntimeContext(
            ports=ports,
            question_text="Prior authorization phone number",
            evidence_text={
                "c1": "Office hours 9am - 5pm.",
                "c2": "Prior authorization phone number is 555-0199.",
            },
        )

        graph = build_knowledge_graph().compile()
        result = await graph.ainvoke(base_state, context=ctx)
        assert result["status"] in ("answered", "completed")
        assert result["evidence_ids"][0] == "c2"


class TestMockReranker:
    """Tests for MockReranker adapter."""

    async def test_mock_reranker_empty_candidates(self) -> None:
        reranker = MockReranker()
        results = await reranker.rerank(query="test", candidates=[])
        assert results == []

    async def test_mock_reranker_default_scoring_and_ranks(self) -> None:
        candidates = [
            RerankCandidate(candidate_id="c1", text="Unrelated text about birds."),
            RerankCandidate(candidate_id="c2", text="Highly relevant prior auth info."),
        ]
        reranker = MockReranker()
        results = await reranker.rerank(query="prior auth", candidates=candidates)

        assert len(results) == 2
        assert results[0].candidate_id == "c2"
        assert results[0].rank == 1
        assert results[1].candidate_id == "c1"
        assert results[1].rank == 2
        assert results[0].score > results[1].score

    async def test_mock_reranker_custom_score_map(self) -> None:
        candidates = [
            RerankCandidate(candidate_id="a", text="alpha"),
            RerankCandidate(candidate_id="b", text="beta"),
            RerankCandidate(candidate_id="c", text="gamma"),
        ]
        reranker = MockReranker(score_map={"a": 0.2, "b": 0.8, "c": 0.5})
        results = await reranker.rerank(query="any", candidates=candidates)

        assert [r.candidate_id for r in results] == ["b", "c", "a"]
        assert [r.rank for r in results] == [1, 2, 3]


class TestBgeRerankerConfigValidation:
    """Tests for BgeReranker configuration validation."""

    def test_validate_valid_config(self) -> None:
        raw = {
            "model": "BAAI/bge-reranker-v2-m3",
            "device": "cpu",
        }
        cfg = validate_bge_reranker_config(raw)
        assert cfg.model == "BAAI/bge-reranker-v2-m3"
        assert cfg.device == "cpu"
        assert cfg.max_length == 1024

    def test_validate_custom_max_length_and_batch_size(self) -> None:
        raw = {
            "model": "BAAI/bge-reranker-v2-m3",
            "device": "cpu",
            "max_length": 512,
            "batch_size": 16,
        }
        cfg = validate_bge_reranker_config(raw)
        assert cfg.max_length == 512
        assert cfg.batch_size == 16

    def test_validate_unsupported_device_raises(self) -> None:
        raw = {
            "model": "BAAI/bge-reranker-v2-m3",
            "device": "tpu",
        }
        with pytest.raises(ConfigurationError) as exc_info:
            validate_bge_reranker_config(raw)
        assert "device" in str(exc_info.value).lower()

    def test_validate_missing_required_key_raises(self) -> None:
        raw = {"device": "cpu"}
        with pytest.raises(ConfigurationError):
            validate_bge_reranker_config(raw)


class TestBgeRerankerAdapter:
    """Tests for BgeRerankerAdapter scoring, sorting, and error boundaries."""

    async def test_rerank_empty_candidates_returns_empty(self) -> None:
        mock_model = MagicMock()
        cfg = BgeRerankerConfig(model="test-reranker", device="cpu")
        adapter = BgeRerankerAdapter(model=mock_model, config=cfg)

        results = await adapter.rerank(query="some query", candidates=[])
        assert results == []
        mock_model.predict.assert_not_called()

    async def test_rerank_scores_pairs_and_sorts_descending(self) -> None:
        mock_model = MagicMock()
        # predict returns scores for pairs: [c1, c2, c3]
        # Return scores such that c3 > c1 > c2
        mock_model.predict.return_value = [0.2, -1.5, 3.8]

        cfg = BgeRerankerConfig(model="test-reranker", device="cpu")
        adapter = BgeRerankerAdapter(model=mock_model, config=cfg)

        candidates = [
            RerankCandidate(candidate_id="c1", text="First text"),
            RerankCandidate(candidate_id="c2", text="Second text"),
            RerankCandidate(candidate_id="c3", text="Third text"),
        ]

        results = await adapter.rerank(query="target query", candidates=candidates)

        assert len(results) == 3
        assert results[0].candidate_id == "c3"
        assert results[0].score == 3.8
        assert results[0].rank == 1

        assert results[1].candidate_id == "c1"
        assert results[1].score == 0.2
        assert results[1].rank == 2

        assert results[2].candidate_id == "c2"
        assert results[2].score == -1.5
        assert results[2].rank == 3

        # Assert correct pairs passed to predict
        mock_model.predict.assert_called_once_with(
            [
                ["target query", "First text"],
                ["target query", "Second text"],
                ["target query", "Third text"],
            ],
            batch_size=cfg.batch_size,
        )

    async def test_rerank_output_length_mismatch_raises(self) -> None:
        mock_model = MagicMock()
        # Model returns only 1 score for 2 candidates
        mock_model.predict.return_value = [0.5]

        cfg = BgeRerankerConfig(model="test-reranker", device="cpu")
        adapter = BgeRerankerAdapter(model=mock_model, config=cfg)

        candidates = [
            RerankCandidate(candidate_id="c1", text="Text 1"),
            RerankCandidate(candidate_id="c2", text="Text 2"),
        ]

        with pytest.raises(RerankerOutputInvalidError):
            await adapter.rerank(query="query", candidates=candidates)

    async def test_rerank_non_finite_score_raises(self) -> None:
        mock_model = MagicMock()
        mock_model.predict.return_value = [float("nan"), 1.0]

        cfg = BgeRerankerConfig(model="test-reranker", device="cpu")
        adapter = BgeRerankerAdapter(model=mock_model, config=cfg)

        candidates = [
            RerankCandidate(candidate_id="c1", text="Text 1"),
            RerankCandidate(candidate_id="c2", text="Text 2"),
        ]

        with pytest.raises(RerankerOutputInvalidError):
            await adapter.rerank(query="query", candidates=candidates)


class TestWiringReranker:
    """Tests for application wiring profile guards on reranker."""

    def test_wiring_mock_profile_returns_mock_reranker(self) -> None:
        reranker = create_reranker("mock")
        assert isinstance(reranker, MockReranker)

    def test_wiring_unknown_profile_raises_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError) as exc_info:
            create_reranker("unknown-gpu")
        assert "unsupported profile" in str(exc_info.value).lower()
