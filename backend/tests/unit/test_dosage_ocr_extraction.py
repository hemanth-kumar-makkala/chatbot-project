"""Unit tests for Stage H: Dosage OCR extraction and multi-line safety.

Verifies:
1. OCR field extraction for 'dosage' takes value strictly from the line labeled 'Dosage:'.
2. An empty 'Dosage:' label does not capture neighbouring lines (e.g., Refill count or Patient).
3. All field patterns in extraction subgraph do not overflow into neighbouring lines when empty.
4. Correct extraction against skewed fax layout.
"""

from __future__ import annotations

from app.core.extraction.subgraph import (
    extract_fields_from_ocr_text,
    parse_vision_json_reading,
)


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


def test_dosage_extraction_with_scrambled_line_order_from_real_skewed_fax_ocr() -> None:
    """Verify that scrambled OCR line order does not cause dosage to capture neighbouring lines.

    In skewed_fax.pdf Docling OCR, 'Take 1 capsule...' appears before 'Dosage:', and 'Dosage:' is
    immediately followed by 'Patient: Alex Morgan...'. The dosage extractor must return
    'Take 1 capsule by mouth three times daily for 10 days' and NEVER capture 'Patient: Alex Morgan'
    or refill lines.
    """
    real_ocr_text = (
        "* * * SYNTHETIC TEST DOCUMENT *** TRANSMISSION CONFIDENTIAL MEDICAL RECORD FACSIMILE\n\n"
        "NOTICE PEDIATRIC & ADULT URGENT CARE TRANSMISSION Date: 2026-02-14 "
        "Attn: Pharmacy Dispensing Coordinator Clinic Phone: 555-0145\n\n"
        "Prescription Refill Authorization: approved for Amoxicillin 5oOmg Dr. Evelyn Reed. "
        "Urgent prescription refill request Take 1 capsule by mouth three times daily for 10 days: "
        "Dosage: Patient: Alex Morgan (Chart #MRN-4412-A) Refill count: 0 (one-time course). by"
    )
    fields, confidences = extract_fields_from_ocr_text(real_ocr_text)
    assert fields.get("dosage") == "Take 1 capsule by mouth three times daily for 10 days"
    assert "Patient" not in str(fields.get("dosage", ""))
    assert "Refill" not in str(fields.get("dosage", ""))
    assert confidences.get("dosage", 0.0) >= 0.90


def test_real_invalid_npi_fax_ocr_stops_at_next_field_label() -> None:
    """Verify OCR field extraction on real invalid_npi_fax.pdf stops at any 'Label:' pattern."""
    real_invalid_npi_ocr = (
        "* * * SYNTHETIC TEST DOCUMENT * * * TRANSMISSION CONFIDENTIAL MEDICAL RECORD FACSIMILE\n\n"
        "URGENT CLINIC INTAKE FAX Date: 2026-02-14 "
        "Patient Name: Casey Bennett Chart Number: MRN-1102-K "
        "Authorized Medication: Amoxicillin 5OOmg Authorization Date: 2026-02-14 "
        "Prescriber NPI: 1982736451 Prescriber Signature: Dr. Evelyn Reed "
        "Clinic Phone: 555-0145"
    )
    fields, _ = extract_fields_from_ocr_text(real_invalid_npi_ocr)
    assert fields.get("patient_name") == "Casey Bennett"
    assert fields.get("chart_number") == "MRN-1102-K"
    assert fields.get("authorized_medication") == "Amoxicillin 5OOmg"
    assert fields.get("authorization_date") == "2026-02-14"
    assert fields.get("prescriber_npi") == "1982736451"
    assert fields.get("prescriber_signature") == "Dr. Evelyn Reed"
    assert fields.get("clinic_phone") == "555-0145"
    assert fields.get("dosage") is None


def test_absent_tokens_null_none_na_empty_treated_as_absent_in_ocr_and_vision_parsers() -> None:
    """Any read that is empty, 'null', 'none', 'n/a' or similar must be treated as absent (None)."""
    ocr_with_nulls = (
        "Patient Name: Casey Bennett\n"
        "Dosage: null\n"
        "Secondary Insurance: N/A\n"
        "Procedure Ordered: none\n"
        "Clinical Justification: [blank]\n"
    )
    ocr_fields, _ = extract_fields_from_ocr_text(ocr_with_nulls)
    assert ocr_fields.get("patient_name") == "Casey Bennett"
    assert ocr_fields.get("dosage") is None
    assert ocr_fields.get("secondary_insurance") is None
    assert ocr_fields.get("procedure_ordered") is None
    assert ocr_fields.get("clinical_justification") is None

    vision_json = (
        '{"patient_name": "Casey Bennett", "dosage": "null", '
        '"secondary_insurance": "N/A", "refill_count": "none", "facility_location": ""}'
    )
    vis_fields = parse_vision_json_reading(vision_json)
    assert vis_fields.get("patient_name") == "Casey Bennett"
    assert vis_fields.get("dosage") is None
    assert vis_fields.get("secondary_insurance") is None
    assert vis_fields.get("refill_count") is None
    assert vis_fields.get("facility_location") is None

