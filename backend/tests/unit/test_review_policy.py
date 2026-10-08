"""Unit tests for core ingestion review gating policy.

Runs in CI as part of core unit tests. Pure Python with zero model dependencies.
Covers all four PageType variants from Task 2.3 and error handling on unknown types.
"""

from __future__ import annotations

import pytest

from app.core.ingestion.review_policy import (
    evaluate_page_review,
    evaluate_version_review_status,
    is_page_indexable,
    should_flag_page_for_review,
)
from app.ports.types import PageType


class TestPageReviewPolicyAllTypes:
    """Validates review gating logic under all four page types and score edge cases."""

    # 1. SCANNED
    def test_scanned_nan_ocr_score_flags_review(self) -> None:
        """Scanned page with NaN ocr_score must be flagged for review."""
        flag, reason = evaluate_page_review(
            page_type=PageType.SCANNED,
            has_text_layer=False,
            ocr_score=float("nan"),
            threshold=0.70,
        )
        assert flag is True
        assert "nan" in reason.lower() or "unreadable" in reason.lower()
        assert should_flag_page_for_review(PageType.SCANNED, False, float("nan"), 0.70) is True

    def test_scanned_none_ocr_score_flags_review(self) -> None:
        """Scanned page with None ocr_score must be flagged for review."""
        flag, _ = evaluate_page_review(
            page_type=PageType.SCANNED,
            has_text_layer=False,
            ocr_score=None,
            threshold=0.70,
        )
        assert flag is True

    def test_scanned_below_threshold_flags_review(self) -> None:
        """Scanned page with score < threshold must be flagged for review."""
        flag, reason = evaluate_page_review(
            page_type=PageType.SCANNED,
            has_text_layer=False,
            ocr_score=0.69,
            threshold=0.70,
        )
        assert flag is True
        assert "below threshold" in reason.lower()

    def test_scanned_above_threshold_passes(self) -> None:
        """Scanned page with score >= threshold passes without review flag."""
        flag, reason = evaluate_page_review(
            page_type=PageType.SCANNED,
            has_text_layer=False,
            ocr_score=0.70,
            threshold=0.70,
        )
        assert flag is False
        assert reason == ""

    # 2. IMAGE_HEAVY without text layer (OCR expected)
    def test_image_heavy_without_text_layer_nan_flags_review(self) -> None:
        """Image-heavy page without text layer and NaN ocr_score must be flagged."""
        flag, reason = evaluate_page_review(
            page_type=PageType.IMAGE_HEAVY,
            has_text_layer=False,
            ocr_score=float("nan"),
            threshold=0.70,
        )
        assert flag is True
        assert "image-heavy" in reason.lower()

    def test_image_heavy_without_text_layer_none_flags_review(self) -> None:
        """Image-heavy page without text layer and None ocr_score must be flagged."""
        flag, _ = evaluate_page_review(
            page_type=PageType.IMAGE_HEAVY,
            has_text_layer=False,
            ocr_score=None,
            threshold=0.70,
        )
        assert flag is True

    def test_image_heavy_without_text_layer_below_threshold_flags_review(self) -> None:
        """Image-heavy page without text layer and score < threshold must be flagged."""
        flag, _ = evaluate_page_review(
            page_type=PageType.IMAGE_HEAVY,
            has_text_layer=False,
            ocr_score=0.45,
            threshold=0.70,
        )
        assert flag is True

    def test_image_heavy_without_text_layer_above_threshold_passes(self) -> None:
        """Image-heavy page without text layer and score >= threshold passes."""
        flag, _ = evaluate_page_review(
            page_type=PageType.IMAGE_HEAVY,
            has_text_layer=False,
            ocr_score=0.88,
            threshold=0.70,
        )
        assert flag is False

    # 3. IMAGE_HEAVY with text layer (Not flagged for missing OCR)
    def test_image_heavy_with_text_layer_not_flagged(self) -> None:
        """Image-heavy page with text layer is not flagged for missing/NaN OCR."""
        flag, reason = evaluate_page_review(
            page_type=PageType.IMAGE_HEAVY,
            has_text_layer=True,
            ocr_score=float("nan"),
            threshold=0.70,
        )
        assert flag is False
        assert reason == ""

    # 4. DIGITAL_TEXT (Not flagged for missing OCR)
    def test_digital_text_nan_not_flagged(self) -> None:
        """Digital text page with NaN ocr_score is not flagged."""
        flag, reason = evaluate_page_review(
            page_type=PageType.DIGITAL_TEXT,
            has_text_layer=True,
            ocr_score=float("nan"),
            threshold=0.70,
        )
        assert flag is False
        assert reason == ""

    def test_digital_text_none_not_flagged(self) -> None:
        """Digital text page with None ocr_score is not flagged."""
        flag, _ = evaluate_page_review(
            page_type=PageType.DIGITAL_TEXT,
            has_text_layer=True,
            ocr_score=None,
            threshold=0.70,
        )
        assert flag is False

    # 5. BLANK (Never flagged, never indexed)
    def test_blank_page_never_flagged(self) -> None:
        """Blank page is never flagged for review."""
        flag, reason = evaluate_page_review(
            page_type=PageType.BLANK,
            has_text_layer=False,
            ocr_score=None,
            threshold=0.70,
        )
        assert flag is False
        assert reason == ""

    def test_blank_page_never_indexed(self) -> None:
        """Blank page is never indexed, even if needs_review is False."""
        assert is_page_indexable(PageType.BLANK, needs_review=False) is False
        assert is_page_indexable(PageType.BLANK, needs_review=True) is False

    def test_non_blank_page_indexability_respects_review_flag(self) -> None:
        """Non-blank pages are indexed only when needs_review is False (Invariant 6)."""
        assert is_page_indexable(PageType.DIGITAL_TEXT, needs_review=False) is True
        assert is_page_indexable(PageType.DIGITAL_TEXT, needs_review=True) is False
        assert is_page_indexable(PageType.SCANNED, needs_review=False) is True
        assert is_page_indexable(PageType.SCANNED, needs_review=True) is False

    # 6. TYPE GUARDS (Reject str and unknown types)
    def test_string_page_type_raises_value_error(self) -> None:
        """Passing a string instead of PageType enum raises ValueError."""
        with pytest.raises(ValueError, match="Must be an instance of PageType enum"):
            evaluate_page_review(page_type="scanned")  # type: ignore[arg-type]

    def test_invalid_type_raises_value_error(self) -> None:
        """Passing an invalid object raises ValueError."""
        with pytest.raises(ValueError, match="Must be an instance of PageType enum"):
            evaluate_page_review(page_type=123)  # type: ignore[arg-type]

    def test_indexable_invalid_type_raises_value_error(self) -> None:
        """Passing an invalid object to is_page_indexable raises ValueError."""
        with pytest.raises(ValueError, match="Must be an instance of PageType enum"):
            is_page_indexable(page_type="blank", needs_review=False)  # type: ignore[arg-type]

    # 7. VERSION STATUS
    def test_one_bad_page_flags_entire_version(self) -> None:
        """If any page is flagged, document version is flagged as needs_review."""
        assert evaluate_version_review_status([False, True, False]) is True
        assert evaluate_version_review_status([False, False, False]) is False
