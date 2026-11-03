"""Vision confidence threshold calibration script (Task Phase 2 Part B, OPEN_QUESTIONS 2.4).

Evaluates Ollama upload-time vision transcription (qwen3-vl:4b) against golden VISUAL_RECOVERY
documents from evals/golden/manifest.json.

For each page:
- Transcribes page image via ModelProvider.read_page / transcribe_page.
- Extracts confidence score from logprobs geometric mean.
- Verifies presence of key ground-truth values (names, IDs, numbers, dates; normalized).
- Writes results and summary to evals/reports/vision_calibration.json.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pypdfium2  # type: ignore[import-untyped]
from dotenv import load_dotenv

from app.ports.errors import ConfigurationError
from app.ports.types import ModelRole, PageReading
from app.wiring import create_model_provider


def normalize_value(val: str) -> str:
    """Normalize text by lowercasing, stripping punctuation, and collapsing whitespace."""
    s = val.lower().strip()
    s = re.sub(r"[\r\n\t]+", " ", s)
    s = re.sub(r"[^\w\s/]+", " ", s)
    return " ".join(s.split())


def normalize_digits_only(val: str) -> str:
    """Extract only alphanumeric characters for phone and ID comparisons."""
    return re.sub(r"\W+", "", val.lower())


def value_appears_in_reading(expected: str, reading: str) -> bool:
    """Check if expected normalized key value appears in the transcribed reading."""
    norm_expected = normalize_value(expected)
    norm_reading = normalize_value(reading)

    if norm_expected in norm_reading:
        return True

    # Check alphanumeric stripped (e.g. phone numbers '5550155' vs '555-0155'
    # or IDs 'mrn4412a' vs 'mrn-4412-a')
    digits_expected = normalize_digits_only(expected)
    digits_reading = normalize_digits_only(reading)
    if len(digits_expected) >= 4 and digits_expected in digits_reading:
        return True

    # Check word tokens for compound terms (e.g. 'MRI lumbar spine' -> 'mri' and 'lumbar')
    expected_words = norm_expected.split()
    if len(expected_words) > 1:
        all_words_present = True
        for w in expected_words:
            if len(w) > 2 and w not in norm_reading:
                all_words_present = False
                break
        if all_words_present:
            return True

    return False


# Expected key ground-truth values per golden VISUAL_RECOVERY document and page
EXPECTED_PAGE_VALUES: dict[str, dict[int, list[str]]] = {
    "clean_fax.pdf": {
        1: [
            "Dr. Marcus Vance",
            "555-0155",
            "6",
            "2 sessions",
            "3 weeks",
            "2026-02-10",
            "knee arthroscopy",
            "555-0156",
        ],
    },
    "skewed_fax.pdf": {
        1: [
            "Amoxicillin 500mg",
            "1 capsule",
            "three times daily",
            "10 days",
            "2026-02-14",
            "Alex Morgan",
            "MRN-4412-A",
            "0",
            "555-0145",
            "Dr. Evelyn Reed",
        ],
    },
    "faint_fax_2page.pdf": {
        1: [
            "Jordan Taylor",
            "MRI lumbar spine",
            "2026-03-01",
            "09:30",
            "Suite B",
            "555-0170",
        ],
        2: [
            "chronic lower back pain with radiculopathy",
            "8 weeks",
            "7/10",
            "L5",
            "555-0172",
            "Dr. Evelyn Reed",
        ],
    },
}


async def calibrate_visual_recovery_pages(
    docs_dir: Path,
    reports_dir: Path,
    profile: str = "desktop-gpu",
) -> dict[str, Any]:
    """Run calibration on golden VISUAL_RECOVERY pages and generate calibration report."""
    if "HF_HUB_OFFLINE" not in os.environ or os.environ["HF_HUB_OFFLINE"] != "1":
        raise ConfigurationError(
            "HF_HUB_OFFLINE must be set to '1' for real profiles. Offline mode is mandatory.",
            error_code="HF_HUB_OFFLINE_REQUIRED",
        )

    provider = create_model_provider(profile=profile)

    page_results: list[dict[str, Any]] = []

    for file_name, pages_dict in EXPECTED_PAGE_VALUES.items():
        pdf_path = docs_dir / file_name
        if not pdf_path.exists():
            raise FileNotFoundError(f"Golden document not found: {pdf_path}")

        pdf_doc = pypdfium2.PdfDocument(str(pdf_path))
        for page_num, expected_vals in pages_dict.items():
            print(f"--> Transcribing {file_name} (Page {page_num})...")
            start_t = time.perf_counter()

            page_idx = page_num - 1
            pdf_page = pdf_doc[page_idx]
            pil_image = pdf_page.render(scale=150.0 / 72.0).to_pil()

            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            image_bytes = buf.getvalue()

            reading_result: PageReading = await provider.transcribe_page(
                role=ModelRole.VISION,
                image=image_bytes,
            )
            elapsed = time.perf_counter() - start_t

            confidence = reading_result.confidence
            raw_text = reading_result.reading

            misread_values: list[str] = []
            for val in expected_vals:
                if not value_appears_in_reading(val, raw_text):
                    misread_values.append(val)

            is_correct = len(misread_values) == 0

            print(
                f"    Confidence: {confidence:.4f} | "
                f"Correct: {is_correct} | "
                f"Misread ({len(misread_values)}): {misread_values} | "
                f"Time: {elapsed:.2f}s"
            )

            snippet = raw_text[:300].replace("\n", " ").strip()
            page_results.append(
                {
                    "file_name": file_name,
                    "page_number": page_num,
                    "confidence": confidence,
                    "correct": is_correct,
                    "expected_values": expected_vals,
                    "misread_values": misread_values,
                    "reading_snippet": snippet,
                    "time_seconds": round(elapsed, 2),
                }
            )

    # Compute summary
    correct_confidences: list[float] = [
        p["confidence"] for p in page_results if p["correct"] and p["confidence"] is not None
    ]
    misread_confidences: list[float] = [
        p["confidence"] for p in page_results if not p["correct"] and p["confidence"] is not None
    ]

    lowest_correct = min(correct_confidences) if len(correct_confidences) > 0 else None
    highest_misread = max(misread_confidences) if len(misread_confidences) > 0 else None

    summary = {
        "total_pages_evaluated": len(page_results),
        "correct_pages_count": len(correct_confidences),
        "misread_pages_count": len(misread_confidences),
        "lowest_confidence_among_correct": lowest_correct,
        "highest_confidence_among_misreads": highest_misread,
        "current_provisional_threshold": 0.70,
    }

    report_payload = {
        "pages": page_results,
        "summary": summary,
    }

    reports_dir.mkdir(parents=True, exist_ok=True)
    report_file = reports_dir / "vision_calibration.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report_payload, f, indent=2)

    print(f"\n[OK] Calibration report written to: {report_file}")
    return report_payload


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    load_dotenv(repo_root / ".env")
    load_dotenv(repo_root / "backend" / ".env")
    load_dotenv()
    docs_dir = repo_root / "evals" / "golden" / "docs"
    reports_dir = repo_root / "evals" / "reports"

    if "APP_PROFILE" in os.environ and os.environ["APP_PROFILE"]:
        profile = os.environ["APP_PROFILE"]
    else:
        profile = "desktop-gpu"

    report = asyncio.run(calibrate_visual_recovery_pages(docs_dir, reports_dir, profile))
    summary = report["summary"]

    print("\n" + "=" * 60)
    print("VISION THRESHOLD CALIBRATION SUMMARY (OPEN_QUESTIONS 2.4)")
    print("=" * 60)
    print(f"Total Pages Evaluated:               {summary['total_pages_evaluated']}")
    print(f"Pages Fully Correct:                 {summary['correct_pages_count']}")
    print(f"Pages with Misread Key Values:       {summary['misread_pages_count']}")
    print(f"Lowest Confidence (Correct Read):    {summary['lowest_confidence_among_correct']}")
    print(f"Highest Confidence (Misread Value):  {summary['highest_confidence_among_misreads']}")
    print(f"Provisional Code Threshold:          {summary['current_provisional_threshold']}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
