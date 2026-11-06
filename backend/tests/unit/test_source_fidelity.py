"""Source fidelity regression tests (Items 1 and 3).

Verifies:
1. Apex SLA: pa_turnaround_times.xlsx row 3 contains exact cell values:
   Payer Name: 'Apex Health Insurance', Service Category: 'Diagnostic Imaging',
   Urgency: 'Routine', Standard SLA: '48 Hours'.
2. Prior auth documentation checklist in prior_auth_guide.pdf specifies
   'Diagnostic imaging reports and relevant laboratory panels', and does NOT
   contain 'imaging requisition' (proving zero source mixing).
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pypdfium2  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_DIR = REPO_ROOT / "evals" / "golden" / "docs"


def test_apex_diagnostic_imaging_sla_exact_cells() -> None:
    """Verify pa_turnaround_times.xlsx has 48 Hours for Apex diagnostic imaging."""
    xlsx_path = DOCS_DIR / "pa_turnaround_times.xlsx"
    assert xlsx_path.is_file(), f"Missing {xlsx_path}"

    wb = openpyxl.load_workbook(xlsx_path)
    sheet = wb["Sheet1"]

    # Header is row 2, Apex Diagnostic Imaging is row 3
    apex_row = list(sheet.iter_rows(values_only=True))[2]
    payer, service, urgency, standard_sla, liaison = apex_row

    assert payer == "Apex Health Insurance"
    assert service == "Diagnostic Imaging"
    assert urgency == "Routine"
    assert standard_sla == "48 Hours"
    assert liaison == "555-0120"
    assert "5 days" not in str(standard_sla).lower()


def test_prior_auth_guide_source_fidelity_no_imaging_requisition() -> None:
    """Check prior auth guide wording for imaging reports, not requisitions."""
    pdf_path = DOCS_DIR / "prior_auth_guide.pdf"
    assert pdf_path.is_file(), f"Missing {pdf_path}"

    doc = pypdfium2.PdfDocument(pdf_path)
    full_text = "\n".join(page.get_textpage().get_text_range() for page in doc)

    # Must contain exact checklist items
    assert "Complete clinical consultation notes within past 30 days" in full_text
    assert "Diagnostic imaging reports and relevant laboratory panels" in full_text
    assert "Proposed CPT procedure codes and ICD-10 diagnostic indications" in full_text

    # Must not contain "imaging requisition" (which was an inaccurate test paraphrase)
    assert "imaging requisition" not in full_text.lower()
