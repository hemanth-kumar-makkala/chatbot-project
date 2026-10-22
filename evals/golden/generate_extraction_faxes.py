"""Generator for the three synthetic EXTRACTION faxes (Phase 4 Task 4.7).

Produces, deterministically, into evals/golden/docs/:
- invalid_npi_fax.pdf       prescriber NPI is printed but its check digit is wrong
- unreadable_field_fax.pdf  the clinical justification text is blacked out (field exists,
                            cannot be read)
- missing_field_fax.pdf     several schema fields are simply not printed on the form

The printed lines below are the single source of truth for the expected values in
evals/golden/extractions.json. Every name, number and identifier is synthetic.

Run from the repository root:  python evals/golden/generate_extraction_faxes.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from PIL import ImageDraw

GOLDEN_DIR = Path(__file__).resolve().parent
DOCS_DIR = GOLDEN_DIR / "docs"

# Pixel geometry mirrors generate._create_scanned_image: content starts at y=250, 55 px per line.
CONTENT_TOP_Y = 250
LINE_HEIGHT = 55
CONTENT_LEFT_X = 150
CONTENT_RIGHT_X = 1550
BLACKOUT_PADDING = 12

INVALID_NPI_FAX_LINES: list[str] = [
    "URGENT CLINIC INTAKE FAX",
    "Date: 2026-02-14",
    "Patient Name: Casey Bennett",
    "Chart Number: MRN-1102-K",
    "Authorized Medication: Amoxicillin 500mg",
    "Authorization Date: 2026-02-14",
    "Prescriber NPI: 1982736451",
    "Prescriber Signature: Dr. Evelyn Reed",
    "Clinic Phone: 555-0145",
]

# The line below the "Clinical Justification:" label is printed, then fully covered.
UNREADABLE_FAX_HIDDEN_TEXT: str = "Persistent hypertension despite two prior agents."
UNREADABLE_FAX_LINES: list[str] = [
    "PRIOR AUTHORIZATION CLINICAL JUSTIFICATION FAX",
    "Date: 2026-02-14",
    "Patient Name: Taylor Hayes",
    "Authorized Medication: Lisinopril 10mg",
    "Authorization Date: 2026-02-14",
    "Prescriber NPI: 1234567893",
    "Clinical Justification:",
    UNREADABLE_FAX_HIDDEN_TEXT,
    "Clinic Phone: 555-0145",
]
UNREADABLE_FAX_BLACKOUT_LINE_INDEX: int = 7

MISSING_FIELD_FAX_LINES: list[str] = [
    "STANDARDIZED PRESCRIPTION INTAKE FAX",
    "Date: 2026-02-14",
    "Patient Name: Morgan Bailey",
    "Authorized Medication: Atorvastatin 20mg",
    "Authorization Date: 2026-02-14",
    "Prescriber NPI: 1234567893",
    "Clinic Phone: 555-0145",
]


def _load_golden_helpers() -> ModuleType:
    spec = importlib.util.spec_from_file_location("golden_generate", GOLDEN_DIR / "generate.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load evals/golden/generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_extraction_fax_pdfs() -> dict[str, bytes]:
    """Render the three faxes and return {file_name: pdf_bytes}."""
    helpers = _load_golden_helpers()

    invalid_img = helpers._create_scanned_image(INVALID_NPI_FAX_LINES, faint=False, rotation_deg=0.0)

    unreadable_img = helpers._create_scanned_image(
        UNREADABLE_FAX_LINES, faint=False, rotation_deg=0.0
    )
    blackout_top = (
        CONTENT_TOP_Y + LINE_HEIGHT * UNREADABLE_FAX_BLACKOUT_LINE_INDEX - BLACKOUT_PADDING
    )
    ImageDraw.Draw(unreadable_img).rectangle(
        [
            (CONTENT_LEFT_X - BLACKOUT_PADDING, blackout_top),
            (CONTENT_RIGHT_X, blackout_top + LINE_HEIGHT + BLACKOUT_PADDING),
        ],
        fill=(0, 0, 0),
    )

    missing_img = helpers._create_scanned_image(
        MISSING_FIELD_FAX_LINES, faint=False, rotation_deg=0.0
    )

    return {
        "invalid_npi_fax.pdf": helpers._save_scanned_pdf([invalid_img]),
        "unreadable_field_fax.pdf": helpers._save_scanned_pdf([unreadable_img]),
        "missing_field_fax.pdf": helpers._save_scanned_pdf([missing_img]),
    }


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for file_name, pdf_bytes in build_extraction_fax_pdfs().items():
        (DOCS_DIR / file_name).write_bytes(pdf_bytes)
        print(f"wrote {DOCS_DIR / file_name} ({len(pdf_bytes)} bytes)")


if __name__ == "__main__":
    main()
