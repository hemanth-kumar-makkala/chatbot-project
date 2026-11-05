"""Compare local qwen3-vl:4b vs beam:cloud on throwaway database.

Runs:
1. Ingests 6 golden documents on desktop-gpu profile.
2. 12 smoke questions + 3-turn follow-up chain with qwen3-vl:4b.
3. 12 smoke questions + 3-turn follow-up chain with beam:cloud.
4. Generates side-by-side table classifying failures as MODEL, PIPELINE, or JSON.
"""

# ruff: noqa: E501, ARG001, ARG005, E402

from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

getattr(sys.stdout, "reconfigure", lambda **kw: None)(line_buffering=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BACKEND_DIR / ".env")

from alembic import command
from httpx import ASGITransport, AsyncClient
from procrastinate import PsycopgConnector
from sqlalchemy import select
from tests.conftest import isolated_throwaway_database

from app.adapters.auth.builtin import BuiltinAuthProvider
from app.api.app import create_app
from app.api.config import ApiSettings
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

GOLDEN_QS_PATH = PROJECT_ROOT / "evals" / "golden" / "questions.json"
GOLDEN_QS: dict[str, Any] = {
    q["id"]: q for q in json.loads(GOLDEN_QS_PATH.read_text(encoding="utf-8"))
}

ACCEPTED_PLANS_CSV_PATH = PROJECT_ROOT / "evals" / "golden" / "docs" / "accepted_plans.csv"
CSV_PLANS: list[dict[str, str]] = []
with open(ACCEPTED_PLANS_CSV_PATH, encoding="utf-8") as f:
    clean_lines = [line for line in f if not line.strip().startswith("#")]
    for row in csv.DictReader(clean_lines):
        CSV_PLANS.append(row)


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
    """Q5: What insurance plans are accepted? Compare cells to CSV."""
    for r in CSV_PLANS:
        for col_name, cell_val in r.items():
            if col_name not in ans or cell_val not in ans:
                return False
    if "blue horizon" in ans.lower() or "crv-epo-03" in ans.lower():
        return False
    only_accepted_plans = (
        "accepted_plans.csv" in src and "front_desk" not in src and "prior_auth" not in src
    )
    return only_accepted_plans


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
        if resp.status_code != 200:
            answer = f"HTTP ERROR {resp.status_code}: {resp.text}"
            ans_mode = "unknown"
            ev_state = "ERROR"
            sources_str = "None"
            is_correct = False
            if "MALFORMED_OUTPUT" in resp.text or "JSON" in resp.text:
                json_error = True
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
            }
        )

    return results


async def main() -> None:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    app_user = os.getenv("DB_APP_USER", "app_user")
    app_pass = os.getenv("DB_APP_PASSWORD", "")

    storage_dir = PROJECT_ROOT / "data" / "smoke_storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    os.environ["LOCAL_STORAGE_PATH"] = str(storage_dir)
    os.environ["APP_PROFILE"] = "desktop-gpu"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["OLLAMA_BASE_URL"] = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

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
                    # RUN 1: Local Model (qwen3-vl:4b)
                    # =========================================================
                    api_settings_4b = ApiSettings(
                        cors_origins=["http://localhost:3000"],
                        cookie_secure=False,
                        cookie_samesite="lax",
                        idle_timeout_minutes=30,
                        absolute_timeout_hours=24,
                        allow_bearer_auth=False,
                        max_upload_bytes=5 * 1024 * 1024,
                        profile="desktop-gpu",
                        storage_dir=str(storage_dir),
                        chat_model="qwen3-vl:4b",
                    )
                    app_4b = create_app(settings=api_settings_4b)
                    transport_4b = ASGITransport(app=app_4b)

                    async with AsyncClient(
                        transport=transport_4b, base_url="http://test"
                    ) as client_4b:
                        login_resp = await client_4b.post(
                            "/v1/auth/login",
                            json={"email": demo_email, "password": demo_password},
                        )
                        assert login_resp.status_code == 200
                        cookies_4b = dict(login_resp.cookies)
                        csrf_4b = cookies_4b.get("csrf_token", "")

                        results_4b = await run_evaluation_suite(
                            client=client_4b,
                            asst_id=asst_id,
                            cookies=cookies_4b,
                            csrf_token=csrf_4b,
                            model_label="qwen3-vl:4b (Local 4B Model)",
                        )

                    # =========================================================
                    # RUN 2: Cloud Model (beam:cloud)
                    # =========================================================
                    api_settings_beam = ApiSettings(
                        cors_origins=["http://localhost:3000"],
                        cookie_secure=False,
                        cookie_samesite="lax",
                        idle_timeout_minutes=30,
                        absolute_timeout_hours=24,
                        allow_bearer_auth=False,
                        max_upload_bytes=5 * 1024 * 1024,
                        profile="desktop-gpu",
                        storage_dir=str(storage_dir),
                        chat_model="beam:cloud",
                    )
                    app_beam = create_app(settings=api_settings_beam)
                    transport_beam = ASGITransport(app=app_beam)

                    async with AsyncClient(
                        transport=transport_beam, base_url="http://test"
                    ) as client_beam:
                        login_resp = await client_beam.post(
                            "/v1/auth/login",
                            json={"email": demo_email, "password": demo_password},
                        )
                        assert login_resp.status_code == 200
                        cookies_beam = dict(login_resp.cookies)
                        csrf_beam = cookies_beam.get("csrf_token", "")

                        results_beam = await run_evaluation_suite(
                            client=client_beam,
                            asst_id=asst_id,
                            cookies=cookies_beam,
                            csrf_token=csrf_beam,
                            model_label="beam:cloud (Ollama Cloud Model)",
                        )

                    # =========================================================
                    # SIDE-BY-SIDE TABLE & FAILURE CLASSIFICATION
                    # =========================================================
                    print("\n" + "=" * 120)
                    print("SIDE-BY-SIDE MODEL COMPARISON TABLE: qwen3-vl:4b vs beam:cloud")
                    print("=" * 120)
                    md_header = (
                        "| # | Question | 4B Answer (exact text) | Beam Answer (exact text) | "
                        "4B Correct | Beam Correct | 4B (s) | Beam (s) | Classification |"
                    )
                    md_sep = "|---|---|---|---|---|---|---|---|---|"
                    print(md_header)
                    print(md_sep)

                    comparison_data: list[dict[str, Any]] = []

                    for r4b, rbm in zip(results_4b, results_beam, strict=False):
                        qid = r4b["id"]
                        q_text = r4b["question"]
                        a4b = r4b["answer"]
                        abm = rbm["answer"]
                        c4b = r4b["correct"]
                        cbm = rbm["correct"]
                        s4b = r4b["seconds"]
                        sbm = rbm["seconds"]

                        if r4b["json_error"] or rbm["json_error"]:
                            classification = "JSON"
                        elif cbm and not c4b:
                            classification = "MODEL"
                        elif not cbm and not c4b:
                            classification = "PIPELINE"
                        else:
                            classification = "PASS"

                        clean_a4b = a4b.replace("\n", " ").replace("|", "\\|")
                        clean_abm = abm.replace("\n", " ").replace("|", "\\|")
                        clean_q = q_text.replace("|", "\\|")

                        row_line = (
                            f"| {qid} | {clean_q} | {clean_a4b} | {clean_abm} | "
                            f"{'Yes' if c4b else 'No'} | {'Yes' if cbm else 'No'} | "
                            f"{s4b}s | {sbm}s | {classification} |"
                        )
                        print(row_line)

                        comparison_data.append(
                            {
                                "id": qid,
                                "question": q_text,
                                "answer_4b": a4b,
                                "answer_beam": abm,
                                "correct_4b": c4b,
                                "correct_beam": cbm,
                                "seconds_4b": s4b,
                                "seconds_beam": sbm,
                                "classification": classification,
                            }
                        )

                    print("=" * 120)

                    # Save comparison artifact
                    out_path = PROJECT_ROOT / "docs" / "comparison_4b_vs_beam.json"
                    out_path.write_text(json.dumps(comparison_data, indent=2), encoding="utf-8")
                    print(f"Saved comparison data to {out_path}")

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
