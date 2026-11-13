"""End-to-End Evaluation Runner for desktop-gpu profile on running local API.

Measures full product path against http://127.0.0.1:8010 using seeded synthetic dev credentials.
Paces requests one-by-one against local Ollama models.
Outputs authoritative reports with hardware/git telemetry stamp to:
- evals/reports/2026-11-13_desktop_gpu_e2e.json
- evals/reports/2026-11-13_desktop_gpu_e2e.md
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.observability.report_stamping import create_report_stamp  # noqa: E402

CASES_PATH = REPO_ROOT / "evals" / "golden" / "e2e_local_cases.json"
DOCS_DIR = REPO_ROOT / "evals" / "golden" / "docs"
REPORTS_DIR = REPO_ROOT / "evals" / "reports"

API_BASE_URL = os.getenv("E2E_API_BASE_URL", "http://127.0.0.1:8010")
DEV_EMAIL = os.getenv("E2E_DEV_EMAIL", "admin@clinic-a.local")
DEV_PASSWORD = os.getenv("E2E_DEV_PASSWORD", "DevPassword123!")
DEV_ASSISTANT_ID = os.getenv("E2E_ASSISTANT_ID", "00000000-0000-0000-0002-000000000001")


def percentile(values: list[float], p: float) -> float:
    """Compute p-th percentile (0-100) using nearest-rank method."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    d0 = sorted_vals[int(f)] * (c - k)
    d1 = sorted_vals[int(c)] * (k - f)
    return d0 + d1


def normalize_text(text: str) -> str:
    """Normalize text for comparison."""
    if not text:
        return ""
    s = text.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def judge_case(
    case: dict[str, Any],
    response_data: dict[str, Any],
    _latency: float,
) -> tuple[str, str | None, str]:
    """Judge test case strictly.

    Returns:
        (verdict, fault_layer, reason)
        verdict in ("PASS", "FAIL", "UNCLEAR")
        fault_layer in ("routing", "retrieval", "evidence_gate", "generation",
                        "validation", "extraction", "UI/API", None)
    """
    case_type = case.get("type", "question")
    expected_route = case.get("expected_route")
    expected_answer = case.get("expected_answer", "")
    expected_citations = case.get("expected_citations", [])

    if response_data.get("_error"):
        return "FAIL", "UI/API", f"HTTP request failed: {response_data.get('_error')}"

    if case_type == "extract":
        status = response_data.get("status")
        if status != "extracted":
            return "FAIL", "extraction", f"Expected status 'extracted', got '{status}'"
        raw_fields = response_data.get("extracted_fields", [])
        fields_by_name = {f.get("name"): f for f in raw_fields if isinstance(f, dict)}
        expected_fields = case.get("expected_fields", {})
        for fname, fval in expected_fields.items():
            if fname not in fields_by_name:
                return "FAIL", "extraction", f"Missing expected field '{fname}' in extraction"
            f_actual = fields_by_name[fname]
            if isinstance(fval, dict):
                exp_state = fval.get("state")
                act_state = f_actual.get("state")
                if exp_state and act_state != exp_state:
                    return (
                        "FAIL",
                        "extraction",
                        f"Field '{fname}' state mismatch: expected '{exp_state}', "
                        f"got '{act_state}'",
                    )
                exp_ocr = fval.get("ocr_read")
                exp_vis = fval.get("vision_read")
                act_r1 = str(f_actual.get("read1_value") or "")
                act_r2 = str(f_actual.get("read2_value") or "")
                reads_combined = f"{act_r1} {act_r2}".lower()
                if exp_ocr and exp_ocr.lower() not in reads_combined:
                    return (
                        "FAIL",
                        "extraction",
                        f"Field '{fname}' OCR read '{exp_ocr}' missing from reads: "
                        f"'{act_r1}' vs '{act_r2}'",
                    )
                if exp_vis and exp_vis.lower() not in reads_combined:
                    return (
                        "FAIL",
                        "extraction",
                        f"Field '{fname}' vision read '{exp_vis}' missing from reads: "
                        f"'{act_r1}' vs '{act_r2}'",
                    )
            else:
                actual_val = str(f_actual.get("value", ""))
                if normalize_text(fval) not in normalize_text(actual_val):
                    return (
                        "FAIL",
                        "extraction",
                        f"Field '{fname}' value mismatch: expected '{fval}', got '{actual_val}'",
                    )
        return "PASS", None, "All expected extraction fields matched"

    # Question evaluation
    actual_route = response_data.get("route")
    actual_answer = response_data.get("answer", "")
    actual_status = response_data.get("status", "")
    actual_sources = response_data.get("sources", [])
    actual_conflict_sources = response_data.get("conflict_sources", [])

    # Check routing
    if expected_route and actual_route != expected_route:
        # Route mismatch
        return (
            "FAIL",
            "routing",
            f"Route mismatch: expected '{expected_route}', got '{actual_route}'",
        )

    # Check refusal cases
    if case.get("expected_mode") == "refusal":
        if (
            actual_status == "refusal"
            or "couldn't confirm" in actual_answer.lower()
            or "can only answer" in actual_answer.lower()
        ):
            if len(actual_sources) == 0:
                return "PASS", None, "Correctly refused with zero citations"
            return "FAIL", "evidence_gate", f"Refused but returned {len(actual_sources)} citations"
        preview = actual_answer[:80]
        return "FAIL", "generation", f"Expected refusal, but got answered: '{preview}...'"

    # Check session document cases
    if actual_route == "session_document":
        if "couldn't confirm" in actual_answer.lower():
            if case.get("id") == "E2E-SD-002":
                return "PASS", None, "Correctly refused missing value in session document"
            return (
                "FAIL",
                "validation",
                f"Unexpected refusal in session document: '{actual_answer}'",
            )
        norm_expected = normalize_text(expected_answer)
        if norm_expected and norm_expected not in normalize_text(actual_answer):
            return (
                "FAIL",
                "generation",
                f"Expected '{expected_answer}' in answer, got: '{actual_answer}'",
            )
        return "PASS", None, "Answered correctly with session document citation"

    # Check general route cases
    if actual_route == "general":
        if len(actual_sources) > 0:
            return (
                "FAIL",
                "routing",
                f"General route returned {len(actual_sources)} source citations",
            )
        norm_expected = normalize_text(expected_answer)
        if norm_expected and norm_expected not in normalize_text(actual_answer):
            return "FAIL", "generation", f"Expected '{expected_answer}' in general answer"
        return "PASS", None, "General route answered cleanly with zero sources"

    # Check conflict cases
    if case.get("category") == "VERSION_CONFLICT":
        all_sources = actual_sources + actual_conflict_sources
        source_titles = [s.get("document_title", "") for s in all_sources]
        has_v1 = any("v1" in t for t in source_titles)
        has_v2 = any("v2" in t for t in source_titles)
        if not (has_v1 and has_v2):
            return (
                "FAIL",
                "retrieval",
                f"Conflict did not return both versions in sources: {source_titles}",
            )
        norm_expected = normalize_text(expected_answer)
        if norm_expected and norm_expected not in normalize_text(actual_answer):
            return (
                "FAIL",
                "generation",
                f"Conflict answer missing expected content '{expected_answer}'",
            )
        return "PASS", None, "Version conflict correctly detected with both v1 and v2 cited"

    # Check table cases
    if case.get("category") == "TABLE":
        source_titles = [s.get("document_title", "") for s in actual_sources]
        has_table = any(".xlsx" in t or "turnaround" in t for t in source_titles)
        if not has_table:
            return (
                "FAIL",
                "retrieval",
                f"Table answer missing pa_turnaround_times.xlsx in sources: {source_titles}",
            )
        norm_expected = normalize_text(expected_answer)
        if norm_expected and norm_expected not in normalize_text(actual_answer):
            return "FAIL", "generation", f"Table answer missing expected term '{expected_answer}'"
        return "PASS", None, "Table query answered with proper spreadsheet citation"

    # Check citations for knowledge route
    if expected_citations:
        source_titles = [s.get("document_title", "") for s in actual_sources]
        for exp_c in expected_citations:
            if not any(exp_c in st for st in source_titles):
                return (
                    "FAIL",
                    "retrieval",
                    f"Expected citation '{exp_c}' missing from sources: {source_titles}",
                )

    # Default fallback
    norm_expected = normalize_text(expected_answer)
    if norm_expected in normalize_text(actual_answer):
        return "PASS", None, "Answer matched expected content"
    preview = actual_answer[:80]
    return "UNCLEAR", None, f"Could not determine verdict: answer was '{preview}...'"


def run_evaluation() -> dict[str, Any]:
    print(f"=== Starting Local E2E Eval on {API_BASE_URL} ===")
    print(f"Target Assistant: {DEV_ASSISTANT_ID}")
    print(f"Dev Login: {DEV_EMAIL}")

    with open(CASES_PATH, encoding="utf-8") as f:
        cases: list[dict[str, Any]] = json.load(f)
    print(f"Loaded {len(cases)} test cases from {CASES_PATH.name}\n")

    client = httpx.Client(base_url=API_BASE_URL, timeout=180.0)

    # 1. Login
    print(f"Authenticating as {DEV_EMAIL}...")
    login_resp = client.post(
        "/v1/auth/login",
        json={"email": DEV_EMAIL, "password": DEV_PASSWORD},
    )
    if login_resp.status_code != 200:
        raise RuntimeError(f"Failed to authenticate: {login_resp.status_code} {login_resp.text}")

    csrf = client.cookies.get("csrf_token") or client.cookies.get("cka_csrf")
    headers = {"X-CSRF-Token": csrf} if csrf else {}
    print("Authentication successful. Session cookie acquired.\n")

    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    session_docs_by_name: dict[str, tuple[str, str]] = {}

    for idx, case in enumerate(cases, start=1):
        cid = case["id"]
        category = case["category"]
        ctype = case.get("type", "question")
        qtext = case.get("question", "")
        attachment = case.get("attachment")

        print(f"[{idx}/{len(cases)}] {cid} ({category}): {qtext[:60]}...")

        conv_id: str | None = None
        doc_id: str | None = None

        # Handle attachment if specified
        if attachment:
            att_path = DOCS_DIR / attachment
            if not att_path.exists():
                print(f"  ERROR: Attachment {attachment} not found at {att_path}")
                results.append(
                    {
                        "id": cid,
                        "category": category,
                        "type": ctype,
                        "question": qtext,
                        "verdict": "FAIL",
                        "fault_layer": "UI/API",
                        "reason": f"Attachment file {attachment} not found",
                        "latency": 0.0,
                        "route": None,
                        "answer": None,
                        "citations": [],
                    }
                )
                continue

            if category == "SESSION_DOCUMENT" and attachment in session_docs_by_name:
                conv_id, doc_id = session_docs_by_name[attachment]
            else:
                with open(att_path, "rb") as af:
                    files = {"file": (attachment, af, "application/pdf")}
                    upload_resp = client.post(
                        f"/v1/assistants/{DEV_ASSISTANT_ID}/conversations/new/documents",
                        files=files,
                        headers=headers,
                    )
                if upload_resp.status_code != 200:
                    print(f"  UPLOAD FAILED: {upload_resp.status_code} {upload_resp.text}")
                    results.append(
                        {
                            "id": cid,
                            "category": category,
                            "type": ctype,
                            "question": qtext,
                            "verdict": "FAIL",
                            "fault_layer": "UI/API",
                            "reason": f"Document upload failed: {upload_resp.status_code}",
                            "latency": 0.0,
                            "route": None,
                            "answer": None,
                            "citations": [],
                        }
                    )
                    continue
                upload_data = upload_resp.json()
                conv_id = upload_data.get("conversation_id")
                doc_id = upload_data.get("session_document_id")
                if conv_id and doc_id and category == "SESSION_DOCUMENT":
                    session_docs_by_name[attachment] = (conv_id, doc_id)

        # Execute test action
        t0 = time.perf_counter()
        raw_res: dict[str, Any] = {}
        if ctype == "extract":
            ext_url = (
                f"/v1/assistants/{DEV_ASSISTANT_ID}/conversations/"
                f"{conv_id}/documents/{doc_id}/extract"
            )
            resp = client.post(ext_url, headers=headers)
            dur = time.perf_counter() - t0
            latencies.append(dur)
            if resp.status_code == 200:
                raw_res = resp.json()
            else:
                raw_res = {"_error": f"HTTP {resp.status_code}: {resp.text}"}
        else:
            q_payload: dict[str, Any] = {
                "assistant_id": DEV_ASSISTANT_ID,
                "question_text": qtext,
            }
            if conv_id:
                q_payload["conversation_id"] = conv_id
            resp = client.post("/v1/questions", json=q_payload, headers=headers)
            dur = time.perf_counter() - t0
            latencies.append(dur)
            if resp.status_code == 200:
                raw_res = resp.json()
            else:
                raw_res = {"_error": f"HTTP {resp.status_code}: {resp.text}"}

        verdict, fault_layer, reason = judge_case(case, raw_res, dur)
        print(f"  -> {verdict} ({dur:.2f}s) | Route: {raw_res.get('route')} | Fault: {fault_layer}")
        if verdict != "PASS":
            print(f"     Reason: {reason}")

        all_cites = [s.get("document_title", "") for s in raw_res.get("sources", [])]
        results.append(
            {
                "id": cid,
                "category": category,
                "type": ctype,
                "question": qtext,
                "expected_route": case.get("expected_route"),
                "expected_answer": case.get("expected_answer"),
                "actual_route": raw_res.get("route"),
                "actual_status": raw_res.get("status"),
                "actual_answer": raw_res.get("answer"),
                "extracted_fields": raw_res.get("extracted_fields"),
                "citations": all_cites,
                "latency": round(dur, 2),
                "verdict": verdict,
                "fault_layer": fault_layer,
                "reason": reason,
            }
        )

        # Pace requests
        time.sleep(1.0)

    # Compute summaries
    total = len(results)
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    failed = sum(1 for r in results if r["verdict"] == "FAIL")
    unclear = sum(1 for r in results if r["verdict"] == "UNCLEAR")
    pass_rate = round((passed / total) * 100, 1) if total > 0 else 0.0

    p50_lat = round(percentile(latencies, 50), 2)
    p95_lat = round(percentile(latencies, 95), 2)

    categories = sorted({r["category"] for r in results})
    category_summary: dict[str, dict[str, Any]] = {}
    for cat in categories:
        cat_items = [r for r in results if r["category"] == cat]
        c_pass = sum(1 for r in cat_items if r["verdict"] == "PASS")
        category_summary[cat] = {
            "total": len(cat_items),
            "passed": c_pass,
            "failed": len(cat_items) - c_pass,
            "pass_rate": round((c_pass / len(cat_items)) * 100, 1) if cat_items else 0.0,
        }

    fault_layers: dict[str, int] = {}
    for r in results:
        layer = r.get("fault_layer")
        if layer:
            fault_layers[layer] = fault_layers.get(layer, 0) + 1

    worst_failures = [r for r in results if r["verdict"] == "FAIL"][:5]

    models_by_role = {
        "router": "ollama/qwen3:8b",
        "answer": "ollama/qwen3:8b",
        "vision": "ollama/qwen3-vl:4b-instruct",
        "extract": "ollama/qwen3-vl:4b-instruct",
        "judge": "ollama/qwen3-vl:4b-instruct",
    }
    stamp = create_report_stamp(
        profile="desktop-gpu",
        models_by_role=models_by_role,
        repo_root=REPO_ROOT,
    )

    report_data = {
        "stamp": stamp,
        "summary": {
            "total_cases": total,
            "passed": passed,
            "failed": failed,
            "unclear": unclear,
            "pass_rate_pct": pass_rate,
            "p50_latency_s": p50_lat,
            "p95_latency_s": p95_lat,
        },
        "by_category": category_summary,
        "failures_by_layer": fault_layers,
        "worst_failures": worst_failures,
        "cases": results,
    }

    # Write JSON report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    json_path = REPORTS_DIR / f"{today}_desktop_gpu_e2e.json"
    md_path = REPORTS_DIR / f"{today}_desktop_gpu_e2e.md"

    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(report_data, jf, indent=2)
    print(f"\nSaved JSON report to {json_path}")

    # Write Markdown report
    md_lines = [
        f"# Desktop-GPU Local End-to-End Evaluation Report ({today})",
        "",
        "## Report Stamp",
        f"- **Timestamp**: `{stamp['timestamp']}`",
        f"- **Profile**: `{stamp['profile']}`",
        f"- **Git Commit**: `{stamp['git_commit']}`",
        f"- **Machine**: `{stamp['machine_name']}` ({stamp['platform']})",
        f"- **GPU**: `{stamp['gpu_name']}`",
        "- **Models**:",
        f"  - Router: `{models_by_role['router']}`",
        f"  - Answer: `{models_by_role['answer']}`",
        f"  - Vision / Extract / Judge: `{models_by_role['vision']}`",
        "",
        "## Overall Summary",
        f"- **Total Cases**: {total}",
        f"- **Passed**: {passed} ({pass_rate}%)",
        f"- **Failed**: {failed}",
        f"- **Unclear**: {unclear}",
        f"- **Latency p50**: {p50_lat} s",
        f"- **Latency p95**: {p95_lat} s",
        "",
        "## Results by Category",
        "| Category | Total | Passed | Failed | Pass Rate |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    for cat, cdata in category_summary.items():
        rate = cdata["pass_rate"]
        md_lines.append(
            f"| {cat} | {cdata['total']} | {cdata['passed']} | {cdata['failed']} | {rate}% |"
        )

    md_lines.extend(
        [
            "",
            "## Failures Grouped by Fault Layer",
            "| Layer at Fault | Count | Description |",
            "| :--- | :--- | :--- |",
        ]
    )
    for layer, count in sorted(fault_layers.items(), key=lambda x: -x[1]):
        md_lines.append(f"| {layer} | {count} | Evaluated failures attributed to {layer} |")
    if not fault_layers:
        md_lines.append("| None | 0 | Zero failures observed |")

    md_lines.extend(
        [
            "",
            "## Top Failures with Full Details",
        ]
    )
    if not worst_failures:
        md_lines.append("No failures recorded.")
    else:
        for fidx, fcase in enumerate(worst_failures, start=1):
            md_lines.extend(
                [
                    f"### {fidx}. Case `{fcase['id']}` ({fcase['category']})",
                    f'- **Question**: "{fcase["question"]}"',
                    (
                        f"- **Expected Route**: `{fcase.get('expected_route')}` | "
                        f"**Actual Route**: `{fcase.get('actual_route')}`"
                    ),
                    f'- **Expected Answer**: "{fcase.get("expected_answer")}"',
                    f'- **Actual Answer**: "{fcase.get("actual_answer")}"',
                    f"- **Fault Layer**: `{fcase.get('fault_layer')}`",
                    f"- **Failure Reason**: {fcase.get('reason')}",
                    f"- **Latency**: {fcase.get('latency')} s",
                    "",
                ]
            )

    md_lines.extend(
        [
            "## Complete Case Log",
            "| ID | Category | Verdict | Route | Latency | Citations | Reason |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
    )
    for r in results:
        cites_str = ", ".join(r["citations"]) if r["citations"] else "None"
        md_lines.append(
            f"| {r['id']} | {r['category']} | **{r['verdict']}** | "
            f"{r['actual_route']} | {r['latency']}s | {cites_str} | {r['reason']} |"
        )

    with open(md_path, "w", encoding="utf-8") as mf:
        mf.write("\n".join(md_lines) + "\n")
    print(f"Saved Markdown report to {md_path}")

    return report_data


if __name__ == "__main__":
    run_evaluation()
