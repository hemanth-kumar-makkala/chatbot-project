"""Exception telemetry scrubber port interface (Phase 5 Task 5.4, Invariant 10).

Defines the contract for scrubbing PHI patterns from exception messages and stack
traces before any operational log line is emitted. Serves as a defense-in-depth
safety net on top of the strict OperationalJsonFormatter field allowlist.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScrubbedExceptionData:
    """Scrubbed exception telemetry payload with zero raw PHI."""

    error_type: str
    scrubbed_message: str
    scrubbed_traceback: str
    redacted_entity_counts: dict[str, int] = field(default_factory=dict)


class ExceptionScrubber(ABC):
    """Port interface for scrubbing PHI from exception messages and stack traces."""

    @abstractmethod
    def scrub_text(self, text: str) -> str:
        """Scrub PHI entities (names, phones, MRNs, DOBs, emails) from raw text."""

    @abstractmethod
    def scrub_exception(self, exc: BaseException) -> ScrubbedExceptionData:
        """Scrub PHI entities from an exception's message and formatted stack trace."""
