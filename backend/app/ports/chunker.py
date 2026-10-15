"""Port interface for Chunker (Step 7 and Task 3.1).

Defines structure-aware text chunking contracts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

EvidenceTypeLiteral = Literal["text", "visual", "table"]


@dataclass(frozen=True)
class ChunkingContext:
    """Typed context carrying page and version metadata for deterministic chunking."""

    tenant_id: str
    document_version_id: UUID
    processing_version: str
    page_number: int
    evidence_type: EvidenceTypeLiteral
    document_title: str


@dataclass(frozen=True)
class TextChunk:
    """A structure-aware chunk of text extracted from a page."""

    chunk_id: str
    text: str
    context_prefix: str
    page_number: int
    language: str
    ts_config: str
    evidence_type: EvidenceTypeLiteral


class Chunker(ABC):
    """Abstract port for document chunking."""

    @abstractmethod
    def chunk_page(
        self,
        readings: list[str],
        context: ChunkingContext,
        language: str = "english",
    ) -> list[TextChunk]:
        """Split page readings into structure-aware chunks with document context prefix."""
        ...
