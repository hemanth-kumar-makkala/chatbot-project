"""Mock reranker adapter for testing and $0 local execution without model dependencies."""

from __future__ import annotations

from app.ports.reranker import Reranker
from app.ports.types import RerankCandidate, RerankResult


class MockReranker(Reranker):
    """Deterministic in-memory reranker for tests and mock profile."""

    def __init__(self, score_map: dict[str, float] | None = None) -> None:
        self.score_map = score_map

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
    ) -> list[RerankResult]:
        """Rerank candidates based on score_map or keyword overlap."""
        if not candidates:
            return []

        scored: list[tuple[RerankCandidate, float]] = []
        if self.score_map is not None:
            for c in candidates:
                scored.append((c, self.score_map.get(c.candidate_id, 0.0)))
        else:
            q_terms = [t for t in query.lower().split() if t]
            total = len(candidates)
            for idx, c in enumerate(candidates):
                c_text = c.text.lower()
                matches = sum(1 for term in q_terms if term in c_text)
                # Small tie-breaker to preserve initial order on equal matches
                score = float(matches) + 0.001 * (total - idx)
                scored.append((c, score))

        # Sort descending by score
        scored.sort(key=lambda item: item[1], reverse=True)

        return [
            RerankResult(
                candidate_id=cand.candidate_id,
                score=score,
                rank=rank,
            )
            for rank, (cand, score) in enumerate(scored, start=1)
        ]
