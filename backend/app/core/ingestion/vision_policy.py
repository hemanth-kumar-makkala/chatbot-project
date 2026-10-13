"""Domain policy for upload-time vision transcription, gating, and reading selection.

Pure Python logic governing Invariant 5 and Invariant 6:
- Triggers vision transcription on scanned low-OCR or image-heavy pages.
- Enforces Option A review recovery: successful vision reading recovers the page,
  clearing the low_ocr_confidence review flag.
- Safe defaults: missing or low vision confidence (< 0.70) or empty text flags page.
- Selects readings for chunking: vision replaces low-confidence OCR (< 0.70);
  when native text layer / high-confidence OCR is present, both are indexed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

from app.ports.types import PageReading, PageType, ReviewReason


@dataclass(frozen=True)
class VisionReadingOutcome:
    """Outcome of vision reading evaluation for page review and indexing status."""

    needs_review: bool
    review_reason: ReviewReason | None
    is_indexable: bool


@dataclass(frozen=True)
class VisionReadingSelection:
    """A selected reading designated for chunking with its evidence classification."""

    evidence_type: str  # "text" or "visual"
    text: str
    page_reading_id: UUID | None


CLEARABLE_REVIEW_REASONS: frozenset[ReviewReason] = frozenset(
    {"processing_failed", "vision_failed", "image_too_large", "low_ocr_confidence"}
)


def should_trigger_vision_read(
    page_type: PageType,
    ocr_score: float | None = None,
    has_text_layer: bool = False,
    threshold: float = 0.70,
) -> bool:
    """Determine whether an individual page requires upload-time vision transcription.

    Reuses exact review policy thresholds:
    - IMAGE_HEAVY: always triggers vision transcription.
    - SCANNED: triggers if OCR confidence is < 0.70, None, or NaN.
    - DIGITAL_TEXT and BLANK: never trigger vision read.
    """
    _ = has_text_layer
    if not isinstance(page_type, PageType):
        raise ValueError(f"Invalid page_type: {page_type!r}. Must be PageType enum.")

    if page_type == PageType.IMAGE_HEAVY:
        return True

    if page_type == PageType.SCANNED:
        if ocr_score is None or math.isnan(ocr_score):
            return True
        return ocr_score < threshold

    if page_type in (PageType.DIGITAL_TEXT, PageType.BLANK):
        return False

    return False


def evaluate_vision_reading_outcome(
    page_type: PageType,
    vision_reading: PageReading | None,
    prior_needs_review: bool = False,
    prior_review_reason: ReviewReason | None = None,
    threshold: float = 0.70,
    error_code: str | None = None,
) -> VisionReadingOutcome:
    """Evaluate page review and indexability state after upload-time vision reading.

    Option A decision:
    - Successful vision reading (non-empty text, confidence >= 0.70) recovers the page,
      clearing the 'low_ocr_confidence' flag and making the page indexable as visual evidence.
    - Vision may ONLY clear flags it is responsible for ('low_ocr_confidence').
    - Failed vision reading, missing confidence, low confidence, or empty text flags page.
    """
    if not isinstance(page_type, PageType):
        raise ValueError(f"Invalid page_type: {page_type!r}. Must be PageType enum.")

    _ = error_code

    if vision_reading is None:
        return VisionReadingOutcome(
            needs_review=True,
            review_reason="vision_failed",
            is_indexable=False,
        )

    # Validate confidence
    confidence = vision_reading.confidence
    if confidence is None or math.isnan(confidence):
        return VisionReadingOutcome(
            needs_review=True,
            review_reason="vision_confidence_missing",
            is_indexable=False,
        )

    if confidence < threshold:
        return VisionReadingOutcome(
            needs_review=True,
            review_reason="vision_low_confidence",
            is_indexable=False,
        )

    # Validate text content
    text = vision_reading.reading
    if not text or not text.strip():
        return VisionReadingOutcome(
            needs_review=True,
            review_reason="vision_failed",
            is_indexable=False,
        )

    # Successful reading: check if prior flag can be cleared
    if prior_needs_review:
        if prior_review_reason in CLEARABLE_REVIEW_REASONS:
            # Vision was responsible for recovering -> cleared!
            return VisionReadingOutcome(
                needs_review=False,
                review_reason=None,
                is_indexable=True,
            )
        # Page was flagged for an unrelated reason (e.g. malware, format) -> remain flagged
        return VisionReadingOutcome(
            needs_review=True,
            review_reason=prior_review_reason,
            is_indexable=False,
        )

    # No prior flag -> remains clean
    return VisionReadingOutcome(
        needs_review=False,
        review_reason=None,
        is_indexable=True,
    )


def select_readings_for_chunking(
    page_type: PageType,
    ocr_text: str | None = None,
    ocr_score: float | None = None,
    ocr_reading_id: UUID | None = None,
    vision_reading: str | None = None,
    vision_reading_id: UUID | None = None,
    threshold: float = 0.70,
) -> list[VisionReadingSelection]:
    """Select which readings feed chunking and vector embedding.

    Rules:
    - When OCR is high confidence (>= 0.70) or digital text, and vision ran:
      BOTH are indexed (text as 'text', vision as 'visual').
    - When OCR is low confidence (< 0.70, None, NaN):
      Vision reading replaces low-confidence OCR. Only the vision reading is indexed.
      Low-confidence OCR text is retained in page_readings but not chunked.
    - When vision did not run, reliable OCR/digital text is indexed as 'text'.
    """
    selections: list[VisionReadingSelection] = []

    has_reliable_ocr = bool(
        ocr_text
        and ocr_text.strip()
        and (
            page_type == PageType.DIGITAL_TEXT
            or (ocr_score is not None and not math.isnan(ocr_score) and ocr_score >= threshold)
        )
    )

    has_valid_vision = bool(vision_reading and vision_reading.strip())

    if has_reliable_ocr:
        # Reliable text layer: index as text
        selections.append(
            VisionReadingSelection(
                evidence_type="text",
                text=ocr_text.strip(),  # type: ignore[union-attr]
                page_reading_id=ocr_reading_id,
            )
        )
        # If vision also ran (e.g. image-heavy page with native text layer): also index as visual
        if has_valid_vision:
            selections.append(
                VisionReadingSelection(
                    evidence_type="visual",
                    text=vision_reading.strip(),  # type: ignore[union-attr]
                    page_reading_id=vision_reading_id,
                )
            )
    else:
        # Low-confidence or missing OCR: vision replaces low-confidence OCR
        if has_valid_vision:
            selections.append(
                VisionReadingSelection(
                    evidence_type="visual",
                    text=vision_reading.strip(),  # type: ignore[union-attr]
                    page_reading_id=vision_reading_id,
                )
            )

    return selections
