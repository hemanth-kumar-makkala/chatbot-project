"""Stage U3: the extraction field table always lists every field of the extraction template.

Enforces:
- Every field of the fax intake template appears in the extracted field list, in template order.
- A template field absent from the document is shown as NOT_PRESENT with no value (never omitted).
- Stored extraction results created before this rule are completed the same way when re-served.
- Fixtures are the OCR text of the synthetic golden faxes invalid_npi_fax.pdf and
  missing_field_fax.pdf (evals/golden/generate_extraction_faxes.py) and the vision JSON a model
  returns when it follows rule 2 of the vision prompt (only fields present on the page).
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.core.extraction.field_state import ExtractedField, FieldState
from app.core.extraction.subgraph import (
    EXTRACTION_VISION_PROMPT,
    build_extraction_subgraph,
    extract_fields_from_ocr_text,
    parse_vision_json_reading,
)
from app.core.extraction.template import (
    FAX_INTAKE_TEMPLATE_FIELDS,
    complete_template_field_dicts,
)
from app.core.graph import GraphPorts, GraphRuntimeContext

# Docling OCR text of evals/golden/docs/invalid_npi_fax.pdf (same fixture as Stage T2).
INVALID_NPI_FAX_OCR = (
    "* * * SYNTHETIC TEST DOCUMENT * * * TRANSMISSION CONFIDENTIAL MEDICAL RECORD FACSIMILE\n\n"
    "URGENT CLINIC INTAKE FAX Date: 2026-02-14 "
    "Patient Name: Casey Bennett Chart Number: MRN-1102-K "
    "Authorized Medication: Amoxicillin 5OOmg Authorization Date: 2026-02-14 "
    "Prescriber NPI: 1982736451 Prescriber Signature: Dr. Evelyn Reed "
    "Clinic Phone: 555-0145"
)
INVALID_NPI_FAX_VISION_JSON = (
    '{"patient_name": "Casey Bennett", "chart_number": "MRN-1102-K", '
    '"authorized_medication": "Amoxicillin 500mg", "authorization_date": "2026-02-14", '
    '"prescriber_npi": "1982736451", "prescriber_signature": "Dr. Evelyn Reed", '
    '"clinic_phone": "555-0145"}'
)

# Docling-style OCR text of evals/golden/docs/missing_field_fax.pdf
# (printed lines: evals/golden/generate_extraction_faxes.py MISSING_FIELD_FAX_LINES).
MISSING_FIELD_FAX_OCR = (
    "* * * SYNTHETIC TEST DOCUMENT * * * TRANSMISSION CONFIDENTIAL MEDICAL RECORD FACSIMILE\n\n"
    "STANDARDIZED PRESCRIPTION INTAKE FAX Date: 2026-02-14 "
    "Patient Name: Morgan Bailey "
    "Authorized Medication: Atorvastatin 2Omg Authorization Date: 2026-02-14 "
    "Prescriber NPI: 1234567893 "
    "Clinic Phone: 555-0145"
)
MISSING_FIELD_FAX_VISION_JSON = (
    '{"patient_name": "Morgan Bailey", "authorized_medication": "Atorvastatin 20mg", '
    '"authorization_date": "2026-02-14", "prescriber_npi": "1234567893", '
    '"clinic_phone": "555-0145"}'
)


async def _run_extraction_subgraph(
    ocr_text: str, vision_json: str, document_title: str
) -> GraphRuntimeContext:
    ocr_dict, conf_dict = extract_fields_from_ocr_text(ocr_text)
    vision_dict = parse_vision_json_reading(vision_json)
    ctx = GraphRuntimeContext(
        ports=GraphPorts(),
        evidence_text={"document_title": document_title, "page_number": "1"},
    )
    ctx.extracted_fields["raw_readings"] = {
        "ocr": ocr_dict,
        "vision": vision_dict,
        "confidences": conf_dict,
    }
    subgraph = build_extraction_subgraph().compile()
    await subgraph.ainvoke(
        {
            "request_id": "u3-template-test",
            "tenant_id": ctx.tenant.tenant_id,
            "route": "extraction",
            "status": "in_progress",
        },
        context=ctx,
    )
    return ctx


def _fields(ctx: GraphRuntimeContext) -> list[ExtractedField]:
    fields: list[ExtractedField] = ctx.extracted_fields.get("fields", [])
    return fields


def test_template_lists_exactly_the_fields_requested_in_the_vision_prompt() -> None:
    prompt_fields = tuple(re.findall(r'^- "([a-z_]+)":', EXTRACTION_VISION_PROMPT, re.MULTILINE))
    assert prompt_fields == FAX_INTAKE_TEMPLATE_FIELDS
    assert len(set(FAX_INTAKE_TEMPLATE_FIELDS)) == len(FAX_INTAKE_TEMPLATE_FIELDS)


@pytest.mark.asyncio
async def test_invalid_npi_fax_field_table_lists_every_template_field_with_dosage_not_present() -> (
    None
):
    ctx = await _run_extraction_subgraph(
        INVALID_NPI_FAX_OCR, INVALID_NPI_FAX_VISION_JSON, "invalid_npi_fax.pdf"
    )
    fields = _fields(ctx)
    names = [f.name for f in fields]
    assert names[: len(FAX_INTAKE_TEMPLATE_FIELDS)] == list(FAX_INTAKE_TEMPLATE_FIELDS)
    assert set(names) == set(FAX_INTAKE_TEMPLATE_FIELDS)

    by_name = {f.name: f for f in fields}
    dosage = by_name["dosage"]
    assert dosage.state == FieldState.NOT_PRESENT
    assert dosage.value is None
    assert dosage.read1_value is None
    assert dosage.read2_value is None

    # Printed fields keep their real states
    assert by_name["patient_name"].value == "Casey Bennett"
    assert by_name["prescriber_npi"].state == FieldState.INVALID
    assert by_name["clinic_phone"].value == "555-0145"

    # Fields never printed on this fax are listed as NOT_PRESENT
    for absent in (
        "refill_count",
        "referring_provider_phone",
        "procedure_ordered",
        "appointment_date",
        "facility_location",
        "clinical_justification",
        "pain_scale_score",
        "secondary_insurance",
    ):
        assert by_name[absent].state == FieldState.NOT_PRESENT
        assert by_name[absent].value is None

    # Rendered table and API payload carry the dosage row
    assert any(
        line.startswith("| dosage |") and "NOT_PRESENT" in line
        for line in ctx.response_text.splitlines()
    )
    payload_names = [f["name"] for f in ctx.generated_answer["fields"]]
    assert payload_names == names


@pytest.mark.asyncio
async def test_missing_field_fax_shows_every_missing_template_field_as_not_present() -> None:
    ctx = await _run_extraction_subgraph(
        MISSING_FIELD_FAX_OCR, MISSING_FIELD_FAX_VISION_JSON, "missing_field_fax.pdf"
    )
    fields = _fields(ctx)
    names = [f.name for f in fields]
    assert names[: len(FAX_INTAKE_TEMPLATE_FIELDS)] == list(FAX_INTAKE_TEMPLATE_FIELDS)
    assert set(names) == set(FAX_INTAKE_TEMPLATE_FIELDS)

    by_name = {f.name: f for f in fields}
    assert by_name["patient_name"].value == "Morgan Bailey"
    assert by_name["prescriber_npi"].value == "1234567893"

    printed = {
        "patient_name",
        "authorized_medication",
        "authorization_date",
        "prescriber_npi",
        "clinic_phone",
    }
    for name in FAX_INTAKE_TEMPLATE_FIELDS:
        if name in printed:
            continue
        assert by_name[name].state == FieldState.NOT_PRESENT, name
        assert by_name[name].value is None, name

    # secondary_insurance is the missing field named in the golden set
    assert any(
        line.startswith("| secondary_insurance |") and "NOT_PRESENT" in line
        for line in ctx.response_text.splitlines()
    )


@pytest.mark.asyncio
async def test_not_present_fields_never_carry_a_value() -> None:
    for ocr, vision, title in (
        (INVALID_NPI_FAX_OCR, INVALID_NPI_FAX_VISION_JSON, "invalid_npi_fax.pdf"),
        (MISSING_FIELD_FAX_OCR, MISSING_FIELD_FAX_VISION_JSON, "missing_field_fax.pdf"),
    ):
        ctx = await _run_extraction_subgraph(ocr, vision, title)
        for f in _fields(ctx):
            if f.state == FieldState.NOT_PRESENT:
                assert f.value is None
                assert f.document_title == title


def test_stored_extraction_result_missing_template_fields_is_completed_in_template_order() -> None:
    stored: list[dict[str, Any]] = [
        ExtractedField(
            name="prescriber_npi",
            value="1982736451",
            state=FieldState.INVALID,
            confidence=0.95,
            read1_value="1982736451",
            read2_value="1982736451",
            validator_error="NPI check digit mismatch",
            document_title="invalid_npi_fax.pdf",
        ).to_dict(),
        ExtractedField(
            name="patient_name",
            value="Casey Bennett",
            state=FieldState.FILLED_HIGH,
            confidence=0.96,
            read1_value="Casey Bennett",
            read2_value="Casey Bennett",
            document_title="invalid_npi_fax.pdf",
        ).to_dict(),
    ]
    completed = complete_template_field_dicts(stored, document_title="invalid_npi_fax.pdf")

    assert [f["name"] for f in completed] == list(FAX_INTAKE_TEMPLATE_FIELDS)
    by_name = {f["name"]: f for f in completed}
    # Stored rows are returned unchanged
    assert by_name["prescriber_npi"] == stored[0]
    assert by_name["patient_name"] == stored[1]
    # Missing template fields are NOT_PRESENT with no value
    assert by_name["dosage"]["state"] == "NOT_PRESENT"
    assert by_name["dosage"]["value"] is None
    assert by_name["dosage"]["document_title"] == "invalid_npi_fax.pdf"


def test_stored_extraction_result_keeps_non_template_fields_after_template_fields() -> None:
    extra = ExtractedField(
        name="emergency_override",
        value=None,
        state=FieldState.NOT_APPLICABLE,
        confidence=0.95,
    ).to_dict()
    completed = complete_template_field_dicts([extra], document_title="missing_field_fax.pdf")
    assert [f["name"] for f in completed] == [*FAX_INTAKE_TEMPLATE_FIELDS, "emergency_override"]
    assert completed[-1] == extra
