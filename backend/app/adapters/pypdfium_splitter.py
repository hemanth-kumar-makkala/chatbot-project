"""PyPdfium document splitter adapter (Task 2.3).

Isolates pypdfium2 and Pillow vendor libraries behind the DocumentSplitter port.
Renders pages at 150 DPI and enforces safety caps:
- Image.MAX_IMAGE_PIXELS = 50,000,000.
- Rejects PDF canvases > 5000x5000 pt.
- Rejects documents exceeding configurable max_pages (default 500).
- Thread safety: Process-wide threading.Lock protects all pypdfium2 calls.
"""

from __future__ import annotations

import hashlib
import io
import threading
import warnings
from typing import TYPE_CHECKING

import pypdfium2  # type: ignore[import-untyped]
from PIL import Image, ImageSequence

from app.ports.document_splitter import DocumentSplitter
from app.ports.errors import (
    CorruptDocumentError,
    DecompressionBombError,
    EncryptedDocumentError,
    PageLimitExceededError,
)
from app.ports.types import PageArtifact, PageType

if TYPE_CHECKING:
    pass

# Cap decompressed pixel allocation to protect against image bombs (~50 MP)
Image.MAX_IMAGE_PIXELS = 50_000_000
MAX_PDF_DIMENSION_PTS: float = 5000.0
TARGET_RENDER_DPI: int = 150
PDF_BASE_DPI: int = 72
RENDER_SCALE: float = TARGET_RENDER_DPI / PDF_BASE_DPI

# Process-wide lock: PDFium C++ library context is not thread-safe across threads
_PDFIUM_LOCK = threading.Lock()


def _classify_metrics(char_count: int, image_area_ratio: float) -> tuple[PageType, bool]:
    """Pure classification mapping according to approved MECE decision table."""
    if char_count == 0:
        if image_area_ratio < 0.05:
            return PageType.BLANK, False
        if image_area_ratio < 0.70:
            return PageType.IMAGE_HEAVY, False
        return PageType.SCANNED, False

    if image_area_ratio >= 0.70:
        return PageType.SCANNED, True
    if image_area_ratio >= 0.30:
        return PageType.IMAGE_HEAVY, True
    return PageType.DIGITAL_TEXT, True


class PyPdfiumDocumentSplitter(DocumentSplitter):
    """Adapter splitting multi-page PDFs and TIFFs using pypdfium2 and Pillow."""

    async def split(
        self,
        content: bytes,
        extension: str,
        max_pages: int = 500,
    ) -> list[PageArtifact]:
        """Split document into classified page artifacts with 150 DPI rendered PNGs."""
        clean_ext = extension.lower().strip()
        if not clean_ext.startswith("."):
            clean_ext = f".{clean_ext}"

        if clean_ext == ".pdf":
            return self._split_pdf(content, max_pages=max_pages)
        if clean_ext in {".tiff", ".tif"}:
            return self._split_tiff(content, max_pages=max_pages)

        raise CorruptDocumentError(f"Unsupported file extension for splitting: {clean_ext}")

    def _split_pdf(self, content: bytes, max_pages: int) -> list[PageArtifact]:
        """Open, preflight, split, classify, and render PDF pages under process lock."""
        with _PDFIUM_LOCK:
            try:
                doc = pypdfium2.PdfDocument(content)
            except pypdfium2.PdfiumError as exc:
                err_str = str(exc).lower()
                err_code = getattr(exc, "raw_code", getattr(exc, "err_code", None))
                if err_code == 4 or "password" in err_str:
                    raise EncryptedDocumentError(
                        "PDF document is password protected or encrypted"
                    ) from exc
                raise CorruptDocumentError(
                    f"PDF document is corrupt or cannot be parsed: {exc}"
                ) from exc
            except Exception as exc:
                raise CorruptDocumentError(f"Unexpected error opening PDF: {exc}") from exc

            try:
                # 1. Page count check
                page_count = len(doc)
                if page_count > max_pages:
                    raise PageLimitExceededError(
                        f"Document contains {page_count} pages, exceeding the limit of {max_pages}"
                    )

                # 2. Preflight dimension check of every page
                for i in range(page_count):
                    page = doc[i]
                    try:
                        pw, ph = page.get_size()
                    except Exception as exc:
                        raise CorruptDocumentError(
                            f"Corrupt page stream at page {i + 1}: {exc}"
                        ) from exc

                    if pw > MAX_PDF_DIMENSION_PTS or ph > MAX_PDF_DIMENSION_PTS:
                        raise DecompressionBombError(
                            f"Page {i + 1} dimensions ({pw}x{ph} pt) exceed safe limit of 5000 pt"
                        )
                    rendered_pixels = (pw * RENDER_SCALE) * (ph * RENDER_SCALE)
                    if rendered_pixels > Image.MAX_IMAGE_PIXELS:
                        raise DecompressionBombError(
                            f"Page {i + 1} render size ({int(rendered_pixels)} px) exceeds cap"
                        )

                # 3. Extraction, classification, and rendering
                artifacts: list[PageArtifact] = []
                for i in range(page_count):
                    page = doc[i]
                    pw, ph = page.get_size()
                    total_page_area = max(pw * ph, 1.0)

                    # Text extraction
                    textpage = page.get_textpage()
                    raw_text = textpage.get_text_range().strip()
                    char_count = len(raw_text)

                    # Image area ratio calculation
                    img_area = 0.0
                    for obj in page.get_objects():
                        if isinstance(obj, pypdfium2.PdfImage):
                            left, b, right, t = obj.get_bounds()
                            img_area += abs(right - left) * abs(t - b)
                    image_area_ratio = min(img_area / total_page_area, 1.0)

                    # Classification
                    page_type, has_text_layer = _classify_metrics(char_count, image_area_ratio)

                    # Render to 150 DPI bitmap and encode as PNG
                    bitmap = page.render(scale=RENDER_SCALE)
                    pil_img = bitmap.to_pil()
                    img_buf = io.BytesIO()
                    pil_img.save(img_buf, format="PNG")
                    img_bytes = img_buf.getvalue()
                    page_sha = hashlib.sha256(img_bytes).hexdigest()

                    artifacts.append(
                        PageArtifact(
                            page_number=i + 1,
                            image_bytes=img_bytes,
                            mime_type="image/png",
                            page_sha256=page_sha,
                            page_type=page_type,
                            char_count=char_count,
                            has_text_layer=has_text_layer,
                            image_area_ratio=round(image_area_ratio, 4),
                            width_pts=pw,
                            height_pts=ph,
                        )
                    )

                return artifacts
            finally:
                doc.close()

    def _split_tiff(self, content: bytes, max_pages: int) -> list[PageArtifact]:
        """Open, preflight, and render TIFF frames using Pillow."""
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("error", category=Image.DecompressionBombWarning)
                img = Image.open(io.BytesIO(content))
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise DecompressionBombError(
                f"TIFF file exceeds safety limit of 50M pixels: {exc}"
            ) from exc
        except Exception as exc:
            raise CorruptDocumentError(f"Corrupt TIFF image: {exc}") from exc

        # 1. Frame count preflight check
        frame_count: int = int(getattr(img, "n_frames", 1))
        if frame_count > max_pages:
            raise PageLimitExceededError(
                f"TIFF document contains {frame_count} frames, exceeding limit of {max_pages}"
            )

        artifacts: list[PageArtifact] = []
        for i, frame in enumerate(ImageSequence.Iterator(img)):
            frame_pixels = frame.width * frame.height
            if Image.MAX_IMAGE_PIXELS and frame_pixels > Image.MAX_IMAGE_PIXELS:
                raise DecompressionBombError(
                    f"TIFF frame {i + 1} has {frame_pixels} pixels, exceeding 50M pixel limit"
                )

            # Convert to standard RGB and encode to PNG
            rgb_frame = frame.convert("RGB")
            buf = io.BytesIO()
            rgb_frame.save(buf, format="PNG")
            img_bytes = buf.getvalue()
            page_sha = hashlib.sha256(img_bytes).hexdigest()

            artifacts.append(
                PageArtifact(
                    page_number=i + 1,
                    image_bytes=img_bytes,
                    mime_type="image/png",
                    page_sha256=page_sha,
                    page_type=PageType.SCANNED,
                    char_count=0,
                    has_text_layer=False,
                    image_area_ratio=1.0,
                    width_pts=float(frame.width),
                    height_pts=float(frame.height),
                )
            )

        return artifacts
