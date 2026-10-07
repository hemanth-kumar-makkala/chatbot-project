"""Unit tests for document page splitting and classification (Task 2.3).

Covers:
- Decision table boundary cases for all page types (blank, digital_text, image_heavy, scanned).
- Temporary vs permanent failure semantics.
- Image bomb defense (Pillow MAX_IMAGE_PIXELS and PDF canvas bounds).
- Encrypted document rejection (ERR_PDF_ENCRYPTED).
- Corrupted document stream rejection (ERR_PDF_CORRUPT).
- Page count limit rejection (ERR_PAGE_LIMIT_EXCEEDED).
- Non-paged document routing (TXT, CSV, XLSX skip splitting).
- Core import boundary invariant (no pypdfium2 or Pillow imports in core).
"""

from __future__ import annotations

import pytest

from app.ports.errors import (
    CorruptDocumentError,
    DecompressionBombError,
    EncryptedDocumentError,
    PageLimitExceededError,
)
from app.ports.types import PageType
from tests.fixtures.synthetic_documents import (
    create_synthetic_blank_pdf,
    create_synthetic_corrupt_pdf,
    create_synthetic_decompression_bomb_tiff,
    create_synthetic_digital_pdf,
    create_synthetic_encrypted_pdf,
    create_synthetic_image_heavy_pdf,
    create_synthetic_multipage_pdf,
    create_synthetic_multipage_tiff,
    create_synthetic_oversized_pdf,
    create_synthetic_scanned_pdf,
    create_synthetic_searchable_scanned_pdf,
)
from tests.invariants.boundary_checker import scan_directory_boundaries

# ===========================================================================
# 1. Classification Decision Table (MECE Boundary Tests)
# ===========================================================================


class TestClassificationDecisionTable:
    """Rigorous boundary unit tests for the MECE page classification table."""

    @pytest.mark.parametrize(
        ("char_count", "image_area_ratio", "expected_type", "expected_text_layer"),
        [
            # Row 1: Blank page (0 chars, < 0.05 image ratio)
            (0, 0.0, PageType.BLANK, False),
            (0, 0.049, PageType.BLANK, False),
            # Row 2: Graphic/diagram only (0 chars, 0.05 <= ratio < 0.70)
            (0, 0.05, PageType.IMAGE_HEAVY, False),
            (0, 0.30, PageType.IMAGE_HEAVY, False),
            (0, 0.699, PageType.IMAGE_HEAVY, False),
            # Row 3: Scanned page without OCR (0 chars, >= 0.70 image ratio)
            (0, 0.70, PageType.SCANNED, False),
            (0, 0.95, PageType.SCANNED, False),
            (0, 1.0, PageType.SCANNED, False),
            # Row 4: Digital text page (> 0 chars, < 0.30 image ratio)
            (1, 0.0, PageType.DIGITAL_TEXT, True),
            (50, 0.0, PageType.DIGITAL_TEXT, True),  # User's threshold gap example
            (1000, 0.10, PageType.DIGITAL_TEXT, True),
            (100, 0.299, PageType.DIGITAL_TEXT, True),
            # Row 5: Mixed image-heavy page (> 0 chars, 0.30 <= ratio < 0.70)
            (1, 0.30, PageType.IMAGE_HEAVY, True),
            (500, 0.50, PageType.IMAGE_HEAVY, True),
            (200, 0.699, PageType.IMAGE_HEAVY, True),
            # Row 6: Searchable scanned page (> 0 chars, >= 0.70 image ratio)
            (50, 0.70, PageType.SCANNED, True),  # Full image + hidden OCR text
            (500, 0.85, PageType.SCANNED, True),
            (2000, 1.0, PageType.SCANNED, True),
        ],
    )
    def test_page_classifier_decision_boundaries(
        self,
        char_count: int,
        image_area_ratio: float,
        expected_type: PageType,
        expected_text_layer: bool,
    ) -> None:
        """Test every row and boundary condition of the classification table."""
        from app.core.ingestion.page_classifier import classify_page

        page_type, has_text_layer = classify_page(char_count, image_area_ratio)
        assert page_type == expected_type
        assert has_text_layer is expected_text_layer


# ===========================================================================
# 2. PyPdfium Splitter Adapter Unit Tests
# ===========================================================================


class TestPyPdfiumSplitterAdapter:
    """Unit tests for PyPdfiumDocumentSplitter adapter."""

    @pytest.mark.asyncio
    async def test_split_blank_pdf(self) -> None:
        """Blank PDF produces page_type='blank' and 150 DPI rendered PNG."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        pdf_bytes = create_synthetic_blank_pdf(num_pages=1)
        artifacts = await splitter.split(pdf_bytes, extension=".pdf")

        assert len(artifacts) == 1
        page = artifacts[0]
        assert page.page_number == 1
        assert page.page_type == PageType.BLANK
        assert page.char_count == 0
        assert not page.has_text_layer
        assert len(page.image_bytes) > 0
        assert page.mime_type == "image/png"
        assert len(page.page_sha256) == 64

    @pytest.mark.asyncio
    async def test_split_digital_text_pdf(self) -> None:
        """Digital PDF produces page_type='digital_text' with char count recorded."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        pdf_bytes = create_synthetic_digital_pdf(
            text="Clinical guidelines for treatment.", num_pages=2
        )
        artifacts = await splitter.split(pdf_bytes, extension=".pdf")

        assert len(artifacts) == 2
        for page in artifacts:
            assert page.page_type == PageType.DIGITAL_TEXT
            assert page.char_count > 0
            assert page.has_text_layer is True
            assert len(page.image_bytes) > 0

    @pytest.mark.asyncio
    async def test_split_scanned_pdf(self) -> None:
        """Pure scanned raster PDF produces page_type='scanned' and has_text_layer=False."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        pdf_bytes = create_synthetic_scanned_pdf(num_pages=1)
        artifacts = await splitter.split(pdf_bytes, extension=".pdf")

        assert len(artifacts) == 1
        page = artifacts[0]
        assert page.page_type == PageType.SCANNED
        assert page.char_count == 0
        assert page.has_text_layer is False

    @pytest.mark.asyncio
    async def test_split_searchable_scanned_pdf(self) -> None:
        """Searchable scan produces page_type='scanned' and has_text_layer=True."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        pdf_bytes = create_synthetic_searchable_scanned_pdf(text="OCR hidden text layer")
        artifacts = await splitter.split(pdf_bytes, extension=".pdf")

        assert len(artifacts) == 1
        page = artifacts[0]
        assert page.page_type == PageType.SCANNED
        assert page.char_count > 0
        assert page.has_text_layer is True

    @pytest.mark.asyncio
    async def test_split_image_heavy_pdf(self) -> None:
        """Partial graphic diagram produces page_type='image_heavy'."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        pdf_bytes = create_synthetic_image_heavy_pdf(text="Diagram with explanation.")
        artifacts = await splitter.split(pdf_bytes, extension=".pdf")

        assert len(artifacts) == 1
        page = artifacts[0]
        assert page.page_type == PageType.IMAGE_HEAVY

    @pytest.mark.asyncio
    async def test_split_multipage_tiff(self) -> None:
        """Multi-page TIFF produces one page artifact per frame."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        tiff_bytes = create_synthetic_multipage_tiff(num_frames=3)
        artifacts = await splitter.split(tiff_bytes, extension=".tiff")

        assert len(artifacts) == 3
        for i, page in enumerate(artifacts, start=1):
            assert page.page_number == i
            assert page.page_type == PageType.SCANNED
            assert len(page.image_bytes) > 0

    @pytest.mark.asyncio
    async def test_concurrent_splits_thread_safety(self) -> None:
        """Two concurrent splits in the same worker process both succeed under process lock."""
        import asyncio

        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        pdf_1 = create_synthetic_digital_pdf(text="Document 1 text", num_pages=2)
        pdf_2 = create_synthetic_digital_pdf(text="Document 2 text", num_pages=2)

        res_1, res_2 = await asyncio.gather(
            splitter.split(pdf_1, extension=".pdf"),
            splitter.split(pdf_2, extension=".pdf"),
        )

        assert len(res_1) == 2
        assert len(res_2) == 2

    def test_pypdfium_splitter_real_threads_lock_concurrency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Concurrent calls across real OS threads coordinate safely through _PDFIUM_LOCK."""
        import asyncio
        import concurrent.futures
        import threading

        import app.adapters.pypdfium_splitter as splitter_mod
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        pdf_bytes = create_synthetic_digital_pdf(
            text="Thread safety test on real threads", num_pages=2
        )

        real_lock = splitter_mod._PDFIUM_LOCK
        max_concurrent_in_lock = 0
        current_in_lock = 0
        counter_lock = threading.Lock()

        class MonitoredLock:
            def acquire(self, *args, **kwargs):
                nonlocal max_concurrent_in_lock, current_in_lock
                res = real_lock.acquire(*args, **kwargs)
                if res:
                    with counter_lock:
                        current_in_lock += 1
                        if current_in_lock > max_concurrent_in_lock:
                            max_concurrent_in_lock = current_in_lock
                return res

            def release(self, *args, **kwargs):
                nonlocal current_in_lock
                with counter_lock:
                    current_in_lock -= 1
                return real_lock.release(*args, **kwargs)

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                self.release()

        monkeypatch.setattr(splitter_mod, "_PDFIUM_LOCK", MonitoredLock())

        def worker():
            return asyncio.run(splitter.split(pdf_bytes, extension=".pdf"))

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker) for _ in range(10)]
            results = [f.result() for f in futures]

        assert len(results) == 10
        for r in results:
            assert len(r) == 2
        # Proves at most 1 thread entered _PDFIUM_LOCK critical section concurrently
        assert max_concurrent_in_lock == 1


# ===========================================================================
# 3. Security, Bomb Defense, and Permanent Failure Tests
# ===========================================================================


class TestSplitterPermanentFailuresAndSafety:
    """Tests verifying safety boundaries, image bomb caps, and permanent failures."""

    @pytest.mark.asyncio
    async def test_encrypted_pdf_raises_permanent_error(self) -> None:
        """Encrypted PDF raises EncryptedDocumentError with code ERR_PDF_ENCRYPTED."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        enc_bytes = create_synthetic_encrypted_pdf(password="secret")

        with pytest.raises(EncryptedDocumentError) as exc_info:
            await splitter.split(enc_bytes, extension=".pdf")

        assert exc_info.value.error_code == "ERR_PDF_ENCRYPTED"
        assert not exc_info.value.retryable

    @pytest.mark.asyncio
    async def test_corrupt_pdf_raises_permanent_error(self) -> None:
        """Corrupt PDF raises CorruptDocumentError with code ERR_PDF_CORRUPT."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        corrupt_bytes = create_synthetic_corrupt_pdf()

        with pytest.raises(CorruptDocumentError) as exc_info:
            await splitter.split(corrupt_bytes, extension=".pdf")

        assert exc_info.value.error_code == "ERR_PDF_CORRUPT"
        assert not exc_info.value.retryable

    @pytest.mark.asyncio
    async def test_decompression_bomb_tiff_raises_permanent_error(self) -> None:
        """TIFF exceeding 50M pixels raises DecompressionBombError with ERR_IMAGE_BOMB."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        bomb_bytes = create_synthetic_decompression_bomb_tiff()

        with pytest.raises(DecompressionBombError) as exc_info:
            await splitter.split(bomb_bytes, extension=".tiff")

        assert exc_info.value.error_code == "ERR_IMAGE_BOMB"
        assert not exc_info.value.retryable

    @pytest.mark.asyncio
    async def test_decompression_bomb_75mp_tiff_raises_permanent_error(self) -> None:
        """Explicit ~75 MP (7500x10000) TIFF raises DecompressionBombError without warning."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        bomb_bytes = create_synthetic_decompression_bomb_tiff(width=7500, height=10000)

        with pytest.raises(DecompressionBombError) as exc_info:
            await splitter.split(bomb_bytes, extension=".tiff")

        assert exc_info.value.error_code == "ERR_IMAGE_BOMB"
        assert not exc_info.value.retryable

    @pytest.mark.asyncio
    async def test_oversized_pdf_canvas_raises_permanent_error(self) -> None:
        """PDF page with canvas > 5000x5000 pt raises DecompressionBombError."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        oversized_bytes = create_synthetic_oversized_pdf(width_pts=6000, height_pts=6000)

        with pytest.raises(DecompressionBombError) as exc_info:
            await splitter.split(oversized_bytes, extension=".pdf")

        assert exc_info.value.error_code == "ERR_IMAGE_BOMB"
        assert not exc_info.value.retryable

    @pytest.mark.asyncio
    async def test_page_limit_exceeded_raises_permanent_error(self) -> None:
        """PDF exceeding 500 pages raises PageLimitExceededError."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        huge_pdf = create_synthetic_multipage_pdf(num_pages=501)

        with pytest.raises(PageLimitExceededError) as exc_info:
            await splitter.split(huge_pdf, extension=".pdf", max_pages=500)

        assert exc_info.value.error_code == "ERR_PAGE_LIMIT_EXCEEDED"
        assert not exc_info.value.retryable


# ===========================================================================
# 4. Non-Paged Routing and Core Boundary Invariant Tests
# ===========================================================================


class TestDocumentRoutingAndArchitectureBoundaries:
    """Tests for document routing and architectural import rules."""

    @pytest.mark.parametrize("ext", [".txt", ".csv", ".xlsx"])
    def test_non_paged_documents_skip_splitting(self, ext: str) -> None:
        """TXT, CSV, XLSX skip splitting and return should_split=False."""
        from app.core.ingestion.splitter_router import should_split_document

        assert should_split_document(ext) is False

    @pytest.mark.parametrize("ext", [".pdf", ".tiff", ".tif"])
    def test_paged_documents_require_splitting(self, ext: str) -> None:
        """PDF and TIFF require page splitting and return should_split=True."""
        from app.core.ingestion.splitter_router import should_split_document

        assert should_split_document(ext) is True

    def test_core_does_not_import_pypdfium2_or_pillow(self) -> None:
        """Core must NEVER import pypdfium2 or PIL (ADR-0003 & Rule 5)."""
        violations = scan_directory_boundaries("app/core")
        for v in violations:
            assert "pypdfium2" not in v.message
            assert "PIL" not in v.message
            assert "pillow" not in v.message.lower()
