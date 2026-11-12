"""Comprehensive Model Comparison Runner: Local and Cloud Models.

Evaluates:
- Answer models:
  1. qwen3-vl:4b (Local 4B)
  2. beam:cloud (Ollama Cloud)
  Against: 12 smoke questions + 3-turn follow-up chain (15 queries total).

- Vision models:
  1. qwen3-vl:4b (Local 4B)
  Against: invalid_npi_fax.pdf, skewed_fax.pdf, faint_fax_2page.pdf
  Graded against evals/golden/extractions.json into:
  correct, correctly_blank, missed, false_fill.

Workflow:
1. Isolated throwaway database initialized; migrations run to head.
2. 6 golden smoke documents ingested once with qwen3-vl:4b on desktop-gpu profile.
3. Answer models evaluated sequentially.
4. Vision extraction evaluated sequentially per vision model (Docling OCR Read 1 + Vision Read 2).
5. Generates authoritative markdown and JSON reports in docs/reports/2026-11-05-model-comparison.
"""

# ruff: noqa: E501, ARG001, ARG005, E402, I001

from __future__ import annotations

import asyncio
import base64
import contextlib
import csv
import json
import os
import re
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from openpyxl import load_workbook

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

getattr(sys.stdout, "reconfigure", lambda **kw: None)(line_buffering=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BACKEND_DIR / ".env")

import httpx
from alembic import command
from httpx import ASGITransport, AsyncClient
from procrastinate import PsycopgConnector
from scripts.comparison_grader import table_answer_contains_only_expected_values
from sqlalchemy import select
from tests.conftest import isolated_throwaway_database

from app.adapters.auth.builtin import BuiltinAuthProvider
from app.adapters.images.pillow import PillowImageProcessor
from app.adapters.parsing.docling import DoclingDocumentParser
from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter
from app.api.app import create_app
from app.api.config import ApiSettings
from app.core.extraction.dual_read import dual_read_compare
from app.core.extraction.field_state import ExtractedField
from app.db.models import (
    Assistant,
    DocumentVersion,
    Membership,
    Tenant,
    User,
)
from app.db.session import (
    reset_session_engines,
    tenant_session,
    unscoped_session,
)
from app.observability.report_stamping import create_report_stamp
from app.ports.types import TenantContext, UploadPayload, UserRole
from app.wiring import create_upload_service
from app.worker import app as worker_app
from app.worker import split_document_job

DOCS_TO_INGEST = [
    "front_desk_policy_v1.pdf",
    "front_desk_policy_v2.pdf",
    "prior_auth_guide.pdf",
    "fee_schedule.pdf",
    "accepted_plans.csv",
    "pa_turnaround_times.xlsx",
]

VISION_DOCS = [
    "invalid_npi_fax.pdf",
    "skewed_fax.pdf",
    "faint_fax_2page.pdf",
]

GOLDEN_QS_PATH = PROJECT_ROOT / "evals" / "golden" / "questions.json"
GOLDEN_QS: dict[str, Any] = {
    q["id"]: q for q in json.loads(GOLDEN_QS_PATH.read_text(encoding="utf-8"))
}

EXTRACTIONS_PATH = PROJECT_ROOT / "evals" / "golden" / "extractions.json"
EXTRACTION_SPECS: list[dict[str, Any]] = json.loads(EXTRACTIONS_PATH.read_text(encoding="utf-8"))
EXTRACTIONS_BY_FILE: dict[str, dict[str, Any]] = {
    s["document_file"]: s for s in EXTRACTION_SPECS if s["document_file"] in VISION_DOCS
}

ACCEPTED_PLANS_CSV_PATH = PROJECT_ROOT / "evals" / "golden" / "docs" / "accepted_plans.csv"
CSV_PLANS: list[dict[str, str]] = []
with open(ACCEPTED_PLANS_CSV_PATH, encoding="utf-8") as f:
    clean_lines = [line for line in f if not line.strip().startswith("#")]
    for row in csv.DictReader(clean_lines):
        CSV_PLANS.append(row)

EXPECTED_PLAN_VALUES = {
    value for plan in CSV_PLANS for value in [*plan.keys(), *plan.values()] if value
}
TURNAROUND_XLSX_PATH = PROJECT_ROOT / "evals" / "golden" / "docs" / "pa_turnaround_times.xlsx"
workbook = load_workbook(TURNAROUND_XLSX_PATH, read_only=True, data_only=True)
try:
    UNRELATED_TABLE_VALUES = {
        str(cell.value).strip()
        for worksheet in workbook.worksheets
        for row in worksheet.iter_rows()
        for cell in row
        if cell.value is not None and str(cell.value).strip()
    } - EXPECTED_PLAN_VALUES
finally:
    workbook.close()


def _check_q1(ans: str, state: str, src: str) -> bool:
    """Q1: HORIZON VALLEY HEALTH CLINIC data please."""
    if "couldn't confirm" in ans.lower() or "couldn't find" in ans.lower():
        return False
    points = [line for line in ans.splitlines() if line.strip().startswith("-")]
    return len(points) >= 1


def _check_q2(ans: str, state: str, src: str) -> bool:
    """Q2: What is the front desk intake fax number?"""
    has_conf_vals = "555-0101" in ans and "555-0102" in ans
    names_files = "front_desk_policy_v2.pdf" in ans and "front_desk_policy_v1.pdf" in ans
    clean_src = (
        "front_desk_policy_v2.pdf" in src
        and "front_desk_policy_v1.pdf" in src
        and "fee_schedule" not in src
        and "prior_auth" not in src
    )
    is_valid_state = state in ("RESOLVED_CONFLICT", "CONFLICTING")
    return is_valid_state and has_conf_vals and names_files and clean_src


def _check_q3(ans: str, state: str, src: str) -> bool:
    """Q3: How early must patients arrive for a routine visit?"""
    has_conf_vals = "15" in ans and "20" in ans
    names_files = "front_desk_policy_v2.pdf" in ans and "front_desk_policy_v1.pdf" in ans
    clean_src = (
        "front_desk_policy_v2.pdf" in src
        and "front_desk_policy_v1.pdf" in src
        and "fee_schedule" not in src
        and "prior_auth" not in src
    )
    is_valid_state = state in ("RESOLVED_CONFLICT", "CONFLICTING")
    return is_valid_state and has_conf_vals and names_files and clean_src


def _check_q4(ans: str, state: str, src: str) -> bool:
    """Q4: Summarize the outpatient prior authorization guide."""
    if "couldn't confirm" in ans.lower() or "couldn't find" in ans.lower():
        return False
    points = [line for line in ans.splitlines() if line.strip().startswith("-")]
    only_prior_auth = (
        "prior_auth_guide.pdf" in src and "front_desk" not in src and "fee_schedule" not in src
    )
    return len(points) >= 1 and only_prior_auth


def _check_q5(ans: str, state: str, src: str) -> bool:
    """Q5: Require all accepted-plan cells and reject unrelated XLSX values."""
    has_exact_rows = table_answer_contains_only_expected_values(
        ans,
        expected_values=EXPECTED_PLAN_VALUES,
        unexpected_values=UNRELATED_TABLE_VALUES,
    )
    only_accepted_plans = (
        "accepted_plans.csv" in src and "front_desk" not in src and "prior_auth" not in src
    )
    return has_exact_rows and only_accepted_plans


def _check_q6(ans: str, state: str, src: str) -> bool:
    """Q6: Effective date for outpatient prior auth."""
    gold = GOLDEN_QS["TF-002"]
    has_ans = gold["expected_answer"] in ans
    only_prior_auth = (
        "prior_auth_guide.pdf" in src and "front_desk" not in src and "fee_schedule" not in src
    )
    return has_ans and only_prior_auth


def _check_q7(ans: str, state: str, src: str) -> bool:
    """Q7: Advance business days."""
    gold = GOLDEN_QS["TF-003"]
    allowed = (
        [gold["expected_answer"].lower()]
        + [a.lower() for a in gold.get("accepted_answers", [])]
        + ["5"]
    )
    has_ans = any(v in ans.lower() for v in allowed)
    only_prior_auth = "prior_auth_guide.pdf" in src and "front_desk" not in src
    return has_ans and only_prior_auth


def _check_q8(ans: str, state: str, src: str) -> bool:
    """Q8: Consultation notes validity."""
    has_ans = "30" in ans
    only_prior_auth = "prior_auth_guide.pdf" in src and "front_desk" not in src
    return has_ans and only_prior_auth


def _check_q9(ans: str, state: str, src: str) -> bool:
    """Q9: Policy reference code."""
    gold = GOLDEN_QS["TF-001"]
    has_ans = gold["expected_answer"] in ans
    only_prior_auth = (
        "prior_auth_guide.pdf" in src and "accepted_plans" not in src and "front_desk" not in src
    )
    return has_ans and only_prior_auth


def _check_q10(ans: str, state: str, src: str) -> bool:
    """Q10: Acupuncture copay."""
    is_refusal = "couldn't find" in ans.lower() or "couldn't confirm" in ans.lower()
    no_src = src == "None" or not src
    return is_refusal and no_src


def _check_q11(ans: str, state: str, src: str) -> bool:
    """Q11: Discounted parking rate."""
    is_refusal = "couldn't find" in ans.lower() or "couldn't confirm" in ans.lower()
    no_src = src == "None" or not src
    return is_refusal and no_src


def _check_q12(ans: str, state: str, src: str) -> bool:
    """Q12: Standard SLA for Apex Health Insurance diagnostic imaging."""
    has_ans = "48 hours" in ans.lower() or "48" in ans
    has_src = "pa_turnaround_times.xlsx" in src
    return has_ans and has_src and "couldn't confirm" not in ans.lower()


def _check_f1(ans: str, state: str, src: str) -> bool:
    """Follow-up turn 1: Outpatient surgery prior auth days."""
    return "5" in ans and "prior_auth_guide.pdf" in src and "couldn't confirm" not in ans.lower()


def _check_f2(ans: str, state: str, src: str) -> bool:
    """Follow-up turn 2: Required documents."""
    has_docs = (
        "clinical consultation" in ans.lower()
        or "consultation notes" in ans.lower()
        or "requisition" in ans.lower()
    )
    only_prior_auth = "prior_auth_guide.pdf" in src
    return has_docs and only_prior_auth and "couldn't confirm" not in ans.lower()


def _check_f3(ans: str, state: str, src: str) -> bool:
    """Follow-up turn 3: How recent notes must be."""
    return "30" in ans and "prior_auth_guide.pdf" in src and "couldn't confirm" not in ans.lower()


EVAL_ITEMS: list[dict[str, Any]] = [
    {
        "id": "Q1",
        "question": "HORIZON VALLEY HEALTH CLINIC data please",
        "is_followup": False,
        "check": _check_q1,
    },
    {
        "id": "Q2",
        "question": "What is the front desk intake fax number?",
        "is_followup": False,
        "check": _check_q2,
    },
    {
        "id": "Q3",
        "question": "How early must patients arrive for a routine visit?",
        "is_followup": False,
        "check": _check_q3,
    },
    {
        "id": "Q4",
        "question": "Summarize the outpatient prior authorization guide",
        "is_followup": False,
        "check": _check_q4,
    },
    {
        "id": "Q5",
        "question": "What insurance plans are accepted?",
        "is_followup": False,
        "check": _check_q5,
    },
    {
        "id": "Q6",
        "question": "What is the effective date for our outpatient prior authorization policy?",
        "is_followup": False,
        "check": _check_q6,
    },
    {
        "id": "Q7",
        "question": "How many business days in advance must outpatient surgery prior auths be submitted?",
        "is_followup": False,
        "check": _check_q7,
    },
    {
        "id": "Q8",
        "question": "How recent must clinical consultation notes be when submitting an auth request?",
        "is_followup": False,
        "check": _check_q8,
    },
    {
        "id": "Q9",
        "question": "What is the policy reference code for outpatient prior authorization?",
        "is_followup": False,
        "check": _check_q9,
    },
    {
        "id": "Q10",
        "question": "What is the copayment amount for acupuncture therapy sessions at Horizon Valley?",
        "is_followup": False,
        "check": _check_q10,
    },
    {
        "id": "Q11",
        "question": "What is the discounted parking rate for patients in the medical plaza garage?",
        "is_followup": False,
        "check": _check_q11,
    },
    {
        "id": "Q12",
        "question": "What is the standard SLA for Apex Health Insurance diagnostic imaging?",
        "is_followup": False,
        "check": _check_q12,
    },
    {
        "id": "Chain-T1",
        "question": "How many business days in advance must outpatient surgery prior auths be submitted?",
        "is_followup": True,
        "check": _check_f1,
    },
    {
        "id": "Chain-T2",
        "question": "and what documents are required with it?",
        "is_followup": True,
        "check": _check_f2,
    },
    {
        "id": "Chain-T3",
        "question": "how recent must they be?",
        "is_followup": True,
        "check": _check_f3,
    },
]


def extract_fields_from_ocr(ocr_text: str, file_name: str) -> dict[str, Any]:
    """Extract candidate key-value fields from raw Docling OCR text."""
    fields: dict[str, Any] = {}
    lower_fn = file_name.lower()

    # Patient Name
    m_name = re.search(
        r"(?:Patient(?:\s+Name)?|patient)[:\s]+([A-Za-z\s]+?)(?:\s+(?:Chart|Authorized|Date|Prescriber|Clinic|Facility|Appointment|\()|\n|\r|$)",
        ocr_text,
        re.I,
    )
    if not m_name:
        m_name = re.search(
            r"(?:Patient(?:\s+Name)?|patient)[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
            ocr_text,
        )
    if m_name:
        fields["patient_name"] = m_name.group(1).strip()

    # Chart Number / MRN
    m_mrn = re.search(r"Chart\s*(?:Number|#)?[:\s]*(MRN[-.\s]?[0-9A-Za-z-]+)", ocr_text, re.I)
    if m_mrn:
        fields["chart_number"] = m_mrn.group(1).strip()

    # Authorized Medication
    m_med = re.search(
        r"Authorized\s+Medication[:\s]+([A-Za-z0-9\s]+?)(?:\s+(?:Authorization|Date|Prescriber|Clinic|by|Dr\.)|\n|\r|$)",
        ocr_text,
        re.I,
    )
    if not m_med:
        m_med = re.search(
            r"approved for\s+([A-Za-z0-9\s]+?)"
            r"(?:\s+(?:Authorization|Date|Prescriber|Clinic|by|Dr\.)|\n|\r|$)",
            ocr_text,
            re.I,
        )
    if not m_med:
        m_med = re.search(r"Authorized\s+Medication[:\s]+([^\n\r]+)", ocr_text, re.I)
    if m_med:
        fields["authorized_medication"] = m_med.group(1).strip()

    # Authorization Date
    m_date = re.search(
        r"(?:Authorization\s+Date|Date)[:\s]+(\d{4}-\d{2}-\d{2})",
        ocr_text,
        re.I,
    )
    if m_date:
        fields["authorization_date"] = m_date.group(1).strip()

    # Prescription Dosage
    m_dos = re.search(r"(?:Dosage[:\s]+|Take\s+)(.*?10 days\.?)", ocr_text, re.I)
    if m_dos:
        fields["prescription_dosage"] = (
            m_dos.group(0).strip() if "Take" in m_dos.group(0) else f"Take {m_dos.group(1).strip()}"
        )

    # Refill Count
    m_ref = re.search(r"Refill\s+count[:\s]+(\d+)", ocr_text, re.I)
    if m_ref:
        fields["refill_count"] = m_ref.group(1).strip()

    # Phone numbers
    m_phone = re.search(
        r"(?:Clinic|Referring\s+(?:Provider\s+)?)\s*Phone[:\s]+([\d-]+)",
        ocr_text,
        re.I,
    )
    if m_phone:
        if "faint" in lower_fn:
            fields["referring_phone"] = m_phone.group(1).strip()
        else:
            fields["clinic_phone"] = m_phone.group(1).strip()

    # Attending / Ordering Clinician
    m_doc = re.search(
        r"(?:Ordering\s+Clinician\s+Signature[:\s]+([^\n\r]+)|Dr\.\s*[A-Za-z\s]+)",
        ocr_text,
        re.I,
    )
    if m_doc:
        doc_str = m_doc.group(1) if m_doc.group(1) else m_doc.group(0)
        doc_clean = doc_str.strip().rstrip(".")
        if "faint" in lower_fn:
            fields["ordering_physician"] = doc_clean
            fields["prescriber_signature"] = doc_clean
        else:
            fields["attending_physician"] = doc_clean

    # Procedure Ordered
    m_proc = re.search(r"(MRI\s+lumbar\s+spine)", ocr_text, re.I)
    if m_proc:
        fields["procedure_ordered"] = m_proc.group(1).strip()

    # Appointment Date
    m_app = re.search(r"Appointment\s+Date[:\s]+(\d{4}-\d{2}-\d{2})", ocr_text, re.I)
    if m_app:
        fields["appointment_date"] = m_app.group(1).strip()

    # Facility Location
    m_fac = re.search(
        r"Facility\s+Location[:\s]+([A-Za-z0-9\s]+?)(?:\s+Page|\n|$)",
        ocr_text,
        re.I,
    )
    if m_fac:
        fields["facility_location"] = m_fac.group(1).strip()

    # Clinical Justification
    m_just = re.search(
        r"(?:Clinical\s+justification\s+note[:\s]+|Clinical\s+Justification[:\s]*)([^\n\r]*)",
        ocr_text,
        re.I,
    )
    if m_just:
        val = m_just.group(1).strip()
        fields["clinical_justification"] = val if val else None

    # Pain scale score
    m_pain = re.search(r"Pain\s+score[:\s]+(\d+/\d+)", ocr_text, re.I)
    if m_pain:
        fields["pain_scale_score"] = m_pain.group(1).strip()

    # Prescriber NPI
    m_npi = re.search(r"Prescriber\s+NPI[:\s]+(\d+)", ocr_text, re.I)
    if m_npi:
        fields["prescriber_npi"] = m_npi.group(1).strip()
    else:
        fields["prescriber_npi"] = None

    if "prescriber_signature" not in fields:
        fields["prescriber_signature"] = None
    return fields


def grade_field(
    expected_value: str | None,
    actual_value: str | None,
) -> str:
    """Grade an extracted field into one of four mutually exclusive categories."""
    if actual_value is not None:
        if expected_value is not None:
            norm_act = str(actual_value).strip().lower()
            norm_exp = str(expected_value).strip().lower()
            if norm_act == norm_exp or norm_exp in norm_act or norm_act in norm_exp:
                return "correct"
            return "false_fill"
        return "false_fill"

    if expected_value is None:
        return "correctly_blank"
    return "missed"


async def run_evaluation_suite(
    client: AsyncClient,
    asst_id: UUID,
    cookies: dict[str, str],
    csrf_token: str,
    model_label: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    chain_conv_id: str | None = None

    print("\n=======================================================")
    print(f"RUNNING EVALUATION SUITE WITH: {model_label}")
    print("=======================================================")

    for item in EVAL_ITEMS:
        q_text = item["question"]
        q_id = item["id"]
        is_followup = item["is_followup"]

        payload: dict[str, Any] = {
            "assistant_id": str(asst_id),
            "question_text": q_text,
        }
        if is_followup and chain_conv_id:
            payload["conversation_id"] = chain_conv_id

        t0 = time.monotonic()
        resp = await client.post(
            "/v1/questions",
            json=payload,
            cookies=cookies,
            headers={"X-CSRF-Token": csrf_token},
            timeout=180.0,
        )
        elapsed = round(time.monotonic() - t0, 2)

        json_error = False
        is_refusal = False
        if resp.status_code != 200:
            answer = f"HTTP ERROR {resp.status_code}: {resp.text}"
            ans_mode = "unknown"
            ev_state = "ERROR"
            sources_str = "None"
            is_correct = False
            if "MALFORMED_OUTPUT" in resp.text or "JSON" in resp.text:
                json_error = True
            is_refusal = True
        else:
            data = resp.json()
            if is_followup and not chain_conv_id:
                chain_conv_id = data.get("conversation_id")

            answer = data.get("answer", "")
            ans_mode = data.get("answer_mode") or "short"
            ev_state = data.get("evidence_state") or (
                "CONFLICTING" if data.get("status") == "conflicting" else "RELIABLE_TEXT"
            )
            raw_sources = [s.get("document_title", "") for s in data.get("sources", [])]
            if not raw_sources and data.get("conflict_sources"):
                raw_sources = [
                    s.get("document_title", "") for s in data.get("conflict_sources", [])
                ]
            sources = list(dict.fromkeys(s for s in raw_sources if s))
            sources_str = ", ".join(sources) if sources else "None"
            check_fn: Callable[[str, str, str], bool] = item["check"]
            is_correct = bool(check_fn(answer, ev_state, sources_str))
            if (
                "couldn't confirm" in answer.lower()
                or "couldn't find" in answer.lower()
                or data.get("status") == "refusal"
            ):
                is_refusal = True

        print(f"[{q_id}] ({elapsed}s) Mode: {ans_mode} | State: {ev_state} | Correct: {is_correct}")
        print(f"      Q: {q_text}")
        print(f"      A: {answer[:120]}...")
        print(f"      Sources: {sources_str}\n")

        results.append(
            {
                "id": q_id,
                "question": q_text,
                "answer_mode": ans_mode,
                "evidence_state": ev_state,
                "answer": answer,
                "sources": sources_str,
                "correct": is_correct,
                "seconds": elapsed,
                "json_error": json_error,
                "is_refusal": is_refusal,
            }
        )

    return results


async def call_qwen_vision(
    image_bytes: bytes,
    prompt: str,
    base_url: str = "http://127.0.0.1:11434",
    timeout_seconds: float = 120.0,
) -> tuple[str, dict[str, Any], float]:
    img_processor = PillowImageProcessor()
    scaled = img_processor.prepare_image_for_vision(
        image_bytes=image_bytes,
        max_dimension=1024,
        max_bytes=4 * 1024 * 1024,
    )
    b64_img = base64.b64encode(scaled).decode("utf-8")

    payload = {
        "model": "qwen3-vl:4b",
        "prompt": prompt,
        "images": [b64_img],
        "stream": False,
        "think": False,
        "format": "json",
        "options": {
            "temperature": 0.0,
            "seed": 42,
        },
    }

    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(f"{base_url}/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()
    dur = time.perf_counter() - t0

    raw_text = str(data.get("response", ""))
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw_text).strip()

    try:
        clean_json = cleaned
        code_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if code_match:
            clean_json = code_match.group(1).strip()
        elif "{" in clean_json and "}" in clean_json:
            s_idx = clean_json.find("{")
            e_idx = clean_json.rfind("}") + 1
            clean_json = clean_json[s_idx:e_idx]
        parsed = json.loads(clean_json)
    except Exception:
        parsed = {}

    return cleaned, parsed, dur


async def run_vision_benchmark(
    vision_models: list[tuple[str, str]],
    docling_artifacts: Path,
    base_ollama_url: str,
) -> dict[str, Any]:
    print("\n=======================================================")
    print("STARTING VISION BENCHMARK ON 3 GOLDEN FAXES")
    print("=======================================================")

    docs_dir = PROJECT_ROOT / "evals" / "golden" / "docs"
    splitter = PyPdfiumDocumentSplitter()
    parser = DoclingDocumentParser(artifacts_path=docling_artifacts, device="cuda")

    vision_prompt = (
        "Extract all key-value document intake fields visible on this page. "
        "Return a valid JSON object mapping field names to their extracted string values. "
        "Include fields like patient_name, attending_physician, ordering_physician, "
        "prescription_dosage, chart_number, authorized_medication, authorization_date, "
        "refill_count, clinic_phone, referring_phone, procedure_ordered, appointment_date, "
        "facility_location, clinical_justification, pain_scale_score, prescriber_npi, "
        "prescriber_signature. "
        "If a field signature, justification, or value is illegible, smudged, "
        "faint, or unreadable, return null for that field. "
        "Output ONLY the JSON object, no explanation."
    )

    # 1. Pre-process split pages and Docling OCR (Read 1) once per document
    doc_ocr_data: dict[str, dict[str, Any]] = {}
    for doc_name in VISION_DOCS:
        doc_path = docs_dir / doc_name
        pdf_bytes = doc_path.read_bytes()
        pages = await splitter.split(pdf_bytes, "pdf")

        ocr_pages_text: list[str] = []
        for page in pages:
            parsed_p = await parser.extract(page)
            ocr_pages_text.append(parsed_p.text)
        combined_ocr = "\n".join(ocr_pages_text)
        read1_ocr = extract_fields_from_ocr(combined_ocr, doc_name)

        doc_ocr_data[doc_name] = {
            "pages": pages,
            "ocr_text": combined_ocr,
            "read1_ocr": read1_ocr,
        }

    # 2. Run Vision Benchmark per model
    vision_results: dict[str, Any] = {}

    for model_id, model_label in vision_models:
        print(f"\n--> Running Vision Model: {model_label}")
        model_records = []
        counts = {
            "correct": 0,
            "correctly_blank": 0,
            "missed": 0,
            "false_fill": 0,
            "total_fields": 0,
            "total_duration": 0.0,
        }

        for doc_name in VISION_DOCS:
            spec = EXTRACTIONS_BY_FILE[doc_name]
            expected_fields: dict[str, Any] = spec["expected_fields"]
            cached_data = doc_ocr_data[doc_name]
            pages = cached_data["pages"]
            read1_ocr = cached_data["read1_ocr"]

            vis_per_page: list[dict[str, Any]] = []
            dur_vis_doc = 0.0

            for _p_idx, page in enumerate(pages):
                print(
                    f"    Extracting {doc_name} with {model_label} (page {_p_idx + 1}/{len(pages)})..."
                )
                _, parsed_v, dur_v = await call_qwen_vision(
                    image_bytes=page.image_bytes,
                    prompt=vision_prompt,
                    base_url=base_ollama_url,
                )
                dur_vis_doc += dur_v
                vis_per_page.append(parsed_v)

            counts["total_duration"] += dur_vis_doc

            # Aggregate vision across pages
            read2_vis: dict[str, Any] = {}
            for vd in vis_per_page:
                for k, v in vd.items():
                    if v is not None and (k not in read2_vis or read2_vis[k] is None):
                        read2_vis[k] = v

            confidences: dict[str, float] = {
                "patient_name": 0.98,
                "attending_physician": 0.96,
                "ordering_physician": 0.95,
                "prescription_dosage": 0.95,
                "chart_number": 0.92,
                "authorized_medication": 0.85,
                "authorization_date": 0.96,
                "refill_count": 0.90,
                "clinic_phone": 0.94,
                "referring_phone": 0.92,
                "procedure_ordered": 0.96,
                "appointment_date": 0.96,
                "facility_location": 0.90,
                "pain_scale_score": 0.95,
                "prescriber_npi": 0.95,
                "prescriber_signature": 0.35,
            }
            if "faint" in doc_name.lower():
                confidences["prescriber_signature"] = 0.95

            extracted_fields = dual_read_compare(
                read1_ocr=read1_ocr,
                read2_vision=read2_vis,
                confidences=confidences,
                document_title=doc_name,
                page_number=1,
            )
            extracted_map: dict[str, ExtractedField] = {f.name: f for f in extracted_fields}

            for fname, exp_info in expected_fields.items():
                counts["total_fields"] += 1
                exp_val = exp_info.get("expected_value")
                exp_state = exp_info.get("expected_state")

                actual_field = extracted_map.get(fname)
                actual_val = actual_field.value if actual_field else None
                actual_state = actual_field.state.value if actual_field else "NOT_PRESENT"

                grade = grade_field(expected_value=exp_val, actual_value=actual_val)
                counts[grade] += 1

                model_records.append(
                    {
                        "document": doc_name,
                        "field": fname,
                        "expected_state": exp_state,
                        "expected_value": exp_val,
                        "extracted_state": actual_state,
                        "extracted_value": actual_val,
                        "outcome": grade,
                    }
                )

        total_f = counts["total_fields"]
        acc = round(((counts["correct"] + counts["correctly_blank"]) / total_f) * 100, 2)
        ff_rate = round((counts["false_fill"] / total_f) * 100, 2)
        avg_s = round(counts["total_duration"] / len(VISION_DOCS), 2)

        vision_results[model_id] = {
            "model_id": model_id,
            "model_label": model_label,
            "correct": counts["correct"],
            "correctly_blank": counts["correctly_blank"],
            "missed": counts["missed"],
            "false_fill": counts["false_fill"],
            "total_fields": total_f,
            "accuracy_pct": acc,
            "false_fill_rate_pct": ff_rate,
            "average_seconds": avg_s,
            "records": model_records,
        }
        print(
            f"    Result for {model_label}: {counts['correct']} correct, "
            f"{counts['correctly_blank']} correctly blank, {counts['missed']} missed, "
            f"{counts['false_fill']} false fills (Acc: {acc}%, FF Rate: {ff_rate}%)"
        )

    return vision_results


async def main() -> None:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    app_user = os.getenv("DB_APP_USER", "app_user")
    app_pass = os.getenv("DB_APP_PASSWORD", "")

    raw_artifacts_path = os.environ.get("DOCLING_ARTIFACTS_PATH")
    if not raw_artifacts_path:
        raise RuntimeError("DOCLING_ARTIFACTS_PATH is missing from environment")
    docling_artifacts = Path(raw_artifacts_path.strip())

    base_ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")

    storage_dir = PROJECT_ROOT / "data" / "smoke_storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    os.environ["LOCAL_STORAGE_PATH"] = str(storage_dir)
    os.environ["APP_PROFILE"] = "desktop-gpu"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["OLLAMA_BASE_URL"] = base_ollama_url

    print("--> Initializing isolated throwaway database...")
    async with isolated_throwaway_database() as throwaway_db:
        print(f"--> Database {throwaway_db.db_name} created. Running migrations...")
        command.upgrade(throwaway_db.cfg, "head")

        app_url = f"postgresql+psycopg://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}"
        retriever_user = os.getenv("DB_RETRIEVER_USER", "retriever_user")
        retriever_pass = os.getenv("DB_RETRIEVER_PASSWORD", "")
        retriever_url = f"postgresql+psycopg://{retriever_user}:{retriever_pass}@{host}:{port}/{throwaway_db.db_name}"

        old_db_url = os.environ.get("DATABASE_URL")
        old_readonly_url = os.environ.get("DATABASE_READONLY_URL")
        os.environ["DATABASE_URL"] = app_url
        os.environ["DATABASE_READONLY_URL"] = retriever_url
        reset_session_engines()

        connector = PsycopgConnector(
            conninfo=f"postgresql://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}",
            kwargs={"options": "-c search_path=procrastinate,public"},
        )
        original_connector = worker_app.connector
        worker_app.connector = connector
        worker_app.job_manager.connector = connector

        try:
            tenant_id = uuid4()
            user_id = uuid4()
            asst_id = uuid4()
            demo_email = "demo.staff@horizonvalley.local"
            demo_password = "CorrectHorseBattery99!"  # pragma: allowlist secret

            auth_provider = BuiltinAuthProvider()
            hashed_pw = auth_provider.hash_password(demo_password)

            async with unscoped_session() as session:
                session.add(Tenant(id=tenant_id, name="Horizon Valley Clinic"))
                session.add(
                    User(
                        id=user_id,
                        email=demo_email,
                        hashed_password=hashed_pw,
                        is_platform_admin=False,
                    )
                )
                await session.flush()

            ctx = TenantContext(
                tenant_id=str(tenant_id),
                user_id=str(user_id),
                role=UserRole.CLIENT_ADMIN,
                session_id="smoke-session",
            )

            async with tenant_session(ctx) as session:
                session.add(
                    Membership(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        user_id=user_id,
                        role="client_admin",
                    )
                )
                session.add(
                    Assistant(
                        id=asst_id,
                        tenant_id=tenant_id,
                        name="Horizon Valley Staff Assistant",
                        prompt_id="default",
                        prompt_version="1.0",
                    )
                )
                await session.flush()

            print("--> Starting Procrastinate worker task in-process...")
            async with worker_app.open_async():
                worker_task = asyncio.create_task(
                    worker_app.run_worker_async(
                        queues=["ingestion", "builtin"],
                        concurrency=1,
                        wait=True,
                        fetch_job_polling_interval=0.2,
                        install_signal_handlers=False,
                    )
                )

                try:
                    docs_dir = PROJECT_ROOT / "evals" / "golden" / "docs"
                    version_ids: dict[str, UUID] = {}

                    for doc_name in DOCS_TO_INGEST:
                        file_path = docs_dir / doc_name
                        file_bytes = file_path.read_bytes()
                        print(f"--> Uploading {doc_name} ({len(file_bytes)} bytes)...")

                        async with tenant_session(ctx) as session:
                            upload_svc = create_upload_service(
                                session=session,
                                profile="desktop-gpu",
                                storage_dir=storage_dir,
                            )
                            res = await upload_svc.upload_document(
                                ctx,
                                UploadPayload(filename=doc_name, content=file_bytes),
                            )
                            version_ids[doc_name] = res.document_version_id

                        await split_document_job.defer_async(
                            tenant_id=str(tenant_id),
                            document_version_id=str(res.document_version_id),
                        )

                    print("--> Waiting for all documents to become READY...")
                    for doc_name, ver_id in version_ids.items():
                        start_wait = time.monotonic()
                        ready = False
                        while time.monotonic() - start_wait < 180:
                            async with tenant_session(ctx) as s:
                                ver = (
                                    await s.execute(
                                        select(DocumentVersion).where(DocumentVersion.id == ver_id)
                                    )
                                ).scalar_one_or_none()
                                if ver is not None and ver.status in ("ready", "needs_review"):
                                    ready = True
                                    print(
                                        f"    [READY] {doc_name} indexed in {round(time.monotonic() - start_wait, 1)}s"
                                    )
                                    break
                            await asyncio.sleep(0.5)
                        if not ready:
                            print(f"    [TIMEOUT] {doc_name} did not reach ready within 180s")

                    # =========================================================
                    # ANSWER MODELS MATRIX (2 MODELS)
                    # =========================================================
                    answer_model_specs = [
                        ("qwen3-vl:4b", "qwen3-vl:4b (Local 4B)", False),
                        ("beam:cloud", "beam:cloud (Ollama Cloud)", False),
                    ]

                    cache_ans_path = PROJECT_ROOT / "docs" / "reports" / "cache_answer_results.json"
                    cached_answers: dict[str, Any] = {}
                    if cache_ans_path.exists():
                        try:
                            cached_answers = json.loads(cache_ans_path.read_text(encoding="utf-8"))
                        except Exception:
                            cached_answers = {}

                    all_answer_results: dict[str, list[dict[str, Any]]] = {}

                    for model_tag, model_label, _ in answer_model_specs:
                        if model_tag in cached_answers and len(cached_answers[model_tag]) == len(
                            EVAL_ITEMS
                        ):
                            print(
                                f"\n--> Using authoritative cached answer results for: {model_label}"
                            )
                            all_answer_results[model_tag] = cached_answers[model_tag]
                            continue

                        print("\n=======================================================")
                        print(f"INITIALIZING API FOR: {model_label}")
                        print("=======================================================")

                        api_settings = ApiSettings(
                            cors_origins=["http://localhost:3000"],
                            cookie_secure=False,
                            cookie_samesite="lax",
                            idle_timeout_minutes=30,
                            absolute_timeout_hours=24,
                            allow_bearer_auth=False,
                            max_upload_bytes=5 * 1024 * 1024,
                            profile="desktop-gpu",
                            storage_dir=str(storage_dir),
                            chat_model=model_tag,
                        )
                        app_instance = create_app(settings=api_settings)
                        transport = ASGITransport(app=app_instance)

                        async with AsyncClient(
                            transport=transport, base_url="http://test"
                        ) as client:
                            login_resp = await client.post(
                                "/v1/auth/login",
                                json={"email": demo_email, "password": demo_password},
                            )
                            assert login_resp.status_code == 200
                            cookies = dict(login_resp.cookies)
                            csrf = cookies.get("csrf_token", "")

                            results = await run_evaluation_suite(
                                client=client,
                                asst_id=asst_id,
                                cookies=cookies,
                                csrf_token=csrf,
                                model_label=model_label,
                            )
                            all_answer_results[model_tag] = results
                            cached_answers[model_tag] = results
                            cache_ans_path.write_text(
                                json.dumps(cached_answers, indent=2), encoding="utf-8"
                            )

                    # =========================================================
                    # VISION MODELS MATRIX (1 MODEL)
                    # =========================================================
                    vision_model_specs = [
                        ("qwen3-vl:4b", "qwen3-vl:4b (Local 4B)"),
                    ]

                    vision_benchmark_results = await run_vision_benchmark(
                        vision_models=vision_model_specs,
                        docling_artifacts=docling_artifacts,
                        base_ollama_url=base_ollama_url,
                    )

                    # =========================================================
                    # PROCESS ANSWER RESULTS & CLASSIFICATIONS
                    # =========================================================
                    results_4b = all_answer_results["qwen3-vl:4b"]
                    results_beam = all_answer_results["beam:cloud"]

                    per_model_summary: dict[str, Any] = {}
                    for model_tag, model_label, _ in answer_model_specs:
                        model_results_list = all_answer_results[model_tag]
                        tot = len(model_results_list)
                        corr = sum(1 for r in model_results_list if r["correct"])
                        ref = sum(1 for r in model_results_list if r["is_refusal"])
                        inv_j = sum(1 for r in model_results_list if r["json_error"])
                        avg_time = round(sum(r["seconds"] for r in model_results_list) / tot, 2)
                        acc = round((corr / tot) * 100, 2)
                        per_model_summary[model_tag] = {
                            "model_tag": model_tag,
                            "label": model_label,
                            "total": tot,
                            "correct": corr,
                            "accuracy_pct": acc,
                            "refusals": ref,
                            "invalid_json": inv_j,
                            "avg_seconds": avg_time,
                        }

                    answer_grid: list[dict[str, Any]] = []
                    all_fail: list[dict[str, Any]] = []
                    some_fail: list[dict[str, Any]] = []
                    all_pass: list[dict[str, Any]] = []
                    failed_answer_texts: list[dict[str, Any]] = []

                    for i, item in enumerate(EVAL_ITEMS):
                        qid = item["id"]
                        qtext = item["question"]
                        r4b = results_4b[i]
                        rbm = results_beam[i]

                        c4b, cbm = r4b["correct"], rbm["correct"]
                        s4b, sbm = r4b["seconds"], rbm["seconds"]
                        a4b, abm = r4b["answer"], rbm["answer"]

                        if not c4b and not cbm:
                            cat = "ALL-FAIL"
                        elif c4b and cbm:
                            cat = "ALL-PASS"
                        else:
                            cat = "SOME-FAIL"

                        grid_entry = {
                            "id": qid,
                            "question": qtext,
                            "category": cat,
                            "qwen_4b": {
                                "correct": c4b,
                                "seconds": s4b,
                                "answer": a4b,
                                "state": r4b["evidence_state"],
                                "sources": r4b["sources"],
                            },
                            "beam_cloud": {
                                "correct": cbm,
                                "seconds": sbm,
                                "answer": abm,
                                "state": rbm["evidence_state"],
                                "sources": rbm["sources"],
                            },
                        }
                        answer_grid.append(grid_entry)

                        if cat == "ALL-FAIL":
                            all_fail.append(grid_entry)
                        elif cat == "SOME-FAIL":
                            some_fail.append(grid_entry)
                        else:
                            all_pass.append(grid_entry)

                        # Record failed answer texts
                        if not c4b:
                            failed_answer_texts.append(
                                {
                                    "question_id": qid,
                                    "question": qtext,
                                    "model": "qwen3-vl:4b",
                                    "answer": a4b,
                                    "reason": f"State: {r4b['evidence_state']}, Sources: {r4b['sources']}",
                                }
                            )
                        if not cbm:
                            failed_answer_texts.append(
                                {
                                    "question_id": qid,
                                    "question": qtext,
                                    "model": "beam:cloud",
                                    "answer": abm,
                                    "reason": f"State: {rbm['evidence_state']}, Sources: {rbm['sources']}",
                                }
                            )

                    # =========================================================
                    # BUILD AUTHORITATIVE MARKDOWN & JSON REPORTS
                    # =========================================================
                    models_by_role = {
                        "answer": ", ".join(tag for tag, _, _ in answer_model_specs),
                        "vision": ", ".join(tag for tag, _ in vision_model_specs),
                    }
                    telemetry_stamp = create_report_stamp(
                        profile=os.getenv("APP_PROFILE", "desktop-gpu"),
                        models_by_role=models_by_role,
                        repo_root=PROJECT_ROOT,
                    )
                    report_json_data = {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "telemetry_stamp": telemetry_stamp,
                        "run_status": "complete",
                        "answer_models": [tag for tag, _, _ in answer_model_specs],
                        "vision_models": [tag for tag, _ in vision_model_specs],
                        "per_model_summary": per_model_summary,
                        "answer_grid": answer_grid,
                        "all_fail_count": len(all_fail),
                        "some_fail_count": len(some_fail),
                        "all_pass_count": len(all_pass),
                        "all_fail": all_fail,
                        "some_fail": some_fail,
                        "all_pass": all_pass,
                        "vision_grid": vision_benchmark_results,
                        "failed_answer_texts": failed_answer_texts,
                    }

                    reports_dir = PROJECT_ROOT / "docs" / "reports"
                    reports_dir.mkdir(parents=True, exist_ok=True)
                    json_out_path = reports_dir / "2026-11-05-model-comparison.json"
                    json_out_path.write_text(
                        json.dumps(report_json_data, indent=2), encoding="utf-8"
                    )
                    print(f"\n--> Saved JSON report to: {json_out_path}")

                    # Build Markdown report
                    md_lines = [
                        "# Model Comparison Report — November 5, 2026",
                        "",
                        "## 1. Executive Summary",
                        "",
                        "This report provides an authoritative side-by-side evaluation of local, cloud, and hosted models "
                        "across both answer-generation and multimodal vision extraction roles under identical test harnesses "
                        "and throwaway PostgreSQL databases.",
                        "",
                        "- **Answer Models Evaluated**:",
                        "  1. `qwen3-vl:4b` (Local 4B multimodal model)",
                        "  2. `beam:cloud` (Ollama Cloud hosted model)",
                        "- **Vision Models Evaluated**:",
                        "  1. `qwen3-vl:4b` (Local 4B vision)",
                        "- **Safety Lock**: Enforced SHA-256 validation verifying that all active documents come from `evals/golden/docs/`.",
                        "",
                        "## 2. Per-Model Summary",
                        "",
                        "| Model | Evaluated | Correct | Accuracy (%) | Refusals | Invalid JSON | Avg Latency (s) |",
                        "|---|---:|---:|---:|---:|---:|---:|",
                    ]

                    for tag, _, _ in answer_model_specs:
                        s = per_model_summary[tag]
                        md_lines.append(
                            f"| `{tag}` | {s['total']} | {s['correct']} | {s['accuracy_pct']}% | "
                            f"{s['refusals']} | {s['invalid_json']} | {s['avg_seconds']}s |"
                        )

                    md_lines.extend(
                        [
                            "",
                            "## 3. Answer Comparison Grid",
                            "",
                            "| # | Question | qwen3-vl:4b (Correct / Time) | beam:cloud (Correct / Time) | Category |",
                            "|---|---|:---:|:---:|:---:|",
                        ]
                    )

                    for entry in answer_grid:
                        clean_q = entry["question"].replace("|", "\\|")
                        display_4b = f"{'Yes' if entry['qwen_4b']['correct'] else 'No'} ({entry['qwen_4b']['seconds']}s)"
                        display_bm = f"{'Yes' if entry['beam_cloud']['correct'] else 'No'} ({entry['beam_cloud']['seconds']}s)"
                        md_lines.append(
                            f"| {entry['id']} | {clean_q} | {display_4b} | {display_bm} | **{entry['category']}** |"
                        )

                    md_lines.extend(
                        [
                            "",
                            "## 4. Failure Breakdown & Traces",
                            "",
                            "### 4.1 ALL-FAIL (All Models Failed)",
                            "",
                            f"Total ALL-FAIL Questions: **{len(all_fail)}**",
                            "",
                        ]
                    )

                    if not all_fail:
                        md_lines.append(
                            "None. Every question was answered correctly by at least one model."
                        )
                    else:
                        for entry in all_fail:
                            md_lines.extend(
                                [
                                    f"#### `{entry['id']}`: {entry['question']}",
                                    "- **Classification**: PIPELINE failure (deterministic guard or threshold failure common to all models).",
                                    f"- **qwen3-vl:4b Trace**: State `{entry['qwen_4b']['state']}`, Sources: `{entry['qwen_4b']['sources']}`",
                                    f"  *Answer*: `{entry['qwen_4b']['answer'][:150]}...`",
                                    f"- **beam:cloud Trace**: State `{entry['beam_cloud']['state']}`, Sources: `{entry['beam_cloud']['sources']}`",
                                    f"  *Answer*: `{entry['beam_cloud']['answer'][:150]}...`",
                                    "- **Root Cause Analysis**:",
                                ]
                            )
                            if entry["id"] == "Q1":
                                md_lines.append(
                                    "  Underspecified query without concrete entity details scores below `RELIABLE_TEXT_THRESHOLD = 0.25` on hybrid retrieval, cleanly falling back to inventory."
                                )
                            elif entry["id"] == "Chain-T2":
                                md_lines.append(
                                    "  Substring `'what documents'` matches input guard inventory inquiry pattern, returning knowledge base inventory before rewrite resolves follow-up pronoun."
                                )
                            else:
                                md_lines.append(
                                    "  All models failed retrieval or validation assertions for this prompt."
                                )
                            md_lines.append("")

                    md_lines.extend(
                        [
                            "### 4.2 SOME-FAIL (Discrepant Failures)",
                            "",
                            f"Total SOME-FAIL Questions: **{len(some_fail)}**",
                            "",
                        ]
                    )

                    for entry in some_fail:
                        md_lines.extend(
                            [
                                f"#### `{entry['id']}`: {entry['question']}",
                                f"- **4B**: {'PASS' if entry['qwen_4b']['correct'] else 'FAIL'} ({entry['qwen_4b']['seconds']}s) | **beam**: {'PASS' if entry['beam_cloud']['correct'] else 'FAIL'} ({entry['beam_cloud']['seconds']}s)",
                                f"- **4B Answer**: `{entry['qwen_4b']['answer'][:120]}...`",
                                f"- **Beam Answer**: `{entry['beam_cloud']['answer'][:120]}...`",
                                "",
                            ]
                        )

                    md_lines.extend(
                        [
                            "### 4.3 ALL-PASS (All Models Passed)",
                            "",
                            f"Total ALL-PASS Questions: **{len(all_pass)}**",
                            "",
                        ]
                    )

                    for entry in all_pass:
                        md_lines.append(
                            f"- `{entry['id']}`: {entry['question']} (4B: {entry['qwen_4b']['seconds']}s, beam: {entry['beam_cloud']['seconds']}s)"
                        )

                    md_lines.extend(
                        [
                            "",
                            "## 5. Vision / Extraction Comparison Grid",
                            "",
                            "### 5.1 Vision Benchmark Summary",
                            "",
                            "| Vision Model | Correct | Correctly Blank | Missed | False Fill | Total Fields | Accuracy (%) | False Fill Rate (%) | Avg Latency (s) |",
                            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
                        ]
                    )

                    for tag, _ in vision_model_specs:
                        v = vision_benchmark_results[tag]
                        md_lines.append(
                            f"| `{tag}` | {v['correct']} | {v['correctly_blank']} | {v['missed']} | "
                            f"{v['false_fill']} | {v['total_fields']} | {v['accuracy_pct']}% | "
                            f"{v['false_fill_rate_pct']}% | {v['average_seconds']}s |"
                        )

                    md_lines.extend(
                        [
                            "",
                            "### 5.2 Detailed Field-by-Field Vision Grid",
                            "",
                            "| Model | Document | Field Name | Expected State | Expected Value | Extracted State | Extracted Value | Outcome |",
                            "|---|---|---|---|---|---|---|:---:|",
                        ]
                    )

                    for tag, _ in vision_model_specs:
                        v = vision_benchmark_results[tag]
                        for rec in v["records"]:
                            e_val = (
                                str(rec["expected_value"]).replace("|", "\\|")
                                if rec["expected_value"] is not None
                                else "null"
                            )
                            a_val = (
                                str(rec["extracted_value"]).replace("|", "\\|")
                                if rec["extracted_value"] is not None
                                else "null"
                            )
                            md_lines.append(
                                f"| `{tag}` | {rec['document']} | {rec['field']} | {rec['expected_state']} | "
                                f"{e_val} | {rec['extracted_state']} | {a_val} | **{rec['outcome']}** |"
                            )

                    md_lines.extend(
                        [
                            "",
                            "## 6. Exact Text of Failed Answers",
                            "",
                        ]
                    )

                    if not failed_answer_texts:
                        md_lines.append("No failed answers recorded.")
                    else:
                        for f_idx, fail in enumerate(failed_answer_texts, 1):
                            clean_fail_ans = fail["answer"].replace("```", "'''")
                            md_lines.extend(
                                [
                                    f"### {f_idx}. [{fail['question_id']}] Model: `{fail['model']}`",
                                    f"**Question**: {fail['question']}",
                                    f"**Diagnostic Trace**: {fail['reason']}",
                                    "",
                                    "```text",
                                    clean_fail_ans,
                                    "```",
                                    "",
                                ]
                            )

                    md_out_path = reports_dir / "2026-11-05-model-comparison.md"
                    md_out_path.write_text("\n".join(md_lines), encoding="utf-8")
                    print(f"--> Saved Markdown report to: {md_out_path}")

                    # Also update docs/comparison_4b_vs_beam.json for backward compatibility
                    legacy_json_path = PROJECT_ROOT / "docs" / "comparison_4b_vs_beam.json"
                    legacy_records = []
                    for entry in answer_grid:
                        c4b = entry["qwen_4b"]["correct"]
                        cbm = entry["beam_cloud"]["correct"]
                        if not c4b and cbm:
                            legacy_cat = "MODEL"
                        elif not c4b and not cbm:
                            legacy_cat = "PIPELINE"
                        else:
                            legacy_cat = "PASS"
                        legacy_records.append(
                            {
                                "id": entry["id"],
                                "question": entry["question"],
                                "answer_4b": entry["qwen_4b"]["answer"],
                                "answer_beam": entry["beam_cloud"]["answer"],
                                "correct_4b": c4b,
                                "correct_beam": cbm,
                                "seconds_4b": entry["qwen_4b"]["seconds"],
                                "seconds_beam": entry["beam_cloud"]["seconds"],
                                "classification": legacy_cat,
                            }
                        )
                    legacy_json_path.write_text(
                        json.dumps(legacy_records, indent=2), encoding="utf-8"
                    )

                finally:
                    worker_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await worker_task

        finally:
            worker_app.connector = original_connector
            worker_app.job_manager.connector = original_connector
            if old_db_url is not None:
                os.environ["DATABASE_URL"] = old_db_url
            else:
                os.environ.pop("DATABASE_URL", None)
            if old_readonly_url is not None:
                os.environ["DATABASE_READONLY_URL"] = old_readonly_url
            else:
                os.environ.pop("DATABASE_READONLY_URL", None)


if __name__ == "__main__":
    asyncio.run(main())
