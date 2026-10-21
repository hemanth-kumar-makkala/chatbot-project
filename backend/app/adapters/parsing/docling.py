"""Docling document parser adapter (Task 2.4).

Implements DocumentParser port using IBM Docling and PyPdfium.
Lazy imports docling inside functions/methods so the module can be safely imported
and tested on environments without docling / OCR model weights installed.
"""

from __future__ import annotations

import importlib.util
import io
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.ports.document_parser import DocumentParser
from app.ports.errors import ConfigurationError
from app.ports.types import PageArtifact, ParsedPageContent, ParserDevice, TableData

HAS_DOCLING = importlib.util.find_spec("docling") is not None

_DOCLING_NOT_INSTALLED_ERROR = (
    "Docling is not installed. Install with dependency group 'parsing' to use this adapter."
)


def _get_docling_converter(
    artifacts_path: Path,
    device: ParserDevice,
) -> Any:
    """Lazy import and factory for Docling DocumentConverter."""
    if not HAS_DOCLING:
        raise ConfigurationError(
            _DOCLING_NOT_INSTALLED_ERROR,
            error_code="DOCLING_NOT_INSTALLED",
        )
    try:
        import os

        os.environ["HF_HUB_OFFLINE"] = "1"

        from docling.datamodel.accelerator_options import AcceleratorDevice
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            EasyOcrOptions,
            PdfPipelineOptions,
        )
        from docling.document_converter import (
            DocumentConverter,
            ImageFormatOption,
            PdfFormatOption,
        )

        if device == "cuda":
            accelerator_device = AcceleratorDevice.CUDA
        elif device == "cpu":
            accelerator_device = AcceleratorDevice.CPU
        else:
            raise ConfigurationError(
                f"Unsupported parser device '{device}'. Expected 'cuda' or 'cpu'.",
                error_code="INVALID_PARSER_DEVICE",
            )

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.artifacts_path = artifacts_path
        pipeline_options.accelerator_options.device = accelerator_device

        pipeline_options.ocr_options = EasyOcrOptions(
            lang=["es", "en"],
            confidence_threshold=0.5,
        )

        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
            }
        )
    except ImportError as exc:
        raise ConfigurationError(
            _DOCLING_NOT_INSTALLED_ERROR,
            error_code="DOCLING_NOT_INSTALLED",
        ) from exc


class DoclingDocumentParser(DocumentParser):
    """Adapter implementing DocumentParser using IBM Docling."""

    def __init__(
        self,
        artifacts_path: Path,
        device: ParserDevice,
    ) -> None:
        import os

        self.artifacts_path = artifacts_path
        self.device = device
        os.environ["HF_HUB_OFFLINE"] = "1"

    def get_configured_options(self) -> dict[str, Any]:
        """Return resolved configuration options for testing/introspection."""
        import os

        hf_offline = None
        if "HF_HUB_OFFLINE" in os.environ:
            hf_offline = os.environ["HF_HUB_OFFLINE"]
        return {
            "artifacts_path": self.artifacts_path,
            "device": self.device,
            "hf_hub_offline": hf_offline,
        }

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

        converter = _get_docling_converter(
            artifacts_path=self.artifacts_path,
            device=self.device,
        )
        from docling.datamodel.base_models import DocumentStream

        stream = DocumentStream(
            name=f"page_{page.page_number}.png",
            stream=io.BytesIO(page.image_bytes),
        )
        conv_res = converter.convert(stream)

        # 1. Text extraction
        docling_text = conv_res.document.export_to_markdown()
        text_content = docling_text

        # For digital PDFs, compare Docling text with the PDF text layer
        # and prefer the text layer when Docling reorders words
        pdf_text = page.text_content
        if pdf_text and len(pdf_text.strip()) > 0:
            import re

            pdf_words = [w.lower() for w in re.findall(r"\b\w+\b", pdf_text)]
            docling_words = [w.lower() for w in re.findall(r"\b\w+\b", docling_text)]
            if pdf_words and pdf_words != docling_words:
                text_content = pdf_text

        # 2. Table extraction
        extracted_tables: list[TableData] = []
        try:
            tables = conv_res.document.tables
            for table in tables:
                df = table.export_to_dataframe()
                headers = [str(c) for c in df.columns]
                rows = [[str(v) for v in row] for row in df.values.tolist()]
                extracted_tables.append(TableData(headers=headers, rows=rows))
        except AttributeError:
            pass

        # 3. Confidence score resolution: each call converts exactly one page.
        # Require exactly one entry in conv_res.confidence.pages and read that one.
        # If confidence is missing, empty, has more than one page, or the score is missing,
        # set confidence to float("nan") so the review policy flags the page.
        confidence = float("nan")
        if conv_res.confidence is not None:
            try:
                pages_conf = conv_res.confidence.pages
                if pages_conf is not None and len(pages_conf) == 1:
                    page_conf = (
                        next(iter(pages_conf.values()))
                        if isinstance(pages_conf, dict)
                        else pages_conf[0]
                    )
                    score = page_conf.ocr_score
                    if score is not None:
                        confidence = float(score)
            except AttributeError:
                pass

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
