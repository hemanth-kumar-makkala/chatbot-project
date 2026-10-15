"""Mock chunker adapter for synthetic testing (Task 2.7)."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from app.ports.chunker import Chunker, ChunkingContext, TextChunk


class MockChunker(Chunker):
    """Deterministic mock chunker returning structure-aware chunks."""

    def chunk_page(
        self,
        readings: list[str],
        context: ChunkingContext,
        language: str = "english",
    ) -> list[TextChunk]:
        """Split page readings into structure-aware chunks."""
        chunks: list[TextChunk] = []
        ts_config = "spanish" if language.lower().startswith("es") else "english"
        for idx, text in enumerate(readings):
            if not text.strip():
                continue
            unique_key = (
                f"{context.tenant_id}:{context.document_version_id}:"
                f"{context.processing_version}:{context.page_number}:"
                f"{context.evidence_type}:{idx}"
            )
            chunk_id = str(uuid5(NAMESPACE_URL, unique_key))
            prefix = f"{context.document_title} - Page {context.page_number} - Section {idx + 1}"
            chunks.append(
                TextChunk(
                    chunk_id=chunk_id,
                    text=text.strip(),
                    context_prefix=prefix,
                    page_number=context.page_number,
                    language=language,
                    ts_config=ts_config,
                    evidence_type=context.evidence_type,
                )
            )
        return chunks
