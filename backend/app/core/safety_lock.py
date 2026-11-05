"""Safety Lock and Vision Capability Detection for Cloud and Local Models.

Enforces:
1. SAFETY LOCK: A cloud model may only be used when every active document
   in the assistant is from evals/golden/docs (synthetic). If any other
   document is active (for example Work_Tracker_FINAL.xlsx), refuse to start
   with a clear error naming the file.
2. VISION CAPABILITY: Detect whether a model supports multimodal vision.
   If a non-vision model is used, skip vision-only steps (question-time vision read,
   dual-read comparison) and fallback to OCR evidence.

No vendor names or environment reads in app/core (Architecture Boundary Invariant).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from app.ports.model_provider import is_cloud_model, is_vision_model

# Authoritative set of synthetic golden document filenames
GOLDEN_SYNTHETIC_DOCS: frozenset[str] = frozenset(
    {
        "accepted_plans.csv",
        "clean_fax.pdf",
        "clinical_protocols_manual.pdf",
        "compliance_notice.pdf",
        "eligibility_checklist.pdf",
        "faint_fax_2page.pdf",
        "fee_schedule.pdf",
        "front_desk_policy_v1.pdf",
        "front_desk_policy_v2.pdf",
        "injected_coverage_override.pdf",
        "injected_fax_redirect.pdf",
        "injected_visual_hidden.pdf",
        "invalid_npi_fax.pdf",
        "missing_field_fax.pdf",
        "pa_turnaround_times.xlsx",
        "payer_contact_table.pdf",
        "prior_auth_guide.pdf",
        "skewed_fax.pdf",
        "unreadable_field_fax.pdf",
    }
)


class CloudModelSafetyLockError(Exception):
    """Raised when a cloud model is used with non-synthetic active documents."""

    def __init__(self, filename: str, model_name: str) -> None:
        self.filename = filename
        self.model_name = model_name
        super().__init__(
            f"SAFETY LOCK REFUSAL: Cloud model '{model_name}' may only be used with "
            f"synthetic golden documents from evals/golden/docs. "
            f"Active non-synthetic document detected: '{filename}'."
        )


def verify_cloud_model_safety_lock(
    model_name: str,
    active_document_titles: Iterable[str],
    is_cloud_override: bool = False,
) -> None:
    """Verify that every active document is a synthetic golden document if a cloud model is used.

    Raises:
        CloudModelSafetyLockError: If a cloud model is used and any active document is not golden.
    """
    if not is_cloud_model(model_name, is_cloud_override=is_cloud_override):
        return

    for doc_title in active_document_titles:
        clean_title = Path(doc_title.strip()).name.lower()
        if clean_title not in GOLDEN_SYNTHETIC_DOCS:
            raise CloudModelSafetyLockError(filename=doc_title, model_name=model_name)


__all__ = [
    "GOLDEN_SYNTHETIC_DOCS",
    "CloudModelSafetyLockError",
    "is_cloud_model",
    "is_vision_model",
    "verify_cloud_model_safety_lock",
]
