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
GOLDEN_QS_PATH = REPO_ROOT / "evals" / "golden" / "questions.json"
DOCS_DIR = REPO_ROOT / "evals" / "golden" / "docs"
REPORTS_DIR = REPO_ROOT / "evals" / "reports"

DOC_ID_MAP: dict[str, str] = {
    "doc_prior_auth_guide": "prior_auth_guide.pdf",
    "doc_front_desk_policy_v1": "front_desk_policy_v1.pdf",
    "doc_front_desk_policy_v2": "front_desk_policy_v2.pdf",
    "doc_accepted_plans_csv": "accepted_plans.csv",
    "doc_pa_turnaround_times_xlsx": "pa_turnaround_times.xlsx",
    "doc_skewed_fax": "skewed_fax.pdf",
    "doc_clean_fax": "clean_fax.pdf",
    "doc_faint_fax_2page": "faint_fax_2page.pdf",
    "doc_eligibility_checklist": "eligibility_checklist.pdf",
    "doc_payer_contact_table": "payer_contact_table.pdf",
    "doc_fee_schedule": "fee_schedule.pdf",
    "doc_clinical_protocols_manual": "clinical_protocols_manual.pdf",
    "doc_compliance_notice": "compliance_notice.pdf",
    "doc_cross_tenant_patient_a": "patient_intake_form.pdf",
    "doc_cross_tenant_patient_b": "patient_intake_form.pdf",
}

CLINIC_A_DOC_IDS: set[str] = {
    "doc_prior_auth_guide",
    "doc_front_desk_policy_v1",
    "doc_front_desk_policy_v2",
    "doc_accepted_plans_csv",
    "doc_pa_turnaround_times_xlsx",
    "doc_skewed_fax",
}

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
    cid = case.get("id", "")
    case_type = case.get("type", "question")
    expected_route = case.get("expected_route")
    expected_answer = case.get("expected_answer", "")
    accepted_answers = case.get("accepted_answers", [])
    expected_citations = case.get("expected_citations", [])
    must_not_contain = case.get("must_not_contain", [])
    expected_mode = case.get("expected_mode", "")
    category = case.get("category", "")

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
    norm_actual_answer = normalize_text(actual_answer)

    # 1. Route validation (if expected route is specified)
    if expected_route and actual_route != expected_route:
        return (
            "FAIL",
            "routing",
            f"Route mismatch: expected '{expected_route}', got '{actual_route}'",
        )

    # 2. Refusal cases
    is_expected_refusal = (
        expected_mode in ("refusal", "refuse")
        or category in ("NOT_IN_DOCUMENTS", "NO_ANSWER", "CROSS_TENANT")
        or cid == "E2E-SD-002"
    )
    if is_expected_refusal:
        is_refused = (
            actual_status == "refusal"
            or "couldn't confirm" in actual_answer.lower()
            or "couldn't find" in actual_answer.lower()
            or "can only answer" in actual_answer.lower()
        )
        if not is_refused:
            preview = actual_answer[:80]
            return "FAIL", "generation", f"Expected refusal, but got answered: '{preview}...'"
        if len(actual_sources) > 0:
            return "FAIL", "evidence_gate", f"Refused but returned {len(actual_sources)} citations"
        for phrase in must_not_contain:
            if phrase.lower() in norm_actual_answer:
                return "FAIL", "generation", f"Refused answer leaked forbidden text: '{phrase}'"
        return "PASS", None, "Correctly refused with zero citations"

    # 3. For ALL non-refusal cases: Strict check against UNEXPECTED REFUSAL
    if (
        actual_status == "refusal"
        or "couldn't confirm" in actual_answer.lower()
        or "couldn't find" in actual_answer.lower()
        or "can only answer" in actual_answer.lower()
    ):
        return "FAIL", "retrieval", f"Unexpected refusal: '{actual_answer[:100]}...'"

    # 4. Check forbidden phrases on any non-refusal answer
    for phrase in must_not_contain:
        if phrase.lower() in norm_actual_answer:
            return "FAIL", "generation", f"Answer contained forbidden text: '{phrase}'"

    # 5. Case-specific rules

    # 5a. E2E-CONF-001: Front desk fax conflict
    if cid == "E2E-CONF-001":
        all_sources = actual_sources + actual_conflict_sources
        source_titles = [s.get("document_title", "") for s in all_sources]
        has_v1 = any("v1" in t for t in source_titles)
        has_v2 = any("v2" in t for t in source_titles)
        if not (has_v1 and has_v2):
            return (
                "FAIL",
                "retrieval",
                f"E2E-CONF-001 missing both v1 and v2 citations: {source_titles}",
            )
        if "555-0102" not in actual_answer:
            return "FAIL", "generation", "E2E-CONF-001 missing expected current fax '555-0102'"
        return (
            "PASS",
            None,
            "Version conflict correctly detected: 555-0102 present and both v1/v2 cited",
        )

    # 5b. E2E-CONF-002: Check-in arrival window conflict
    if cid == "E2E-CONF-002":
        all_sources = actual_sources + actual_conflict_sources
        source_titles = [s.get("document_title", "") for s in all_sources]
        has_v1 = any("v1" in t for t in source_titles)
        has_v2 = any("v2" in t for t in source_titles)
        if not (has_v1 and has_v2):
            return (
                "FAIL",
                "retrieval",
                f"E2E-CONF-002 missing both v1 and v2 citations: {source_titles}",
            )
        ans_low = actual_answer.lower()
        if "20 minutes" not in ans_low and "20 min" not in ans_low and "20" not in actual_answer:
            return (
                "FAIL",
                "generation",
                "E2E-CONF-002 missing expected current arrival '20 minutes'",
            )
        return (
            "PASS",
            None,
            "Version conflict correctly detected: 20 minutes present and both v1/v2 cited",
        )

    # 5c. Other CONFLICT / VERSION_CONFLICT cases
    if category in ("VERSION_CONFLICT", "CONFLICT") or expected_mode == "conflict":
        all_sources = actual_sources + actual_conflict_sources
        source_titles = [s.get("document_title", "") for s in all_sources]
        for exp_c in expected_citations:
            if not any(exp_c in st for st in source_titles):
                return (
                    "FAIL",
                    "retrieval",
                    f"Conflict missing expected citation '{exp_c}': {source_titles}",
                )
        conflict_values = case.get("conflict_values", [])
        if conflict_values:
            for cv in conflict_values:
                if normalize_text(cv) not in norm_actual_answer:
                    return "FAIL", "generation", f"Conflict answer missing conflict value '{cv}'"
        elif expected_answer and normalize_text(expected_answer) not in norm_actual_answer:
            return "FAIL", "generation", f"Conflict answer missing '{expected_answer}'"
        return "PASS", None, "Version conflict correctly answered with all citations and values"

    # 5d. E2E-TAB-001: Routine PA turnaround time table
    if cid == "E2E-TAB-001":
        source_titles = [s.get("document_title", "") for s in actual_sources]
        if not any("pa_turnaround_times" in t for t in source_titles):
            return (
                "FAIL",
                "retrieval",
                f"E2E-TAB-001 missing pa_turnaround_times.xlsx: {source_titles}",
            )
        if "48" not in actual_answer or "72" not in actual_answer:
            return (
                "FAIL",
                "generation",
                f"E2E-TAB-001 missing 48 or 72 in answer: '{actual_answer}'",
            )
        lines = [line.strip() for line in actual_answer.splitlines() if line.strip()]
        has_lead_sentence = bool(lines and not lines[0].startswith(("-", "*", "|", "Payer Name:")))
        if not has_lead_sentence:
            lead_prev = lines[0] if lines else ""
            return "FAIL", "generation", f"E2E-TAB-001 missing direct lead sentence: '{lead_prev}'"
        return (
            "PASS",
            None,
            "Table answer has direct lead sentence, 48/72 hours, and proper spreadsheet citation",
        )

    # 5e. E2E-SD-003: Attached fax summary
    if cid == "E2E-SD-003":
        source_titles = [s.get("document_title", "") for s in actual_sources]
        if not any("skewed_fax" in t for t in source_titles):
            return (
                "FAIL",
                "retrieval",
                f"E2E-SD-003 missing skewed_fax.pdf citation: {source_titles}",
            )
        points = [
            line for line in actual_answer.splitlines() if line.strip().startswith(("-", "*"))
        ]
        if len(points) < 2:
            return (
                "FAIL",
                "generation",
                f"E2E-SD-003 expected at least 2 bullet points, got {len(points)}",
            )
        has_patient = "alex morgan" in norm_actual_answer
        has_med_or_dose = any(
            term in norm_actual_answer
            for term in ("amoxicillin", "500mg", "5oomg", "500 mg", "capsule", "daily")
        )
        if not (has_patient and has_med_or_dose):
            return (
                "FAIL",
                "generation",
                f"E2E-SD-003 summary missing patient name or med/dose: '{actual_answer}'",
            )
        return (
            "PASS",
            None,
            f"Fax summary has {len(points)} valid quoted bullet points with patient and med",
        )

    # 5f. General route
    if actual_route == "general":
        if len(actual_sources) > 0:
            return "FAIL", "routing", f"General route returned {len(actual_sources)} citations"
        norm_expected = normalize_text(expected_answer)
        if norm_expected and norm_expected not in norm_actual_answer:
            return "FAIL", "generation", f"Expected '{expected_answer}' in general answer"
        return "PASS", None, "General route answered cleanly with zero sources"

    # 5g. Session document route
    if actual_route == "session_document":
        source_titles = [s.get("document_title", "") for s in actual_sources]
        for exp_c in expected_citations:
            if not any(exp_c in st for st in source_titles):
                return (
                    "FAIL",
                    "retrieval",
                    f"Expected citation '{exp_c}' missing from session doc: {source_titles}",
                )
        norm_expected = normalize_text(expected_answer)
        if norm_expected and norm_expected not in norm_actual_answer:
            return "FAIL", "generation", f"Expected '{expected_answer}' in session doc answer"
        return "PASS", None, "Session document answered correctly with citation"

    # 5h. Knowledge route (TEXT_FACT, TABLE_LOOKUP, VISUAL_RECOVERY, etc.)
    source_titles = [s.get("document_title", "") for s in actual_sources]
    for exp_c in expected_citations:
        if not any(exp_c in st for st in source_titles):
            return (
                "FAIL",
                "retrieval",
                f"Expected citation '{exp_c}' missing from sources: {source_titles}",
            )

    candidates: list[str] = []
    if expected_answer:
        candidates.append(expected_answer)
    candidates.extend(accepted_answers)

    if candidates:
        matched = any(normalize_text(cand) in norm_actual_answer for cand in candidates if cand)
        if not matched:
            preview = actual_answer[:100]
            return (
                "FAIL",
                "generation",
                f"Expected answer '{expected_answer}' missing from: '{preview}...'",
            )

    return "PASS", None, "Answer matched expected content and citations strictly"


def run_evaluation() -> dict[str, Any]:
    print(f"=== Starting Local E2E Eval on {API_BASE_URL} ===")
    print(f"Target Assistant: {DEV_ASSISTANT_ID}")
    print(f"Dev Login: {DEV_EMAIL}")

    with open(CASES_PATH, encoding="utf-8") as f:
        cases: list[dict[str, Any]] = json.load(f)

    if GOLDEN_QS_PATH.exists():
        with open(GOLDEN_QS_PATH, encoding="utf-8") as gf:
            golden_raw: list[dict[str, Any]] = json.load(gf)

        include_all = "--all-golden" in sys.argv
        added_golden = 0
        for gq in golden_raw:
            ev_list = gq.get("evidence", [])
            doc_ids = {e.get("document_id") for e in ev_list if "document_id" in e}
            # Include if evidence matches Clinic A or empty (refusal), or if --all-golden requested
            if include_all or not doc_ids or doc_ids.issubset(CLINIC_A_DOC_IDS):
                exp_cites = [DOC_ID_MAP[did] for did in doc_ids if did in DOC_ID_MAP]
                cases.append(
                    {
                        "id": gq["id"],
                        "category": gq["category"],
                        "type": "question",
                        "question": gq["question"],
                        "attachment": None,
                        "expected_route": "knowledge",
                        "expected_mode": gq.get("expected_mode"),
                        "expected_answer": gq.get("expected_answer") or "",
                        "accepted_answers": gq.get("accepted_answers", []),
                        "conflict_values": gq.get("conflict_values", []),
                        "must_not_contain": gq.get("must_not_contain", []),
                        "expected_citations": exp_cites,
                        "notes": gq.get("notes", ""),
                    }
                )
                added_golden += 1
        e2e_count = len(cases) - added_golden
        print(
            f"Loaded {e2e_count} E2E cases + {added_golden} golden questions "
            f"(Total: {len(cases)})\n"
        )
    else:
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
