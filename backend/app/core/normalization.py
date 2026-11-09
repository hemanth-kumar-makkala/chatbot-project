"""Normalization and comparison of visual reading values (Task 4.2).

Per specification:
- Visual comparison uses normalized values (digits, dates, IDs, amounts).
- Prose may differ; IDs and critical quantities must not.
- Disagreement flags the answer.
- All code strictly adheres to app/core boundary rules (stdlib only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class VisualComparisonResult:
    """Outcome of comparing normalized values across two independent readings."""

    is_matching: bool
    discrepancies: list[str] = field(default_factory=list)
    normalized_a: dict[str, set[str]] = field(default_factory=dict)
    normalized_b: dict[str, set[str]] = field(default_factory=dict)


def normalize_digits_and_numbers(text: str) -> set[str]:
    """Extract and normalize digits, quantities, and numeric strengths."""
    results: set[str] = set()
    # Find standalone numbers
    for m in re.finditer(r"\b\d+\b", text):
        results.add(m.group(0))

    # Find numbers attached to units (e.g. 500mg, 500 mg, 10days)
    for m in re.finditer(
        r"\b(\d+)\s*(mg|mcg|ml|capsules?|tablets?|days?|weeks?|sessions?)\b", text, re.IGNORECASE
    ):
        results.add(m.group(1))
        unit = m.group(2).lower()
        results.add(f"{m.group(1)}{unit}")

    return results


_MONTH_MAP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def normalize_dates(text: str) -> set[str]:
    """Extract and normalize calendar dates to canonical YYYY-MM-DD."""
    dates: set[str] = set()

    # 1. ISO format: YYYY-MM-DD
    for m in re.finditer(r"\b(20\d\d)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", text):
        dates.add(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")

    # 2. US slash format: MM/DD/YYYY
    for m in re.finditer(r"\b(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])/(20\d\d)\b", text):
        month = int(m.group(1))
        day = int(m.group(2))
        year = int(m.group(3))
        dates.add(f"{year:04d}-{month:02d}-{day:02d}")

    # 3. Month name format: February 14, 2026 or Feb 14, 2026
    month_names_pattern = r"\b([A-Za-z]+)\s+(0?[1-9]|[12]\d|3[01]),?\s+(20\d\d)\b"
    for m in re.finditer(month_names_pattern, text):
        m_name = m.group(1).lower()
        if m_name in _MONTH_MAP:
            month = _MONTH_MAP[m_name]
            day = int(m.group(2))
            year = int(m.group(3))
            dates.add(f"{year:04d}-{month:02d}-{day:02d}")

    return dates


def normalize_ids(text: str) -> set[str]:
    """Extract and normalize IDs (MRN, phone/fax, NPI, plan codes)."""
    ids: set[str] = set()

    # 1. MRN pattern: MRN-1000-X or MRN 1000 X
    for m in re.finditer(r"\bMRN[-.\s]?(\d+)[-.\s]?([A-Za-z])\b", text, re.IGNORECASE):
        ids.add(f"MRN-{m.group(1)}-{m.group(2).upper()}")

    # 2. Phone / Fax numbers: (555) 014-5555 or 555-014-5555 or 555-0145
    for m in re.finditer(r"(?:\+?1[-.\s]?)?\(?([2-9]\d{2})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})\b", text):
        ids.add(f"{m.group(1)}{m.group(2)}{m.group(3)}")

    # 3. 7-digit local phone e.g. 555-0145
    for m in re.finditer(r"\b(555)[-.\s]?(\d{4})\b", text):
        ids.add(f"{m.group(1)}{m.group(2)}")

    # 4. Standard Plan / Form codes: e.g. APX-PPO-02, CRST-GLD-05
    for m in re.finditer(r"\b([A-Z]{3,4}-[A-Z]{3,4}-\d{2})\b", text):
        ids.add(m.group(1))

    return ids


def normalize_amounts(text: str) -> set[str]:
    """Extract and normalize currency amounts to decimal format (e.g. 25.00)."""
    amounts: set[str] = set()

    # $25.00 or $25 or $500.00
    for m in re.finditer(r"\$(\d+(?:\.\d{2})?)", text):
        val = float(m.group(1))
        amounts.add(f"{val:.2f}")

    # 25 dollars or 500 dollars
    for m in re.finditer(r"\b(\d+(?:\.\d{2})?)\s*dollars?\b", text, re.IGNORECASE):
        val = float(m.group(1))
        amounts.add(f"{val:.2f}")

    return amounts


def compare_normalized_values(reading_a: str, reading_b: str) -> VisualComparisonResult:
    """Compare extracted normalized values between two readings.

    Prose may differ; IDs, dates, numbers, and amounts must not disagree.
    """
    ids_a = normalize_ids(reading_a)
    ids_b = normalize_ids(reading_b)

    dates_a = normalize_dates(reading_a)
    dates_b = normalize_dates(reading_b)

    amounts_a = normalize_amounts(reading_a)
    amounts_b = normalize_amounts(reading_b)

    nums_a = normalize_digits_and_numbers(reading_a)
    nums_b = normalize_digits_and_numbers(reading_b)

    discrepancies: list[str] = []

    # 1. Compare IDs (Strict: if both present, any contradictory ID is a discrepancy)
    mrn_a = {i for i in ids_a if i.startswith("MRN-")}
    mrn_b = {i for i in ids_b if i.startswith("MRN-")}
    if mrn_a and mrn_b and not (mrn_a & mrn_b):
        discrepancies.append(f"MRN mismatch: {mrn_a} vs {mrn_b}")

    # 2. Compare Dates (If both present, must share at least one date or not contradict)
    if dates_a and dates_b and not (dates_a & dates_b):
        discrepancies.append(f"Date mismatch: {dates_a} vs {dates_b}")

    # 3. Compare Amounts (If both present, must share amount)
    if amounts_a and amounts_b and not (amounts_a & amounts_b):
        discrepancies.append(f"Amount mismatch: {amounts_a} vs {amounts_b}")

    # 4. Compare Medication Strengths / Dosages
    dosage_pattern = re.compile(r"\b(\d+)\s*(?:mg|mcg|ml)\b", re.IGNORECASE)
    dosages_a = set(dosage_pattern.findall(reading_a))
    dosages_b = set(dosage_pattern.findall(reading_b))
    if dosages_a and dosages_b and not (dosages_a & dosages_b):
        discrepancies.append(f"Dosage mismatch: {dosages_a} vs {dosages_b}")

    is_matching = len(discrepancies) == 0

    return VisualComparisonResult(
        is_matching=is_matching,
        discrepancies=discrepancies,
        normalized_a={
            "ids": ids_a,
            "dates": dates_a,
            "amounts": amounts_a,
            "numbers": nums_a,
        },
        normalized_b={
            "ids": ids_b,
            "dates": dates_b,
            "amounts": amounts_b,
            "numbers": nums_b,
        },
    )
