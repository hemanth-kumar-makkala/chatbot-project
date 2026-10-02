"""Port interface for Reranker.

Cross-encoder model that scores and reranks candidates retrieved by hybrid search.
"""

from abc import ABC, abstractmethod

from app.ports.types import RerankCandidate, RerankResult


class Reranker(ABC):
    """Abstract port for candidate reranking."""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
    ) -> list[RerankResult]:
        """Rerank candidates against the query in descending order of relevance."""
        ...
