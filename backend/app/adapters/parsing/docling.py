"""Docling document parser adapter (Task 2.4).

Implements DocumentParser port using IBM Docling and PyPdfium.
Lazy imports docling inside functions/methods so the module can be safely imported
and tested on environments without docling / OCR model weights installed.
"""

from __future__ import annotations

import importlib.util
import io
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

from app.ports.document_parser import DocumentParser
from app.ports.errors import ConfigurationError
from app.ports.types import PageArtifact, ParsedPageContent, TableData

HAS_DOCLING = importlib.util.find_spec("docling") is not None

_DOCLING_NOT_INSTALLED_ERROR = (
    "Docling is not installed. Install with dependency group 'parsing' to use this adapter."
)


def _get_docling_converter() -> Any:
    """Lazy import and factory for Docling DocumentConverter."""
    if not HAS_DOCLING:
        raise ConfigurationError(
            _DOCLING_NOT_INSTALLED_ERROR,
            error_code="DOCLING_NOT_INSTALLED",
        )
    try:
        from docling.datamodel.base_models import InputFormat  # type: ignore[import-not-found]
        from docling.datamodel.pipeline_options import (  # type: ignore[import-not-found]
            EasyOcrOptions,
            PdfPipelineOptions,
        )
        from docling.document_converter import (  # type: ignore[import-not-found]
            DocumentConverter,
            PdfFormatOption,
        )

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.ocr_options = EasyOcrOptions(
            lang=["es", "en"],
            use_gpu=True,
            confidence_threshold=0.5,
        )

        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )
    except ImportError as exc:
        raise ConfigurationError(
            _DOCLING_NOT_INSTALLED_ERROR,
            error_code="DOCLING_NOT_INSTALLED",
        ) from exc


class DoclingDocumentParser(DocumentParser):
    """Adapter implementing DocumentParser using IBM Docling."""

    def __init__(self) -> None:
        pass

    async def split_pages(
        self,
        file: bytes,
        filename: str,
    ) -> AsyncIterator[PageArtifact]:
        """Split a document into individual page artifacts."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        extension = filename.rsplit(".", 1)[-1] if "." in filename else "pdf"
        pages = await splitter.split(file, extension)
        for page in pages:
            yield page

    async def extract(
        self,
        page: PageArtifact,
    ) -> ParsedPageContent:
        """Extract text, tables, and per-page confidence scores from an individual page."""
        if not HAS_DOCLING:
            raise ConfigurationError(
                _DOCLING_NOT_INSTALLED_ERROR,
                error_code="DOCLING_NOT_INSTALLED",
            )

        converter = _get_docling_converter()
        from docling.datamodel.base_models import DocumentStream

        stream = DocumentStream(
            name=f"page_{page.page_number}.png",
            stream=io.BytesIO(page.image_bytes),
        )
        conv_res = converter.convert(stream)

        # 1. Text extraction
        text_content = conv_res.document.export_to_markdown()

        # 2. Table extraction
        extracted_tables: list[TableData] = []
        if hasattr(conv_res.document, "tables"):
            for table in conv_res.document.tables:
                df = table.export_to_dataframe()
                headers = [str(c) for c in df.columns]
                rows = [[str(v) for v in row] for row in df.values.tolist()]
                extracted_tables.append(TableData(headers=headers, rows=rows))

        # 3. Confidence score resolution
        confidence = 1.0
        if conv_res.confidence and hasattr(conv_res.confidence, "pages"):
            pages_conf = conv_res.confidence.pages
            page_conf = None
            if isinstance(pages_conf, dict):
                page_conf = pages_conf.get(page.page_number) or pages_conf.get(
                    str(page.page_number)
                )
            elif isinstance(pages_conf, list) and len(pages_conf) >= page.page_number:
                page_conf = pages_conf[page.page_number - 1]

            if page_conf and hasattr(page_conf, "ocr_score"):
                confidence = float(page_conf.ocr_score)

        return ParsedPageContent(
            page_number=page.page_number,
            text=text_content,
            tables=extracted_tables,
            confidence=confidence,
        )


def filter_indexable_pages(
    page_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter out pages flagged with needs_review=True prior to chunk indexing (Invariant 6)."""
    return [p for p in page_results if not p.get("needs_review", False)]


def build_page_reading_record(
    tenant_id: UUID,
    page_id: UUID,
    source: str,
    text: str,
    model_id: str,
    model_version: str,
) -> dict[str, Any]:
    """Build a database insertion payload for page_readings with unique idempotency key."""
    return {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "page_id": page_id,
        "source": source,
        "text": text,
        "model_id": model_id,
        "model_version": model_version,
        "idempotency_key": (
            str(tenant_id),
            str(page_id),
            source,
            model_id,
            model_version,
        ),
    }


def build_table_data_records(
    tenant_id: UUID,
    document_version_id: UUID,
    page_id: UUID | None,
    table_index: int,
    sheets: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Build database insertion payloads for tables_data with sheet_name and page_id."""
    records: list[dict[str, Any]] = []
    for sheet_name, rows in sheets.items():
        for row_idx, row_data in enumerate(rows, start=1):
            key = (
                str(tenant_id),
                str(document_version_id),
                sheet_name,
                str(page_id) if page_id is not None else None,
                table_index,
                row_idx,
            )
            records.append(
                {
                    "id": uuid4(),
                    "tenant_id": tenant_id,
                    "document_version_id": document_version_id,
                    "sheet_name": sheet_name,
                    "page_id": page_id,
                    "table_index": table_index,
                    "row_index": row_idx,
                    "row_data": row_data,
                    "idempotency_key": key,
                }
            )
    return records
