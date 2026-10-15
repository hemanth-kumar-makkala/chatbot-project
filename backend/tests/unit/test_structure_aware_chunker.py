"""Unit tests for the structure-aware chunker adapter (TDD - no database).

Contracts under test:
1. Deterministic chunk IDs: derived strictly from tenant_id, document_version_id,
   processing_version, page_number, evidence_type, and chunk_index (not from title or text).
   - Same text in two different versions or tenants gives different ids.
   - Same version re-run gives identical ids.
2. Required typed evidence_type Literal with no default.
3. Sentence overlap and minimum size:
   - Overlap only between sentence-split pieces of one long paragraph.
   - Overlap text is still an exact substring.
   - Trailing sentences under MIN_CHUNK_SIZE_WORDS are merged.
4. Large tables:
   - Split a table larger than target size into groups of whole rows.
   - Header row goes into context prefix, not repeated in the text.
   - 200-row table produces several chunks, each whole rows, each exact substring,
     every prefix contains the header.
5. Exact text:
   - Every chunk's text is an exact substring of its page text.
6. Pipeline path:
   - A page whose selected reading is visual produces chunks marked visual.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.adapters.chunking.structure_aware import (
    MIN_CHUNK_SIZE_WORDS,
    TARGET_CHUNK_SIZE_WORDS,
    StructureAwareChunker,
)
from app.core.ingestion.pipeline import PagePipelineService
from app.ports.chunker import ChunkingContext, EvidenceTypeLiteral
from app.ports.types import (
    CURRENT_PROCESSING_VERSION,
    PageRecord,
    PageType,
)


@pytest.fixture
def chunker() -> StructureAwareChunker:
    return StructureAwareChunker()


def _make_context(
    tenant_id: str | None = None,
    document_version_id: UUID | None = None,
    processing_version: str = CURRENT_PROCESSING_VERSION,
    page_number: int = 1,
    evidence_type: EvidenceTypeLiteral = "text",
    document_title: str = "Clinical Guidelines",
) -> ChunkingContext:
    return ChunkingContext(
        tenant_id=tenant_id if tenant_id is not None else "00000000-0000-0000-0000-000000000001",
        document_version_id=document_version_id if document_version_id is not None else uuid4(),
        processing_version=processing_version,
        page_number=page_number,
        evidence_type=evidence_type,
        document_title=document_title,
    )


def test_chunk_ids_deterministic_from_context_only(chunker: StructureAwareChunker) -> None:
    """Same text in two different versions gives different ids; re-run gives identical ids."""
    sample_text = "Treatment guidelines establish standard dosing of monoclonal antibody therapies."
    ctx_v1 = _make_context(
        tenant_id="11111111-1111-1111-1111-111111111111",
        document_version_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        page_number=1,
        evidence_type="text",
        document_title="Version A",
    )
    ctx_v1_rerun = _make_context(
        tenant_id="11111111-1111-1111-1111-111111111111",
        document_version_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        page_number=1,
        evidence_type="text",
        document_title="Version A Renamed Title",  # Title change must NOT change chunk id
    )
    ctx_v2 = _make_context(
        tenant_id="11111111-1111-1111-1111-111111111111",
        document_version_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        page_number=1,
        evidence_type="text",
        document_title="Version A",
    )
    ctx_other_tenant = _make_context(
        tenant_id="22222222-2222-2222-2222-222222222222",
        document_version_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        page_number=1,
        evidence_type="text",
        document_title="Version A",
    )

    chunks_v1 = chunker.chunk_page(readings=[sample_text], context=ctx_v1)
    chunks_v1_rerun = chunker.chunk_page(readings=[sample_text], context=ctx_v1_rerun)
    chunks_v2 = chunker.chunk_page(readings=[sample_text], context=ctx_v2)
    chunks_other_tenant = chunker.chunk_page(readings=[sample_text], context=ctx_other_tenant)

    assert len(chunks_v1) == len(chunks_v1_rerun) == len(chunks_v2) == len(chunks_other_tenant) == 1
    # Idempotent re-run: identical id (not affected by title or timestamp)
    assert chunks_v1[0].chunk_id == chunks_v1_rerun[0].chunk_id
    # Different version id: different chunk id
    assert chunks_v1[0].chunk_id != chunks_v2[0].chunk_id
    # Different tenant id: different chunk id
    assert chunks_v1[0].chunk_id != chunks_other_tenant[0].chunk_id


def test_multi_paragraph_page_splits_on_paragraph_boundaries(
    chunker: StructureAwareChunker,
) -> None:
    """Multi-paragraph page splits on paragraph boundaries within the size limit."""
    p1 = " ".join(["Clinical trial protocol Section 1.1 establishes the primary cohort."] * 25)
    p2 = " ".join(["Exclusion criteria define patients with prior cardiac events."] * 25)
    p3 = " ".join(["Secondary endpoints track progression free survival over 24 months."] * 25)
    page_text = f"{p1}\n\n{p2}\n\n{p3}"

    ctx = _make_context(page_number=1, evidence_type="text")
    chunks = chunker.chunk_page(readings=[page_text], context=ctx)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk.text.split()) <= TARGET_CHUNK_SIZE_WORDS + 50
        assert chunk.text in page_text


def test_long_paragraph_splits_on_sentences_with_overlap(
    chunker: StructureAwareChunker,
) -> None:
    """Long paragraph splits on sentences with overlap; overlap text is still an exact substring."""
    sentences = [
        f"Sentence {i:02d} describes the clinical laboratory standard for metabolic evaluation."
        for i in range(1, 45)
    ]
    long_paragraph = " ".join(sentences)

    ctx = _make_context(page_number=2, evidence_type="text")
    chunks = chunker.chunk_page(readings=[long_paragraph], context=ctx)

    assert len(chunks) >= 2
    # Verify every chunk text is an exact substring of the original page text
    for chunk in chunks:
        assert chunk.text in long_paragraph
        first_word = chunk.text.split()[0]
        last_word = chunk.text.split()[-1]
        assert first_word == "Sentence"
        assert last_word.endswith(".")

    # Verify overlap exists between consecutive pieces of the long paragraph
    chunk_1_sentences = set(chunks[0].text.split(". "))
    chunk_2_sentences = set(chunks[1].text.split(". "))
    overlap = chunk_1_sentences.intersection(chunk_2_sentences)
    assert len(overlap) >= 1

    # Verify trailing sentences under MIN_CHUNK_SIZE_WORDS were merged rather than orphaned
    for chunk in chunks:
        assert len(chunk.text.split()) >= MIN_CHUNK_SIZE_WORDS


def test_large_table_split_into_whole_rows_header_in_prefix(
    chunker: StructureAwareChunker,
) -> None:
    """A 200-row table produces several chunks, every chunk is whole rows,

    every chunk text is an exact substring, and every prefix contains the header.
    """
    header_line = "| Patient ID | Lab Metric | Standard Range | Clinical Interpretation |"
    separator_line = "|---|---|---|---|"
    data_rows = [
        f"| PT-{i:04d} | Hemoglobin A1c | 4.0-5.6 percent | NORMAL REFERENCE VALUE |"
        for i in range(1, 201)
    ]
    table_text = f"{header_line}\n{separator_line}\n" + "\n".join(data_rows)

    ctx = _make_context(
        page_number=3,
        evidence_type="table",
        document_title="Lab Specimen Register",
    )
    chunks = chunker.chunk_page(readings=[table_text], context=ctx)

    # 200 rows with 8 words each = ~1600 words; should produce at least 4 chunks
    assert len(chunks) >= 4

    for chunk in chunks:
        # Every chunk text is an exact substring of the original page text
        assert chunk.text in table_text
        # Every chunk consists of whole rows only
        for line in chunk.text.strip().splitlines():
            line_s = line.strip()
            if line_s and line_s != separator_line:
                assert line_s.startswith("| PT-")
                assert line_s.endswith("|")
        # Every chunk's context prefix contains the table header
        assert header_line in chunk.context_prefix
        # Header line is NOT repeated inside the chunk's stored data text
        assert header_line not in chunk.text


def test_chunk_text_is_exact_substring_of_page_text(
    chunker: StructureAwareChunker,
) -> None:
    """Every chunk's text is an exact substring of its page text."""
    page_text = (
        "## Oncology Guidelines\n\n"
        "First-line treatment protocols specify platinum-based doublet regimens.\n"
        "Dosage adjustments must follow measured renal creatinine clearance.\n"
    )
    ctx = _make_context(page_number=4, evidence_type="text")
    chunks = chunker.chunk_page(readings=[page_text], context=ctx)
    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.text in page_text


def test_empty_page_gives_zero_chunks(chunker: StructureAwareChunker) -> None:
    """Empty or whitespace-only page gives zero chunks."""
    ctx = _make_context(page_number=1, evidence_type="text")
    assert len(chunker.chunk_page(readings=[""], context=ctx)) == 0
    assert len(chunker.chunk_page(readings=["   \n\n\t  "], context=ctx)) == 0


def test_visual_reading_input_keeps_visual_marking(
    chunker: StructureAwareChunker,
) -> None:
    """Visual reading input keeps the visual evidence marking."""
    visual_text = "Prior Authorization Form received via facsimile transmission."
    ctx = _make_context(page_number=1, evidence_type="visual")
    chunks = chunker.chunk_page(readings=[visual_text], context=ctx)
    assert len(chunks) == 1
    assert chunks[0].evidence_type == "visual"
    assert chunks[0].text == visual_text


@pytest.mark.asyncio
async def test_pipeline_visual_reading_produces_visual_chunks() -> None:
    """Pipeline path: a page whose selected reading is visual produces chunks marked visual."""
    tenant_id = "33333333-3333-3333-3333-333333333333"
    ver_id = uuid4()
    page_id = uuid4()

    # Mock repository
    mock_repo = AsyncMock()
    mock_repo.get_page.return_value = PageRecord(
        id=page_id,
        tenant_id=tenant_id,
        document_version_id=ver_id,
        page_number=1,
        page_sha256="abc",
        page_type=PageType.SCANNED,
        image_key=f"{tenant_id}/docs/1.png",
    )
    mock_repo.get_document_title_for_version.return_value = "Real Policy Document"
    mock_repo.create_page_reading.return_value = uuid4()

    captured_chunks: list[dict] = []

    async def fake_create_chunks_batch(tenant_id: str, records: list[dict]) -> None:
        _ = tenant_id
        captured_chunks.extend(records)

    mock_repo.create_chunks_batch = fake_create_chunks_batch

    # Mock low-OCR parser (confidence 0.40 triggers vision reading)
    from app.ports.types import ParsedPageContent

    mock_parser = AsyncMock()
    mock_parser.extract.return_value = ParsedPageContent(
        page_number=1,
        text="Low confidence scanned garbled text",
        tables=[],
        confidence=0.40,
    )

    # Mock model provider returning clear vision transcription
    mock_model = AsyncMock()
    from app.ports.types import PageReading as PageReadingDTO

    mock_model.transcribe_page.return_value = PageReadingDTO(
        reading="Clean vision transcription text from fax.",
        confidence=0.96,
        model_id="mock-qwen",
        model_version="4b",
    )

    # Mock embedding provider
    mock_embedder = AsyncMock()
    mock_embedder.model_id = "bge-m3"
    mock_embedder.version = "v1"
    mock_embedder.embed.return_value = [[0.1] * 1024]

    # Real structure-aware chunker
    real_chunker = StructureAwareChunker()

    pipeline = PagePipelineService(
        repository=mock_repo,
        parser=mock_parser,
        chunker=real_chunker,
        embedding_provider=mock_embedder,
        model_provider=mock_model,
        storage=None,
    )

    await pipeline.process_page(
        tenant_id=tenant_id,
        document_version_id=ver_id,
        page_number=1,
    )

    # Assert chunks were inserted into batch and marked with evidence_type="visual"
    assert len(captured_chunks) >= 1
    for chunk in captured_chunks:
        assert chunk["evidence_type"] == "visual"
        assert chunk["text"] == "Clean vision transcription text from fax."
        assert "Real Policy Document" in chunk["context_prefix"]
        assert chunk["id"] is not None


def test_context_prefix_contains_title_page_and_heading(
    chunker: StructureAwareChunker,
) -> None:
    """The context prefix is separate and contains title, page number and nearest heading."""
    page_text = (
        "## Prior Authorization Section 4\n\n"
        "Specialty medications require prior authorization review.\n"
        "Requests must include clinical documentation and lab results.\n"
    )
    ctx = _make_context(
        page_number=5,
        evidence_type="text",
        document_title="Formulary Handbook",
    )
    chunks = chunker.chunk_page(readings=[page_text], context=ctx)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert "Formulary Handbook" in chunk.context_prefix
    assert "Page 5" in chunk.context_prefix
    assert "Prior Authorization Section 4" in chunk.context_prefix
    assert chunk.context_prefix not in chunk.text
    assert chunk.text in page_text


@pytest.mark.asyncio
async def test_pipeline_unknown_evidence_type_raises_error() -> None:
    """An unknown evidence type in the pipeline raises a typed error with no silent fallback."""
    from app.ports.errors import InvalidEvidenceTypeError
    from app.ports.types import ParsedPageContent

    tenant_id = "44444444-4444-4444-4444-444444444444"
    ver_id = uuid4()

    mock_repo = AsyncMock()
    mock_repo.get_page.return_value = PageRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        document_version_id=ver_id,
        page_number=1,
        page_sha256="abc",
        page_type=PageType.DIGITAL_TEXT,
        image_key=f"{tenant_id}/docs/1.png",
    )
    mock_repo.get_document_title_for_version.return_value = "Policy Spec"

    mock_parser = AsyncMock()
    mock_parser.extract.return_value = ParsedPageContent(
        page_number=1,
        text="Normal document text",
        tables=[],
        confidence=0.99,
    )

    mock_embedder = AsyncMock()
    mock_embedder.model_id = "bge-m3"
    mock_embedder.version = "v1"
    mock_embedder.embed.return_value = [[0.1] * 1024]

    pipeline = PagePipelineService(
        repository=mock_repo,
        parser=mock_parser,
        chunker=StructureAwareChunker(),
        embedding_provider=mock_embedder,
        model_provider=AsyncMock(),
        storage=None,
    )

    # Monkeypatch select_readings_for_chunking to return an unknown evidence type
    import app.core.ingestion.pipeline as pipeline_module
    from app.core.ingestion.vision_policy import VisionReadingSelection

    orig_select = pipeline_module.select_readings_for_chunking
    try:
        pipeline_module.select_readings_for_chunking = lambda **_kwargs: [
            VisionReadingSelection(
                evidence_type="unsupported_format",
                text="sample text",
                page_reading_id=None,
            )
        ]
        with pytest.raises(InvalidEvidenceTypeError) as exc_info:
            await pipeline.process_page(
                tenant_id=tenant_id,
                document_version_id=ver_id,
                page_number=1,
            )
        assert "Unknown evidence type" in str(exc_info.value)
    finally:
        pipeline_module.select_readings_for_chunking = orig_select


@pytest.mark.asyncio
async def test_pipeline_embedding_count_mismatch_raises_error() -> None:
    """If embedding provider returns different count of vectors than chunks, raise error."""
    from app.ports.errors import EmbeddingCountMismatchError
    from app.ports.types import ParsedPageContent

    tenant_id = "55555555-5555-5555-5555-555555555555"
    ver_id = uuid4()

    mock_repo = AsyncMock()
    mock_repo.get_page.return_value = PageRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        document_version_id=ver_id,
        page_number=1,
        page_sha256="abc",
        page_type=PageType.DIGITAL_TEXT,
        image_key=f"{tenant_id}/docs/1.png",
    )
    mock_repo.get_document_title_for_version.return_value = "Policy Spec"

    stored_batches: list[list[dict]] = []

    async def fake_create_chunks(tenant_id: str, records: list[dict]) -> None:
        _ = tenant_id
        stored_batches.append(records)

    mock_repo.create_chunks_batch = fake_create_chunks

    mock_parser = AsyncMock()
    mock_parser.extract.return_value = ParsedPageContent(
        page_number=1,
        text="Normal document text paragraph.",
        tables=[],
        confidence=0.99,
    )

    # Embedding provider returns 0 vectors for 1 chunk -> mismatch
    mock_embedder = AsyncMock()
    mock_embedder.model_id = "bge-m3"
    mock_embedder.version = "v1"
    mock_embedder.embed.return_value = []

    pipeline = PagePipelineService(
        repository=mock_repo,
        parser=mock_parser,
        chunker=StructureAwareChunker(),
        embedding_provider=mock_embedder,
        model_provider=AsyncMock(),
        storage=None,
    )

    with pytest.raises(EmbeddingCountMismatchError) as exc_info:
        await pipeline.process_page(
            tenant_id=tenant_id,
            document_version_id=ver_id,
            page_number=1,
        )
    assert "Embedding provider returned count mismatching chunk count" in str(exc_info.value)
    # Asserts that no chunks were stored without embeddings
    assert len(stored_batches) == 0
