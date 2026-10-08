"""Domain policy for evaluating document page review gating.

Pure Python logic governing Invariant 6:
- Pages below the confidence threshold or with unreadable/missing OCR on scanned
  or image-heavy pages without text layer are gated for human review and excluded from indexing.
- Image-heavy pages with text layer and digital text pages are not flagged for missing OCR.
- Blank pages are never flagged and never indexed.
- Any unknown page type raises an error, never passing silently.

Enforces:
- AGENTS.md Build Rule 4: Zero vendor names inside core.
"""

from __future__ import annotations

import math

from app.ports.types import PageType


def evaluate_page_review(
    page_type: PageType,
    has_text_layer: bool = False,
    ocr_score: float | None = None,
    threshold: float = 0.70,
) -> tuple[bool, str]:
    """Evaluate whether an individual page requires manual review.

    Args:
        page_type: PageType enum strictly. Strings or unknown types raise ValueError.
        has_text_layer: True if the page has an underlying text layer.
        ocr_score: Confidence score from OCR (0.0 to 1.0, NaN, or None).
        threshold: Minimum acceptable confidence score (default 0.70).

    Returns:
        tuple[bool, str]: (needs_review, reason)

    Raises:
        ValueError: If page_type is not a valid PageType enum member.
    """
    if not isinstance(page_type, PageType):
        raise ValueError(f"Invalid page_type: {page_type!r}. Must be an instance of PageType enum.")

    if page_type == PageType.SCANNED:
        if ocr_score is None or math.isnan(ocr_score):
            return True, "Scanned page has unreadable/unset OCR score (NaN)"
        if ocr_score < threshold:
            return (
                True,
                f"Scanned page OCR score {ocr_score:.2f} is below threshold {threshold:.2f}",
            )
        return False, ""

    if page_type == PageType.IMAGE_HEAVY:
        if not has_text_layer:
            if ocr_score is None or math.isnan(ocr_score):
                return (
                    True,
                    "Image-heavy page without text layer has unreadable/unset OCR score (NaN)",
                )
            if ocr_score < threshold:
                return (
                    True,
                    f"Image-heavy page score {ocr_score:.2f} is below threshold {threshold:.2f}",
                )
            return False, ""
        # Image-heavy page with native text layer is not flagged for missing OCR
        return False, ""

    if page_type == PageType.DIGITAL_TEXT:
        # Digital text has native text layer; not flagged for missing OCR
        return False, ""

    if page_type == PageType.BLANK:
        # Blank pages are never flagged for review
        return False, ""

    # Exhaustive guard
    raise ValueError(f"Unhandled page_type: {page_type!r}")


def should_flag_page_for_review(
    page_type: PageType,
    has_text_layer: bool = False,
    ocr_score: float | None = None,
    threshold: float = 0.70,
) -> bool:
    """Return True if the page must be gated for human review."""
    flag, _ = evaluate_page_review(
        page_type=page_type,
        has_text_layer=has_text_layer,
        ocr_score=ocr_score,
        threshold=threshold,
    )
    return flag


def is_page_indexable(page_type: PageType, needs_review: bool) -> bool:
    """Determine whether a page is eligible for chunk indexing.

    Rules:
    - Blank pages are never indexed.
    - Pages flagged for review (needs_review=True) are excluded from indexing (Invariant 6).
    """
    if not isinstance(page_type, PageType):
        raise ValueError(f"Invalid page_type: {page_type!r}. Must be an instance of PageType enum.")
    if page_type == PageType.BLANK:
        return False
    return not needs_review


def evaluate_version_review_status(page_review_flags: list[bool]) -> bool:
    """Evaluate overall document version status from individual page flags.

    A document version transitions to 'needs_review' if ANY page is flagged.
    """
    return any(page_review_flags)
