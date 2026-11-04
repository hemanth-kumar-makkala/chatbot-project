"""Deterministic mock exception scrubber for CI and mock profile (Phase 5 Task 5.4).

Scrubs synthetic PHI patterns (patient names, phone numbers, MRNs, dates of birth,
emails, and SSNs) from exception messages and stack traces without requiring
spaCy language model weights to be installed in CI.
"""

from __future__ import annotations

import re
import traceback

from app.ports.scrubber import ExceptionScrubber, ScrubbedExceptionData

_MOCK_PHI_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "EMAIL_ADDRESS",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    (
        "MEDICAL_RECORD_NUMBER",
        re.compile(
            r"\b(?:MRN[-:\s#]*[A-Za-z0-9-]{3,20}|[A-Z]{2,5}-MRN-\d{3,12})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "DATE_TIME",
        re.compile(
            r"\b(?:(?:DOB|Date\s+of\s+Birth|birth\s+date)[-:\s]*)?"
            r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "PHONE_NUMBER",
        re.compile(
            r"(?:\+?1[-.\s]?)?\(\d{3}\)\s*\d{3}[-.\s]\d{4}"
            r"|\b(?:\+?1[-.\s]?)?\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"
            r"|\b\d{3}-\d{4}\b"
        ),
    ),
    (
        "US_SSN",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    (
        "PERSON",
        re.compile(
            r"(?<=\bPatient:\s)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+"
            r"|(?<=\bPatient\s)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+"
            r"|(?<=\bpatient\s)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+"
            r"|(?<=\bName:\s)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+"
        ),
    ),
)


class MockExceptionScrubber(ExceptionScrubber):
    """Regex-backed mock exception scrubber for the mock profile and CI."""

    def _scrub_with_counts(self, text: str) -> tuple[str, dict[str, int]]:
        if not text:
            return "", {}

        counts: dict[str, int] = {}
        scrubbed = text
        for entity_type, pattern in _MOCK_PHI_PATTERNS:
            matches = list(pattern.finditer(scrubbed))
            if matches:
                counts[entity_type] = counts.get(entity_type, 0) + len(matches)
                scrubbed = pattern.sub(f"<{entity_type}>", scrubbed)
        return scrubbed, counts

    def scrub_text(self, text: str) -> str:
        scrubbed, _ = self._scrub_with_counts(text)
        return scrubbed

    def scrub_exception(self, exc: BaseException) -> ScrubbedExceptionData:
        raw_msg = str(exc)
        raw_tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

        scrubbed_msg, msg_counts = self._scrub_with_counts(raw_msg)
        scrubbed_tb, tb_counts = self._scrub_with_counts(raw_tb)

        merged_counts: dict[str, int] = dict(msg_counts)
        for k, v in tb_counts.items():
            merged_counts[k] = merged_counts.get(k, 0) + v

        return ScrubbedExceptionData(
            error_type=type(exc).__name__,
            scrubbed_message=scrubbed_msg,
            scrubbed_traceback=scrubbed_tb,
            redacted_entity_counts=merged_counts,
        )

