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

import hashlib
from collections.abc import Iterable
from pathlib import Path

from app.ports.model_provider import is_cloud_model, is_vision_model

GOLDEN_DOCS_PATH = Path(__file__).resolve().parents[3] / "evals" / "golden" / "docs"
GOLDEN_SYNTHETIC_HASHES_BY_NAME: dict[str, str] = {
    path.name.lower(): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(GOLDEN_DOCS_PATH.iterdir())
    if path.is_file()
}
GOLDEN_SYNTHETIC_SHA256: frozenset[str] = frozenset(GOLDEN_SYNTHETIC_HASHES_BY_NAME.values())

# Kept for display and compatibility; identity is enforced by content hash, never filename.
GOLDEN_SYNTHETIC_DOCS: frozenset[str] = frozenset(GOLDEN_SYNTHETIC_HASHES_BY_NAME)


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
    active_document_hashes: Iterable[str] | None = None,
    is_cloud_override: bool = False,
) -> None:
    """Allow cloud models only when every active stored-file hash is golden."""
    if not is_cloud_model(model_name, is_cloud_override=is_cloud_override):
        return

    titles = list(active_document_titles)
    hashes = list(active_document_hashes or [])
    if len(hashes) != len(titles):
        filename = titles[0] if titles else "(unverifiable active document)"
        raise CloudModelSafetyLockError(filename=filename, model_name=model_name)
    for title, content_hash in zip(titles, hashes, strict=True):
        if content_hash.lower() not in GOLDEN_SYNTHETIC_SHA256:
            raise CloudModelSafetyLockError(filename=title, model_name=model_name)


__all__ = [
    "GOLDEN_SYNTHETIC_DOCS",
    "GOLDEN_SYNTHETIC_HASHES_BY_NAME",
    "GOLDEN_SYNTHETIC_SHA256",
    "CloudModelSafetyLockError",
    "is_cloud_model",
    "is_vision_model",
    "verify_cloud_model_safety_lock",
]
