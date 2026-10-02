"""Port interface for EmbeddingProvider.

Provides dense vector embeddings for knowledge chunks and search queries.
Embedding model is fixed per index version to avoid vector dimension drift.
"""

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Abstract port for vector embedding models."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Compute vector embeddings for a list of text strings."""
        ...

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Identifier of the active embedding model."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Version of the active embedding model."""
        ...
