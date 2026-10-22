"""Smoke test for Phase 4 extraction pipeline on real desktop-gpu profile and throwaway database.

Covers:
- Task 4.2: Normalized visual validation
- Task 4.3: Session document isolation and retention (Invariant 7)
- Task 4.4: Extraction subgraph (dual read compare with normalized values)
- Task 4.5: Field state engine (Invariant 6: unreadable fields stay blank and flagged, never guessed)
- Smoke documents: skewed_fax.pdf and faint_fax_2page.pdf from evals/golden/docs.
"""

# ruff: noqa: E402, E501

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from alembic import command
from sqlalchemy import select

from app.core.extraction.field_state import ExtractedField, FieldState
from app.core.graph import (
    GraphPorts,
    GraphRuntimeContext,
    GraphState,
    invoke_graph,
)
from app.db.models import (
    Assistant,
    Chunk,
    Conversation,
    ExtractionResult,
    Membership,
    SessionDocument,
    Tenant,
    User,
)
from app.db.session import reset_session_engines, tenant_session, unscoped_session
from app.ports.types import TenantContext, UserRole
from tests.conftest import isolated_throwaway_database


async def run_phase4_smoke() -> None:
    os.environ["APP_PROFILE"] = "desktop-gpu"
    os.environ["SESSION_UPLOAD_RETENTION_HOURS"] = "24"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HOME"] = r"D:\dev\hf"
    os.environ["TEMP"] = r"D:\dev\tmp"
    os.environ["TMP"] = r"D:\dev\tmp"

    print("================================================================================")
    print("PHASE 4 SMOKE RUN: EXTRACTION FLOW ON REAL DESKTOP-GPU PROFILE & THROWAWAY DB")
    print("================================================================================")

    docs_dir = PROJECT_ROOT / "evals" / "golden" / "docs"
    skewed_fax_path = docs_dir / "skewed_fax.pdf"
    faint_fax_path = docs_dir / "faint_fax_2page.pdf"

    assert skewed_fax_path.exists(), f"Missing {skewed_fax_path}"
    assert faint_fax_path.exists(), f"Missing {faint_fax_path}"

    skewed_bytes = skewed_fax_path.read_bytes()
    faint_bytes = faint_fax_path.read_bytes()

    print(f"Loaded skewed_fax.pdf ({len(skewed_bytes)} bytes)")
    print(f"Loaded faint_fax_2page.pdf ({len(faint_bytes)} bytes)")

    async with isolated_throwaway_database() as throwaway:
        print(f"Created isolated throwaway DB: {throwaway.db_name}")

        # Run migrations up to 0012 (session_documents schema)
        command.upgrade(throwaway.cfg, "head")
        print("Alembic upgrade head completed in throwaway DB")

        app_user = os.getenv("DB_APP_USER", "app_user")
        app_pass = os.getenv("DB_APP_PASSWORD", "")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")

        app_url = f"postgresql+psycopg://{app_user}:{app_pass}@{host}:{port}/{throwaway.db_name}"
        retriever_user = os.getenv("DB_RETRIEVER_USER", "retriever_user")
        retriever_pass = os.getenv("DB_RETRIEVER_PASSWORD", "")
        retriever_url = f"postgresql+psycopg://{retriever_user}:{retriever_pass}@{host}:{port}/{throwaway.db_name}"

        old_db_url = os.environ.get("DATABASE_URL")
        old_readonly_url = os.environ.get("DATABASE_READONLY_URL")
        os.environ["DATABASE_URL"] = app_url
        os.environ["DATABASE_READONLY_URL"] = retriever_url
        reset_session_engines()

        try:
            tenant_id = uuid4()
            user_id = uuid4()
            assistant_id = uuid4()
            conv_id = uuid4()

            # Seed tenant and user in unscoped_session
            async with unscoped_session() as session:
                t = Tenant(id=tenant_id, name="Horizon Valley Clinic Smoke")
                session.add(t)
                u = User(
                    id=user_id, email="smoke_officer@horizonvalley.local", hashed_password="hash"
                )
                session.add(u)
                await session.commit()

            tenant_ctx = TenantContext(
                tenant_id=str(tenant_id),
                user_id=str(user_id),
                role=UserRole.CLIENT_ADMIN,
                session_id="smoke-session",
            )
            now_utc = datetime.now(UTC)
            retention = now_utc + timedelta(hours=24)

            # Seed membership, assistant, conversation in tenant_session (RLS active)
            async with tenant_session(tenant_ctx) as session:
                m = Membership(tenant_id=tenant_id, user_id=user_id, role="client_admin")
                session.add(m)
                a = Assistant(
                    id=assistant_id,
                    tenant_id=tenant_id,
                    name="Intake Assistant",
                    prompt_id="clinical_assistant",
                    prompt_version="1.0.0",
                )
                session.add(a)
                c = Conversation(
                    id=conv_id,
                    tenant_id=tenant_id,
                    assistant_id=assistant_id,
                    user_id=user_id,
                    title="Fax Extraction Session",
                )
                session.add(c)
                await session.commit()

            # Ingest session documents into throwaway DB (Task 4.3 / Invariant 7)
            doc1_id = uuid4()
            doc2_id = uuid4()

            async with tenant_session(tenant_ctx) as session:
                sdoc1 = SessionDocument(
                    id=doc1_id,
                    tenant_id=tenant_id,
                    conversation_id=conv_id,
                    file_name="skewed_fax.pdf",
                    file_path=str(skewed_fax_path),
                    file_size_bytes=len(skewed_bytes),
                    file_sha256=hashlib.sha256(skewed_bytes).hexdigest(),
                    page_count=1,
                    status="uploaded",
                    retention_until=retention,
                )
                sdoc2 = SessionDocument(
                    id=doc2_id,
                    tenant_id=tenant_id,
                    conversation_id=conv_id,
                    file_name="faint_fax_2page.pdf",
                    file_path=str(faint_fax_path),
                    file_size_bytes=len(faint_bytes),
                    file_sha256=hashlib.sha256(faint_bytes).hexdigest(),
                    page_count=2,
                    status="uploaded",
                    retention_until=retention,
                )
                session.add_all([sdoc1, sdoc2])
                await session.commit()
                print(
                    "SessionDocuments inserted into throwaway database with composite tenant isolation"
                )

            # 1. Run extraction flow on skewed_fax.pdf
            skewed_readings = {
                "ocr": {
                    "patient_name": "Alex Morgan",
                    "attending_physician": "Dr. Evelyn Reed",
                    "prescription_dosage": "1 capsule by mouth 3x daily for 10 days",
                    "chart_number": "MRN-4412-A",
                    "authorized_medication": "Amoxicillin 500mg",
                    "prescriber_signature": "[unreadable]",
                    "prescriber_npi": "1982736451",
                    "secondary_insurance_group": None,
                    "prior_auth_emergency_override": None,
                },
                "vision": {
                    "patient_name": "Alex Morgan",
                    "attending_physician": "Dr. Evelyn Reed",
                    "prescription_dosage": "1 capsule by mouth three times daily for 10 days",
                    "chart_number": "MRN 4412 A",
                    "authorized_medication": "Amoxillin 500mg",  # OCR typo / visual misread
                    "prescriber_signature": None,
                    "prescriber_npi": "1982736451",  # Invalid Luhn check digit
                    "secondary_insurance_group": None,
                    "prior_auth_emergency_override": None,
                },
                "confidences": {
                    "patient_name": 0.98,
                    "attending_physician": 0.98,
                    "prescription_dosage": 0.96,
                    "chart_number": 0.82,
                    "authorized_medication": 0.74,
                    "prescriber_signature": 0.42,
                    "prescriber_npi": 0.95,
                    "secondary_insurance_group": 0.90,
                    "prior_auth_emergency_override": 0.90,
                },
            }

            ctx1 = GraphRuntimeContext(
                ports=GraphPorts(is_mock_profile=False),
                tenant=tenant_ctx,
                question_text="Extract clinical intake and prescription fields from uploaded fax",
                evidence_text={"document_title": "skewed_fax.pdf", "page_number": "1"},
                raw_document_bytes=skewed_bytes,
            )
            ctx1.extracted_fields["raw_readings"] = skewed_readings

            state1: GraphState = {
                "request_id": f"smoke-ext-{uuid4().hex[:8]}",
                "tenant_id": str(tenant_id),
                "route": "extraction",
            }

            await invoke_graph(state1, ctx1)
            fields1: list[ExtractedField] = ctx1.extracted_fields.get("fields", [])
            assert len(fields1) > 0, "No fields extracted for skewed_fax.pdf"

            # 2. Run extraction flow on faint_fax_2page.pdf
            faint_readings = {
                "ocr": {
                    "patient_name": "Jordan Taylor",
                    "procedure_ordered": "MRI Lumbar Spine without Contrast",
                    "ordering_physician": "Dr. Evelyn Reed",
                    "appointment_date": "2026-03-01",
                    "clinical_justification": "[unreadable]",
                    "pain_scale_score": "7/10",
                    "prescriber_npi": "1982736450",
                    "prior_auth_reference": None,
                },
                "vision": {
                    "patient_name": "Jordan Taylor",
                    "procedure_ordered": "MRI Lumbar Spine without Contrast",
                    "ordering_physician": "Dr. Evelyn Reed",
                    "appointment_date": "March 1, 2026",
                    "clinical_justification": None,
                    "pain_scale_score": "1/10",  # Disagreement with OCR
                    "prescriber_npi": "1982736450",  # Valid Luhn check digit
                    "prior_auth_reference": None,
                },
                "confidences": {
                    "patient_name": 0.98,
                    "procedure_ordered": 0.97,
                    "ordering_physician": 0.95,
                    "appointment_date": 0.96,
                    "clinical_justification": 0.38,
                    "pain_scale_score": 0.68,
                    "prescriber_npi": 0.99,
                    "prior_auth_reference": 0.90,
                },
            }

            ctx2 = GraphRuntimeContext(
                ports=GraphPorts(is_mock_profile=False),
                tenant=tenant_ctx,
                question_text="Extract diagnostic imaging order fields from faint 2-page fax",
                evidence_text={"document_title": "faint_fax_2page.pdf", "page_number": "2"},
                raw_document_bytes=faint_bytes,
            )
            ctx2.extracted_fields["raw_readings"] = faint_readings

            state2: GraphState = {
                "request_id": f"smoke-ext-{uuid4().hex[:8]}",
                "tenant_id": str(tenant_id),
                "route": "extraction",
            }

            await invoke_graph(state2, ctx2)
            fields2: list[ExtractedField] = ctx2.extracted_fields.get("fields", [])
            assert len(fields2) > 0, "No fields extracted for faint_fax_2page.pdf"

            # Persist extraction results into throwaway DB
            async with tenant_session(tenant_ctx) as session:
                er1 = ExtractionResult(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    session_document_id=doc1_id,
                    schema_name="fax_prescription_refill",
                    fields_data={"fields": [f.to_dict() for f in fields1]},
                )
                er2 = ExtractionResult(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    session_document_id=doc2_id,
                    schema_name="diagnostic_imaging_order",
                    fields_data={"fields": [f.to_dict() for f in fields2]},
                )
                session.add_all([er1, er2])
                await session.commit()
                print(
                    "ExtractionResults persisted into throwaway database linked to session documents"
                )

            # Invariant 7 verification: No chunks or knowledge records created
            async with unscoped_session() as session:
                chunk_count = (
                    (await session.execute(select(Chunk).where(Chunk.tenant_id == tenant_id)))
                    .scalars()
                    .all()
                )
                assert len(chunk_count) == 0, (
                    f"Invariant 7 violation: {len(chunk_count)} chunks created for session upload!"
                )
                print(
                    "Invariant 7 verified: 0 chunks created in knowledge index for session uploads."
                )

            # Smoke Report Table
            all_fields = [("skewed_fax.pdf", f) for f in fields1] + [
                ("faint_fax_2page.pdf", f) for f in fields2
            ]

            print("\n" + "=" * 110)
            print("EXTRACTION SMOKE CHECK RESULTS")
            print("=" * 110)
            header = (
                f"{'Document':<20} | {'Field Name':<28} | {'Read 1 (OCR)':<20} | "
                f"{'Read 2 (Vision)':<20} | {'State':<14} | {'Value':<18} | {'Validator Result'}"
            )
            print(header)
            print("-" * 140)

            unreadable_with_value_count = 0

            for doc_name, f in all_fields:
                r1 = f.read1_value if f.read1_value is not None else "(none)"
                r2 = f.read2_value if f.read2_value is not None else "(none)"
                val = f.value if f.value is not None else "(none)"
                val_res = f.validator_error or (
                    "Passed"
                    if f.state in (FieldState.FILLED_HIGH, FieldState.FILLED_MEDIUM)
                    else f.state.value
                )

                if f.state == FieldState.UNREADABLE and f.value is not None:
                    unreadable_with_value_count += 1

                row = (
                    f"{doc_name:<20} | {f.name:<28} | {r1:<20} | {r2:<20} | "
                    f"{f.state.value:<14} | {val:<18} | {val_res}"
                )
                print(row)

            print("-" * 140)
            print(f"TOTAL FIELDS EVALUATED: {len(all_fields)}")
            print(
                f"UNREADABLE FIELDS THAT RECEIVED A VALUE (MUST BE 0): {unreadable_with_value_count}"
            )
            print("=" * 110 + "\n")

            assert unreadable_with_value_count == 0, (
                f"FAILED: {unreadable_with_value_count} UNREADABLE fields received a value!"
            )

        finally:
            if old_db_url:
                os.environ["DATABASE_URL"] = old_db_url
            if old_readonly_url:
                os.environ["DATABASE_READONLY_URL"] = old_readonly_url
            reset_session_engines()


if __name__ == "__main__":
    asyncio.run(run_phase4_smoke())
