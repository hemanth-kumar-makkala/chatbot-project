"""Extraction templates: the fixed field list every extraction result must show (Stage U3).

Enforces:
- The field table always lists every field of the extraction template, in template order.
- A template field absent from the document is NOT_PRESENT with no value, never omitted.
- Non-template fields returned by a reader are kept, after the template fields.
- Adheres strictly to app/core boundary rules (stdlib only).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from app.core.extraction.field_state import ExtractedField, FieldState

FAX_INTAKE_SCHEMA_NAME = "fax_intake"

# Single source of truth for the fax intake template. EXTRACTION_VISION_PROMPT requests exactly
# these fields in this order (checked by tests/unit/test_extraction_template_fields.py).
FAX_INTAKE_TEMPLATE_FIELDS: tuple[str, ...] = (
    "patient_name",
    "chart_number",
    "authorized_medication",
    "authorization_date",
    "dosage",
    "refill_count",
    "clinic_phone",
    "referring_provider_phone",
    "ordering_clinician_signature",
    "prescriber_signature",
    "procedure_ordered",
    "appointment_date",
    "facility_location",
    "clinical_justification",
    "pain_scale_score",
    "prescriber_npi",
    "secondary_insurance",
)

# Confidence recorded for a field neither reading found (same default dual_read_compare uses).
NOT_PRESENT_CONFIDENCE = 0.95


def template_ordered_keys(
    template_fields: Sequence[str], *found_key_groups: Iterable[str]
) -> list[str]:
    """Return template fields first (template order), then any other found keys in order seen."""
    keys: dict[str, None] = dict.fromkeys(template_fields)
    for group in found_key_groups:
        for key in group:
            keys.setdefault(key, None)
    return list(keys)


def not_present_field(name: str, document_title: str = "", page_number: int = 1) -> ExtractedField:
    """Build the NOT_PRESENT entry for a template field absent from the document."""
    return ExtractedField(
        name=name,
        value=None,
        state=FieldState.NOT_PRESENT,
        confidence=NOT_PRESENT_CONFIDENCE,
        page_number=page_number,
        document_title=document_title,
    )


def complete_template_field_dicts(
    stored_fields: Sequence[dict[str, Any]],
    template_fields: Sequence[str] = FAX_INTAKE_TEMPLATE_FIELDS,
    document_title: str = "",
    page_number: int = 1,
) -> list[dict[str, Any]]:
    """Complete stored field dicts so every template field is listed, in template order.

    Stored entries are returned unchanged. Missing template fields are added as NOT_PRESENT with
    value None. Entries without a usable name are dropped; non-template entries follow the
    template fields in their stored order.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for entry in stored_fields:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name and name not in by_name:
            by_name[name] = entry

    completed: list[dict[str, Any]] = []
    for name in template_ordered_keys(template_fields, by_name):
        if name in by_name:
            completed.append(by_name[name])
        else:
            completed.append(not_present_field(name, document_title, page_number).to_dict())
    return completed
