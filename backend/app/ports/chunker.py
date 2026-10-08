"""Port interface for Chunker (Step 7 and Task 3.1).

Defines structure-aware text chunking contracts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class TextChunk:
    """A structure-aware chunk of text extracted from a page."""

    chunk_id: UUID
    text: str
    context_prefix: str
    page_number: int
    language: str
    ts_config: str


class Chunker(ABC):
    """Abstract port for document chunking."""

    @abstractmethod
    def chunk_page(
        self,
        document_title: str,
        page_number: int,
        readings: list[str],
        language: str = "english",
    ) -> list[TextChunk]:
        """Split page readings into structure-aware chunks with document context prefix."""
        ...
