# ruff: noqa: E501
"""Unit tests for digital PDF reading order preservation (Task 3.5 / Ingestion word order).

Verifies that for digital PDFs, Docling extraction preserves the exact reading order
from the PDF text layer instead of reordering words (e.g. moving 'prior' to the end of the next line).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from app.adapters.parsing.docling import DoclingDocumentParser
from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter
from app.ports.errors import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_DIR = PROJECT_ROOT.parent / "evals" / "golden" / "docs"
if not DOCS_DIR.exists():
    DOCS_DIR = PROJECT_ROOT / "evals" / "golden" / "docs"


@pytest.mark.asyncio
async def test_front_desk_v1_and_v2_word_order_preserved() -> None:
    """Assert that front_desk_policy_v1 and v2 preserve reading order and key phrases."""
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv()

    raw_artifacts_path = os.environ.get("DOCLING_ARTIFACTS_PATH")
    if not raw_artifacts_path or not raw_artifacts_path.strip():
        raise ConfigurationError(
            "DOCLING_ARTIFACTS_PATH environment variable is missing.",
            error_code="DOCLING_ARTIFACTS_PATH_MISSING",
        )
    artifacts_path = Path(raw_artifacts_path.strip())
    if not artifacts_path.is_dir():
        raise ConfigurationError(
            f"DOCLING_ARTIFACTS_PATH directory does not exist: {artifacts_path}",
            error_code="DOCLING_ARTIFACTS_PATH_NOT_FOUND",
        )

    raw_storage_path = os.environ.get("LOCAL_STORAGE_PATH")
    if not raw_storage_path or not raw_storage_path.strip():
        raise ConfigurationError(
            "LOCAL_STORAGE_PATH environment variable is missing.",
            error_code="LOCAL_STORAGE_PATH_MISSING",
        )
    storage_path = Path(raw_storage_path.strip())
    if not storage_path.is_dir():
        raise ConfigurationError(
            f"LOCAL_STORAGE_PATH directory does not exist: {storage_path}",
            error_code="LOCAL_STORAGE_PATH_NOT_FOUND",
        )

    v1_bytes = (DOCS_DIR / "front_desk_policy_v1.pdf").read_bytes()
    v2_bytes = (DOCS_DIR / "front_desk_policy_v2.pdf").read_bytes()

    splitter = PyPdfiumDocumentSplitter()
    v1_pages = await splitter.split(v1_bytes, "pdf")
    v2_pages = await splitter.split(v2_bytes, "pdf")

    assert len(v1_pages) == 1
    assert len(v2_pages) == 1
    assert v1_pages[0].has_text_layer is True
    assert v2_pages[0].has_text_layer is True
    assert v1_pages[0].text_content is not None
    assert v2_pages[0].text_content is not None

    parser = DoclingDocumentParser(artifacts_path=artifacts_path, device="cuda")
    v1_parsed = await parser.extract(v1_pages[0])
    v2_parsed = await parser.extract(v2_pages[0])

    # 1. Word order for arrival window in v1 and v2
    assert "15 minutes prior to scheduled visit" in v1_parsed.text
    assert "20 minutes prior to scheduled visit" in v2_parsed.text
    assert "rescheduled prior" not in v1_parsed.text
    assert "rescheduled prior" not in v2_parsed.text

    # 2. Intake fax line wording
    assert "Front desk intake fax number is 555-0101" in v1_parsed.text
    assert "this intake fax line" in v1_parsed.text
    assert "revised" not in v1_parsed.text

    assert "Front desk intake fax number is 555-0102" in v2_parsed.text
    assert "this revised intake fax line" in v2_parsed.text
    assert "revised" in v2_parsed.text
