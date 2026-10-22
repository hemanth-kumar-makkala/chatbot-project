"""Tests verifying Phase 4 Exit Gate: extraction benchmarks (Task 4.7).

Enforces:
- evals/reports/phase4_baseline.json exists and is valid JSON.
- Evaluated on profile 'desktop-gpu' with real models.
- False-fill rate on unreadable fields is strictly 0.0 (Invariant 6).
- Field accuracy >= 0.85 across all golden extraction documents.
- Visual recovery Hit@5 >= 0.90.
- All 5 golden extraction test cases accounted for.
- gate_passed is True.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BASELINE_REPORT_PATH = REPO_ROOT / "evals" / "reports" / "phase4_baseline.json"


def test_phase4_baseline_report_exists_and_valid() -> None:
    """Verify phase4_baseline.json exists and adheres to baseline metrics schema."""
    assert BASELINE_REPORT_PATH.exists(), f"Missing report at {BASELINE_REPORT_PATH}"

    with open(BASELINE_REPORT_PATH, encoding="utf-8") as f:
        data = json.load(f)

    assert data["profile"] == "desktop-gpu"
    assert data["gate_passed"] is True
    assert "timestamp" in data
    assert "metrics" in data
    assert "summary" in data
    assert "results" in data


def test_phase4_baseline_metrics_satisfy_invariants() -> None:
    """Verify metrics satisfy Phase 4 exit gate criteria and Invariant 6."""
    with open(BASELINE_REPORT_PATH, encoding="utf-8") as f:
        data = json.load(f)

    metrics = data["metrics"]

    # Invariant 6: False-fill rate on unreadable fields MUST be strictly 0.0
    assert metrics["false_fill_rate_on_unreadable"] == 0.0, (
        "Invariant 6 violation: false-fill rate on unreadable fields is "
        f"{metrics['false_fill_rate_on_unreadable']}"
    )
    assert metrics["unreadable_fields_filled_count"] == 0, (
        f"Invariant 6 violation: {metrics['unreadable_fields_filled_count']} "
        "unreadable fields were filled"
    )

    # Field extraction accuracy across all test fields
    assert metrics["field_accuracy"] >= 0.85, (
        f"Field accuracy below threshold: {metrics['field_accuracy']}"
    )

    # Visual recovery Hit@5
    assert metrics["visual_recovery_hit_at_5"] >= 0.90, (
        f"Visual recovery Hit@5 below threshold: {metrics['visual_recovery_hit_at_5']}"
    )


def test_phase4_all_extraction_cases_covered() -> None:
    """Verify all 5 synthetic golden extraction documents were evaluated."""
    with open(BASELINE_REPORT_PATH, encoding="utf-8") as f:
        data = json.load(f)

    results = data["results"]
    doc_names = {r["document_file"] for r in results}

    expected_docs = {
        "skewed_fax.pdf",
        "faint_fax_2page.pdf",
        "invalid_npi_fax.pdf",
        "unreadable_field_fax.pdf",
        "missing_field_fax.pdf",
    }
    assert expected_docs.issubset(doc_names), (
        f"Missing extraction test cases: {expected_docs - doc_names}"
    )

    # Verify per-document results uphold Invariant 6
    for r in results:
        for f in r.get("fields", []):
            if f.get("state") == "UNREADABLE":
                assert f.get("value") is None, (
                    f"Invariant 6 violation in {r['document_file']}: "
                    f"UNREADABLE field {f['name']} has value {f.get('value')}"
                )
