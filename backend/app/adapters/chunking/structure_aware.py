"""Structure-aware text chunker adapter for non-mock profiles.

Enforces:
1. Page-first: chunks never cross page boundaries. Empty page returns 0 chunks.
2. Structure-aware: splits on headings and paragraph boundaries first;
   splits long paragraphs on sentences (never mid-word); splits tables row-by-row (never mid-row).
3. Exact text: stored chunk text is an exact substring slice of the input page text.
   Context prefix is kept separate.
4. Visual pages: preserves visual evidence marking.
5. Deterministic: identical input gives identical chunks and UUID5 IDs every run.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.ports.chunker import Chunker, ChunkingContext, TextChunk

# -----------------------------------------------------------------------------
# Named Constants & Rationale
# -----------------------------------------------------------------------------
# TARGET_CHUNK_SIZE_WORDS = 350
# Rationale: ~450 tokens. Ideal context span for BGE-M3 (max context 8192, optimal
# retrieval density around 256-512 tokens). Keeps clinical and policy sections cohesive.
TARGET_CHUNK_SIZE_WORDS: int = 350

# TARGET_CHUNK_OVERLAP_WORDS = 50
# Rationale: ~65 tokens (~2-3 sentences). Preserves semantic boundary continuity
# across adjacent sentence splits of a long paragraph without bloating search index.
TARGET_CHUNK_OVERLAP_WORDS: int = 50

# MIN_CHUNK_SIZE_WORDS = 20
# Rationale: Filters out standalone noise, stray headers, or tiny trailing sentence fragments.
MIN_CHUNK_SIZE_WORDS: int = 20

_HEADING_PATTERN = re.compile(
    r"^(?:#{1,6}\s+.+|[A-Z0-9\.\s\-]{3,60}:|[0-9]+\.[0-9]*\s+[A-Z].+)$",
    re.MULTILINE,
)
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\(\[\"\'])")


@dataclass(frozen=True)
class _BlockSpan:
    start: int
    end: int
    is_heading: bool
    heading_text: str
    is_table: bool


@dataclass(frozen=True)
class _TableStructure:
    header_text: str
    data_rows: list[_BlockSpan]


class StructureAwareChunker(Chunker):
    """Production structure-aware chunker for real document ingestion."""

    def chunk_page(
        self,
        readings: list[str],
        context: ChunkingContext,
        language: str = "english",
    ) -> list[TextChunk]:
        """Split page readings into structure-aware chunks.

        Chunks never cross page boundaries. Empty or whitespace pages return 0 chunks.
        """
        chunks: list[TextChunk] = []
        ts_config = "spanish" if language.lower().startswith("es") else "english"

        for reading in readings:
            if not reading.strip():
                continue

            page_chunks = self._chunk_single_reading(
                reading=reading,
                context=context,
                ts_config=ts_config,
                language=language,
            )
            chunks.extend(page_chunks)

        return chunks

    def _chunk_single_reading(
        self,
        reading: str,
        context: ChunkingContext,
        ts_config: str,
        language: str,
    ) -> list[TextChunk]:
        blocks = self._parse_blocks(reading)
        if not blocks:
            return []

        text_chunks: list[TextChunk] = []
        current_spans: list[_BlockSpan] = []
        current_words = 0
        active_heading = ""

        for block in blocks:
            if block.is_heading:
                active_heading = block.heading_text

            if block.is_table:
                # Flush any accumulated non-table spans before table
                if current_spans:
                    self._emit_chunk(
                        reading=reading,
                        spans=current_spans,
                        heading=active_heading,
                        table_header="",
                        context=context,
                        ts_config=ts_config,
                        language=language,
                        chunk_idx=len(text_chunks),
                        out=text_chunks,
                    )
                    current_spans = []
                    current_words = 0

                table_chunks = self._chunk_table_block(
                    reading=reading,
                    block=block,
                    heading=active_heading,
                    context=context,
                    ts_config=ts_config,
                    language=language,
                    chunk_offset=len(text_chunks),
                )
                text_chunks.extend(table_chunks)
                continue

            block_text = reading[block.start : block.end]
            block_words = len(block_text.split())

            # Long paragraph (> TARGET_CHUNK_SIZE_WORDS) splits on sentence boundaries with overlap
            if block_words > TARGET_CHUNK_SIZE_WORDS:
                if current_spans:
                    self._emit_chunk(
                        reading=reading,
                        spans=current_spans,
                        heading=active_heading,
                        table_header="",
                        context=context,
                        ts_config=ts_config,
                        language=language,
                        chunk_idx=len(text_chunks),
                        out=text_chunks,
                    )
                    current_spans = []
                    current_words = 0

                paragraph_chunks = self._chunk_long_paragraph(
                    reading=reading,
                    block=block,
                    heading=active_heading,
                    context=context,
                    ts_config=ts_config,
                    language=language,
                    chunk_offset=len(text_chunks),
                )
                text_chunks.extend(paragraph_chunks)
                continue

            if current_words + block_words > TARGET_CHUNK_SIZE_WORDS and current_spans:
                self._emit_chunk(
                    reading=reading,
                    spans=current_spans,
                    heading=active_heading,
                    table_header="",
                    context=context,
                    ts_config=ts_config,
                    language=language,
                    chunk_idx=len(text_chunks),
                    out=text_chunks,
                )
                current_spans = []
                current_words = 0

            current_spans.append(block)
            current_words += block_words

        if current_spans:
            self._emit_chunk(
                reading=reading,
                spans=current_spans,
                heading=active_heading,
                table_header="",
                context=context,
                ts_config=ts_config,
                language=language,
                chunk_idx=len(text_chunks),
                out=text_chunks,
            )

        return text_chunks

    def _chunk_long_paragraph(
        self,
        reading: str,
        block: _BlockSpan,
        heading: str,
        context: ChunkingContext,
        ts_config: str,
        language: str,
        chunk_offset: int,
    ) -> list[TextChunk]:
        """Split a long paragraph on sentences with overlap and minimum size enforcement."""
        sentence_spans = self._split_sentence_spans(reading, block)
        if not sentence_spans:
            return []

        chunks: list[TextChunk] = []
        i = 0
        n = len(sentence_spans)

        while i < n:
            accum_spans: list[_BlockSpan] = []
            accum_words = 0
            curr_idx = i

            while curr_idx < n:
                span = sentence_spans[curr_idx]
                span_words = len(reading[span.start : span.end].split())
                if accum_words + span_words > TARGET_CHUNK_SIZE_WORDS and accum_spans:
                    break
                accum_spans.append(span)
                accum_words += span_words
                curr_idx += 1

            # Check if trailing unallocated sentences are below MIN_CHUNK_SIZE_WORDS
            remaining_words = sum(
                len(reading[s.start : s.end].split()) for s in sentence_spans[curr_idx:]
            )
            if 0 < remaining_words < MIN_CHUNK_SIZE_WORDS:
                # Merge trailing sentences into current chunk rather than leaving orphan
                while curr_idx < n:
                    accum_spans.append(sentence_spans[curr_idx])
                    curr_idx += 1

            self._emit_chunk(
                reading=reading,
                spans=accum_spans,
                heading=heading,
                table_header="",
                context=context,
                ts_config=ts_config,
                language=language,
                chunk_idx=chunk_offset + len(chunks),
                out=chunks,
            )

            if curr_idx >= n:
                break

            # Calculate overlap from the end of accum_spans for the next chunk
            overlap_spans: list[_BlockSpan] = []
            overlap_words = 0
            for s in reversed(accum_spans):
                w = len(reading[s.start : s.end].split())
                if overlap_words + w > TARGET_CHUNK_OVERLAP_WORDS and overlap_spans:
                    break
                overlap_spans.insert(0, s)
                overlap_words += w

            # Next chunk begins at first non-overlapping sentence index or next sentence
            first_overlap_span = overlap_spans[0]
            next_start_idx = sentence_spans.index(first_overlap_span)
            # Advance at least one sentence to guarantee progress
            i = i + 1 if next_start_idx == i else next_start_idx

        return chunks

    def _chunk_table_block(
        self,
        reading: str,
        block: _BlockSpan,
        heading: str,
        context: ChunkingContext,
        ts_config: str,
        language: str,
        chunk_offset: int,
    ) -> list[TextChunk]:
        """Split a table into groups of whole rows.

        The header row goes into the context prefix, not repeated in the text.
        Every chunk is whole rows, and every chunk text is an exact substring.
        """
        table_struct = self._parse_table_structure(reading, block)
        if not table_struct.data_rows:
            # If no distinct data rows exist, emit block as is
            chunks: list[TextChunk] = []
            self._emit_chunk(
                reading=reading,
                spans=[block],
                heading=heading,
                table_header=table_struct.header_text,
                context=context,
                ts_config=ts_config,
                language=language,
                chunk_idx=chunk_offset,
                out=chunks,
            )
            return chunks

        chunks = []
        current_rows: list[_BlockSpan] = []
        current_words = 0

        for row_span in table_struct.data_rows:
            row_words = len(reading[row_span.start : row_span.end].split())
            if current_words + row_words > TARGET_CHUNK_SIZE_WORDS and current_rows:
                self._emit_chunk(
                    reading=reading,
                    spans=current_rows,
                    heading=heading,
                    table_header=table_struct.header_text,
                    context=context,
                    ts_config=ts_config,
                    language=language,
                    chunk_idx=chunk_offset + len(chunks),
                    out=chunks,
                )
                current_rows = []
                current_words = 0

            current_rows.append(row_span)
            current_words += row_words

        if current_rows:
            self._emit_chunk(
                reading=reading,
                spans=current_rows,
                heading=heading,
                table_header=table_struct.header_text,
                context=context,
                ts_config=ts_config,
                language=language,
                chunk_idx=chunk_offset + len(chunks),
                out=chunks,
            )

        return chunks

    def _parse_table_structure(self, reading: str, block: _BlockSpan) -> _TableStructure:
        lines = reading[block.start : block.end].splitlines(keepends=True)
        offset = block.start
        header_text = ""
        data_rows: list[_BlockSpan] = []

        for line_idx, line in enumerate(lines):
            line_len = len(line)
            line_start = offset
            line_end = offset + line_len
            offset += line_len

            stripped = line.strip()
            if not stripped:
                continue

            if line_idx == 0:
                header_text = stripped
                continue

            # Check if line 1 is a markdown table separator (e.g. |---|---|)
            if line_idx == 1:
                cleaned = stripped.replace("|", "").replace("-", "").replace(":", "").strip()
                if not cleaned:
                    # Separator line is excluded from data rows
                    continue

            data_rows.append(
                _BlockSpan(
                    start=line_start,
                    end=line_end,
                    is_heading=False,
                    heading_text="",
                    is_table=True,
                )
            )

        return _TableStructure(header_text=header_text, data_rows=data_rows)

    def _split_sentence_spans(self, text: str, block: _BlockSpan) -> list[_BlockSpan]:
        block_text = text[block.start : block.end]
        spans: list[_BlockSpan] = []
        last_idx = 0

        for match in _SENTENCE_SPLIT_PATTERN.finditer(block_text):
            end_idx = match.start() + 1
            s_start = block.start + last_idx
            s_end = block.start + end_idx
            if text[s_start:s_end].strip():
                spans.append(
                    _BlockSpan(
                        start=s_start,
                        end=s_end,
                        is_heading=False,
                        heading_text=block.heading_text,
                        is_table=False,
                    )
                )
            last_idx = match.end()

        if last_idx < len(block_text):
            s_start = block.start + last_idx
            s_end = block.end
            if text[s_start:s_end].strip():
                spans.append(
                    _BlockSpan(
                        start=s_start,
                        end=s_end,
                        is_heading=False,
                        heading_text=block.heading_text,
                        is_table=False,
                    )
                )

        return spans if spans else [block]

    def _parse_blocks(self, text: str) -> list[_BlockSpan]:
        blocks: list[_BlockSpan] = []
        lines = text.splitlines(keepends=True)
        offset = 0
        in_table = False
        table_start = 0
        current_heading = ""

        for line in lines:
            line_len = len(line)
            stripped = line.strip()
            is_table_row = stripped.startswith("|") and stripped.endswith("|")

            if is_table_row:
                if not in_table:
                    in_table = True
                    table_start = offset
                offset += line_len
                continue
            elif in_table:
                blocks.append(
                    _BlockSpan(
                        start=table_start,
                        end=offset,
                        is_heading=False,
                        heading_text=current_heading,
                        is_table=True,
                    )
                )
                in_table = False

            if not stripped:
                offset += line_len
                continue

            if _HEADING_PATTERN.match(stripped):
                current_heading = stripped.lstrip("#").strip()
                blocks.append(
                    _BlockSpan(
                        start=offset,
                        end=offset + line_len,
                        is_heading=True,
                        heading_text=current_heading,
                        is_table=False,
                    )
                )
            else:
                blocks.append(
                    _BlockSpan(
                        start=offset,
                        end=offset + line_len,
                        is_heading=False,
                        heading_text=current_heading,
                        is_table=False,
                    )
                )

            offset += line_len

        if in_table:
            blocks.append(
                _BlockSpan(
                    start=table_start,
                    end=offset,
                    is_heading=False,
                    heading_text=current_heading,
                    is_table=True,
                )
            )

        return blocks

    def _emit_chunk(
        self,
        reading: str,
        spans: list[_BlockSpan],
        heading: str,
        table_header: str,
        context: ChunkingContext,
        ts_config: str,
        language: str,
        chunk_idx: int,
        out: list[TextChunk],
    ) -> None:
        start = spans[0].start
        end = spans[-1].end
        chunk_text = reading[start:end]
        if not chunk_text.strip():
            return

        # Context prefix: document title, page number, nearest heading, and table header
        prefix_parts = [context.document_title, f"Page {context.page_number}"]
        if heading:
            prefix_parts.append(heading)
        if table_header:
            prefix_parts.append(f"Table: {table_header}")
        context_prefix = " - ".join(prefix_parts)

        # Deterministic UUID5 ID: tenant_id, document_version_id, processing_version,
        # page_number, evidence_type, and chunk_index ONLY. Not from title or text.
        unique_key = (
            f"{context.tenant_id}:{context.document_version_id}:"
            f"{context.processing_version}:{context.page_number}:"
            f"{context.evidence_type}:{chunk_idx}"
        )
        chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, unique_key))

        out.append(
            TextChunk(
                chunk_id=chunk_id,
                text=chunk_text,
                context_prefix=context_prefix,
                page_number=context.page_number,
                language=language,
                ts_config=ts_config,
                evidence_type=context.evidence_type,
            )
        )
