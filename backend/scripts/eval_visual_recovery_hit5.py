"""Recompute Visual Recovery Hit@5 against a live PostgreSQL database on desktop-gpu profile.

Steps:
1. Provisions isolated throwaway PostgreSQL database.
2. Runs all Alembic migrations to head.
3. Ingests golden visual recovery documents (clean_fax, skewed_fax, faint_fax_2page)
   plus distractors (front_desk_policy_v1, prior_auth_guide, fee_schedule, accepted_plans)
   using real desktop-gpu pipeline (Docling OCR, Ollama vision, BGE-M3 embeddings).
4. Runs PostgresRetriever (hybrid dense pgvector + tsvector RRF) for all 16 golden
   VISUAL_RECOVERY questions (VR-001 to VR-016).
5. Reports whether expected target page is retrieved in Top-5 for each question.
"""

# ruff: noqa: E402
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BACKEND_DIR / ".env")

from alembic import command
from procrastinate import PsycopgConnector
from sqlalchemy import select, text
from tests.conftest import isolated_throwaway_database

from app.adapters.auth.builtin import BuiltinAuthProvider
from app.adapters.retrieval.postgres_retriever import PostgresRetriever
from app.db.models import Assistant, DocumentVersion, Membership, Tenant, User
from app.db.session import reset_session_engines, tenant_session, unscoped_session
from app.ports.types import RetrievalFilters, TenantContext, UploadPayload, UserRole
from app.wiring import create_embedding_provider, create_upload_service
from app.worker import app as worker_app
from app.worker import split_document_job

DOCS_TO_INGEST = [
    "clean_fax.pdf",
    "skewed_fax.pdf",
    "faint_fax_2page.pdf",
    "front_desk_policy_v1.pdf",
    "prior_auth_guide.pdf",
    "fee_schedule.pdf",
    "accepted_plans.csv",
]

DOC_ID_TO_FILENAME = {
    "doc_clean_fax": "clean_fax.pdf",
    "doc_skewed_fax": "skewed_fax.pdf",
    "doc_faint_fax_2page": "faint_fax_2page.pdf",
}


async def run_visual_recovery_eval() -> dict[str, Any]:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    app_user = os.getenv("DB_APP_USER", "app_user")
    app_pass = os.getenv("DB_APP_PASSWORD", "")
    retriever_user = os.getenv("DB_RETRIEVER_USER", "retriever_user")
    retriever_pass = os.getenv("DB_RETRIEVER_PASSWORD", "")

    storage_dir = PROJECT_ROOT / "data" / "vr_smoke_storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    os.environ["LOCAL_STORAGE_PATH"] = str(storage_dir)
    os.environ["APP_PROFILE"] = "desktop-gpu"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["OLLAMA_BASE_URL"] = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    questions_path = PROJECT_ROOT / "evals" / "golden" / "questions.json"
    all_qs = json.loads(questions_path.read_text(encoding="utf-8"))
    vr_questions = [q for q in all_qs if q.get("category") == "VISUAL_RECOVERY"]

    print(f"Loaded {len(vr_questions)} VISUAL_RECOVERY questions.")

    async with isolated_throwaway_database() as throwaway_db:
        print(f"--> Database {throwaway_db.db_name} created. Running migrations...")
        command.upgrade(throwaway_db.cfg, "head")

        app_url = f"postgresql+psycopg://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}"
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
            auth_provider = BuiltinAuthProvider()
            hashed_pw = auth_provider.hash_password("DevPassword123!")

            async with unscoped_session() as session:
                session.add(Tenant(id=tenant_id, name="Visual Recovery Evaluation Tenant"))
                session.add(
                    User(
                        id=user_id,
                        email="eval@tenant.local",
                        hashed_password=hashed_pw,
                        is_platform_admin=False,
                    )
                )
                await session.flush()

            ctx = TenantContext(
                tenant_id=str(tenant_id),
                user_id=str(user_id),
                role=UserRole.CLIENT_ADMIN,
                session_id="vr-eval-session",
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
                        id=uuid4(),
                        tenant_id=tenant_id,
                        name="VR Evaluation Assistant",
                        prompt_id="default",
                        prompt_version="1.0",
                    )
                )
                await session.flush()

            print("--> Starting Procrastinate worker task...")
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
                        print(f"--> Ingesting {doc_name} ({len(file_bytes)} bytes)...")

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

                    print("--> Waiting for all documents to reach terminal status...")
                    for doc_name, ver_id in version_ids.items():
                        start_wait = time.monotonic()
                        while time.monotonic() - start_wait < 180:
                            async with tenant_session(ctx) as s:
                                ver = (
                                    await s.execute(
                                        select(DocumentVersion).where(DocumentVersion.id == ver_id)
                                    )
                                ).scalar_one_or_none()
                                if ver is not None and ver.status in (
                                    "ready",
                                    "needs_review",
                                    "error",
                                ):
                                    elapsed = round(time.monotonic() - start_wait, 1)
                                    status_str = ver.status.upper()
                                    print(f"    [{status_str}] {doc_name} in {elapsed}s")
                                    if ver.status == "error":
                                        print(
                                            f"    ERROR details for {doc_name}: {ver.error_message}"
                                        )
                                    break
                            await asyncio.sleep(1.0)

                    # Build page lookup map: page_id -> (file_name, page_number)
                    page_lookup: dict[UUID, tuple[str, int]] = {}
                    async with tenant_session(ctx) as s:
                        res_pages = await s.execute(
                            text(
                                "SELECT p.id, d.title, p.page_number "
                                "FROM pages p "
                                "JOIN document_versions dv ON p.document_version_id = dv.id "
                                "JOIN documents d ON dv.document_id = d.id "
                                "WHERE p.tenant_id = :tid"
                            ),
                            {"tid": tenant_id},
                        )
                        for row in res_pages:
                            page_lookup[row[0]] = (row[1], row[2])

                    print(f"Built page lookup with {len(page_lookup)} pages.")

                    # Create real PostgresRetriever with real BGE-M3 embeddings
                    embed_provider = create_embedding_provider("desktop-gpu")
                    retriever = PostgresRetriever(embedding_provider=embed_provider)

                    eval_results: list[dict[str, Any]] = []
                    hits_count = 0

                    print("\n" + "=" * 90)
                    print(
                        "EVALUATING VISUAL RECOVERY QUESTIONS (VR-001 TO VR-016) "
                        "VIA HYBRID RETRIEVAL"
                    )
                    print("=" * 90)

                    for q in vr_questions:
                        qid = q["id"]
                        q_text = q["question"]
                        ev = q["evidence"][0]
                        expected_doc_id = ev["document_id"]
                        expected_filename = DOC_ID_TO_FILENAME.get(expected_doc_id, expected_doc_id)
                        expected_page = ev["page"]

                        # Search top 5 candidates
                        search_results = await retriever.search(
                            tenant_ctx=ctx,
                            query=q_text,
                            filters=RetrievalFilters(limit=5),
                        )

                        retrieved_pages: list[tuple[str, int]] = []
                        for r in search_results:
                            if r.page_id:
                                p_uuid = UUID(str(r.page_id))
                                if p_uuid in page_lookup:
                                    retrieved_pages.append(page_lookup[p_uuid])

                        # Check if expected (filename, page) in top 5
                        hit = any(
                            fn == expected_filename and pn == expected_page
                            for fn, pn in retrieved_pages
                        )
                        if hit:
                            hits_count += 1

                        eval_results.append(
                            {
                                "id": qid,
                                "question": q_text,
                                "expected_document": expected_filename,
                                "expected_page": expected_page,
                                "hit": hit,
                                "retrieved_pages": [f"{fn}:p{pn}" for fn, pn in retrieved_pages],
                            }
                        )

                        hit_str = "YES" if hit else "NO"
                        top_pages_str = ", ".join(f"{fn}:p{pn}" for fn, pn in retrieved_pages)
                        target_info = f"{expected_filename} (p{expected_page})"
                        print(
                            f"[{qid}] Hit: {hit_str:<3} | "
                            f"Target: {target_info} | "
                            f"Top-5: {top_pages_str}"
                        )

                    hit_at_5 = round(hits_count / len(vr_questions), 4)

                    print("\n" + "=" * 90)
                    print("VISUAL RECOVERY RETRIEVAL RESULTS TABLE")
                    print("=" * 90)
                    print(
                        "| Question ID | Target Document | Page | In Top-5? | "
                        "Top-5 Retrieved Candidates |"
                    )
                    print("|---|---|---|---|---|")
                    for r in eval_results:
                        hit_badge = "**YES**" if r["hit"] else "NO"
                        top_str = "; ".join(r["retrieved_pages"])
                        doc_col = r["expected_document"]
                        page_col = r["expected_page"]
                        row_line = (
                            f"| {r['id']} | {doc_col} | {page_col} | {hit_badge} | {top_str} |"
                        )
                        print(row_line)
                    print("=" * 90)
                    print(f"Total VR Questions:       {len(vr_questions)}")
                    print(f"Top-5 Hits:               {hits_count}")
                    print(f"Visual Recovery Hit@5:    {hit_at_5 * 100:.2f}%")
                    print("=" * 90)

                    return {
                        "total_questions": len(vr_questions),
                        "hits_count": hits_count,
                        "hit_at_5": hit_at_5,
                        "results": eval_results,
                    }

                finally:
                    worker_task.cancel()
                    await asyncio.sleep(0.5)

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
    asyncio.run(run_visual_recovery_eval())
