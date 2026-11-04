"""Unit tests for Presidio Exception Telemetry Scrubber (Phase 5 Task 5.4, Invariant 10).

Verifies:
1. Synthetic exceptions and stack traces containing a fake patient name, phone number,
   MRN, date of birth, and email are scrubbed before any operational log line is written.
2. Safety net integration with OperationalJsonFormatter (allowlist remains primary defense).
3. Offline operation: forces en_core_web_sm explicitly and tldextract's bundled offline
   suffix list with zero network calls attempted.
4. Real profiles fail at startup with a typed ConfigurationError if the spaCy model
   (en_core_web_sm) is missing, never silently logging unscrubbed text.
5. CI (mock profile) uses MockExceptionScrubber without requiring spaCy model weights.
"""

from __future__ import annotations

import io
import json
import logging
import socket
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from app.adapters.scrubbing.mock import MockExceptionScrubber
from app.adapters.scrubbing.presidio import PresidioExceptionScrubber
from app.api.app import create_app
from app.api.config import ApiSettings
from app.observability.logger import (
    ALLOWED_LOG_FIELDS,
    OperationalJsonFormatter,
    get_exception_scrubber,
    request_id_var,
    scrub_exception_for_log,
    set_exception_scrubber,
    tenant_id_var,
)
from app.ports.errors import ConfigurationError
from app.ports.scrubber import ExceptionScrubber, ScrubbedExceptionData
from app.wiring import create_exception_scrubber


def _raise_synthetic_phi_exception() -> None:
    """Helper that raises a multi-frame exception containing all 5 required PHI types."""
    patient_record = (
        "Patient: Evelyn Vance | Phone: (555) 234-5678 | "
        "MRN: MRN-994821 | DOB: 1984-07-19 | "
        "Email: evelyn.vance@synthetic-clinic.org"
    )
    raise RuntimeError(f"Database constraint failure while indexing record -> {patient_record}")


class TestMockExceptionScrubberForCI:
    """CI-safe tests (no spaCy model weights required) verifying scrubber contract & wiring."""

    def test_mock_scrubber_scrubs_patient_name_phone_mrn_dob_and_email(self) -> None:
        scrubber = MockExceptionScrubber()
        assert isinstance(scrubber, ExceptionScrubber)

        try:
            _raise_synthetic_phi_exception()
        except RuntimeError as exc:
            result = scrubber.scrub_exception(exc)

        assert isinstance(result, ScrubbedExceptionData)
        assert result.error_type == "RuntimeError"

        for forbidden_phi in (
            "Evelyn Vance",
            "(555) 234-5678",
            "234-5678",
            "MRN-994821",
            "994821",
            "1984-07-19",
            "evelyn.vance@synthetic-clinic.org",
        ):
            assert forbidden_phi not in result.scrubbed_message, (
                f"PHI '{forbidden_phi}' leaked in scrubbed_message: {result.scrubbed_message}"
            )
            assert forbidden_phi not in result.scrubbed_traceback, (
                f"PHI '{forbidden_phi}' leaked in scrubbed_traceback: {result.scrubbed_traceback}"
            )

        assert sum(result.redacted_entity_counts.values()) >= 5

    def test_operational_logger_scrubs_exception_before_writing_and_preserves_allowlist(
        self,
    ) -> None:
        scrubber = MockExceptionScrubber()
        prev_scrubber = get_exception_scrubber()
        set_exception_scrubber(scrubber)

        stream = io.StringIO()
        logger = logging.getLogger("test_presidio_safety_net_logger")
        logger.disabled = False
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.handlers.clear()

        handler = logging.StreamHandler(stream)
        handler.setFormatter(OperationalJsonFormatter())
        logger.addHandler(handler)

        request_id_var.set("req-scrub-001")
        tenant_id_var.set("tenant-scrub-001")

        try:
            try:
                _raise_synthetic_phi_exception()
            except RuntimeError as exc:
                scrubbed = scrub_exception_for_log(exc)
                for forbidden_phi in (
                    "Evelyn Vance",
                    "(555) 234-5678",
                    "MRN-994821",
                    "1984-07-19",
                    "evelyn.vance@synthetic-clinic.org",
                ):
                    assert forbidden_phi not in scrubbed.scrubbed_message
                    assert forbidden_phi not in scrubbed.scrubbed_traceback

                logger.error(
                    f"Unhandled error: {exc}",
                    exc_info=exc,
                    extra={
                        "node": "retrieve",
                        "status": "error",
                        "error_code": "ERR_DB_FAILURE",
                        "error_type": scrubbed.error_type,
                    },
                )
        finally:
            set_exception_scrubber(prev_scrubber)

        output = stream.getvalue().strip()
        assert output
        for forbidden_phi in (
            "Evelyn Vance",
            "(555) 234-5678",
            "MRN-994821",
            "1984-07-19",
            "evelyn.vance@synthetic-clinic.org",
        ):
            assert forbidden_phi not in output

        data = json.loads(output)
        assert set(data.keys()) <= ALLOWED_LOG_FIELDS
        assert data["error_type"] == "RuntimeError"
        assert data["error_code"] == "ERR_DB_FAILURE"
        assert data["status"] == "error"

    def test_real_profiles_fail_at_startup_with_configuration_error_when_spacy_model_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """If en_core_web_sm is unavailable, real profiles fail at startup with ConfigurationError."""
        monkeypatch.setattr("spacy.util.is_package", lambda _name: False)

        for real_profile in ("desktop-gpu", "office-cpu", "home-gpu", "cloud"):
            with pytest.raises(ConfigurationError) as exc_info:
                create_exception_scrubber(real_profile)
            assert exc_info.value.error_code == "SPACY_MODEL_MISSING"
            assert "en_core_web_sm" in str(exc_info.value)

        # Also verify create_app fails at startup on real profile when spaCy model is missing
        settings = ApiSettings(
            profile="desktop-gpu",
            storage_dir=str(tmp_path),
            session_upload_retention_hours=24,
        )
        with pytest.raises(ConfigurationError) as app_exc_info:
            create_app(settings=settings)
        assert app_exc_info.value.error_code == "SPACY_MODEL_MISSING"

    def test_mock_profile_starts_cleanly_without_spacy_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """CI runs APP_PROFILE=mock without scrubbing group installed and starts cleanly."""
        monkeypatch.setattr("spacy.util.is_package", lambda _name: False)
        settings = ApiSettings(
            profile="mock",
            storage_dir=str(tmp_path),
            session_upload_retention_hours=24,
        )
        app = create_app(settings=settings)
        assert isinstance(app.state.exception_scrubber, MockExceptionScrubber)


@pytest.mark.models
class TestPresidioExceptionScrubberRealModel:
    """Real Microsoft Presidio + spaCy en_core_web_sm tests (@pytest.mark.models)."""

    def test_presidio_scrubber_scrubs_patient_name_phone_mrn_dob_and_email(self) -> None:
        scrubber = PresidioExceptionScrubber()

        try:
            _raise_synthetic_phi_exception()
        except RuntimeError as exc:
            result = scrubber.scrub_exception(exc)

        assert result.error_type == "RuntimeError"

        for forbidden_phi in (
            "Evelyn Vance",
            "(555) 234-5678",
            "234-5678",
            "MRN-994821",
            "994821",
            "1984-07-19",
            "evelyn.vance@synthetic-clinic.org",
        ):
            assert forbidden_phi not in result.scrubbed_message, (
                f"Real Presidio failed to scrub '{forbidden_phi}' from message: "
                f"{result.scrubbed_message}"
            )
            assert forbidden_phi not in result.scrubbed_traceback, (
                f"Real Presidio failed to scrub '{forbidden_phi}' from traceback: "
                f"{result.scrubbed_traceback}"
            )

        assert "<" in result.scrubbed_message and ">" in result.scrubbed_message
        assert sum(result.redacted_entity_counts.values()) >= 5

    def test_presidio_scrubber_works_strictly_offline_with_zero_network_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Proves Presidio uses en_core_web_sm and offline tldextract snapshot with 0 network calls."""

        def _forbid_network(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(
                f"Forbidden network call attempted during Presidio initialization or scrubbing: "
                f"args={args}, kwargs={kwargs}"
            )

        monkeypatch.setattr(socket.socket, "connect", _forbid_network)
        monkeypatch.setattr(socket, "create_connection", _forbid_network)
        monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)

        scrubber = PresidioExceptionScrubber()
        try:
            raise ValueError(
                "Failed parsing intake for Patient Marcus Sterling, "
                "phone 555-839-2011, MRN: 774120, DOB 03/14/1979, "
                "email marcus.sterling@example-health.org, "
                "portal https://portal.example-health.org/intake"
            )
        except ValueError as exc:
            result = scrubber.scrub_exception(exc)

        for forbidden_phi in (
            "Marcus Sterling",
            "555-839-2011",
            "774120",
            "03/14/1979",
            "marcus.sterling@example-health.org",
        ):
            assert forbidden_phi not in result.scrubbed_message
            assert forbidden_phi not in result.scrubbed_traceback

