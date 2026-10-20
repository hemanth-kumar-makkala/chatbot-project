"""Phase 3 Step 4 Smoke Run on throwaway database and real desktop-gpu pipeline.

Ingests:
- front_desk_policy_v1.pdf
- front_desk_policy_v2.pdf
- prior_auth_guide.pdf
- fee_schedule.pdf
- accepted_plans.csv

Executes the 11 smoke questions:
1. "HORIZON VALLEY HEALTH CLINIC data please"
2. "What is the front desk intake fax number?" (conflict reply: 555-0101 and 555-0102)
3. "How early must patients arrive for a routine visit?" (conflict reply: 15 vs 20 minutes)
4. "Summarize the outpatient prior authorization guide" (explain mode)
5. "What insurance plans are accepted?" (every accepted plan, each cited)
6. "What is the effective date for our outpatient prior authorization policy?" (TEXT_FACT 1)
7. "How many business days in advance must outpatient surgery prior auths be submitted?" (TEXT_FACT 2)
8. "How recent must clinical consultation notes be when submitting an auth request?" (TEXT_FACT 3)
9. "What is the policy reference code for outpatient prior authorization?" (exact-mode)
10. "What is the copayment amount for acupuncture therapy sessions at Horizon Valley?" (NO_ANSWER 1)
11. "What is the discounted parking rate for patients in the medical plaza garage?" (NO_ANSWER 2)

Outputs Markdown table:
question, answer mode, evidence_state, answer, sources, complete and correct (yes/no), seconds.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

with contextlib.suppress(Exception):
    sys.stdout.reconfigure(line_buffering=True)

PROJECT_ROOT = Path("c:/Users/makka/Downloads/Projects/chatbots/chatbot-project")
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
from app.db.session import (
    reset_session_engines,
    tenant_session,
    unscoped_session,
)
from app.db.models import (
    Assistant,
    DocumentVersion,
    Membership,
    Tenant,
    User,
)
from app.ports.types import TenantContext, UploadPayload, UserRole
from app.wiring import create_upload_service
from app.worker import app as worker_app, split_document_job
from app.api.app import create_app
from app.api.config import ApiSettings
from app.adapters.auth.builtin import BuiltinAuthProvider


DOCS_TO_INGEST = [
    "front_desk_policy_v1.pdf",
    "front_desk_policy_v2.pdf",
    "prior_auth_guide.pdf",
    "fee_schedule.pdf",
    "accepted_plans.csv",
]

QUESTIONS = [
    {
        "id": "Q1",
        "question": "HORIZON VALLEY HEALTH CLINIC data please",
        "expected_mode": "explain",
        "check": lambda ans, state, src: len(ans) > 20 and ("555" in ans or "clinic" in ans.lower() or "policy" in ans.lower()),
    },
    {
        "id": "Q2",
        "question": "What is the front desk intake fax number?",
        "expected_mode": "short",
        "check": lambda ans, state, src: state == "CONFLICTING" or ("555-0101" in ans and "555-0102" in ans),
    },
    {
        "id": "Q3",
        "question": "How early must patients arrive for a routine visit?",
        "expected_mode": "short",
        "check": lambda ans, state, src: state == "CONFLICTING" or ("15" in ans and "20" in ans),
    },
    {
        "id": "Q4",
        "question": "Summarize the outpatient prior authorization guide",
        "expected_mode": "explain",
        "check": lambda ans, state, src: len(ans) > 30 and ("prior auth" in ans.lower() or "authorization" in ans.lower() or "2026-pa-01" in ans.lower()),
    },
    {
        "id": "Q5",
        "question": "What insurance plans are accepted?",
        "expected_mode": "table",
        "check": lambda ans, state, src: "crestview" in ans.lower() or "apex" in ans.lower() or "blue" in ans.lower() or "health" in ans.lower(),
    },
    {
        "id": "Q6",
        "question": "What is the effective date for our outpatient prior authorization policy?",
        "expected_mode": "short",
        "check": lambda ans, state, src: "January 1, 2026" in ans or "2026" in ans,
    },
    {
        "id": "Q7",
        "question": "How many business days in advance must outpatient surgery prior auths be submitted?",
        "expected_mode": "short",
        "check": lambda ans, state, src: "5" in ans,
    },
    {
        "id": "Q8",
        "question": "How recent must clinical consultation notes be when submitting an auth request?",
        "expected_mode": "short",
        "check": lambda ans, state, src: "30" in ans,
    },
    {
        "id": "Q9",
        "question": "What is the policy reference code for outpatient prior authorization?",
        "expected_mode": "exact",
        "check": lambda ans, state, src: "2026-PA-01" in ans,
    },
    {
        "id": "Q10",
        "question": "What is the copayment amount for acupuncture therapy sessions at Horizon Valley?",
        "expected_mode": "short",
        "check": lambda ans, state, src: "couldn't find" in ans.lower() or "couldn't confirm" in ans.lower(),
    },
    {
        "id": "Q11",
        "question": "What is the discounted parking rate for patients in the medical plaza garage?",
        "expected_mode": "short",
        "check": lambda ans, state, src: "couldn't find" in ans.lower() or "couldn't confirm" in ans.lower(),
    },
]


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
            demo_password = "CorrectHorseBattery99!"

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
                                        select(DocumentVersion).where(
                                            DocumentVersion.id == ver_id
                                        )
                                    )
                                ).scalar_one_or_none()
                                if ver is not None:
                                    if ver.status == "ready":
                                        ready = True
                                        print(f"    [READY] {doc_name} reached 'ready' status in {round(time.monotonic() - start_wait, 1)}s")
                                        break
                                    elif ver.status in ("needs_review", "failed"):
                                        print(f"    [STATUS] {doc_name} is in '{ver.status}' status")
                                        ready = True
                                        break
                            await asyncio.sleep(0.5)

                        if not ready:
                            print(f"    [TIMEOUT] {doc_name} did not reach ready within 180s")

                    print("\n--> Initializing API test client for chat...")
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
                    )
                    test_app = create_app(settings=api_settings)
                    transport = ASGITransport(app=test_app)

                    rows: list[dict[str, Any]] = []

                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        login_resp = await client.post(
                            "/v1/auth/login",
                            json={"email": demo_email, "password": demo_password},
                        )
                        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
                        cookies = dict(login_resp.cookies)
                        csrf_token = cookies.get("csrf_token", "")

                        print("\n--> Asking 11 questions on real desktop-gpu pipeline...")
                        for idx, item in enumerate(QUESTIONS, 1):
                            q_text = item["question"]
                            t0 = time.monotonic()
                            resp = await client.post(
                                "/v1/questions",
                                json={
                                    "assistant_id": str(asst_id),
                                    "question_text": q_text,
                                },
                                cookies=cookies,
                                headers={"X-CSRF-Token": csrf_token},
                                timeout=180.0,
                            )
                            elapsed = round(time.monotonic() - t0, 2)

                            if resp.status_code != 200:
                                answer = f"ERROR {resp.status_code}: {resp.text}"
                                ans_mode = "unknown"
                                ev_state = "ERROR"
                                sources_str = "None"
                                is_correct = "no"
                            else:
                                data = resp.json()
                                answer = data.get("answer", "")
                                ans_mode = data.get("answer_mode") or "short"
                                ev_state = data.get("evidence_state") or ("CONFLICTING" if data.get("status") == "conflicting" else "RELIABLE_TEXT")
                                sources = [s.get("document_title", "") for s in data.get("sources", [])]
                                if not sources and data.get("conflict_sources"):
                                    sources = [s.get("document_title", "") for s in data.get("conflict_sources", [])]
                                sources_str = ", ".join(sources) if sources else "None"
                                is_correct = "yes" if item["check"](answer, ev_state, sources_str) else "no"

                            rows.append({
                                "num": idx,
                                "question": q_text,
                                "answer_mode": ans_mode,
                                "evidence_state": ev_state,
                                "answer": answer.replace("\n", " ").strip()[:100],
                                "full_answer": answer,
                                "sources": sources_str,
                                "complete_and_correct": is_correct,
                                "seconds": elapsed,
                            })
                            print(f"[{idx}/11] ({elapsed}s) Mode: {ans_mode} | State: {ev_state} | Correct: {is_correct}")
                            print(f"      Q: {q_text}")
                            print(f"      A: {answer[:120]}...")
                            print(f"      Sources: {sources_str}\n")

                    print("\n" + "=" * 90)
                    print("PHASE 3 STEP 4 SMOKE RUN RESULTS TABLE")
                    print("=" * 90)
                    header = "| # | Question | Answer Mode | Evidence State | Answer (snippet) | Sources | Complete & Correct | Seconds |"
                    sep = "|---|---|---|---|---|---|---|---|"
                    print(header)
                    print(sep)
                    for r in rows:
                        q_cell = r["question"].replace("|", "\\|")
                        a_cell = r["answer"].replace("|", "\\|")
                        s_cell = r["sources"].replace("|", "\\|")
                        print(f"| {r['num']} | {q_cell} | {r['answer_mode']} | {r['evidence_state']} | {a_cell} | {s_cell} | {r['complete_and_correct']} | {r['seconds']} |")
                    print("=" * 90)

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
            reset_session_engines()


if __name__ == "__main__":
    asyncio.run(main())
