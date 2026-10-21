"""Tests verifying the Phase 3 Exit Gate baseline evaluation report (Task 3.8).

Enforces:
- evals/reports/phase3_baseline.json exists and is valid JSON.
- Evaluated on profile 'desktop-gpu'.
- Zero cross-tenant leaks (Invariant: must be zero, always).
- Hit@5 >= 0.90.
- Faithfulness >= 0.95.
- Citation Precision >= 0.90 and Recall >= 0.90.
- Refusal Accuracy >= 0.90.
- gate_passed is True.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BASELINE_REPORT_PATH = REPO_ROOT / "evals" / "reports" / "phase3_baseline.json"


def test_phase3_baseline_report_exists_and_valid() -> None:
    """Verify phase3_baseline.json exists and adheres to baseline metrics schema."""
    assert BASELINE_REPORT_PATH.exists(), f"Missing report at {BASELINE_REPORT_PATH}"

    with open(BASELINE_REPORT_PATH, encoding="utf-8") as f:
        data = json.load(f)

    assert data["profile"] == "desktop-gpu"
    assert data["gate_passed"] is True
    assert "timestamp" in data
    assert "metrics" in data
    assert "summary" in data
    assert "results" in data


def test_phase3_baseline_metrics_satisfy_invariants() -> None:
    """Verify metrics satisfy all Phase 3 exit gate criteria and safety invariants."""
    with open(BASELINE_REPORT_PATH, encoding="utf-8") as f:
        data = json.load(f)

    metrics = data["metrics"]

    # Invariant: Cross-tenant leaks must be exactly zero, always
    assert metrics["cross_tenant_leaks"] == 0, "Cross-tenant leak detected!"

    # Retrieval and answer quality baselines
    assert metrics["hit_at_5"] >= 0.90, f"Hit@5 below threshold: {metrics['hit_at_5']}"
    assert metrics["faithfulness"] >= 0.95, (
        f"Faithfulness below threshold: {metrics['faithfulness']}"
    )
    assert metrics["citation_precision"] >= 0.90, (
        f"Citation precision below threshold: {metrics['citation_precision']}"
    )
    assert metrics["citation_recall"] >= 0.90, (
        f"Citation recall below threshold: {metrics['citation_recall']}"
    )
    assert metrics["refusal_accuracy"] >= 0.90, (
        f"Refusal accuracy below threshold: {metrics['refusal_accuracy']}"
    )


def test_phase3_all_questions_accounted_for() -> None:
    """Verify all 11 benchmark questions were evaluated and passed."""
    with open(BASELINE_REPORT_PATH, encoding="utf-8") as f:
        data = json.load(f)

    summary = data["summary"]
    assert summary["total_questions"] == 11
    assert summary["passed"] == 11
    assert summary["failed"] == 0
    assert summary["accuracy"] == 1.0

    # Verify per-category coverage
    categories = summary["categories"]
    assert "TEXT_FACT" in categories
    assert "CONFLICT" in categories
    assert "TABLE" in categories
    assert "NO_ANSWER" in categories
    assert categories["TEXT_FACT"]["passed"] == 6
    assert categories["CONFLICT"]["passed"] == 2
    assert categories["TABLE"]["passed"] == 1
    assert categories["NO_ANSWER"]["passed"] == 2
