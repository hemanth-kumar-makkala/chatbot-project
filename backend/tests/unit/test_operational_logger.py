"""Unit tests for the Operational Logger (Task 1.4b).

Enforces:
- Strict field allowlist: ts, request_id, tenant_id, node, status, latency_ms, counts,
  route, profile, model_id, error_code, error_type.
- Any unallowlisted field (e.g. question_body, prompt, text) is rejected/dropped and counted.
- No free-form message or unvetted text in log output.
- Exceptions record only error_type and error_code; exception messages and stack traces
  are excluded.
- request_id and tenant_id propagate via contextvars.
"""

import io
import json
import logging

import pytest

from app.observability.logger import (
    ALLOWED_LOG_FIELDS,
    OperationalJsonFormatter,
    get_dropped_field_count,
    request_id_var,
    reset_dropped_field_count,
    tenant_id_var,
)


class TestOperationalLoggerAllowlist:
    """Validates that operational logs strictly conform to the field allowlist."""

    @pytest.fixture(autouse=True)
    def setup_logging(self) -> tuple[logging.Logger, io.StringIO]:
        reset_dropped_field_count()
        stream = io.StringIO()
        logger = logging.getLogger(f"test_logger_{id(stream)}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.handlers.clear()

        handler = logging.StreamHandler(stream)
        handler.setFormatter(OperationalJsonFormatter())
        logger.addHandler(handler)
        return logger, stream

    def test_operational_logger_allows_valid_fields(
        self, setup_logging: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = setup_logging
        request_id_var.set("req_test_123")
        tenant_id_var.set("tenant_abc")

        logger.info(
            "Node completed",
            extra={
                "node": "retrieve",
                "status": "ok",
                "latency_ms": 142.5,
                "counts": 12,
                "route": "knowledge",
                "profile": "mock",
                "model_id": "test_model_v1",
                "error_code": None,
                "error_type": None,
            },
        )

        output = stream.getvalue().strip()
        data = json.loads(output)

        assert data["request_id"] == "req_test_123"
        assert data["tenant_id"] == "tenant_abc"
        assert data["node"] == "retrieve"
        assert data["status"] == "ok"
        assert data["latency_ms"] == 142.5
        assert data["counts"] == 12
        assert data["route"] == "knowledge"
        assert data["profile"] == "mock"
        assert data["model_id"] == "test_model_v1"
        assert "ts" in data

        # Ensure all keys in log line belong strictly to ALLOWED_LOG_FIELDS
        for key in data:
            assert key in ALLOWED_LOG_FIELDS, f"Unexpected key '{key}' found in operational log"

    def test_operational_logger_drops_unallowlisted_fields_and_counts(
        self, setup_logging: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = setup_logging
        request_id_var.set("req_test_456")
        tenant_id_var.set("tenant_xyz")

        # Attempt to inject sensitive/unallowlisted content fields
        logger.info(
            "Node execution attempt",
            extra={
                "node": "guard_input",
                "status": "ok",
                "latency_ms": 15.0,
                "question_body": "What is patient John Doe's diagnosis?",
                "prompt_content": "You are a helpful assistant...",
                "document_text": "Sensitive PHI record...",
                "answer_text": "Patient has condition X",
            },
        )

        output = stream.getvalue().strip()
        data = json.loads(output)

        # Unallowlisted fields must NEVER appear in formatted JSON
        assert "question_body" not in data
        assert "prompt_content" not in data
        assert "document_text" not in data
        assert "answer_text" not in data
        assert "John Doe" not in output
        assert "Sensitive PHI" not in output

        # Dropped fields counter must have registered all 4 dropped keys
        assert get_dropped_field_count() >= 4

    def test_no_freeform_message_in_output(
        self, setup_logging: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = setup_logging
        request_id_var.set("req_999")
        tenant_id_var.set("tenant_999")

        # The logger should not emit a free-form "message" field containing the format string
        logger.info(
            "Free-form message with sensitive query: SELECT * FROM patient_records",
            extra={"node": "route", "status": "ok", "latency_ms": 5.0},
        )

        output = stream.getvalue().strip()
        data = json.loads(output)

        assert "message" not in data
        assert "SELECT * FROM patient_records" not in output
        assert data["node"] == "route"

    def test_exception_logging_scrubs_message_and_stack(
        self, setup_logging: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = setup_logging
        request_id_var.set("req_err_01")
        tenant_id_var.set("tenant_err_01")

        try:
            # Raise exception containing sensitive PHI or query text in message
            raise ValueError("Failure while processing SSN 000-12-3456 for patient Jane Doe")
        except ValueError as exc:
            logger.error(
                "Step execution failed",
                exc_info=exc,
                extra={
                    "node": "validate",
                    "status": "error",
                    "error_code": "ERR_VALIDATION_FAILED",
                    "error_type": type(exc).__name__,
                },
            )

        output = stream.getvalue().strip()
        data = json.loads(output)

        # Only error_type and error_code are preserved
        assert data["error_type"] == "ValueError"
        assert data["error_code"] == "ERR_VALIDATION_FAILED"
        assert data["status"] == "error"

        # Neither the raw exception message nor stack trace may leak into operational logs
        assert "000-12-3456" not in output
        assert "Jane Doe" not in output
        assert "Traceback" not in output
        assert "exc_info" not in data
        assert "stack" not in data
