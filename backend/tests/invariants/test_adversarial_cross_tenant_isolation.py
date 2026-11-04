"""Adversarial Cross-Tenant Isolation Suite (Phase 5 Task 5.3, Invariants 1, 2, 13, 15).

Proves zero cross-tenant leakage across two synthetic tenants (Tenant A: Horizon Valley Health,
Tenant B: Pinecrest Medical Group) seeded with:
- Identical document filenames: patient_intake_form.pdf, front_desk_policy.pdf, accepted_plans.csv
- Identical patient name: Alex Morgan
- Different tenant-specific facts:
  * Tenant A: fax 555-0131, deductible $250.00, MRN MRN-TENANT-A-101, insurer Apex Health Insurance
  * Tenant B: fax 555-0132, deductible $1500.00, MRN MRN-TENANT-B-909,
    insurer Beacon Mutual Healthcare
- Identical file bytes uploaded in both tenants (same_policy.pdf) proving deduplication never
  crosses tenants (Invariant 15).

Runs simultaneous requests from both tenants via asyncio.gather and verifies 0 leaks across all
9 channels:
1. answers
2. citations
3. source_file_route
4. page_image_routes (knowledge review image, thumbnail, and session document page image)
5. conversations (list, get messages, delete, and cross-tenant conversation_id on /v1/questions)
6. session_documents_and_extraction_results
7. inventory_and_title_search
8. feedback (message and audit event isolation by message_id)
9. dedup (identical SHA-256 uploaded in both tenants)
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy import select

from app.adapters.embedding.mock import MockEmbeddingProvider
from app.adapters.models.mock import MockModelProvider
from app.adapters.retrieval.postgres_retriever import PostgresRetriever
from app.api.app import create_app
from app.api.config import ApiSettings
from app.api.routes import questions as questions_route
from app.core.graph import GraphPorts
from app.db.models import (
    Assistant,
    AuditEvent,
    Chunk,
    Document,
    DocumentVersion,
    ExtractionResult,
    Message,
    Page,
    SessionDocument,
    TableData,
    Tenant,
)
from app.db.session import tenant_session, unscoped_session
from app.ports.types import (
    ChatMessage,
    ModelResponse,
    ModelRole,
    StorageKey,
    TenantContext,
    UserRole,
)
from app.wiring import create_storage_backend
from tests.integration.test_api_knowledge_step2 import (
    _create_user_with_role,
    _login,
    throwaway_step2_environment,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_DIR = REPO_ROOT / "evals" / "golden"


def _load_golden_helpers() -> Any:
    spec = importlib.util.spec_from_file_location("golden_generate", GOLDEN_DIR / "generate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _EvidenceGroundedEchoModel(MockModelProvider):
    """Deterministic model provider that synthesizes answers strictly from retrieved evidence."""

    async def generate(
        self,
        role: ModelRole,
        messages: list[ChatMessage],
        schema: type[BaseModel] | None = None,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        _ = (max_tokens, schema)
        if role == ModelRole.ROUTER:
            return ModelResponse(
                content='{"route":"knowledge","answer_mode":"short","language":"en"}',
                model_id="echo-grounded",
                model_version="1.0",
                structured_output={
                    "route": "knowledge",
                    "answer_mode": "short",
                    "language": "en",
                },
            )
        if role == ModelRole.JUDGE:
            return ModelResponse(
                content='{"has_conflict":false}',
                model_id="echo-grounded",
                model_version="1.0",
                structured_output={"has_conflict": False},
            )
        prompt_text = messages[0].content if messages else ""
        # Extract first evidence ID and body text from formatted prompt evidence ([<uuid>]: <text>)
        import re

        m_chunk = re.search(
            r"\[([0-9a-fA-F-]{36})\]:\s*(.+?)(?=\n\n\[[0-9a-fA-F-]{36}\]:|\n\nQuestion:|\Z)",
            prompt_text,
            flags=re.DOTALL,
        )
        if m_chunk:
            cid = m_chunk.group(1).strip()
            block_lines = [ln.strip() for ln in m_chunk.group(2).strip().splitlines() if ln.strip()]
            line = block_lines[-1] if block_lines else m_chunk.group(2).strip()
            return ModelResponse(
                content=line,
                model_id="echo-grounded",
                model_version="1.0",
                structured_output={
                    "mode": "short",
                    "answer": line,
                    "evidence_ids": [cid],
                    "quoted_span": line,
                    "evidence_type": "text",
                },
            )
        return ModelResponse(
            content="I couldn't confirm that from the documents.",
            model_id="echo-grounded",
            model_version="1.0",
        )


@pytest.mark.asyncio
async def test_adversarial_cross_tenant_suite_zero_leaks_across_all_channels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simultaneously probe all 9 channels across Tenant A and Tenant B and assert 0 leaks."""
    import tempfile

    _ = tmp_path
    with tempfile.TemporaryDirectory(prefix="t53_") as short_tmp:
        storage_dir = Path(short_tmp)
        monkeypatch.setenv("LOCAL_STORAGE_PATH", str(storage_dir))

        await _run_adversarial_cross_tenant_suite(storage_dir, monkeypatch)


async def _run_adversarial_cross_tenant_suite(
    storage_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_settings = ApiSettings(
        cors_origins=["http://localhost:3000"],
        cookie_secure=False,
        cookie_samesite="lax",
        idle_timeout_minutes=30,
        absolute_timeout_hours=24,
        allow_bearer_auth=False,
        max_upload_bytes=5 * 1024 * 1024,
        profile="mock",
        storage_dir=str(storage_dir),
        session_upload_retention_hours=24,
    )
    app = create_app(settings=api_settings)
    storage = create_storage_backend(profile="mock", base_dir=str(storage_dir))
    helpers = _load_golden_helpers()

    leak_counts: dict[str, int] = {
        "answers": 0,
        "citations": 0,
        "source_file_route": 0,
        "page_image_routes": 0,
        "conversations": 0,
        "session_documents_and_extraction_results": 0,
        "inventory_and_title_search": 0,
        "feedback": 0,
        "dedup": 0,
    }

    async with throwaway_step2_environment() as env_info:
        monkeypatch.setenv("DATABASE_READONLY_URL", env_info["url"])

        tenant_a = uuid4()
        tenant_b = uuid4()

        async with unscoped_session() as session:
            session.add_all(
                [
                    Tenant(id=tenant_a, name="Horizon Valley Health (Tenant A)"),
                    Tenant(id=tenant_b, name="Pinecrest Medical Group (Tenant B)"),
                ]
            )
            await session.flush()

        admin_a, email_a, pass_a = await _create_user_with_role(tenant_a, "client_admin")
        admin_b, email_b, pass_b = await _create_user_with_role(tenant_b, "client_admin")

        ctx_a = TenantContext(
            tenant_id=str(tenant_a),
            user_id=str(admin_a.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="sess-a",
        )
        ctx_b = TenantContext(
            tenant_id=str(tenant_b),
            user_id=str(admin_b.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="sess-b",
        )

        asst_a = uuid4()
        asst_b = uuid4()

        # Seed Tenant A and Tenant B with identical filenames & patient names, different values
        doc_intake_a, ver_intake_a, page_intake_a, chunk_intake_a = (
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        )
        doc_policy_a, ver_policy_a, page_policy_a, chunk_policy_a = (
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        )
        doc_plans_a, ver_plans_a, tbl_row_a = uuid4(), uuid4(), uuid4()

        doc_intake_b, ver_intake_b, page_intake_b, chunk_intake_b = (
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        )
        doc_policy_b, ver_policy_b, page_policy_b, chunk_policy_b = (
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        )
        doc_plans_b, ver_plans_b, tbl_row_b = uuid4(), uuid4(), uuid4()

        embedder = MockEmbeddingProvider()
        emb_vec = (await embedder.embed(["Alex Morgan patient intake fax deductible"]))[0]

        text_intake_a = (
            "Patient Name: Alex Morgan. Chart ID: MRN-TENANT-A-101. "
            "Insurance Carrier: Apex Health Insurance. Emergency Contact Fax: 555-0131. "
            "Annual Deductible: $250.00."
        )
        text_intake_b = (
            "Patient Name: Alex Morgan. Chart ID: MRN-TENANT-B-909. "
            "Insurance Carrier: Beacon Mutual Healthcare. Emergency Contact Fax: 555-0132. "
            "Annual Deductible: $1500.00."
        )

        async with tenant_session(ctx_a) as sess_a:
            sess_a.add(
                Assistant(
                    id=asst_a,
                    tenant_id=tenant_a,
                    name="Horizon Valley Assistant",
                    prompt_id="default",
                    prompt_version="1.0",
                )
            )
            sess_a.add_all(
                [
                    Document(
                        id=doc_intake_a,
                        tenant_id=tenant_a,
                        title="patient_intake_form.pdf",
                        is_retired=False,
                    ),
                    Document(
                        id=doc_policy_a,
                        tenant_id=tenant_a,
                        title="front_desk_policy.pdf",
                        is_retired=False,
                    ),
                    Document(
                        id=doc_plans_a,
                        tenant_id=tenant_a,
                        title="accepted_plans.csv",
                        is_retired=False,
                    ),
                ]
            )
            await sess_a.flush()
            sess_a.add_all(
                [
                    DocumentVersion(
                        id=ver_intake_a,
                        tenant_id=tenant_a,
                        document_id=doc_intake_a,
                        sha256="sha-intake-a",
                        uploaded_by=admin_a.id,
                        status="ready",
                        file_extension="pdf",
                    ),
                    DocumentVersion(
                        id=ver_policy_a,
                        tenant_id=tenant_a,
                        document_id=doc_policy_a,
                        sha256="sha-policy-a",
                        uploaded_by=admin_a.id,
                        status="ready",
                        file_extension="pdf",
                    ),
                    DocumentVersion(
                        id=ver_plans_a,
                        tenant_id=tenant_a,
                        document_id=doc_plans_a,
                        sha256="sha-plans-a",
                        uploaded_by=admin_a.id,
                        status="ready",
                        file_extension="csv",
                    ),
                ]
            )
            await sess_a.flush()

            # Activate all 3 versions for Tenant A
            for doc_obj_id, ver_obj_id in (
                (doc_intake_a, ver_intake_a),
                (doc_policy_a, ver_policy_a),
                (doc_plans_a, ver_plans_a),
            ):
                d_row = (
                    await sess_a.execute(select(Document).where(Document.id == doc_obj_id))
                ).scalar_one()
                d_row.active_version_id = ver_obj_id

            sess_a.add_all(
                [
                    Page(
                        id=page_intake_a,
                        tenant_id=tenant_a,
                        document_version_id=ver_intake_a,
                        page_number=1,
                        page_sha256="psha-intake-a",
                        page_type="digital_text",
                        image_key=f"{tenant_a}/pages/{ver_intake_a}/1.png",
                        needs_review=False,
                        is_excluded=False,
                    ),
                    Page(
                        id=page_policy_a,
                        tenant_id=tenant_a,
                        document_version_id=ver_policy_a,
                        page_number=1,
                        page_sha256="psha-policy-a",
                        page_type="digital_text",
                        image_key=f"{tenant_a}/pages/{ver_policy_a}/1.png",
                        needs_review=False,
                        is_excluded=False,
                    ),
                ]
            )
            await sess_a.flush()

            sess_a.add_all(
                [
                    Chunk(
                        id=chunk_intake_a,
                        tenant_id=tenant_a,
                        document_version_id=ver_intake_a,
                        page_id=page_intake_a,
                        text=text_intake_a,
                        context_prefix=(
                            "Document: patient_intake_form.pdf - Page 1 - "
                            "Section: Tenant A Exclusive Oncology Addendum"
                        ),
                        language="en",
                        ts_config="english",
                        evidence_type="text",
                        is_active=True,
                        embedding=emb_vec,
                    ),
                    Chunk(
                        id=chunk_policy_a,
                        tenant_id=tenant_a,
                        document_version_id=ver_policy_a,
                        page_id=page_policy_a,
                        text="Front desk intake fax is 555-0131 for Horizon Valley Health.",
                        context_prefix=(
                            "Document: front_desk_policy.pdf - Page 1 - "
                            "Section: Horizon Valley Check-In Rules"
                        ),
                        language="en",
                        ts_config="english",
                        evidence_type="text",
                        is_active=True,
                        embedding=emb_vec,
                    ),
                    TableData(
                        id=tbl_row_a,
                        tenant_id=tenant_a,
                        document_version_id=ver_plans_a,
                        sheet_name="accepted_plans",
                        row_index=1,
                        row_data={
                            "Payer Name": "Apex Health Insurance",
                            "Deductible": "$250.00",
                            "Patient": "Alex Morgan",
                        },
                    ),
                ]
            )
            doc_excl_a, ver_excl_a = uuid4(), uuid4()
            sess_a.add(
                Document(
                    id=doc_excl_a,
                    tenant_id=tenant_a,
                    title="horizon_valley_oncology_addendum.pdf",
                    is_retired=False,
                )
            )
            await sess_a.flush()
            sess_a.add(
                DocumentVersion(
                    id=ver_excl_a,
                    tenant_id=tenant_a,
                    document_id=doc_excl_a,
                    sha256="sha-excl-a",
                    uploaded_by=admin_a.id,
                    status="ready",
                    file_extension="pdf",
                )
            )
            await sess_a.flush()
            d_excl_a = (
                await sess_a.execute(select(Document).where(Document.id == doc_excl_a))
            ).scalar_one()
            d_excl_a.active_version_id = ver_excl_a

        async with tenant_session(ctx_b) as sess_b:
            sess_b.add(
                Assistant(
                    id=asst_b,
                    tenant_id=tenant_b,
                    name="Pinecrest Assistant",
                    prompt_id="default",
                    prompt_version="1.0",
                )
            )
            sess_b.add_all(
                [
                    Document(
                        id=doc_intake_b,
                        tenant_id=tenant_b,
                        title="patient_intake_form.pdf",
                        is_retired=False,
                    ),
                    Document(
                        id=doc_policy_b,
                        tenant_id=tenant_b,
                        title="front_desk_policy.pdf",
                        is_retired=False,
                    ),
                    Document(
                        id=doc_plans_b,
                        tenant_id=tenant_b,
                        title="accepted_plans.csv",
                        is_retired=False,
                    ),
                ]
            )
            await sess_b.flush()
            sess_b.add_all(
                [
                    DocumentVersion(
                        id=ver_intake_b,
                        tenant_id=tenant_b,
                        document_id=doc_intake_b,
                        sha256="sha-intake-b",
                        uploaded_by=admin_b.id,
                        status="ready",
                        file_extension="pdf",
                    ),
                    DocumentVersion(
                        id=ver_policy_b,
                        tenant_id=tenant_b,
                        document_id=doc_policy_b,
                        sha256="sha-policy-b",
                        uploaded_by=admin_b.id,
                        status="ready",
                        file_extension="pdf",
                    ),
                    DocumentVersion(
                        id=ver_plans_b,
                        tenant_id=tenant_b,
                        document_id=doc_plans_b,
                        sha256="sha-plans-b",
                        uploaded_by=admin_b.id,
                        status="ready",
                        file_extension="csv",
                    ),
                ]
            )
            await sess_b.flush()

            for doc_obj_id, ver_obj_id in (
                (doc_intake_b, ver_intake_b),
                (doc_policy_b, ver_policy_b),
                (doc_plans_b, ver_plans_b),
            ):
                d_row = (
                    await sess_b.execute(select(Document).where(Document.id == doc_obj_id))
                ).scalar_one()
                d_row.active_version_id = ver_obj_id

            sess_b.add_all(
                [
                    Page(
                        id=page_intake_b,
                        tenant_id=tenant_b,
                        document_version_id=ver_intake_b,
                        page_number=1,
                        page_sha256="psha-intake-b",
                        page_type="digital_text",
                        image_key=f"{tenant_b}/pages/{ver_intake_b}/1.png",
                        needs_review=False,
                        is_excluded=False,
                    ),
                    Page(
                        id=page_policy_b,
                        tenant_id=tenant_b,
                        document_version_id=ver_policy_b,
                        page_number=1,
                        page_sha256="psha-policy-b",
                        page_type="digital_text",
                        image_key=f"{tenant_b}/pages/{ver_policy_b}/1.png",
                        needs_review=False,
                        is_excluded=False,
                    ),
                ]
            )
            await sess_b.flush()

            sess_b.add_all(
                [
                    Chunk(
                        id=chunk_intake_b,
                        tenant_id=tenant_b,
                        document_version_id=ver_intake_b,
                        page_id=page_intake_b,
                        text=text_intake_b,
                        context_prefix=(
                            "Document: patient_intake_form.pdf - Page 1 - "
                            "Section: Tenant B Exclusive Cardiology Addendum"
                        ),
                        language="en",
                        ts_config="english",
                        evidence_type="text",
                        is_active=True,
                        embedding=emb_vec,
                    ),
                    Chunk(
                        id=chunk_policy_b,
                        tenant_id=tenant_b,
                        document_version_id=ver_policy_b,
                        page_id=page_policy_b,
                        text="Front desk intake fax is 555-0132 for Pinecrest Medical Group.",
                        context_prefix=(
                            "Document: front_desk_policy.pdf - Page 1 - "
                            "Section: Pinecrest Check-In Rules"
                        ),
                        language="en",
                        ts_config="english",
                        evidence_type="text",
                        is_active=True,
                        embedding=emb_vec,
                    ),
                    TableData(
                        id=tbl_row_b,
                        tenant_id=tenant_b,
                        document_version_id=ver_plans_b,
                        sheet_name="accepted_plans",
                        row_index=1,
                        row_data={
                            "Payer Name": "Beacon Mutual Healthcare",
                            "Deductible": "$1500.00",
                            "Patient": "Alex Morgan",
                        },
                    ),
                ]
            )
            doc_excl_b, ver_excl_b = uuid4(), uuid4()
            sess_b.add(
                Document(
                    id=doc_excl_b,
                    tenant_id=tenant_b,
                    title="pinecrest_cardiology_addendum.pdf",
                    is_retired=False,
                )
            )
            await sess_b.flush()
            sess_b.add(
                DocumentVersion(
                    id=ver_excl_b,
                    tenant_id=tenant_b,
                    document_id=doc_excl_b,
                    sha256="sha-excl-b",
                    uploaded_by=admin_b.id,
                    status="ready",
                    file_extension="pdf",
                )
            )
            await sess_b.flush()
            d_excl_b = (
                await sess_b.execute(select(Document).where(Document.id == doc_excl_b))
            ).scalar_one()
            d_excl_b.active_version_id = ver_excl_b

        # Store distinct PDF and PNG files in storage for Tenant A and Tenant B
        pdf_a_bytes = helpers._create_multipage_digital_pdf(
            [["SYNTHETIC TEST DOCUMENT", "TENANT A SECRET FILE 555-0131 MRN-TENANT-A-101"]]
        )
        pdf_b_bytes = helpers._create_multipage_digital_pdf(
            [["SYNTHETIC TEST DOCUMENT", "TENANT B SECRET FILE 555-0132 MRN-TENANT-B-909"]]
        )
        await storage.put(
            StorageKey(tenant_id=tenant_a, path=f"documents/{ver_intake_a}/original.pdf"),
            pdf_a_bytes,
            "application/pdf",
        )
        await storage.put(
            StorageKey(tenant_id=tenant_b, path=f"documents/{ver_intake_b}/original.pdf"),
            pdf_b_bytes,
            "application/pdf",
        )
        await storage.put(
            StorageKey(tenant_id=tenant_a, path=f"pages/{ver_intake_a}/1.png"),
            b"\x89PNG\r\n\x1a\nTENANT_A_IMAGE_555_0131",
            "image/png",
        )
        await storage.put(
            StorageKey(tenant_id=tenant_b, path=f"pages/{ver_intake_b}/1.png"),
            b"\x89PNG\r\n\x1a\nTENANT_B_IMAGE_555_0132",
            "image/png",
        )

        # Wire PostgresRetriever + EvidenceGroundedEchoModel into questions_route._ports_cache
        real_pg_retriever = PostgresRetriever(embedding_provider=embedder)
        questions_route._ports_cache["mock"] = GraphPorts(
            model_provider=_EvidenceGroundedEchoModel(),
            retriever=real_pg_retriever,
            is_mock_profile=False,
        )

        transport = ASGITransport(app=app)
        async with (
            AsyncClient(transport=transport, base_url="http://test") as client_a,
            AsyncClient(transport=transport, base_url="http://test") as client_b,
        ):
            cookies_a, csrf_a = await _login(client_a, email_a, pass_a)
            cookies_b, csrf_b = await _login(client_b, email_b, pass_b)
            client_a.cookies.update(cookies_a)
            client_b.cookies.update(cookies_b)
            headers_a = {"X-CSRF-Token": csrf_a}
            headers_b = {"X-CSRF-Token": csrf_b}

            # =========================================================================
            # Channels 1 & 2: Simultaneous Questions (Answers + Citations) via asyncio.gather
            # =========================================================================
            q_tasks = [
                client_a.post(
                    "/v1/questions",
                    json={
                        "assistant_id": str(asst_a),
                        "question_text": (
                            "What are Alex Morgan's chart ID, insurer, fax, and deductible?"
                        ),
                        "tenant_id": str(tenant_b),  # Forged tenant_id attempt
                    },
                    headers=headers_a,
                ),
                client_b.post(
                    "/v1/questions",
                    json={
                        "assistant_id": str(asst_b),
                        "question_text": (
                            "What are Alex Morgan's chart ID, insurer, fax, and deductible?"
                        ),
                        "tenant_id": str(tenant_a),  # Forged tenant_id attempt
                    },
                    headers=headers_b,
                ),
                client_a.post(
                    "/v1/questions",
                    json={
                        "assistant_id": str(asst_a),
                        "question_text": (
                            "What is the accepted plan deductible for Alex Morgan in the table?"
                        ),
                    },
                    headers=headers_a,
                ),
                client_b.post(
                    "/v1/questions",
                    json={
                        "assistant_id": str(asst_b),
                        "question_text": (
                            "What is the accepted plan deductible for Alex Morgan in the table?"
                        ),
                    },
                    headers=headers_b,
                ),
            ]
            resp_a1, resp_b1, resp_a_tbl, resp_b_tbl = await asyncio.gather(*q_tasks)
            assert resp_a1.status_code == 200
            assert resp_b1.status_code == 200
            assert resp_a_tbl.status_code == 200
            assert resp_b_tbl.status_code == 200

            body_a1 = resp_a1.json()
            body_b1 = resp_b1.json()
            body_a_tbl = resp_a_tbl.json()
            body_b_tbl = resp_b_tbl.json()

            # Check Channel 1 (answers)
            tenant_b_markers = ("555-0132", "$1500.00", "MRN-TENANT-B-909", "Beacon Mutual")
            tenant_a_markers = ("555-0131", "$250.00", "MRN-TENANT-A-101", "Apex Health")

            for ans_a in (body_a1["answer"], body_a_tbl["answer"]):
                if any(m in ans_a for m in tenant_b_markers):
                    leak_counts["answers"] += 1
            for ans_b in (body_b1["answer"], body_b_tbl["answer"]):
                if any(m in ans_b for m in tenant_a_markers):
                    leak_counts["answers"] += 1

            assert "MRN-TENANT-A-101" in body_a1["answer"] or "555-0131" in body_a1["answer"]
            assert "MRN-TENANT-B-909" in body_b1["answer"] or "555-0132" in body_b1["answer"]
            assert "$250.00" in body_a_tbl["answer"]
            assert "$1500.00" in body_b_tbl["answer"]

            # Check Channel 2 (citations)
            b_ids = {
                str(chunk_intake_b),
                str(chunk_policy_b),
                str(tbl_row_b),
                str(ver_intake_b),
                str(ver_plans_b),
            }
            a_ids = {
                str(chunk_intake_a),
                str(chunk_policy_a),
                str(tbl_row_a),
                str(ver_intake_a),
                str(ver_plans_a),
            }

            for r_a in (body_a1, body_a_tbl):
                if set(r_a.get("citations", [])) & b_ids:
                    leak_counts["citations"] += 1
                for src in r_a.get("sources", []):
                    if src.get("version_id") in b_ids:
                        leak_counts["citations"] += 1

            for r_b in (body_b1, body_b_tbl):
                if set(r_b.get("citations", [])) & a_ids:
                    leak_counts["citations"] += 1
                for src in r_b.get("sources", []):
                    if src.get("version_id") in a_ids:
                        leak_counts["citations"] += 1

            conv_a_id = body_a1["conversation_id"]
            conv_b_id = body_b1["conversation_id"]
            msg_a_id = body_a1["message_id"]
            msg_b_id = body_b1["message_id"]

            # =========================================================================
            # Channel 3: Source File Route (Simultaneous Cross-Tenant Probing)
            # =========================================================================
            file_probes = await asyncio.gather(
                client_a.get(f"/v1/assistants/{asst_a}/knowledge/documents/{ver_intake_b}/file"),
                client_b.get(f"/v1/assistants/{asst_b}/knowledge/documents/{ver_intake_a}/file"),
                client_a.get(f"/v1/assistants/{asst_b}/knowledge/documents/{ver_intake_b}/file"),
                client_b.get(f"/v1/assistants/{asst_a}/knowledge/documents/{ver_intake_a}/file"),
            )
            for fp in file_probes:
                if fp.status_code != 404 or b"SECRET FILE" in fp.content:
                    leak_counts["source_file_route"] += 1

            # =========================================================================
            # Channel 6 & 4: Session Documents, Extraction Results & Page Image Routes
            # =========================================================================
            # Upload session fax in Tenant A and Tenant B simultaneously
            fax_a_bytes = helpers._create_multipage_digital_pdf(
                [
                    [
                        "SYNTHETIC TEST DOCUMENT",
                        "Patient: Alex Morgan",
                        "MRN-TENANT-A-101",
                        "555-0131",
                    ]
                ]
            )
            fax_b_bytes = helpers._create_multipage_digital_pdf(
                [
                    [
                        "SYNTHETIC TEST DOCUMENT",
                        "Patient: Alex Morgan",
                        "MRN-TENANT-B-909",
                        "555-0132",
                    ]
                ]
            )
            sdoc_resp_a, sdoc_resp_b = await asyncio.gather(
                client_a.post(
                    f"/v1/conversations/{conv_a_id}/documents",
                    content=fax_a_bytes,
                    headers={**headers_a, "X-Filename": "patient_intake_form.pdf"},
                ),
                client_b.post(
                    f"/v1/conversations/{conv_b_id}/documents",
                    content=fax_b_bytes,
                    headers={**headers_b, "X-Filename": "patient_intake_form.pdf"},
                ),
            )
            assert sdoc_resp_a.status_code == 200
            assert sdoc_resp_b.status_code == 200
            sdoc_a_id = sdoc_resp_a.json()["session_document_id"]
            sdoc_b_id = sdoc_resp_b.json()["session_document_id"]

            # Cross-tenant session document upload probe
            cross_sdoc_a, cross_sdoc_b = await asyncio.gather(
                client_a.post(
                    f"/v1/conversations/{conv_b_id}/documents",
                    content=fax_a_bytes,
                    headers={**headers_a, "X-Filename": "probe.pdf"},
                ),
                client_b.post(
                    f"/v1/conversations/{conv_a_id}/documents",
                    content=fax_b_bytes,
                    headers={**headers_b, "X-Filename": "probe.pdf"},
                ),
            )
            if cross_sdoc_a.status_code != 404 or cross_sdoc_b.status_code != 404:
                leak_counts["session_documents_and_extraction_results"] += 1

            # Verify DB-level RLS isolation on SessionDocument and ExtractionResult
            async with tenant_session(ctx_a) as sess_a:
                sdocs_in_a = (await sess_a.execute(select(SessionDocument.id))).scalars().all()
                exts_in_a = (
                    (await sess_a.execute(select(ExtractionResult.session_document_id)))
                    .scalars()
                    .all()
                )
                if UUID(sdoc_b_id) in sdocs_in_a or UUID(sdoc_b_id) in exts_in_a:
                    leak_counts["session_documents_and_extraction_results"] += 1

            # Check Channel 4: Knowledge page image, thumbnail, and session document page image
            img_probes = await asyncio.gather(
                client_a.get(
                    f"/v1/assistants/{asst_a}/knowledge/documents/{ver_intake_b}/pages/1/image"
                ),
                client_b.get(
                    f"/v1/assistants/{asst_b}/knowledge/documents/{ver_intake_a}/pages/1/image"
                ),
                client_a.get(
                    f"/v1/assistants/{asst_a}/knowledge/documents/{ver_intake_b}/pages/1/thumbnail"
                ),
                client_b.get(
                    f"/v1/assistants/{asst_b}/knowledge/documents/{ver_intake_a}/pages/1/thumbnail"
                ),
                client_a.get(f"/v1/conversations/{conv_b_id}/documents/{sdoc_b_id}/pages/1/image"),
                client_b.get(f"/v1/conversations/{conv_a_id}/documents/{sdoc_a_id}/pages/1/image"),
            )
            for ip in img_probes:
                if ip.status_code != 404:
                    leak_counts["page_image_routes"] += 1

            # =========================================================================
            # Channel 5: Conversations (List, Get Messages, Delete, Cross-Tenant Question)
            # =========================================================================
            conv_probes = await asyncio.gather(
                client_a.get(f"/v1/assistants/{asst_a}/conversations"),
                client_b.get(f"/v1/assistants/{asst_b}/conversations"),
                client_a.get(f"/v1/conversations/{conv_b_id}/messages"),
                client_b.get(f"/v1/conversations/{conv_a_id}/messages"),
                client_a.delete(f"/v1/conversations/{conv_b_id}", headers=headers_a),
                client_b.delete(f"/v1/conversations/{conv_a_id}", headers=headers_b),
                client_a.post(
                    "/v1/questions",
                    json={
                        "assistant_id": str(asst_a),
                        "question_text": "Probe into Tenant B conversation",
                        "conversation_id": conv_b_id,
                    },
                    headers=headers_a,
                ),
                client_b.post(
                    "/v1/questions",
                    json={
                        "assistant_id": str(asst_b),
                        "question_text": "Probe into Tenant A conversation",
                        "conversation_id": conv_a_id,
                    },
                    headers=headers_b,
                ),
            )
            list_a, list_b, msgs_ab, msgs_ba, del_ab, del_ba, q_ab, q_ba = conv_probes
            ids_in_list_a = {c["id"] for c in list_a.json()}
            ids_in_list_b = {c["id"] for c in list_b.json()}
            if conv_b_id in ids_in_list_a or conv_a_id in ids_in_list_b:
                leak_counts["conversations"] += 1
            for status_resp in (msgs_ab, msgs_ba, del_ab, del_ba, q_ab, q_ba):
                if status_resp.status_code != 404:
                    leak_counts["conversations"] += 1

            # =========================================================================
            # Channel 7: Inventory and Title Search
            # =========================================================================
            inv_a, inv_b, titles_a, titles_b = await asyncio.gather(
                real_pg_retriever.get_inventory(ctx_a, heading_limit=10),
                real_pg_retriever.get_inventory(ctx_b, heading_limit=10),
                real_pg_retriever.search_titles(ctx_a, "Addendum", limit=10),
                real_pg_retriever.search_titles(ctx_b, "Addendum", limit=10),
            )
            assert any("Oncology" in h for h in inv_a.get("headings", []))
            assert any("Cardiology" in h for h in inv_b.get("headings", []))
            assert any("oncology" in t.lower() for t in titles_a)
            assert any("cardiology" in t.lower() for t in titles_b)
            if any("Cardiology" in h for h in inv_a.get("headings", [])):
                leak_counts["inventory_and_title_search"] += 1
            if any("Oncology" in h for h in inv_b.get("headings", [])):
                leak_counts["inventory_and_title_search"] += 1
            if any("cardiology" in t.lower() for t in titles_a) or any(
                "oncology" in t.lower() for t in titles_b
            ):
                leak_counts["inventory_and_title_search"] += 1

            # =========================================================================
            # Channel 8: Feedback / Message & Audit Event Tenant Isolation
            # =========================================================================
            async with tenant_session(ctx_a) as sess_a:
                msg_b_in_a = (
                    await sess_a.execute(select(Message).where(Message.id == UUID(msg_b_id)))
                ).scalar_one_or_none()
                audit_b_in_a = (
                    (
                        await sess_a.execute(
                            select(AuditEvent).where(AuditEvent.tenant_id == tenant_b)
                        )
                    )
                    .scalars()
                    .all()
                )
                if msg_b_in_a is not None or len(audit_b_in_a) > 0:
                    leak_counts["feedback"] += 1

            async with tenant_session(ctx_b) as sess_b:
                msg_a_in_b = (
                    await sess_b.execute(select(Message).where(Message.id == UUID(msg_a_id)))
                ).scalar_one_or_none()
                audit_a_in_b = (
                    (
                        await sess_b.execute(
                            select(AuditEvent).where(AuditEvent.tenant_id == tenant_a)
                        )
                    )
                    .scalars()
                    .all()
                )
                if msg_a_in_b is not None or len(audit_a_in_b) > 0:
                    leak_counts["feedback"] += 1

            # =========================================================================
            # Channel 9: Dedup Isolation (Invariant 15: Identical File Content in Both Tenants)
            # =========================================================================
            same_pdf_bytes = helpers._create_multipage_digital_pdf(
                [["SYNTHETIC TEST DOCUMENT", "SHARED STANDARD HIPAA NOTICE VERSION 2026"]]
            )
            up_a1, up_b1 = await asyncio.gather(
                client_a.post(
                    f"/v1/assistants/{asst_a}/knowledge/upload",
                    content=same_pdf_bytes,
                    headers={**headers_a, "X-Filename": "same_policy.pdf"},
                ),
                client_b.post(
                    f"/v1/assistants/{asst_b}/knowledge/upload",
                    content=same_pdf_bytes,
                    headers={**headers_b, "X-Filename": "same_policy.pdf"},
                ),
            )
            assert up_a1.status_code == 202
            assert up_b1.status_code == 202
            up_a1_json = up_a1.json()
            up_b1_json = up_b1.json()

            # Both must succeed as new documents (is_duplicate=False) with distinct IDs
            if (
                up_a1_json["is_duplicate"] is not False
                or up_b1_json["is_duplicate"] is not False
                or up_a1_json["document_id"] == up_b1_json["document_id"]
                or up_a1_json["document_version_id"] == up_b1_json["document_version_id"]
            ):
                leak_counts["dedup"] += 1

            # Re-uploading within Tenant A must be detected as duplicate within Tenant A only
            up_a2 = await client_a.post(
                f"/v1/assistants/{asst_a}/knowledge/upload",
                content=same_pdf_bytes,
                headers={**headers_a, "X-Filename": "same_policy.pdf"},
            )
            assert up_a2.status_code == 202
            if up_a2.json()["is_duplicate"] is not True:
                leak_counts["dedup"] += 1

        questions_route._ports_cache.clear()

    for channel, count in leak_counts.items():
        assert count == 0, f"Cross-tenant leak detected on channel '{channel}': {count}"
