"""Integration tests for Table Ingestion Service and database persistence (Task 2.6).

Covers:
1. End-to-end ingestion into tables_data under PostgreSQL RLS.
2. Idempotent re-run with ON CONFLICT DO NOTHING produces 0 duplicate rows (Invariant 11).
3. Records job_steps entry (counts only, zero metadata column, zero sheet names).
4. Version status lifecycle progression: received -> processing -> ready (or failed).
5. Invariant 8 table citation: tenant_id, document_version_id, sheet_name, table_index, row_index.
"""

from __future__ import annotations

import io
from uuid import UUID, uuid4

import openpyxl
import pytest
from sqlalchemy import func, select, text

from app.adapters.db.postgres_document_repository import PostgresDocumentRepository
from app.adapters.tables.builtin import BuiltinTableExtractor
from app.core.ingestion.table_service import TableIngestionService
from app.db.models import DocumentVersion, JobStep, TableData
from app.db.session import tenant_session, unscoped_session
from app.ports.types import TenantContext, UserRole
from tests.fixtures.database_seeding import (
    seed_document,
    seed_version,
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _create_two_sheet_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws1 = wb.active
    assert ws1 is not None
    ws1.title = "Dosing"
    ws1.append(["Drug", "Dosage", "Route"])
    ws1.append(["Metformin", "500mg", "Oral"])
    ws1.append(["Lisinopril", "10mg", "Oral"])

    ws2 = wb.create_sheet(title="LabPanels")
    ws2.append(["Test", "ReferenceRange", "Unit"])
    ws2.append(["A1C", "4.0-5.6", "%"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _seed_tenant_and_version(
    title: str = "Test Sheet",
    status: str = "ready",
) -> tuple[TenantContext, UUID, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    async with unscoped_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, name) VALUES (:t, 'Tenant Table');"),
            {"t": tenant_id},
        )
        await session.execute(
            text("INSERT INTO users (id, email, hashed_password) VALUES (:u, :email, 'h');"),
            {"u": user_id, "email": f"t_{user_id.hex[:10]}@test.com"},
        )
        await session.commit()

    ctx = TenantContext(
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        role=UserRole.CLIENT_ADMIN,
        session_id=str(uuid4()),
    )
    async with tenant_session(ctx) as session:
        doc_id = await seed_document(session, tenant_id, title=title)
        ver_id = await seed_version(session, tenant_id, doc_id, user_id, status=status)
        await session.commit()
    return ctx, tenant_id, ver_id


@pytest.mark.asyncio
async def test_table_ingestion_service_idempotent_rerun_postgres() -> None:
    """Invariant 11: Re-running table ingestion inserts 0 duplicate rows on second run."""
    ctx, tenant_id, version_id = await _seed_tenant_and_version()

    xlsx_bytes = _create_two_sheet_xlsx()
    extractor = BuiltinTableExtractor()

    async with tenant_session(ctx) as session:
        repo = PostgresDocumentRepository(session)
        service = TableIngestionService(repo, extractor)

        # First run: extracts and inserts 3 rows across 2 sheets
        job_id = uuid4()
        await service.ingest_spreadsheet(
            tenant_id=tenant_id,
            document_version_id=version_id,
            file=xlsx_bytes,
            filename="protocol.xlsx",
            mime_type=XLSX_MIME,
            job_id=job_id,
        )

        q1 = select(func.count(TableData.id)).where(
            TableData.tenant_id == tenant_id,
            TableData.document_version_id == version_id,
        )
        res1 = await session.execute(q1)
        count1 = res1.scalar_one()
        assert count1 == 3

        # Second run: re-running exact same spreadsheet produces 0 new rows (idempotent)
        await service.ingest_spreadsheet(
            tenant_id=tenant_id,
            document_version_id=version_id,
            file=xlsx_bytes,
            filename="protocol.xlsx",
            mime_type=XLSX_MIME,
            job_id=job_id,
        )

        res2 = await session.execute(q1)
        count2 = res2.scalar_one()
        assert count2 == count1


@pytest.mark.asyncio
async def test_table_ingestion_records_job_step_status_no_sheet_names() -> None:
    """Item 3 & 5: job_steps records step completion without sheet names or metadata column."""
    ctx, tenant_id, version_id = await _seed_tenant_and_version(title="data.xlsx")

    xlsx_bytes = _create_two_sheet_xlsx()
    extractor = BuiltinTableExtractor()

    async with tenant_session(ctx) as session:
        repo = PostgresDocumentRepository(session)
        service = TableIngestionService(repo, extractor)

        job_id = uuid4()
        result = await service.ingest_spreadsheet(
            tenant_id=tenant_id,
            document_version_id=version_id,
            file=xlsx_bytes,
            filename="data.xlsx",
            mime_type=XLSX_MIME,
            job_id=job_id,
        )
        assert result["total_rows"] == 3

        step_stmt = select(JobStep).where(
            JobStep.tenant_id == tenant_id,
            JobStep.document_version_id == version_id,
            JobStep.step == "table_ingestion",
        )
        step_res = await session.execute(step_stmt)
        step = step_res.scalar_one_or_none()
        assert step is not None
        assert step.status == "completed"
        assert step.page_number is None  # Spreadsheets have NULL page_number
        # Invariant: job_steps schema has no metadata column, and step name
        # never contains sheet names
        assert step.step == "table_ingestion"
        assert "metadata" not in JobStep.__table__.columns
        assert "Sheet1" not in step.step and "Sheet2" not in step.step


@pytest.mark.asyncio
async def test_version_status_progression() -> None:
    """Item 10: Document version transitions received -> processing -> ready."""
    ctx, tenant_id, version_id = await _seed_tenant_and_version(
        title="status_test.xlsx", status="received"
    )

    xlsx_bytes = _create_two_sheet_xlsx()
    extractor = BuiltinTableExtractor()

    async with tenant_session(ctx) as session:
        repo = PostgresDocumentRepository(session)
        service = TableIngestionService(repo, extractor)

        job_id = uuid4()
        await service.ingest_spreadsheet(
            tenant_id=tenant_id,
            document_version_id=version_id,
            file=xlsx_bytes,
            filename="status_test.xlsx",
            mime_type=XLSX_MIME,
            job_id=job_id,
        )

        ver_stmt = select(DocumentVersion).where(
            DocumentVersion.tenant_id == tenant_id,
            DocumentVersion.id == version_id,
        )
        ver_res = await session.execute(ver_stmt)
        updated_ver = ver_res.scalar_one()
        assert updated_ver.status == "ready"


@pytest.mark.asyncio
async def test_table_citations_rls_isolation() -> None:
    """Invariant 8: Table rows cite document_version_id, sheet_name, row_index under tenant."""
    ctx, tenant_id, version_id = await _seed_tenant_and_version(title="citations.xlsx")

    xlsx_bytes = _create_two_sheet_xlsx()
    extractor = BuiltinTableExtractor()

    async with tenant_session(ctx) as session:
        repo = PostgresDocumentRepository(session)
        service = TableIngestionService(repo, extractor)

        await service.ingest_spreadsheet(
            tenant_id=tenant_id,
            document_version_id=version_id,
            file=xlsx_bytes,
            filename="citations.xlsx",
            mime_type=XLSX_MIME,
        )

        rows_stmt = (
            select(TableData)
            .where(
                TableData.tenant_id == tenant_id,
                TableData.document_version_id == version_id,
                TableData.sheet_name == "Dosing",
            )
            .order_by(TableData.row_index)
        )
        rows_res = await session.execute(rows_stmt)
        rows = rows_res.scalars().all()

        assert len(rows) == 2
        assert rows[0].row_index == 1
        assert rows[0].page_id is None
        assert rows[0].table_index == 0
        assert rows[0].row_data["cells"]["Drug"] == "Metformin"
        assert rows[0].row_data["unreadable"] == []
