"""Unit tests for upload-time vision policy and reading selection (Task 2.5).

Tests:
1. Trigger rules: which pages trigger vision transcription.
2. Flag states and review reason transitions (Option A recovery vs flagging).
3. Reading selection for chunking: vision replaces low-confidence OCR;
   both are indexed when text layer/OCR is reliable.
4. Safe defaults: missing or low vision confidence flags page.
"""

from __future__ import annotations

from uuid import uuid4

from app.core.ingestion.vision_policy import (
    evaluate_vision_reading_outcome,
    select_readings_for_chunking,
    should_trigger_vision_read,
)
from app.ports.types import PageReading, PageType


class TestVisionTriggerRules:
    """Verifies which pages trigger an upload-time vision read."""

    def test_scanned_low_ocr_triggers_vision(self) -> None:
        """Scanned page with OCR score < 0.70 triggers vision read."""
        assert (
            should_trigger_vision_read(
                page_type=PageType.SCANNED,
                ocr_score=0.69,
                has_text_layer=False,
            )
            is True
        )

    def test_scanned_none_or_nan_ocr_triggers_vision(self) -> None:
        """Scanned page with None or NaN OCR score triggers vision read."""
        assert (
            should_trigger_vision_read(
                page_type=PageType.SCANNED,
                ocr_score=None,
                has_text_layer=False,
            )
            is True
        )
        assert (
            should_trigger_vision_read(
                page_type=PageType.SCANNED,
                ocr_score=float("nan"),
                has_text_layer=False,
            )
            is True
        )

    def test_scanned_high_ocr_skips_vision(self) -> None:
        """Scanned page with OCR score >= 0.70 skips vision read."""
        assert (
            should_trigger_vision_read(
                page_type=PageType.SCANNED,
                ocr_score=0.70,
                has_text_layer=False,
            )
            is False
        )
        assert (
            should_trigger_vision_read(
                page_type=PageType.SCANNED,
                ocr_score=0.95,
                has_text_layer=False,
            )
            is False
        )

    def test_image_heavy_always_triggers_vision(self) -> None:
        """Image-heavy pages always trigger vision read regardless of text layer or OCR."""
        assert (
            should_trigger_vision_read(
                page_type=PageType.IMAGE_HEAVY,
                ocr_score=None,
                has_text_layer=False,
            )
            is True
        )
        assert (
            should_trigger_vision_read(
                page_type=PageType.IMAGE_HEAVY,
                ocr_score=0.99,
                has_text_layer=True,
            )
            is True
        )

    def test_digital_text_and_blank_skip_vision(self) -> None:
        """Digital text and blank pages never trigger vision read."""
        assert (
            should_trigger_vision_read(
                page_type=PageType.DIGITAL_TEXT,
                ocr_score=None,
                has_text_layer=True,
            )
            is False
        )
        assert (
            should_trigger_vision_read(
                page_type=PageType.BLANK,
                ocr_score=None,
                has_text_layer=False,
            )
            is False
        )


class TestVisionReviewFlagTransitions:
    """Verifies exact page state transitions before and after vision reading (Option A)."""

    def test_successful_vision_clears_low_ocr_flag(self) -> None:
        """Successful vision read clears a low_ocr_confidence flag, making page indexable."""
        reading = PageReading(
            reading="Recovered scanned text content.",
            confidence=0.95,
            model_id="mock-vision",
            model_version="1.0",
        )
        outcome = evaluate_vision_reading_outcome(
            page_type=PageType.SCANNED,
            vision_reading=reading,
            prior_needs_review=True,
            prior_review_reason="low_ocr_confidence",
        )
        assert outcome.needs_review is False
        assert outcome.review_reason is None
        assert outcome.is_indexable is True

    def test_missing_confidence_flags_page(self) -> None:
        """If vision confidence is None or missing, page is flagged despite non-empty text."""
        reading = PageReading(
            reading="Some text but missing confidence",
            confidence=None,
            model_id="mock-vision",
            model_version="1.0",
        )
        outcome = evaluate_vision_reading_outcome(
            page_type=PageType.SCANNED,
            vision_reading=reading,
            prior_needs_review=True,
            prior_review_reason="low_ocr_confidence",
        )
        assert outcome.needs_review is True
        assert outcome.review_reason == "vision_confidence_missing"
        assert outcome.is_indexable is False

    def test_low_vision_confidence_flags_page(self) -> None:
        """If vision confidence is below threshold (< 0.70), page is flagged."""
        reading = PageReading(
            reading="Low confidence visual reading",
            confidence=0.65,
            model_id="mock-vision",
            model_version="1.0",
        )
        outcome = evaluate_vision_reading_outcome(
            page_type=PageType.SCANNED,
            vision_reading=reading,
            prior_needs_review=True,
            prior_review_reason="low_ocr_confidence",
        )
        assert outcome.needs_review is True
        assert outcome.review_reason == "vision_low_confidence"
        assert outcome.is_indexable is False

    def test_empty_vision_reading_flags_page(self) -> None:
        """If vision text is empty or blank, page is flagged."""
        reading = PageReading(
            reading="   \n  ",
            confidence=0.99,
            model_id="mock-vision",
            model_version="1.0",
        )
        outcome = evaluate_vision_reading_outcome(
            page_type=PageType.SCANNED,
            vision_reading=reading,
            prior_needs_review=True,
            prior_review_reason="low_ocr_confidence",
        )
        assert outcome.needs_review is True
        assert outcome.review_reason == "vision_failed"
        assert outcome.is_indexable is False

    def test_permanent_vision_error_flags_page(self) -> None:
        """Permanent vision failure flags page with vision_failed reason."""
        outcome = evaluate_vision_reading_outcome(
            page_type=PageType.SCANNED,
            vision_reading=None,
            prior_needs_review=True,
            prior_review_reason="low_ocr_confidence",
            error_code="VISION_PERMANENT_FAILURE",
        )
        assert outcome.needs_review is True
        assert outcome.review_reason == "vision_failed"
        assert outcome.is_indexable is False

    def test_vision_does_not_clear_unrelated_review_flag(self) -> None:
        """Vision reading may only clear flags it is responsible for (low_ocr_confidence)."""
        reading = PageReading(
            reading="Legitimate vision reading",
            confidence=0.99,
            model_id="mock-vision",
            model_version="1.0",
        )
        outcome = evaluate_vision_reading_outcome(
            page_type=PageType.SCANNED,
            vision_reading=reading,
            prior_needs_review=True,
            prior_review_reason="malware_quarantine",
        )
        assert outcome.needs_review is True
        assert outcome.review_reason == "malware_quarantine"

    def test_vision_does_not_clear_legacy_unspecified_flag(self) -> None:
        """Vision reading must not clear legacy_unspecified review flag."""
        reading = PageReading(
            reading="Legitimate vision reading",
            confidence=0.99,
            model_id="mock-vision",
            model_version="1.0",
        )
        outcome = evaluate_vision_reading_outcome(
            page_type=PageType.SCANNED,
            vision_reading=reading,
            prior_needs_review=True,
            prior_review_reason="legacy_unspecified",
        )
        assert outcome.needs_review is True
        assert outcome.review_reason == "legacy_unspecified"
        assert outcome.is_indexable is False


class TestReadingSelectionForChunking:
    """Verifies which readings feed chunking and indexing."""

    def test_vision_replaces_low_confidence_ocr(self) -> None:
        """When OCR score < 0.70, only vision reading is indexed."""
        vision_id = uuid4()
        selected = select_readings_for_chunking(
            page_type=PageType.SCANNED,
            ocr_text="Unreliable low confidence OCR garble",
            ocr_score=0.45,
            vision_reading="Clean vision transcription text",
            vision_reading_id=vision_id,
        )
        assert len(selected) == 1
        assert selected[0].evidence_type == "visual"
        assert selected[0].text == "Clean vision transcription text"
        assert selected[0].page_reading_id == vision_id

    def test_both_readings_indexed_when_text_layer_is_reliable(self) -> None:
        """Case 3: When OCR >= 0.70 or reliable text layer exists, BOTH are indexed."""
        vision_id = uuid4()
        ocr_reading_id = uuid4()
        selected = select_readings_for_chunking(
            page_type=PageType.IMAGE_HEAVY,
            ocr_text="Reliable digital text from underlying layer",
            ocr_score=0.95,
            ocr_reading_id=ocr_reading_id,
            vision_reading="Diagram labels and handwritten annotation",
            vision_reading_id=vision_id,
        )
        assert len(selected) == 2
        # Text reading indexed as text
        assert selected[0].evidence_type == "text"
        assert selected[0].text == "Reliable digital text from underlying layer"
        assert selected[0].page_reading_id == ocr_reading_id
        # Vision reading indexed as visual
        assert selected[1].evidence_type == "visual"
        assert selected[1].text == "Diagram labels and handwritten annotation"
        assert selected[1].page_reading_id == vision_id

    def test_ocr_only_indexed_when_no_vision(self) -> None:
        """When vision does not run, reliable OCR/digital text is indexed as text."""
        ocr_reading_id = uuid4()
        selected = select_readings_for_chunking(
            page_type=PageType.DIGITAL_TEXT,
            ocr_text="Standard digital document body text",
            ocr_score=1.0,
            ocr_reading_id=ocr_reading_id,
            vision_reading=None,
            vision_reading_id=None,
        )
        assert len(selected) == 1
        assert selected[0].evidence_type == "text"
        assert selected[0].text == "Standard digital document body text"
        assert selected[0].page_reading_id == ocr_reading_id
