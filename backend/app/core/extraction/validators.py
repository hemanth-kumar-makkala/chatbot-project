"""Field value format and check-digit validators (Task 4.5).

Enforces:
- 10-digit National Provider Identifier (NPI) Luhn check-digit verification with '80840' prefix.
- ISO and standard calendar date validation (syntax and realistic calendar validity).
- Specific ID formats (MRN, phone, fax).
- Adheres strictly to app/core boundary rules (stdlib only).
"""

from __future__ import annotations

import re
from datetime import datetime


def compute_npi_check_digit(base9: str) -> int:
    """Compute the 10th check digit for a 9-digit NPI base using CMS 80840 Luhn algorithm."""
    full = "80840" + base9
    total = 0
    digits = [int(c) for c in reversed(full)]
    for i, digit in enumerate(digits):
        if i % 2 == 0:
            doubled = digit * 2
            total += doubled if doubled < 10 else (doubled - 9)
        else:
            total += digit
    units = total % 10
    return (10 - units) % 10


def validate_npi(value: str) -> tuple[bool, str | None]:
    """Validate a 10-digit National Provider Identifier (NPI).

    Must be 10 digits, cannot start with '0', and must satisfy the CMS Luhn check digit.
    """
    clean = re.sub(r"\s+", "", str(value).strip())
    if not re.match(r"^\d{10}$", clean):
        return False, "NPI must be exactly 10 digits"

    if clean.startswith("0"):
        return False, "NPI cannot start with digit 0"

    base9 = clean[:9]
    actual_check_digit = int(clean[9])
    expected_check_digit = compute_npi_check_digit(base9)

    if actual_check_digit != expected_check_digit:
        return (
            False,
            (
                f"NPI check digit Luhn algorithm failed "
                f"(expected {expected_check_digit}, got {actual_check_digit})"
            ),
        )

    return True, None


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


def validate_date(value: str) -> tuple[bool, str | None]:
    """Validate calendar date string across ISO, US slash, and month name formats."""
    clean = str(value).strip()
    if not clean:
        return False, "Date is empty"

    # 1. ISO format: YYYY-MM-DD
    m_iso = re.match(r"^(20\d\d)-(0?[1-9]|1[0-2])-(0?[1-9]|[12]\d|3[01])$", clean)
    if m_iso:
        y, m, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        try:
            datetime(y, m, d)
            return True, None
        except ValueError:
            return False, f"Invalid calendar day {d} for month {m}"

    # 2. US slash format: MM/DD/YYYY
    m_slash = re.match(r"^(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])/(20\d\d)$", clean)
    if m_slash:
        m, d, y = int(m_slash.group(1)), int(m_slash.group(2)), int(m_slash.group(3))
        try:
            datetime(y, m, d)
            return True, None
        except ValueError:
            return False, f"Invalid calendar day {d} for month {m}"

    # 3. Spelled out: February 14, 2026 or Feb 14, 2026
    m_text = re.match(r"^([A-Za-z]+)\s+(0?[1-9]|[12]\d|3[01]),?\s+(20\d\d)$", clean)
    if m_text:
        m_name = m_text.group(1).lower()
        if m_name in _MONTH_MAP:
            m = _MONTH_MAP[m_name]
            d = int(m_text.group(2))
            y = int(m_text.group(3))
            try:
                datetime(y, m, d)
                return True, None
            except ValueError:
                return False, f"Invalid calendar day {d} for month {m_name}"

    return False, f"Unrecognized or invalid date format: {clean}"


def validate_id_format(value: str, id_type: str = "general") -> tuple[bool, str | None]:
    """Validate ID formats such as MRN, phone, and fax."""
    clean = str(value).strip()
    if not clean:
        return False, "ID value is empty"

    if id_type.lower() == "mrn":
        if re.match(r"^MRN[-.\s]?\d+[-.\s]?[A-Za-z]$", clean, re.IGNORECASE):
            return True, None
        return False, f"Invalid MRN format: {clean}"

    if id_type.lower() in ("phone", "fax"):
        # 7-digit local (e.g. 555-0145) or 10-digit standard
        digits = re.sub(r"\D", "", clean)
        if len(digits) in (7, 10, 11):
            return True, None
        return False, f"Invalid phone/fax number format: {clean}"

    return True, None
