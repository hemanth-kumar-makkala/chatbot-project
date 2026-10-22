"""Explicit field states and data structures for extraction (Task 4.5).

Enforces:
- Authoritative 7 field states from specification:
  FILLED_HIGH, FILLED_MEDIUM, CONFLICTING, UNREADABLE, INVALID, NOT_PRESENT, NOT_APPLICABLE.
- Invariant 6: Unreadable fields stay blank (value is None) and flagged, never guessed.
- Adheres strictly to app/core boundary rules (stdlib only).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class FieldState(StrEnum):
    """The 7 explicit field states from specification table 'Field states for extraction'."""

    FILLED_HIGH = "FILLED_HIGH"  # Readings agree and validators pass
    FILLED_MEDIUM = "FILLED_MEDIUM"  # One reading only, or lower confidence; shown for checking
    CONFLICTING = "CONFLICTING"  # Readings disagree; both values shown
    UNREADABLE = "UNREADABLE"  # Field exists but cannot be read (Invariant 6: blank & flagged)
    INVALID = "INVALID"  # Read, but fails a validator (date, ID format, NPI check digit)
    NOT_PRESENT = "NOT_PRESENT"  # Field does not appear on the document
    NOT_APPLICABLE = "NOT_APPLICABLE"  # Field does not apply to this document type


@dataclass
class ExtractedField:
    """Representation of an extracted field with explicit provenance and state."""

    name: str
    value: str | None
    state: FieldState
    confidence: float
    read1_value: str | None = None
    read2_value: str | None = None
    validator_error: str | None = None
    page_number: int = 1
    document_title: str = ""

    def __post_init__(self) -> None:
        if self.state == FieldState.UNREADABLE and self.value is not None:
            raise ValueError(
                f"Invariant 6 violation: UNREADABLE field '{self.name}' must stay blank "
                f"(value is None) and never guessed. Received value={self.value!r}."
            )

        if self.state == FieldState.CONFLICTING and self.value is not None:
            # Conflicting fields present both readings side-by-side; primary value is None
            self.value = None

    @property
    def is_unreadable(self) -> bool:
        """True if field is unreadable."""
        return self.state == FieldState.UNREADABLE

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation matching UI and API schemas."""
        return {
            "name": self.name,
            "value": self.value,
            "state": self.state.value,
            "confidence": self.confidence,
            "read1_value": self.read1_value,
            "read2_value": self.read2_value,
            "validator_error": self.validator_error,
            "page_number": self.page_number,
            "document_title": self.document_title,
        }
