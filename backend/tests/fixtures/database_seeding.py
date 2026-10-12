"""Shared database seeding helpers for integration and migration tests."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def seed_document(
    session: AsyncSession,
    tenant_id: UUID | str,
    doc_id: UUID | None = None,
    title: str = "Test Doc",
) -> UUID:
    """Helper to insert a document row."""
    doc_id = doc_id or uuid4()
    await session.execute(
        text(
            """
            INSERT INTO documents (id, tenant_id, title)
            VALUES (:id, :tid, :title);
            """
        ),
        {"id": doc_id, "tid": UUID(str(tenant_id)), "title": title},
    )
    return doc_id


async def seed_version(
    session: AsyncSession,
    tenant_id: UUID | str,
    doc_id: UUID,
    user_id: UUID | str,
    ver_id: UUID | None = None,
    sha256: str | None = None,
    status: str = "ready",
) -> UUID:
    """Helper to insert a document_version row."""
    ver_id = ver_id or uuid4()
    actual_sha = sha256 if sha256 is not None else f"sha256-{uuid4().hex}"
    await session.execute(
        text(
            """
            INSERT INTO document_versions (
                id, tenant_id, document_id, sha256, uploaded_by, status
            )
            VALUES (:id, :tid, :doc_id, :sha, :uid, :status);
            """
        ),
        {
            "id": ver_id,
            "tid": UUID(str(tenant_id)),
            "doc_id": doc_id,
            "sha": actual_sha,
            "uid": UUID(str(user_id)),
            "status": status,
        },
    )
    return ver_id


async def seed_page(
    session: AsyncSession,
    tenant_id: UUID | str,
    ver_id: UUID,
    page_id: UUID | None = None,
    page_number: int = 1,
    page_sha256: str = "sha-p1",
    page_type: str = "digital_text",
    image_key: str | None = None,
    needs_review: bool = False,
    review_reason: str | None = None,
) -> UUID:
    """Helper to insert a page row."""
    page_id = page_id or uuid4()
    img_key = image_key or f"{tenant_id}/documents/{ver_id}/p{page_number}.png"
    await session.execute(
        text(
            """
            INSERT INTO pages (
                id, tenant_id, document_version_id, page_number,
                page_sha256, page_type, image_key, needs_review, review_reason
            )
            VALUES (:id, :tid, :vid, :pnum, :psha, :ptype, :img_key, :nr, :rr);
            """
        ),
        {
            "id": page_id,
            "tid": UUID(str(tenant_id)),
            "vid": ver_id,
            "pnum": page_number,
            "psha": page_sha256,
            "ptype": page_type,
            "img_key": img_key,
            "nr": needs_review,
            "rr": review_reason,
        },
    )
    return page_id


async def seed_reading(
    session: AsyncSession,
    tenant_id: UUID | str,
    page_id: UUID,
    reading_id: UUID | None = None,
    source: str = "ocr",
    body: str = "Sample reading",
    model_id: str = "m1",
    model_version: str = "v1",
) -> UUID:
    """Helper to insert a page_reading row."""
    reading_id = reading_id or uuid4()
    await session.execute(
        text(
            """
            INSERT INTO page_readings (
                id, tenant_id, page_id, source, text, model_id, model_version
            )
            VALUES (:id, :tid, :pid, :src, :txt, :mid, :mver);
            """
        ),
        {
            "id": reading_id,
            "tid": UUID(str(tenant_id)),
            "pid": page_id,
            "src": source,
            "txt": body,
            "mid": model_id,
            "mver": model_version,
        },
    )
    return reading_id


async def seed_chunk(
    session: AsyncSession,
    tenant_id: UUID | str,
    page_id: UUID,
    ver_id: UUID,
    chunk_id: UUID | None = None,
    body: str = "Sample chunk text",
    language: str = "en",
    ts_config: str = "english",
    is_active: bool = False,
) -> UUID:
    """Helper to insert a chunk row."""
    chunk_id = chunk_id or uuid4()
    await session.execute(
        text(
            """
            INSERT INTO chunks (
                id, tenant_id, page_id, document_version_id,
                text, language, ts_config, is_active
            )
            VALUES (:id, :tid, :pid, :vid, :txt, :lang, :cfg, :act);
            """
        ),
        {
            "id": chunk_id,
            "tid": UUID(str(tenant_id)),
            "pid": page_id,
            "vid": ver_id,
            "txt": body,
            "lang": language,
            "cfg": ts_config,
            "act": is_active,
        },
    )
    return chunk_id


async def seed_table_data(
    session: AsyncSession,
    tenant_id: UUID | str,
    ver_id: UUID,
    table_id: UUID | None = None,
    sheet_name: str = "Sheet1",
    row_index: int = 0,
    row_data: str = "{}",
) -> UUID:
    """Helper to insert a tables_data row."""
    table_id = table_id or uuid4()
    await session.execute(
        text(
            """
            INSERT INTO tables_data (
                id, tenant_id, document_version_id,
                sheet_name, row_index, row_data
            )
            VALUES (:id, :tid, :vid, :sheet, :idx, CAST(:data AS jsonb));
            """
        ),
        {
            "id": table_id,
            "tid": UUID(str(tenant_id)),
            "vid": ver_id,
            "sheet": sheet_name,
            "idx": row_index,
            "data": row_data,
        },
    )
    return table_id


async def seed_job(
    session: AsyncSession,
    tenant_id: UUID | str,
    ver_id: UUID,
    job_id: UUID | None = None,
    status: str = "queued",
    attempts: int = 1,
) -> UUID:
    """Helper to insert a job row."""
    job_id = job_id or uuid4()
    await session.execute(
        text(
            """
            INSERT INTO jobs (id, tenant_id, document_version_id, status, attempts)
            VALUES (:id, :tid, :vid, :status, :attempts);
            """
        ),
        {
            "id": job_id,
            "tid": UUID(str(tenant_id)),
            "vid": ver_id,
            "status": status,
            "attempts": attempts,
        },
    )
    return job_id


async def seed_job_step(
    session: AsyncSession,
    tenant_id: UUID | str,
    job_id: UUID,
    ver_id: UUID,
    step_id: UUID | None = None,
    page_number: int | None = 1,
    step: str = "ocr",
    processing_version: str = "v1",
    status: str = "completed",
) -> UUID:
    """Helper to insert a job_step row."""
    step_id = step_id or uuid4()
    await session.execute(
        text(
            """
            INSERT INTO job_steps (
                id, tenant_id, job_id, document_version_id,
                page_number, step, processing_version, status
            )
            VALUES (:id, :tid, :jid, :vid, :pnum, :step, :proc_ver, :status);
            """
        ),
        {
            "id": step_id,
            "tid": UUID(str(tenant_id)),
            "jid": job_id,
            "vid": ver_id,
            "pnum": page_number,
            "step": step,
            "proc_ver": processing_version,
            "status": status,
        },
    )
    return step_id
