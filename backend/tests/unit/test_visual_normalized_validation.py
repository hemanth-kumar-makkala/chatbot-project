"""Unit tests for Task 4.2: Normalized Visual Value Validation (Node 8 validate).

Enforces:
- Visual validation compares question-time and upload-time readings using normalized values
  (digits, dates, IDs, amounts; prose may differ, IDs must not).
- Disagreement between question-time and upload-time readings flags the answer.
- Visual answers always carry the page thumbnail and evidence_type='visual' (Invariant 5).
"""

from __future__ import annotations

import pytest

from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    node_validate,
)
from app.core.normalization import (
    compare_normalized_values,
    normalize_amounts,
    normalize_dates,
    normalize_digits_and_numbers,
    normalize_ids,
)


class TestNormalizedVisualValueExtraction:
    """Unit tests for normalized value extraction helpers."""

    def test_normalize_digits_and_numbers(self) -> None:
        text1 = "Authorized Amoxicillin 500mg capsule with 0 refills for 10 days."
        text2 = "Prescription: amoxicillin 500 mg, count 0, duration 10 days."
        nums1 = normalize_digits_and_numbers(text1)
        nums2 = normalize_digits_and_numbers(text2)
        assert "500" in nums1
        assert "500" in nums2
        assert "0" in nums1
        assert "10" in nums1

    def test_normalize_dates_various_formats(self) -> None:
        assert normalize_dates("Auth Date: 2026-02-14") == {"2026-02-14"}
        assert normalize_dates("Auth Date: 02/14/2026") == {"2026-02-14"}
        assert normalize_dates("Auth Date: February 14, 2026") == {"2026-02-14"}
        assert normalize_dates("Auth Date: Feb 14, 2026") == {"2026-02-14"}

    def test_normalize_ids_mrn_phone_fax(self) -> None:
        t1 = "Patient Alex Morgan, MRN-4412-A, clinic phone (555) 014-5555"
        t2 = "Chart: MRN 4412 A, tel: 555-014-5555"
        ids1 = normalize_ids(t1)
        ids2 = normalize_ids(t2)
        assert "MRN-4412-A" in ids1
        assert "MRN-4412-A" in ids2
        assert "5550145555" in ids1
        assert "5550145555" in ids2

    def test_normalize_amounts_currency(self) -> None:
        t1 = "Deductible is $500.00 standard rate."
        t2 = "Deductible amount: $500 or 500 dollars."
        amt1 = normalize_amounts(t1)
        amt2 = normalize_amounts(t2)
        assert "500.00" in amt1
        assert "500.00" in amt2

    def test_compare_normalized_values_matching_prose_differs(self) -> None:
        """Prose may differ, but IDs, dates, numbers, and amounts must match."""
        reading_a = (
            "Alex Morgan (MRN-4412-A) was authorized Amoxicillin 500mg on 2026-02-14. "
            "Refills: 0. Tel: 555-0145. Fee: $25.00."
        )
        reading_b = (
            "Urgent refill request for MRN: MRN 4412 A. Medication is Amoxicillin 500 mg. "
            "Date of authorization: Feb 14, 2026. Refill count: 0. "
            "Phone: (555) 014-5000 is wrong, wait 555-0145. Copay $25."
        )
        res = compare_normalized_values(reading_a, reading_b)
        assert res.is_matching is True
        assert len(res.discrepancies) == 0

    def test_compare_normalized_values_id_disagreement(self) -> None:
        """Disagreement on critical ID flags visual comparison."""
        reading_a = "Patient Alex Morgan with MRN-4412-A."
        reading_b = "Patient Alex Morgan with MRN-4412-B."
        res = compare_normalized_values(reading_a, reading_b)
        assert res.is_matching is False
        assert any("MRN" in d for d in res.discrepancies)

    def test_compare_normalized_values_dosage_disagreement(self) -> None:
        """Disagreement on dosage number flags visual comparison."""
        reading_a = "Authorized Amoxicillin 500mg."
        reading_b = "Authorized Amoxicillin 250mg."
        res = compare_normalized_values(reading_a, reading_b)
        assert res.is_matching is False
        assert any("500" in d or "250" in d for d in res.discrepancies)


class MockRuntime:
    """Mock LangGraph runtime context for testing node_validate."""

    def __init__(self, context: GraphRuntimeContext) -> None:
        self.context = context


@pytest.mark.asyncio
class TestNode8VisualNormalizedValidation:
    """Tests for Node 8 validate under visual evidence conditions."""

    async def test_matching_visual_readings_pass_validation_with_thumbnail(self) -> None:
        """Matching normalized numbers pass validation and visual answers carry thumbnail."""
        ctx = GraphRuntimeContext(
            ports=GraphPorts(is_mock_profile=True),
            question_text="What is the refill medication dosage for Alex Morgan?",
            evidence_text={
                "page_1_ocr": "Alex Morgan MRN-4412-A Amoxicillin 500mg 2026-02-14 Refill: 0",
                "page_1_vision": (
                    "Patient Alex Morgan (MRN 4412 A) Refill approved for Amoxicillin 500 mg "
                    "on Feb 14, 2026. Quantity 0."
                ),
            },
            candidate_evidence_types={
                "page_1_ocr": "ocr",
                "page_1_vision": "visual",
            },
            page_images={"page_1_vision": b"fake_png_thumb_bytes"},  # pragma: allowlist secret
            generated_answer={
                "mode": "short",
                "answer": (
                    "Dr. Evelyn Reed authorized Amoxicillin 500mg for "
                    "patient Alex Morgan (MRN-4412-A)."
                ),
                "evidence_ids": ["page_1_vision"],
                "evidence_type": "visual",
            },
            response_text=(
                "Dr. Evelyn Reed authorized Amoxicillin 500mg for patient Alex Morgan (MRN-4412-A)."
            ),
        )
        runtime = MockRuntime(ctx)
        state: GraphState = {
            "evidence_ids": ["page_1_vision"],
            "answer_mode": "short",
            "needs_vision": True,
            "validation_attempts": 0,
        }

        result = await node_validate(state, runtime)  # type: ignore[arg-type]
        assert result.get("status") == "completed"
        # Invariant 5: Answer must carry visual evidence type and thumbnail reference
        assert ctx.generated_answer.get("evidence_type") == "visual"
        assert (
            ctx.generated_answer.get("thumbnail") is not None
            or ctx.generated_answer.get("has_thumbnail") is True
        )

    async def test_discrepant_visual_readings_flag_warning(self) -> None:
        """Disagreement between question-time and upload-time readings flags the answer."""
        ctx = GraphRuntimeContext(
            ports=GraphPorts(is_mock_profile=True),
            question_text="What is the refill medication dosage for Alex Morgan?",
            evidence_text={
                "page_1_ocr": "Alex Morgan MRN-4412-A Amoxicillin 250mg Refills: 0",
                "page_1_vision": "Alex Morgan MRN-4412-A Amoxicillin 500mg Refills: 0",
            },
            candidate_evidence_types={
                "page_1_ocr": "ocr",
                "page_1_vision": "visual",
            },
            page_images={"page_1_vision": b"fake_png_image_bytes"},  # pragma: allowlist secret
            generated_answer={
                "mode": "short",
                "answer": "Dr. Evelyn Reed authorized Amoxicillin 500mg for Alex Morgan.",
                "evidence_ids": ["page_1_vision"],
                "evidence_type": "visual",
            },
            response_text="Dr. Evelyn Reed authorized Amoxicillin 500mg for Alex Morgan.",
        )
        runtime = MockRuntime(ctx)
        state: GraphState = {
            "evidence_ids": ["page_1_vision"],
            "answer_mode": "short",
            "needs_vision": True,
            "validation_attempts": 0,
        }

        result = await node_validate(state, runtime)  # type: ignore[arg-type]
        # Disagreement flags answer: either visual_disagreement is set or validation warns
        assert (
            ctx.generated_answer.get("visual_disagreement") is True
            or ctx.generated_answer.get("visual_warning") is not None
            or result.get("status") in ("retry", "refusal")
            or "disagreement" in str(ctx.response_text).lower()
        )
