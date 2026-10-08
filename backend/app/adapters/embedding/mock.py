"""Mock EmbeddingProvider adapter for synthetic testing (Task 2.7)."""

from __future__ import annotations

import hashlib

from app.ports.embedding_provider import EmbeddingProvider


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic mock embedding provider producing 1024-dimension float vectors."""

    def __init__(self, model_id: str = "mock-bge-m3", version: str = "v1") -> None:
        self._model_id = model_id
        self._version = version

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate deterministic 1024-dimension normalized float vector for each text."""
        results: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vector: list[float] = []
            for i in range(1024):
                byte_val = digest[i % len(digest)]
                vector.append(float(byte_val) / 255.0)
            results.append(vector)
        return results

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def version(self) -> str:
        return self._version
