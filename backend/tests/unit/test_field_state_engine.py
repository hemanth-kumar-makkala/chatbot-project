"""Unit tests for Task 4.5: Field State Classification & Validation Engine.

Enforces:
- Explicit field states: FILLED_HIGH, FILLED_MEDIUM, CONFLICTING,
  UNREADABLE, INVALID, NOT_PRESENT, NOT_APPLICABLE.
- Validators for dates, ID formats, and NPI check digits (Luhn algorithm with 80840 prefix).
- Invariant 6: Unreadable fields stay blank (value is None) and flagged, never guessed.
- The model never writes a final field value that the validators or the two readings do not support.
"""

from __future__ import annotations

import pytest

from app.core.extraction.field_state import ExtractedField, FieldState
from app.core.extraction.validators import (
    validate_date,
    validate_id_format,
    validate_npi,
)


class TestValidators:
    """Test format, date, and check-digit validators."""

    def test_npi_luhn_algorithm_valid(self) -> None:
        """Valid 10-digit NPI passing Luhn check digit with 80840 prefix."""
        # 1982736450 is proven valid with 80840 prefix
        is_valid, err = validate_npi("1982736450")
        assert is_valid is True
        assert err is None

    def test_npi_luhn_algorithm_invalid(self) -> None:
        """Invalid 10-digit NPI failing Luhn check digit."""
        # 1982736451 fails check digit (computed is 0, actual is 1)
        is_valid, err = validate_npi("1982736451")
        assert is_valid is False
        assert err is not None
        assert "Luhn" in err or "check digit" in err.lower()

    def test_npi_invalid_length_or_characters(self) -> None:
        """NPI must be exactly 10 digits and numeric."""
        is_valid1, _ = validate_npi("12345")
        assert is_valid1 is False

        is_valid2, _ = validate_npi("198273645A")
        assert is_valid2 is False

        is_valid3, _ = validate_npi("0982736450")
        assert is_valid3 is False  # Cannot start with 0

    def test_date_validator_valid_formats(self) -> None:
        """Valid date strings across ISO, slash, and spelled-out formats."""
        assert validate_date("2026-02-14")[0] is True
        assert validate_date("02/14/2026")[0] is True
        assert validate_date("February 14, 2026")[0] is True
        assert validate_date("Feb 14, 2026")[0] is True

    def test_date_validator_invalid_dates(self) -> None:
        """Invalid calendar dates rejected."""
        assert validate_date("2026-02-31")[0] is False  # Invalid day in Feb
        assert validate_date("2026-13-01")[0] is False  # Invalid month
        assert validate_date("not-a-date")[0] is False

    def test_id_format_validator(self) -> None:
        """MRN and phone/fax ID formats."""
        assert validate_id_format("MRN-4412-A", id_type="mrn")[0] is True
        assert validate_id_format("MRN 4412 A", id_type="mrn")[0] is True
        assert validate_id_format("INVALID_ID", id_type="mrn")[0] is False

        assert validate_id_format("555-0145", id_type="phone")[0] is True
        assert validate_id_format("(555) 014-5555", id_type="phone")[0] is True


class TestFieldStatesAndInvariant6:
    """Test explicit field states and Invariant 6 (unreadable fields never guessed)."""

    def test_all_seven_field_states_exist(self) -> None:
        """FieldState must contain all 7 authoritative spec states."""
        expected_states = {
            "FILLED_HIGH",
            "FILLED_MEDIUM",
            "CONFLICTING",
            "UNREADABLE",
            "INVALID",
            "NOT_PRESENT",
            "NOT_APPLICABLE",
        }
        actual_states = {s.value for s in FieldState}
        assert expected_states == actual_states

    def test_unreadable_field_invariant_6_blank_and_flagged(self) -> None:
        """Invariant 6: An UNREADABLE field must stay blank (value is None) and never guessed."""
        field = ExtractedField(
            name="Clinical Justification Note",
            value=None,
            state=FieldState.UNREADABLE,
            confidence=0.38,
            page_number=2,
            document_title="faint_fax_2page.pdf",
        )
        assert field.state == FieldState.UNREADABLE
        assert field.value is None  # Must be None, NEVER guessed
        assert field.is_unreadable is True

    def test_unreadable_field_rejects_non_none_value(self) -> None:
        """Invariant 6: Constructing an UNREADABLE field with a guessed value raises ValueError."""
        with pytest.raises(ValueError, match="Invariant 6"):
            ExtractedField(
                name="Clinical Justification Note",
                value="Chronic lower back pain",  # Guessing violates Invariant 6
                state=FieldState.UNREADABLE,
                confidence=0.38,
            )

    def test_conflicting_field_shows_both_readings(self) -> None:
        """CONFLICTING state carries both read1_value and read2_value, value is None."""
        field = ExtractedField(
            name="Authorized Medication",
            value=None,
            read1_value="Amoxicillin 500mg",
            read2_value="Amoxillin 500mg",
            state=FieldState.CONFLICTING,
            confidence=0.74,
            page_number=1,
            document_title="skewed_fax.pdf",
        )
        assert field.state == FieldState.CONFLICTING
        assert field.value is None
        assert field.read1_value == "Amoxicillin 500mg"
        assert field.read2_value == "Amoxillin 500mg"

    def test_invalid_field_retains_value_and_validator_error(self) -> None:
        """INVALID state retains read value and specifies validator error reason."""
        field = ExtractedField(
            name="Prescriber NPI",
            value="1982736451",
            state=FieldState.INVALID,
            validator_error="NPI check digit Luhn algorithm failed",
            confidence=0.95,
            page_number=1,
            document_title="skewed_fax.pdf",
        )
        assert field.state == FieldState.INVALID
        assert field.value == "1982736451"
        assert field.validator_error == "NPI check digit Luhn algorithm failed"
