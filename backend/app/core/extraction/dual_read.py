"""Dual read comparison and field reconciliation for extraction subgraph (Task 4.4).

Enforces:
- Reconciles independent OCR and Vision readings using normalized values.
- Assigns explicit field states: FILLED_HIGH, FILLED_MEDIUM, CONFLICTING,
  UNREADABLE, INVALID, NOT_PRESENT, NOT_APPLICABLE.
- Invariant 6: Unreadable fields stay blank (value is None) and flagged, never guessed.
- Adheres strictly to app/core boundary rules (stdlib only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.extraction.field_state import ExtractedField, FieldState
from app.core.extraction.validators import (
    validate_date,
    validate_id_format,
    validate_npi,
)
from app.core.normalization import (
    normalize_dates,
    normalize_digits_and_numbers,
    normalize_ids,
)


@dataclass
class DualReadComparisonResult:
    """Summary result of dual read comparison across an extraction document."""

    fields: list[ExtractedField] = field(default_factory=list)
    dual_read_matches: bool = True
    overall_confidence: float = 1.0


_ABSENT_TOKENS = frozenset(
    {
        "",
        "null",
        "none",
        "n/a",
        "na",
        "nil",
        "[blank]",
        "[none]",
        "[null]",
        "[n/a]",
        "not present",
        "not provided",
        "not specified",
        "not applicable",
        "unknown",
        "—",
        "-",
    }
)


def _clean_str(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in _ABSENT_TOKENS:
        return None
    return s


def _is_unreadable_token(val: str | None) -> bool:
    if val is None:
        return False
    lower = val.lower().strip()
    return lower in (
        "[unreadable]",
        "[blank - unreadable]",
        "unreadable",
        "[unreadable signature]",
        "unreadable signature",
        "[illegible]",
        "illegible",
    )


def dual_read_compare(
    read1_ocr: dict[str, Any],
    read2_vision: dict[str, Any],
    confidences: dict[str, float] | None = None,
    document_title: str = "",
    page_number: int = 1,
) -> list[ExtractedField]:
    """Reconcile OCR and Vision readings into typed ExtractedField objects with explicit states.

    Normalized values (digits, dates, IDs, amounts) are used for comparison.
    Prose may differ; IDs, numbers, and dates must match or be marked CONFLICTING.
    """
    conf_map = confidences or {}
    all_keys = list(dict.fromkeys(list(read1_ocr.keys()) + list(read2_vision.keys())))
    extracted: list[ExtractedField] = []

    for key in all_keys:
        val1 = _clean_str(read1_ocr.get(key))
        val2 = _clean_str(read2_vision.get(key))
        conf = float(conf_map.get(key, 0.95))

        display_name = key

        # 1. Unreadable Check: UNREADABLE is only for a field neither reading can read (Invariant 6)
        u1 = _is_unreadable_token(val1)
        u2 = _is_unreadable_token(val2)
        if (u1 and u2) or (u1 and val2 is None) or (u2 and val1 is None):
            extracted.append(
                ExtractedField(
                    name=display_name,
                    value=None,
                    state=FieldState.UNREADABLE,
                    confidence=conf,
                    read1_value=val1,
                    read2_value=val2,
                    page_number=page_number,
                    document_title=document_title,
                )
            )
            continue

        # 2. Both Missing / Null
        if val1 is None and val2 is None:
            if "override" in key.lower() or "emergency" in key.lower():
                state = FieldState.NOT_APPLICABLE
            else:
                state = FieldState.NOT_PRESENT

            extracted.append(
                ExtractedField(
                    name=display_name,
                    value=None,
                    state=state,
                    confidence=conf,
                    page_number=page_number,
                    document_title=document_title,
                )
            )
            continue

        # 3. Only One Reading Present
        if val1 is None or val2 is None:
            present_val = val1 if val1 is not None else val2
            assert present_val is not None

            # Check validators on single reading
            validator_err = _validate_field_value(key, present_val)
            state = FieldState.INVALID if validator_err else FieldState.FILLED_MEDIUM

            extracted.append(
                ExtractedField(
                    name=display_name,
                    value=present_val,
                    state=state,
                    confidence=min(conf, 0.82),
                    read1_value=val1,
                    read2_value=val2,
                    validator_error=validator_err,
                    page_number=page_number,
                    document_title=document_title,
                )
            )
            continue

        # 4. Both Readings Present: Compare Normalized Values
        agree = _check_values_agree(key, val1, val2)

        if not agree:
            # Readings disagree -> CONFLICTING state with both values shown, value=None
            extracted.append(
                ExtractedField(
                    name=display_name,
                    value=None,
                    state=FieldState.CONFLICTING,
                    confidence=min(conf, 0.74),
                    read1_value=val1,
                    read2_value=val2,
                    page_number=page_number,
                    document_title=document_title,
                )
            )
            continue

        # 5. Readings Agree: Validate canonical value
        if "date" in key.lower() or "dob" in key.lower():
            d_norm = normalize_dates(val1) & normalize_dates(val2)
            if d_norm:
                canonical_val = sorted(d_norm)[0]
            else:
                canonical_val = val1 if len(val1) >= len(val2) else val2
        else:
            canonical_val = val1 if len(val1) >= len(val2) else val2

        validator_err = _validate_field_value(key, canonical_val)

        if validator_err:
            state = FieldState.INVALID
        elif conf >= 0.85:
            state = FieldState.FILLED_HIGH
        else:
            state = FieldState.FILLED_MEDIUM

        extracted.append(
            ExtractedField(
                name=display_name,
                value=canonical_val,
                state=state,
                confidence=conf,
                read1_value=val1,
                read2_value=val2,
                validator_error=validator_err,
                page_number=page_number,
                document_title=document_title,
            )
        )

    return extracted


def _check_values_agree(key: str, val1: str, val2: str) -> bool:
    """Check if two readings agree using normalized comparisons."""
    k_lower = key.lower()

    # Exact string match
    if val1.strip().lower() == val2.strip().lower():
        return True

    # 1. Date comparison
    if "date" in k_lower or "dob" in k_lower:
        d1 = normalize_dates(val1)
        d2 = normalize_dates(val2)
        if d1 and d2:
            return len(d1 & d2) > 0

    # 2. MRN or ID comparison
    if "mrn" in k_lower or "chart" in k_lower or "id" in k_lower:
        id1 = normalize_ids(val1)
        id2 = normalize_ids(val2)
        if id1 and id2:
            return len(id1 & id2) > 0

    # 3. NPI comparison
    if "npi" in k_lower:
        clean1 = re.sub(r"\D", "", val1)
        clean2 = re.sub(r"\D", "", val2)
        return clean1 == clean2

    # 4. Pain scale / Score comparison (e.g. 7/10 vs 1/10)
    if "pain" in k_lower or "score" in k_lower or "scale" in k_lower:
        m1 = re.search(r"\b(\d+)(?:\s*/\s*(\d+))?\b", val1)
        m2 = re.search(r"\b(\d+)(?:\s*/\s*(\d+))?\b", val2)
        if m1 and m2:
            return m1.group(1) == m2.group(1) and m1.group(2) == m2.group(2)
        return False

    # 5. Dosage / Number comparison
    if "dosage" in k_lower or "strength" in k_lower or "refill" in k_lower or "quantity" in k_lower:
        nums1 = normalize_digits_and_numbers(val1)
        nums2 = normalize_digits_and_numbers(val2)
        if nums1 and nums2:
            return len(nums1 & nums2) > 0

    # 5. General normalized text: strip punctuation and compare words
    w1 = set(re.findall(r"\b\w{3,}\b", val1.lower()))
    w2 = set(re.findall(r"\b\w{3,}\b", val2.lower()))
    if w1 and w2:
        overlap = len(w1 & w2) / max(len(w1), len(w2))
        return overlap >= 0.80

    return False


def _validate_field_value(key: str, val: str) -> str | None:
    """Run appropriate validator for a field key."""
    k_lower = key.lower()

    if "npi" in k_lower:
        is_valid, err = validate_npi(val)
        return err if not is_valid else None

    if "date" in k_lower or "dob" in k_lower:
        is_valid, err = validate_date(val)
        return err if not is_valid else None

    if "mrn" in k_lower:
        is_valid, err = validate_id_format(val, id_type="mrn")
        return err if not is_valid else None

    if "phone" in k_lower or "fax" in k_lower or "tel" in k_lower:
        is_valid, err = validate_id_format(val, id_type="phone")
        return err if not is_valid else None

    return None
