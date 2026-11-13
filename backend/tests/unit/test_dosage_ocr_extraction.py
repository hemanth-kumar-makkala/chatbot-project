"""Unit tests for Stage H: Dosage OCR extraction and multi-line safety.

Verifies:
1. OCR field extraction for 'dosage' takes value strictly from the line labeled 'Dosage:'.
2. An empty 'Dosage:' label does not capture neighbouring lines (e.g., Refill count or Patient).
3. All field patterns in extraction subgraph do not overflow into neighbouring lines when empty.
4. Correct extraction against skewed fax layout.
"""

from __future__ import annotations

from app.core.extraction.subgraph import extract_fields_from_ocr_text


def test_dosage_extracted_strictly_from_labeled_line() -> None:
    """Dosage takes the value from the line labeled 'Dosage:'."""
    ocr_text = (
        "CLINICAL INTAKE FORM\n"
        "Authorized Medication: Amoxicillin 500mg\n"
        "Dosage: Take 1 capsule three times daily for 10 days\n"
        "Refill count: 0\n"
    )
    fields, confidences = extract_fields_from_ocr_text(ocr_text)
    assert fields.get("dosage") == "Take 1 capsule three times daily for 10 days"
    assert confidences.get("dosage", 0.0) >= 0.90


def test_empty_dosage_label_never_captures_neighbouring_line() -> None:
    """An empty 'Dosage:' label does not capture the neighbouring line below it."""
    ocr_text = (
        "CLINICAL INTAKE FORM\n"
        "Authorized Medication: Amoxicillin 500mg\n"
        "Dosage:\n"
        "Refill count: 0\n"
        "Patient Name: Alex Morgan\n"
    )
    fields, confidences = extract_fields_from_ocr_text(ocr_text)
    assert "dosage" not in fields or fields["dosage"] == ""
    assert fields.get("dosage") != "Refill count: 0"
    assert fields.get("refill_count") == "0"


def test_field_patterns_do_not_overflow_when_label_empty() -> None:
    """Empty field labels do not capture subsequent lines as their values."""
    ocr_text = (
        "Patient Name:\n"
        "Authorized Medication:\n"
        "Authorization Date:\n"
        "Dosage:\n"
        "Refill count:\n"
        "Clinic Phone: 555-0145\n"
    )
    fields, _ = extract_fields_from_ocr_text(ocr_text)
    assert "Authorized Medication" not in str(fields.get("patient_name", ""))
    assert "Authorization Date" not in str(fields.get("authorized_medication", ""))
    assert "Dosage" not in str(fields.get("authorization_date", ""))
    assert "Refill count" not in str(fields.get("dosage", ""))
    assert fields.get("clinic_phone") == "555-0145"


def test_extraction_with_skewed_fax_layout() -> None:
    """Verify correct field extraction against the skewed fax layout."""
    ocr_text = (
        "Date: 2026-02-14\n"
        "Attn: Pharmacy Dispensing Coordinator\n"
        "Clinic Phone: 555-0145\n"
        "\n"
        "Prescription Refill Authorization:\n"
        "Urgent prescription refill request approved for Amoxicillin 5oOmg by Dr. Evelyn Reed.\n"
        "Dosage: Take 1 capsule by mouth three times daily for 10 days.\n"
        "Patient: Alex Morgan (Chart #MRN-4412-A)\n"
        "Refill count: 0 (one-time course).\n"
    )
    fields, confidences = extract_fields_from_ocr_text(ocr_text)
    assert fields.get("dosage") == "Take 1 capsule by mouth three times daily for 10 days"
    assert fields.get("patient_name") == "Alex Morgan"
    assert fields.get("chart_number") == "MRN-4412-A"
    assert "Amoxicillin 5oOmg" in str(fields.get("authorized_medication", ""))
    assert fields.get("refill_count") == "0"
