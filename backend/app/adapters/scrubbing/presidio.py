"""Microsoft Presidio exception telemetry scrubber adapter (Phase 5 Task 5.4).

Enforces:
- Works strictly offline:
  1. Forces tldextract to use its bundled offline Public Suffix List snapshot
     (suffix_list_urls=(), cache_dir=None, fallback_to_snapshot=True).
  2. Explicitly checks that spaCy model 'en_core_web_sm' is installed via
     spacy.util.is_package('en_core_web_sm') and configures Presidio's
     NlpEngineProvider to load 'en_core_web_sm' directly without ever attempting
     to download 'en_core_web_lg' or any remote model.
- Fails at startup with a typed ConfigurationError (error_code='SPACY_MODEL_MISSING')
  if 'en_core_web_sm' is unavailable, never silently logging unscrubbed text.
- Scrubs patient names (PERSON), phone numbers (PHONE_NUMBER), medical record numbers
  (MEDICAL_RECORD_NUMBER), dates of birth (DATE_TIME), emails (EMAIL_ADDRESS), and
  SSNs (US_SSN) from exception messages and stack traces.
"""

from __future__ import annotations

import traceback
from typing import ClassVar

import spacy.util
import tldextract
import tldextract.tldextract
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine

from app.ports.errors import ConfigurationError
from app.ports.scrubber import ExceptionScrubber, ScrubbedExceptionData

SPACY_MODEL_NAME: str = "en_core_web_sm"
SPACY_MODEL_MISSING_ERROR_CODE: str = "SPACY_MODEL_MISSING"
SPACY_MODEL_MISSING_MESSAGE: str = (
    "SPACY_MODEL_MISSING: Required spaCy model 'en_core_web_sm' is not installed."
)


def _configure_offline_tldextract() -> None:
    """Force tldextract to use its bundled offline snapshot and never make HTTP requests."""
    offline_extractor = tldextract.TLDExtract(
        suffix_list_urls=(),
        cache_dir=None,
        fallback_to_snapshot=True,
    )
    tldextract.tldextract.TLD_EXTRACTOR = offline_extractor


def _build_clinical_recognizers() -> list[PatternRecognizer]:
    """Build deterministic clinical PatternRecognizers supplementing spaCy NER."""
    mrn_recognizer = PatternRecognizer(
        supported_entity="MEDICAL_RECORD_NUMBER",
        name="ClinicalMrnRecognizer",
        patterns=[
            Pattern(
                name="mrn_labeled",
                regex=r"\bMRN[-:\s#]*[A-Za-z0-9-]{3,20}\b",
                score=0.95,
            ),
            Pattern(
                name="mrn_prefixed_code",
                regex=r"\b[A-Z]{2,5}-MRN-\d{3,12}\b",
                score=0.95,
            ),
        ],
    )

    phone_recognizer = PatternRecognizer(
        supported_entity="PHONE_NUMBER",
        name="ClinicalPhoneRecognizer",
        patterns=[
            Pattern(
                name="us_10_digit_phone",
                regex=(
                    r"(?:\+?1[-.\s]?)?\(\d{3}\)\s*\d{3}[-.\s]\d{4}"
                    r"|\b(?:\+?1[-.\s]?)?\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"
                ),
                score=0.90,
            ),
            Pattern(
                name="clinic_7_digit_phone",
                regex=r"\b\d{3}-\d{4}\b",
                score=0.85,
            ),
        ],
    )

    dob_recognizer = PatternRecognizer(
        supported_entity="DATE_TIME",
        name="ClinicalDobRecognizer",
        patterns=[
            Pattern(
                name="iso_or_slash_date",
                regex=(
                    r"\b(?:(?:DOB|Date\s+of\s+Birth|birth\s+date)[-:\s]*)?"
                    r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b"
                ),
                score=0.90,
            ),
        ],
    )

    patient_name_recognizer = PatternRecognizer(
        supported_entity="PERSON",
        name="ClinicalPatientNameRecognizer",
        patterns=[
            Pattern(
                name="labeled_patient_name",
                regex=r"(?<=\bPatient:\s)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+",
                score=0.90,
            ),
            Pattern(
                name="unlabeled_patient_prefix",
                regex=r"(?<=\bPatient\s)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+",
                score=0.90,
            ),
            Pattern(
                name="lowercase_patient_prefix",
                regex=r"(?<=\bpatient\s)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+",
                score=0.90,
            ),
        ],
    )

    email_fallback_recognizer = PatternRecognizer(
        supported_entity="EMAIL_ADDRESS",
        name="ClinicalEmailFallbackRecognizer",
        patterns=[
            Pattern(
                name="standard_or_synthetic_email",
                regex=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
                score=0.95,
            ),
        ],
    )

    ssn_recognizer = PatternRecognizer(
        supported_entity="US_SSN",
        name="ClinicalSsnPatternRecognizer",
        patterns=[
            Pattern(
                name="ssn_dashed",
                regex=r"\b\d{3}-\d{2}-\d{4}\b",
                score=0.90,
            ),
        ],
    )

    return [
        mrn_recognizer,
        phone_recognizer,
        dob_recognizer,
        patient_name_recognizer,
        email_fallback_recognizer,
        ssn_recognizer,
    ]


class PresidioExceptionScrubber(ExceptionScrubber):
    """Offline Microsoft Presidio exception scrubber using en_core_web_sm."""

    TARGET_ENTITIES: ClassVar[list[str]] = [
        "PERSON",
        "PHONE_NUMBER",
        "MEDICAL_RECORD_NUMBER",
        "DATE_TIME",
        "EMAIL_ADDRESS",
        "US_SSN",
        "URL",
    ]

    _shared_analyzer: ClassVar[AnalyzerEngine | None] = None
    _shared_anonymizer: ClassVar[AnonymizerEngine | None] = None

    def __init__(self, model_name: str = SPACY_MODEL_NAME) -> None:
        if not spacy.util.is_package(model_name):
            raise ConfigurationError(
                SPACY_MODEL_MISSING_MESSAGE,
                error_code=SPACY_MODEL_MISSING_ERROR_CODE,
            )

        _configure_offline_tldextract()

        if (
            PresidioExceptionScrubber._shared_analyzer is None
            or PresidioExceptionScrubber._shared_anonymizer is None
            or model_name != SPACY_MODEL_NAME
        ):
            provider = NlpEngineProvider(
                nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": [{"lang_code": "en", "model_name": model_name}],
                }
            )
            nlp_engine = provider.create_engine()
            analyzer = AnalyzerEngine(
                nlp_engine=nlp_engine,
                supported_languages=["en"],
            )
            for custom_rec in _build_clinical_recognizers():
                analyzer.registry.add_recognizer(custom_rec)

            anonymizer = AnonymizerEngine()
            if model_name == SPACY_MODEL_NAME:
                PresidioExceptionScrubber._shared_analyzer = analyzer
                PresidioExceptionScrubber._shared_anonymizer = anonymizer
            self._analyzer = analyzer
            self._anonymizer = anonymizer
        else:
            self._analyzer = PresidioExceptionScrubber._shared_analyzer
            self._anonymizer = PresidioExceptionScrubber._shared_anonymizer

    def _scrub_with_counts(self, text: str) -> tuple[str, dict[str, int]]:
        if not text:
            return "", {}

        results = self._analyzer.analyze(
            text=text,
            language="en",
            entities=self.TARGET_ENTITIES,
        )
        counts: dict[str, int] = {}
        for res in results:
            counts[res.entity_type] = counts.get(res.entity_type, 0) + 1

        anonymized = self._anonymizer.anonymize(
            text=text,
            analyzer_results=results,  # type: ignore[arg-type]
        )
        return anonymized.text, counts

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
