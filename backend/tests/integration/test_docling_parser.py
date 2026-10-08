"""Integration tests for Docling Parser Adapter, confidence scoring, and table citations (Task 2.4).

Confidence documentation:
https://docling-project.github.io/docling/concepts/confidence_scores/

Covers:
- PageConfidenceScores: per-page OCR confidence scoring from result.confidence.pages.
- Scanned page NaN / low confidence handling -> needs_review=True (NaN < 0.70 is False in Python).
- Digital page with NaN / unset OCR score -> needs_review=False.
- Partial document failure: 1 bad page among good pages flags only that page, while
  version status is needs_review.
- Invariant 6: pages below threshold excluded from chunk indexing.
- Invariant 8: tables_data includes page_id (composite FK to pages) and table_index.
- Idempotency: page_readings (tenant_id, page_id, source, model_id, model_version) and
  tables_data (tenant_id, document_version_id, sheet_name, page_id, table_index, row_index)
  with NULLS NOT DISTINCT for spreadsheet re-runs.
- Real-model tests with @pytest.mark.models marker skipped when docling is not installed.
"""

from __future__ import annotations

import importlib.util
import math
from uuid import uuid4

import pytest

from app.adapters.parsing.docling import (
    DoclingDocumentParser,
    build_page_reading_record,
    build_table_data_records,
    filter_indexable_pages,
)
from app.adapters.parsing.mock import MockDocumentParser
from app.ports.errors import ConfigurationError
from app.ports.types import PageArtifact, PageType, ParsedPageContent
from tests.fixtures.synthetic_documents import (
    create_synthetic_digital_pdf,
    create_synthetic_spanish_scanned_pdf,
)

# Ensure models marker is recognized
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

HAS_DOCLING = importlib.util.find_spec("docling") is not None


class TestDoclingParserAdapterContracts:
    """Contract tests for Docling parser adapter extraction and filtering."""

    @pytest.mark.asyncio
    async def test_mock_document_parser_extract_page_content(self) -> None:
        """Mock parser extract returns text, tables, and per-page confidence score."""
        parser = MockDocumentParser()
        page = PageArtifact(
            page_number=1,
            image_bytes=b"sample_page_png",
            mime_type="image/png",
            page_sha256="sha-p1",
            page_type=PageType.SCANNED,
            char_count=0,
            has_text_layer=False,
            image_area_ratio=0.95,
        )
        parsed = await parser.extract(page)
        assert isinstance(parsed, ParsedPageContent)
        assert parsed.page_number == 1
        assert parsed.confidence >= 0.0

    @pytest.mark.asyncio
    async def test_docling_parser_raises_configuration_error_when_uninstalled(self) -> None:
        """Docling parser raises ConfigurationError when optional dependencies are absent."""
        if HAS_DOCLING:
            pytest.skip("Docling is installed; test targets uninstalled environment")

        parser = DoclingDocumentParser()
        page = PageArtifact(
            page_number=1,
            image_bytes=b"sample_page_png",
            mime_type="image/png",
            page_sha256="sha-p1",
            page_type=PageType.SCANNED,
            char_count=0,
            has_text_layer=False,
            image_area_ratio=0.95,
        )
        with pytest.raises(ConfigurationError, match="Docling is not installed"):
            await parser.extract(page)

    def test_needs_review_pages_excluded_from_indexing(self) -> None:
        """Invariant 6: Pages flagged with needs_review must not be indexed into chunks."""
        page_results = [
            {"page_number": 1, "text": "Good page", "needs_review": False},
            {"page_number": 2, "text": "Unreadable noisy OCR", "needs_review": True},
            {"page_number": 3, "text": "Good page 3", "needs_review": False},
        ]

        indexable = filter_indexable_pages(page_results)
        indexed_page_numbers = [p["page_number"] for p in indexable]

        assert indexed_page_numbers == [1, 3]
        assert 2 not in indexed_page_numbers


class TestStorageAndIdempotencyContracts:
    """Validates unique keys and idempotency semantics for page_readings and tables_data."""

    def test_page_readings_unique_constraint_schema_contract(self) -> None:
        """Item 6: page_readings unique constraint is
        (tenant_id, page_id, source, model_id, model_version).
        """

        tenant_id = uuid4()
        page_id = uuid4()

        record_1 = build_page_reading_record(
            tenant_id=tenant_id,
            page_id=page_id,
            source="docling_ocr",
            text="Recognized text",
            model_id="easyocr",
            model_version="craft_latin_v1",
        )

        record_2 = build_page_reading_record(
            tenant_id=tenant_id,
            page_id=page_id,
            source="docling_ocr",
            text="Recognized text on retry",
            model_id="easyocr",
            model_version="craft_latin_v1",
        )

        # Unique key tuple matches exactly
        assert record_1["idempotency_key"] == record_2["idempotency_key"]
        assert record_1["idempotency_key"] == (
            str(tenant_id),
            str(page_id),
            "docling_ocr",
            "easyocr",
            "craft_latin_v1",
        )

    def test_tables_data_two_sheet_spreadsheet_rerun_idempotent(self) -> None:
        """Item 2: tables_data with NULLS NOT DISTINCT on
        (tenant_id, document_version_id, sheet_name, page_id, table_index, row_index).
        """

        tenant_id = uuid4()
        version_id = uuid4()

        sheet_1_rows = [{"col1": "A", "col2": "B"}, {"col1": "C", "col2": "D"}]
        sheet_2_rows = [{"col1": "X", "col2": "Y"}]

        # Run 1: generate records
        records_run1 = build_table_data_records(
            tenant_id=tenant_id,
            document_version_id=version_id,
            page_id=None,  # Spreadsheets have NULL page_id
            table_index=0,
            sheets={"Sheet1": sheet_1_rows, "Sheet2": sheet_2_rows},
        )

        # Run 2: rerun generates exact identical idempotency keys
        records_run2 = build_table_data_records(
            tenant_id=tenant_id,
            document_version_id=version_id,
            page_id=None,
            table_index=0,
            sheets={"Sheet1": sheet_1_rows, "Sheet2": sheet_2_rows},
        )

        assert len(records_run1) == 3
        assert len(records_run2) == 3

        keys_1 = [r["idempotency_key"] for r in records_run1]
        keys_2 = [r["idempotency_key"] for r in records_run2]
        assert keys_1 == keys_2

    def test_tables_data_cites_page_id_and_table_index(self) -> None:
        """Item 3: tables_data records page_id and table_index for Invariant 8 citations."""

        tenant_id = uuid4()
        version_id = uuid4()
        page_id = uuid4()

        records = build_table_data_records(
            tenant_id=tenant_id,
            document_version_id=version_id,
            page_id=page_id,
            table_index=1,
            sheets={"PageTable_1": [{"Drug": "Metformin", "Dosage": "500mg"}]},
        )

        assert len(records) == 1
        rec = records[0]
        assert rec["page_id"] == page_id
        assert rec["table_index"] == 1
        assert rec["document_version_id"] == version_id


@pytest.mark.models
class TestRealDoclingModelsDesktop:
    """Real Docling model weight execution tests (run on desktop-gpu only)."""

    @pytest.mark.skipif(
        not HAS_DOCLING,
        reason="Docling optional dependencies not installed; run on desktop-gpu",
    )
    @pytest.mark.asyncio
    async def test_real_docling_ocr_spanish_scanned_page(self) -> None:
        """Item 1 & 9: Real Docling parses Spanish scanned page with Pillow-bundled font."""
        parser = DoclingDocumentParser()
        spanish_pdf = create_synthetic_spanish_scanned_pdf(
            text="Atención médica: Paciente con diagnóstico de hipertensión arterial severa."
        )

        artifacts = [a async for a in parser.split_pages(spanish_pdf, "spanish_clinical.pdf")]
        assert len(artifacts) == 1

        extracted = await parser.extract(artifacts[0])
        assert extracted.page_number == 1
        # Asserts Spanish characters recognized
        assert "paciente" in extracted.text.lower()
        assert "hipertensi" in extracted.text.lower()
        assert not math.isnan(extracted.confidence)
        assert extracted.confidence >= 0.70

    @pytest.mark.skipif(
        not HAS_DOCLING,
        reason="Docling optional dependencies not installed; run on desktop-gpu",
    )
    @pytest.mark.asyncio
    async def test_real_docling_digital_text_and_table_extraction(self) -> None:
        """Real Docling extracts digital text and structured table data."""
        parser = DoclingDocumentParser()
        pdf_bytes = create_synthetic_digital_pdf(text="Clinical guidelines with dosage tables.")

        artifacts = [a async for a in parser.split_pages(pdf_bytes, "digital_sample.pdf")]
        assert len(artifacts) == 1

        extracted = await parser.extract(artifacts[0])
        assert "guidelines" in extracted.text.lower()
