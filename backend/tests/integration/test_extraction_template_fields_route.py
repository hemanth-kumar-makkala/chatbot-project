"""Stage U3 integration: the extract endpoint always returns every extraction template field.

Uses the synthetic golden faxes missing_field_fax.pdf and invalid_npi_fax.pdf on the mock profile
inside a throwaway database.
- A fresh extraction lists every template field; absent fields are NOT_PRESENT with no value.
- A stored extraction result missing template fields (written before Stage U3) is re-served with
  every template field, the missing ones as NOT_PRESENT.
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.api.config import ApiSettings
from app.core.extraction.field_state import ExtractedField, FieldState
from app.core.extraction.template import FAX_INTAKE_TEMPLATE_FIELDS
from app.db.models import Assistant, Conversation, ExtractionResult, Tenant
from app.db.session import tenant_session, unscoped_session
from app.ports.types import TenantContext, UserRole
from tests.integration.test_api_knowledge_step2 import (
    _create_user_with_role,
    _login,
    throwaway_step2_environment,
)

GOLDEN_DOCS = Path(__file__).resolve().parents[3] / "evals" / "golden" / "docs"


@pytest.fixture
def api_settings(tmp_path: Path) -> ApiSettings:
    return ApiSettings(
        cors_origins=["http://localhost:3000"],
        cookie_secure=False,
        cookie_samesite="lax",
        idle_timeout_minutes=30,
        absolute_timeout_hours=24,
        allow_bearer_auth=False,
        max_upload_bytes=5 * 1024 * 1024,
        profile="mock",
        storage_dir=str(tmp_path),
        session_upload_retention_hours=24,
    )


async def _upload(
    client: AsyncClient,
    asst_id: UUID,
    conv_id: UUID,
    file_name: str,
    cookies: dict[str, str],
    csrf: str,
) -> str:
    pdf_bytes = (GOLDEN_DOCS / file_name).read_bytes()
    resp = await client.post(
        f"/v1/assistants/{asst_id}/conversations/{conv_id}/documents",
        files={"file": (file_name, io.BytesIO(pdf_bytes), "application/pdf")},
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    doc_id: str = resp.json()["session_document_id"]
    return doc_id


@pytest.mark.asyncio
async def test_extract_endpoint_lists_every_template_field_for_missing_and_invalid_npi_faxes(
    api_settings: ApiSettings,
) -> None:
    app = create_app(settings=api_settings)

    async with throwaway_step2_environment():
        tenant_id = uuid4()
        asst_id = uuid4()
        conv_id = uuid4()

        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Clinic U3"))
            await session.flush()

        user, email, password = await _create_user_with_role(tenant_id, "staff")
        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id=str(user.id),
            role=UserRole.CLIENT_ADMIN,
            session_id="seed",
        )
        async with tenant_session(ctx) as session:
            session.add(
                Assistant(
                    id=asst_id,
                    tenant_id=tenant_id,
                    name="Clinic U3 Assistant",
                    prompt_id="answer_synthesis",
                    prompt_version="1.0.0",
                )
            )
            session.add(
                Conversation(
                    id=conv_id,
                    tenant_id=tenant_id,
                    user_id=user.id,
                    assistant_id=asst_id,
                    title="U3 Template Fields",
                )
            )
            await session.flush()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True
        ) as client:
            cookies, csrf = await _login(client, email, password)

            # 1. Fresh extraction of missing_field_fax.pdf lists every template field
            missing_doc_id = await _upload(
                client, asst_id, conv_id, "missing_field_fax.pdf", cookies, csrf
            )
            resp = await client.post(
                f"/v1/assistants/{asst_id}/conversations/{conv_id}"
                f"/documents/{missing_doc_id}/extract",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf},
            )
            assert resp.status_code == 200, resp.text
            fields = resp.json()["extracted_fields"]
            names = [f["name"] for f in fields]
            assert names[: len(FAX_INTAKE_TEMPLATE_FIELDS)] == list(FAX_INTAKE_TEMPLATE_FIELDS)
            for f in fields:
                if f["state"] == "NOT_PRESENT":
                    assert f["value"] is None

            # 2. A stored pre-U3 extraction of invalid_npi_fax.pdf without dosage is completed
            npi_doc_id = await _upload(
                client, asst_id, conv_id, "invalid_npi_fax.pdf", cookies, csrf
            )
            stale_fields = [
                ExtractedField(
                    name="patient_name",
                    value="Casey Bennett",
                    state=FieldState.FILLED_HIGH,
                    confidence=0.96,
                    read1_value="Casey Bennett",
                    read2_value="Casey Bennett",
                    document_title="invalid_npi_fax.pdf",
                ).to_dict(),
                ExtractedField(
                    name="prescriber_npi",
                    value="1982736451",
                    state=FieldState.INVALID,
                    confidence=0.95,
                    read1_value="1982736451",
                    read2_value="1982736451",
                    validator_error="NPI check digit mismatch",
                    document_title="invalid_npi_fax.pdf",
                ).to_dict(),
            ]
            async with tenant_session(ctx) as session:
                session.add(
                    ExtractionResult(
                        tenant_id=tenant_id,
                        session_document_id=UUID(npi_doc_id),
                        schema_name="fax_intake",
                        fields_data={"fields": stale_fields},
                    )
                )
                await session.flush()

            resp = await client.post(
                f"/v1/assistants/{asst_id}/conversations/{conv_id}/documents/{npi_doc_id}/extract",
                cookies=cookies,
                headers={"X-CSRF-Token": csrf},
            )
            assert resp.status_code == 200, resp.text
            fields = resp.json()["extracted_fields"]
            assert [f["name"] for f in fields] == list(FAX_INTAKE_TEMPLATE_FIELDS)
            by_name = {f["name"]: f for f in fields}
            assert by_name["dosage"]["state"] == "NOT_PRESENT"
            assert by_name["dosage"]["value"] is None
            assert by_name["patient_name"] == stale_fields[0]
            assert by_name["prescriber_npi"] == stale_fields[1]
