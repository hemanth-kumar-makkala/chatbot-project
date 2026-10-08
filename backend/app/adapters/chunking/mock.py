"""Mock chunker adapter for synthetic testing (Task 2.7)."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from app.ports.chunker import Chunker, TextChunk


class MockChunker(Chunker):
    """Deterministic mock chunker returning structure-aware chunks."""

    def chunk_page(
        self,
        document_title: str,
        page_number: int,
        readings: list[str],
        language: str = "english",
    ) -> list[TextChunk]:
        """Split page readings into structure-aware chunks."""
        chunks: list[TextChunk] = []
        ts_config = "spanish" if language.lower().startswith("es") else "english"
        for idx, text in enumerate(readings):
            if not text.strip():
                continue
            chunk_id = uuid5(NAMESPACE_URL, f"{document_title}:{page_number}:{idx}:{text.strip()}")
            chunks.append(
                TextChunk(
                    chunk_id=chunk_id,
                    text=text.strip(),
                    context_prefix=f"{document_title} - Page {page_number} - Section {idx + 1}",
                    page_number=page_number,
                    language=language,
                    ts_config=ts_config,
                )
            )
        return chunks
