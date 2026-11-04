"""Operational Logger with strict field allowlist (Task 1.4b).

Enforces:
- Strict field allowlist: ts, request_id, tenant_id, node, status, latency_ms, counts,
  route, profile, model_id, error_code, error_type.
- Zero-content invariant (Invariant 10): NEVER logs prompt text, user questions, answer text,
  or document chunks.
- Any unallowlisted field is dropped and counted.
- No free-form message text.
- Exceptions record only error_type and error_code; exception messages and stack traces
  are excluded.
- request_id and tenant_id propagate via contextvars.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.ports.errors import ConfigurationError
from app.ports.scrubber import ExceptionScrubber, ScrubbedExceptionData

# Context variables for request and tenant tracing across async calls
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default="")

_active_scrubber: ExceptionScrubber | None = None


def set_exception_scrubber(scrubber: ExceptionScrubber | None) -> None:
    """Register the active ExceptionScrubber safety net for operational logging."""
    global _active_scrubber
    _active_scrubber = scrubber


def get_exception_scrubber() -> ExceptionScrubber | None:
    """Return the currently configured ExceptionScrubber, if any."""
    return _active_scrubber


def scrub_exception_for_log(exc: BaseException) -> ScrubbedExceptionData:
    """Scrub an exception message and stack trace before writing any operational log line.

    Never silently returns unscrubbed text: if no ExceptionScrubber has been configured,
    raises ConfigurationError.
    """
    if _active_scrubber is None:
        raise ConfigurationError(
            "EXCEPTION_SCRUBBER_NOT_CONFIGURED: No ExceptionScrubber configured for logging.",
            error_code="EXCEPTION_SCRUBBER_NOT_CONFIGURED",
        )
    return _active_scrubber.scrub_exception(exc)


# Authoritative allowlist of permitted operational log keys
ALLOWED_LOG_FIELDS: frozenset[str] = frozenset(
    {
        "ts",
        "request_id",
        "tenant_id",
        "node",
        "status",
        "latency_ms",
        "counts",
        "route",
        "profile",
        "model_id",
        "error_code",
        "error_type",
    }
)

# Standard Python LogRecord internal attributes to ignore during extra filtering
_STANDARD_LOGRECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

_dropped_field_count: int = 0


def get_dropped_field_count() -> int:
    """Return total count of unallowlisted fields dropped from operational logs."""
    return _dropped_field_count


def reset_dropped_field_count() -> None:
    """Reset the count of dropped unallowlisted fields."""
    global _dropped_field_count
    _dropped_field_count = 0


class OperationalJsonFormatter(logging.Formatter):
    """Formats LogRecords into strictly allowlisted, single-line JSON records."""

    def format(self, record: logging.LogRecord) -> str:
        global _dropped_field_count
        log_data: dict[str, Any] = {}

        # 1. Timestamp (ISO 8601 UTC)
        log_data["ts"] = datetime.now(UTC).isoformat()

        # 2. Trace Context (contextvars with fallback to record attributes)
        req_id = request_id_var.get() or getattr(record, "request_id", None)
        if req_id:
            log_data["request_id"] = str(req_id)

        ten_id = tenant_id_var.get() or getattr(record, "tenant_id", None)
        if ten_id:
            log_data["tenant_id"] = str(ten_id)

        # 3. Handle Exceptions: scrub exception message and stack trace via active
        # ExceptionScrubber safety net (Invariant 10), then record only error_type.
        if record.exc_info and record.exc_info[0]:
            if record.exc_info[1] is not None and _active_scrubber is not None:
                _ = _active_scrubber.scrub_exception(record.exc_info[1])
            record.exc_text = None
            log_data["error_type"] = record.exc_info[0].__name__
            if "status" not in log_data:
                log_data["status"] = "error"
        elif record.exc_text and _active_scrubber is not None:
            record.exc_text = _active_scrubber.scrub_text(record.exc_text)

        # 4. Extract allowlisted extra fields; reject and count unallowlisted ones
        for key, val in record.__dict__.items():
            if key in _STANDARD_LOGRECORD_ATTRS:
                continue
            if key in ALLOWED_LOG_FIELDS:
                if val is not None:
                    log_data[key] = val
            else:
                # Unallowlisted field dropped and tracked
                _dropped_field_count += 1

        # 5. Output pure JSON line
        return json.dumps(log_data, separators=(",", ":"), ensure_ascii=True)


def configure_operational_logger(
    logger_name: str | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure specified logger with the OperationalJsonFormatter."""
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(OperationalJsonFormatter())
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
    return logger
