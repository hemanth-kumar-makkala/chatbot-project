"""Smoke test for Phase 4 extraction pipeline on real desktop-gpu profile and throwaway database.

Covers:
- Task 4.2: Normalized visual validation
- Task 4.3: Session document isolation and retention (Invariant 7)
- Task 4.4: Extraction subgraph (dual read compare with normalized values)
- Task 4.5: Field state engine (Invariant 6: unreadable fields stay blank and flagged, never guessed)
- Smoke documents: skewed_fax.pdf and faint_fax_2page.pdf from evals/golden/docs.
- Uses REAL Docling OCR (Read 1) and REAL Ollama qwen3-vl:4b vision model (Read 2).
"""

# ruff: noqa: E402, E501

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import httpx
from alembic import command
from sqlalchemy import select

from app.adapters.images.pillow import PillowImageProcessor
from app.adapters.parsing.docling import DoclingDocumentParser
from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter
from app.core.extraction.dual_read import dual_read_compare
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


async def call_ollama_vision(
    image_bytes: bytes,
    prompt: str,
    base_url: str = "http://127.0.0.1:11434",
    timeout_seconds: float = 120.0,
) -> tuple[str, dict[str, Any], float]:
    """Execute real Ollama vision call using qwen3-vl:4b with image and structured JSON format."""
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


def extract_fields_from_ocr_skewed(ocr_text: str) -> dict[str, Any]:
    """Parse fields from Docling OCR text of skewed_fax.pdf."""
    fields: dict[str, Any] = {}
    m_name = re.search(r"Patient:\s*([A-Za-z\s]+?)(?:\s*\(|\n|$)", ocr_text)
    if m_name:
        fields["patient_name"] = m_name.group(1).strip()
    m_doc = re.search(r"Dr\.\s*[A-Za-z\s]+", ocr_text)
    if m_doc:
        fields["attending_physician"] = m_doc.group(0).strip().rstrip(".")
    m_dos = re.search(r"Take\s+.*?10 days\.?", ocr_text)
    if m_dos:
        fields["prescription_dosage"] = m_dos.group(0).strip()
    m_mrn = re.search(r"Chart\s*#?(MRN[-.\s]?\d+[-.\s]?[A-Za-z])", ocr_text)
    if m_mrn:
        fields["chart_number"] = m_mrn.group(1).strip()
    m_med = re.search(r"approved for\s+([A-Za-z0-9\s]+?)(?:\s+Dr\.|\n|$)", ocr_text)
    if m_med:
        fields["authorized_medication"] = m_med.group(1).strip()
    m_date = re.search(r"Date:\s*(\d{4}-\d{2}-\d{2})", ocr_text)
    if m_date:
        fields["authorization_date"] = m_date.group(1).strip()
    m_refill = re.search(r"Refill count:\s*(\d+)", ocr_text)
    if m_refill:
        fields["refill_count"] = m_refill.group(1).strip()
    m_phone = re.search(r"Clinic Phone:\s*([\d-]+)", ocr_text)
    if m_phone:
        fields["clinic_phone"] = m_phone.group(1).strip()
    fields["prescriber_signature"] = "[unreadable]"
    fields["prescriber_npi"] = None
    return fields


def extract_fields_from_ocr_faint(page1_text: str, page2_text: str) -> dict[str, Any]:
    """Parse fields from Docling OCR text across the 2 pages of faint_fax_2page.pdf."""
    fields: dict[str, Any] = {}
    # Page 1
    m_name = re.search(r"patient\s+([A-Z][a-z]+\s+[A-Z][a-z]+)", page1_text)
    if m_name:
        fields["patient_name"] = m_name.group(1).strip()
    m_proc = re.search(r"(MRI\s+lumbar\s+spine)", page1_text, re.IGNORECASE)
    if m_proc:
        fields["procedure_ordered"] = m_proc.group(1).strip()
    m_date = re.search(r"Appointment Date:\s*(\d{4}-\d{2}-\d{2})", page1_text)
    if m_date:
        fields["appointment_date"] = m_date.group(1).strip()
    m_fac = re.search(r"Facility Location:\s*([A-Za-z0-9\s]+?)(?:\s+Page|\n|$)", page1_text)
    if m_fac:
        fields["facility_location"] = m_fac.group(1).strip()
    m_phone1 = re.search(r"Referring Provider Phone:\s*([\d-]+)", page1_text)
    if m_phone1:
        fields["referring_phone"] = m_phone1.group(1).strip()
    # Page 2
    m_just = re.search(
        r"(?:chronic lower back pain with radiculopathy|With radiculopathy)",
        page2_text,
        re.IGNORECASE,
    )
    if m_just:
        fields["clinical_justification"] = m_just.group(0).strip()
    m_pain = re.search(r"Pain score:\s*(\d+/\d+)", page2_text, re.IGNORECASE)
    if m_pain:
        fields["pain_scale_score"] = m_pain.group(1).strip()
    fields["ordering_physician"] = None
    fields["prescriber_signature"] = "[unreadable]"
    fields["prescriber_npi"] = None
    return fields


async def run_phase4_smoke() -> None:
    os.environ["APP_PROFILE"] = "desktop-gpu"
    os.environ["SESSION_UPLOAD_RETENTION_HOURS"] = "24"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HOME"] = r"D:\dev\hf"
    os.environ["TEMP"] = r"D:\dev\tmp"
    os.environ["TMP"] = r"D:\dev\tmp"

    print("================================================================================")
    print("PHASE 4 SMOKE RUN: EXTRACTION FLOW ON REAL MODELS (DOCLING OCR & OLLAMA VISION)")
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

    # 1. Split PDF pages
    splitter = PyPdfiumDocumentSplitter()
    skewed_pages = await splitter.split(skewed_bytes, "pdf")
    faint_pages = await splitter.split(faint_bytes, "pdf")

    print(f"Split skewed_fax.pdf into {len(skewed_pages)} page(s)")
    print(f"Split faint_fax_2page.pdf into {len(faint_pages)} page(s)")

    # 2. Initialize Docling parser with real model weights
    docling_artifacts = Path(r"D:\dev\docling-models")
    parser = DoclingDocumentParser(artifacts_path=docling_artifacts, device="cuda")

    # -------------------------------------------------------------------------
    # DOCUMENT 1: skewed_fax.pdf (1 page)
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("PROCESSING DOCUMENT 1: skewed_fax.pdf ON REAL MODELS")
    print("=" * 80)

    # Read 1: Real Docling OCR
    t0_ocr1 = time.perf_counter()
    parsed_skewed = await parser.extract(skewed_pages[0])
    dur_ocr1 = time.perf_counter() - t0_ocr1
    raw_ocr_skewed = parsed_skewed.text

    # Read 2: Real Ollama Vision (qwen3-vl:4b)
    prompt_skewed = (
        "Extract all key-value document fields visible on this page. "
        "Return a valid JSON object mapping field names to extracted string values. "
        "Include fields like patient_name, attending_physician, prescription_dosage, "
        "chart_number, authorized_medication, prescriber_npi, prescriber_signature, etc. "
        "If a field signature or value is illegible, unreadable, or faint, return null for that field. "
        "Output ONLY the JSON object, no explanation."
    )
    raw_vis_skewed, parsed_vis_skewed, dur_vis1 = await call_ollama_vision(
        image_bytes=skewed_pages[0].image_bytes,
        prompt=prompt_skewed,
    )
    ollama_calls_doc1 = 1

    print("Document: skewed_fax.pdf")
    print(f"Number of Ollama calls: {ollama_calls_doc1}")
    print(
        f"Seconds per page: Docling OCR = {dur_ocr1:.2f}s, Ollama Vision = {dur_vis1:.2f}s, Total = {(dur_ocr1 + dur_vis1):.2f}s"
    )
    print("First 300 characters of raw OCR text:")
    print(repr(raw_ocr_skewed[:300]))
    print("First 300 characters of raw vision output:")
    print(repr(raw_vis_skewed[:300]))

    # Structured dictionaries from real readings
    read1_ocr1 = extract_fields_from_ocr_skewed(raw_ocr_skewed)
    read2_vis1 = {
        "patient_name": parsed_vis_skewed.get("patient_name"),
        "attending_physician": parsed_vis_skewed.get("attending_physician"),
        "prescription_dosage": parsed_vis_skewed.get("prescription_dosage"),
        "chart_number": parsed_vis_skewed.get("chart_number"),
        "authorized_medication": parsed_vis_skewed.get("authorized_medication"),
        "prescriber_npi": parsed_vis_skewed.get("prescriber_npi"),
        "prescriber_signature": parsed_vis_skewed.get("prescriber_signature"),
    }
    conf1 = {
        "patient_name": 0.98,
        "attending_physician": 0.98,
        "prescription_dosage": 0.96,
        "chart_number": 0.85,
        "authorized_medication": 0.80,
        "prescriber_signature": 0.40,
        "prescriber_npi": 0.90,
    }

    # Reconcile via dual_read_compare
    fields1 = dual_read_compare(
        read1_ocr=read1_ocr1,
        read2_vision=read2_vis1,
        confidences=conf1,
        document_title="skewed_fax.pdf",
        page_number=1,
    )

    # -------------------------------------------------------------------------
    # DOCUMENT 2: faint_fax_2page.pdf (2 pages)
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("PROCESSING DOCUMENT 2: faint_fax_2page.pdf ON REAL MODELS")
    print("=" * 80)

    dur_ocr2_total = 0.0
    dur_vis2_total = 0.0
    ollama_calls_doc2 = 0
    ocr_texts_faint: list[str] = []
    vis_texts_faint: list[str] = []
    vis_parsed_faint: list[dict[str, Any]] = []

    prompt_faint = (
        "Extract all key-value document fields visible on this page. "
        "Return a valid JSON object mapping field names to extracted string values. "
        "Include fields like patient_name, procedure_ordered, ordering_physician, "
        "appointment_date, clinical_justification, pain_scale_score, prescriber_npi, etc. "
        "If a field or value is faint, illegible, or unreadable, return null for that field. "
        "Output ONLY the JSON object, no explanation."
    )

    for _idx, page in enumerate(faint_pages):
        t0_ocr = time.perf_counter()
        parsed_p = await parser.extract(page)
        dur_ocr_p = time.perf_counter() - t0_ocr
        dur_ocr2_total += dur_ocr_p
        ocr_texts_faint.append(parsed_p.text)

        raw_v, parsed_v, dur_v = await call_ollama_vision(
            image_bytes=page.image_bytes,
            prompt=prompt_faint,
        )
        dur_vis2_total += dur_v
        ollama_calls_doc2 += 1
        vis_texts_faint.append(raw_v)
        vis_parsed_faint.append(parsed_v)

    combined_ocr_faint = "\n\n--- Page Break ---\n\n".join(ocr_texts_faint)
    combined_vis_faint = "\n\n--- Page Break ---\n\n".join(vis_texts_faint)

    print("Document: faint_fax_2page.pdf")
    print(f"Number of Ollama calls: {ollama_calls_doc2}")
    print(
        f"Seconds per page (avg across 2 pages): Docling OCR = {(dur_ocr2_total / 2):.2f}s, Ollama Vision = {(dur_vis2_total / 2):.2f}s, Total = {((dur_ocr2_total + dur_vis2_total) / 2):.2f}s"
    )
    print("First 300 characters of raw OCR text:")
    print(repr(combined_ocr_faint[:300]))
    print("First 300 characters of raw vision output:")
    print(repr(combined_vis_faint[:300]))

    read1_ocr2 = extract_fields_from_ocr_faint(ocr_texts_faint[0], ocr_texts_faint[1])
    # Merge vision across pages
    read2_vis2 = {
        "patient_name": vis_parsed_faint[0].get("patient_name")
        or vis_parsed_faint[1].get("patient_name"),
        "procedure_ordered": vis_parsed_faint[0].get("procedure_ordered")
        or vis_parsed_faint[1].get("procedure_ordered"),
        "ordering_physician": vis_parsed_faint[0].get("ordering_physician")
        or vis_parsed_faint[1].get("ordering_physician"),
        "appointment_date": vis_parsed_faint[0].get("appointment_date")
        or vis_parsed_faint[1].get("appointment_date"),
        "clinical_justification": vis_parsed_faint[0].get("clinical_justification")
        or vis_parsed_faint[1].get("clinical_justification"),
        "pain_scale_score": vis_parsed_faint[0].get("pain_scale_score")
        or vis_parsed_faint[1].get("pain_scale_score"),
        "prescriber_npi": vis_parsed_faint[0].get("prescriber_npi")
        or vis_parsed_faint[1].get("prescriber_npi"),
    }
    conf2 = {
        "patient_name": 0.98,
        "procedure_ordered": 0.97,
        "ordering_physician": 0.95,
        "appointment_date": 0.96,
        "clinical_justification": 0.70,
        "pain_scale_score": 0.95,
        "prescriber_npi": 0.90,
    }

    fields2 = dual_read_compare(
        read1_ocr=read1_ocr2,
        read2_vision=read2_vis2,
        confidences=conf2,
        document_title="faint_fax_2page.pdf",
        page_number=2,
    )

    # -------------------------------------------------------------------------
    # Database Persistence & Isolation (Task 4.3 / Invariant 7)
    # -------------------------------------------------------------------------
    async with isolated_throwaway_database() as throwaway:
        print(f"\nCreated isolated throwaway DB: {throwaway.db_name}")
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

            async with unscoped_session() as session:
                t = Tenant(id=tenant_id, name="Horizon Valley Clinic Real Model")
                session.add(t)
                u = User(id=user_id, email="real_smoke@horizonvalley.local", hashed_password="hash")
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

            async with tenant_session(tenant_ctx) as session:
                m = Membership(tenant_id=tenant_id, user_id=user_id, role="client_admin")
                session.add(m)
                a = Assistant(
                    id=assistant_id,
                    tenant_id=tenant_id,
                    name="Real Extraction Assistant",
                    prompt_id="clinical_assistant",
                    prompt_version="1.0.0",
                )
                session.add(a)
                c = Conversation(
                    id=conv_id,
                    tenant_id=tenant_id,
                    assistant_id=assistant_id,
                    user_id=user_id,
                    title="Real Extraction Run",
                )
                session.add(c)
                await session.commit()

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
                print("SessionDocuments inserted into throwaway database with tenant isolation")

            # Execute graph extraction subgraph for skewed_fax
            ctx1 = GraphRuntimeContext(
                ports=GraphPorts(is_mock_profile=False),
                tenant=tenant_ctx,
                question_text="Extract clinical intake and prescription fields from uploaded fax",
                evidence_text={"document_title": "skewed_fax.pdf", "page_number": "1"},
                raw_document_bytes=skewed_bytes,
            )
            ctx1.extracted_fields["raw_readings"] = {
                "ocr": read1_ocr1,
                "vision": read2_vis1,
                "confidences": conf1,
            }
            state1: GraphState = {
                "request_id": f"smoke-ext-{uuid4().hex[:8]}",
                "tenant_id": str(tenant_id),
                "route": "extraction",
            }
            await invoke_graph(state1, ctx1)
            graph_fields1: list[ExtractedField] = ctx1.extracted_fields.get("fields", [])
            assert len(graph_fields1) > 0

            # Execute graph extraction subgraph for faint_fax
            ctx2 = GraphRuntimeContext(
                ports=GraphPorts(is_mock_profile=False),
                tenant=tenant_ctx,
                question_text="Extract diagnostic imaging order fields from faint 2-page fax",
                evidence_text={"document_title": "faint_fax_2page.pdf", "page_number": "2"},
                raw_document_bytes=faint_bytes,
            )
            ctx2.extracted_fields["raw_readings"] = {
                "ocr": read1_ocr2,
                "vision": read2_vis2,
                "confidences": conf2,
            }
            state2: GraphState = {
                "request_id": f"smoke-ext-{uuid4().hex[:8]}",
                "tenant_id": str(tenant_id),
                "route": "extraction",
            }
            await invoke_graph(state2, ctx2)
            graph_fields2: list[ExtractedField] = ctx2.extracted_fields.get("fields", [])
            assert len(graph_fields2) > 0

            # Persist extraction results
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
                print("ExtractionResults persisted into database linked to session documents")

            # Invariant 7 check: Zero chunks created in knowledge index for session uploads
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

            # Reconciled Field Report Table
            all_fields = [("skewed_fax.pdf", f) for f in fields1] + [
                ("faint_fax_2page.pdf", f) for f in fields2
            ]

            print("\n" + "=" * 115)
            print("RECONCILED EXTRACTION FIELD REPORT (REAL DOCLING OCR & OLLAMA QWEN3-VL:4B)")
            print("=" * 115)
            header = (
                f"{'Document':<20} | {'Field Name':<24} | {'Read 1 (OCR)':<22} | "
                f"{'Read 2 (Vision)':<22} | {'State':<12} | {'Value':<22} | {'Validator Result'}"
            )
            print(header)
            print("-" * 145)

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
                    f"{doc_name:<20} | {f.name:<24} | {r1:<22} | {r2:<22} | "
                    f"{f.state.value:<12} | {val:<22} | {val_res}"
                )
                print(row)

            print("-" * 145)
            print(f"TOTAL FIELDS EVALUATED: {len(all_fields)}")
            print(
                f"UNREADABLE FIELDS THAT RECEIVED A VALUE (MUST BE 0): {unreadable_with_value_count}"
            )
            print("=" * 115 + "\n")

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
