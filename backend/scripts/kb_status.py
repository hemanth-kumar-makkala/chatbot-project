"""Knowledge Base status reporting script for product owner inspections.

Read-only inspection script that prints each active document with pages, chunks,
chunks with embeddings, visual page readings, and table rows for an assistant.
Plainly flags if any Ready document has 0 chunks (or 0 table rows for tabular docs).
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import func, select, text

from app.db.models import (
    Assistant,
    Chunk,
    Document,
    DocumentVersion,
    Page,
    PageReading,
    TableData,
    Tenant,
)
from app.db.session import retriever_tenant_session, unscoped_session
from app.ports.types import TenantContext, UserRole


@dataclass(frozen=True)
class DocumentStatusSummary:
    """Summary metrics for one document version."""

    title: str
    document_id: str
    version_id: str
    status: str
    file_extension: str
    pages_count: int
    chunks_count: int
    embeddings_count: int
    visual_readings_count: int
    table_rows_count: int


def format_kb_status_report(
    assistant_name: str,
    documents: list[DocumentStatusSummary],
) -> str:
    """Format human-readable knowledge base status report."""
    lines: list[str] = [
        f"=== Knowledge Base Status: {assistant_name} ===",
        f"Total Active Documents: {len(documents)}",
        "",
    ]
    warnings: list[str] = []

    for doc in documents:
        lines.append(f"Document: {doc.title}")
        lines.append(
            f"  Version: {doc.version_id} [{doc.status.upper()}] (ext: {doc.file_extension})"
        )
        lines.append(
            f"  Pages: {doc.pages_count}, Chunks: {doc.chunks_count} "
            f"(Embedded: {doc.embeddings_count}), Visual Readings: {doc.visual_readings_count}, "
            f"Table Rows: {doc.table_rows_count}"
        )

        is_tabular = doc.file_extension.lower() in ("csv", "xlsx", "xls")
        if doc.status.lower() == "ready":
            if is_tabular and doc.table_rows_count == 0:
                warn = f"WARNING: Ready tabular document '{doc.title}' has 0 table rows!"
                warnings.append(warn)
                lines.append(f"  --> {warn}")
            elif not is_tabular and doc.chunks_count == 0:
                warn = f"WARNING: Ready document '{doc.title}' has 0 chunks!"
                warnings.append(warn)
                lines.append(f"  --> {warn}")

        lines.append("")

    if warnings:
        lines.append("=== INGESTION ALERTS ===")
        for w in warnings:
            lines.append(f"  - {w}")
    else:
        lines.append("All Ready documents have valid ingested evidence in database.")

    return "\n".join(lines)


async def inspect_knowledge_base(
    assistant_id: UUID | None = None,
) -> tuple[str, list[DocumentStatusSummary]]:
    """Retrieve document status summaries using read-only tenant-scoped queries."""
    # 1. Resolve tenant_id
    async with unscoped_session() as session:
        if assistant_id is not None:
            res = await session.execute(
                text("SELECT lookup_assistant_tenant(:asst_id)"),
                {"asst_id": assistant_id},
            )
            tenant_uuid = res.scalar_one_or_none()
            if tenant_uuid is None:
                raise ValueError(f"No assistant found matching id {assistant_id}")
            tenant_id = tenant_uuid
        else:
            tenant_res = await session.execute(
                select(Tenant.id).order_by(Tenant.created_at.asc()).limit(1)
            )
            tenant_uuid = tenant_res.scalar_one_or_none()
            if tenant_uuid is None:
                raise ValueError("No tenants found in database")
            tenant_id = tenant_uuid

    # 2. Inspect active documents and assistant name under tenant-scoped read-only session
    tenant_ctx = TenantContext(
        tenant_id=str(tenant_id),
        user_id="read-only-inspector",
        role=UserRole.STAFF,
        session_id="inspector-session",
    )

    summaries: list[DocumentStatusSummary] = []
    async with retriever_tenant_session(tenant_ctx) as session:
        asst_stmt = select(Assistant).where(Assistant.tenant_id == tenant_id)
        if assistant_id is not None:
            asst_stmt = asst_stmt.where(Assistant.id == assistant_id)
        asst_row = (
            await session.execute(asst_stmt.order_by(Assistant.created_at.asc()).limit(1))
        ).scalar_one_or_none()
        assistant_name = asst_row.name if asst_row is not None else "Default Assistant"
        doc_stmt = (
            select(Document, DocumentVersion)
            .join(DocumentVersion, Document.active_version_id == DocumentVersion.id)
            .where(
                Document.tenant_id == tenant_id,
                Document.is_retired.is_(False),
                Document.active_version_id.is_not(None),
            )
            .order_by(Document.created_at.asc())
        )
        doc_res = await session.execute(doc_stmt)
        active_docs = doc_res.all()

        for doc, ver in active_docs:
            ver_id = ver.id

            # Pages count
            p_stmt = (
                select(func.count())
                .select_from(Page)
                .where(Page.tenant_id == tenant_id, Page.document_version_id == ver_id)
            )
            pages_count = (await session.execute(p_stmt)).scalar() or 0

            # Chunks count
            c_stmt = (
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.tenant_id == tenant_id, Chunk.document_version_id == ver_id)
            )
            chunks_count = (await session.execute(c_stmt)).scalar() or 0

            # Embedded chunks count
            emb_stmt = (
                select(func.count())
                .select_from(Chunk)
                .where(
                    Chunk.tenant_id == tenant_id,
                    Chunk.document_version_id == ver_id,
                    Chunk.embedding.is_not(None),
                )
            )
            embedded_count = (await session.execute(emb_stmt)).scalar() or 0

            # Visual page readings count
            vis_stmt = (
                select(func.count())
                .select_from(PageReading)
                .join(Page, PageReading.page_id == Page.id)
                .where(
                    Page.tenant_id == tenant_id,
                    Page.document_version_id == ver_id,
                )
            )
            vis_count = (await session.execute(vis_stmt)).scalar() or 0

            # Table rows count
            tbl_stmt = (
                select(func.count())
                .select_from(TableData)
                .where(TableData.tenant_id == tenant_id, TableData.document_version_id == ver_id)
            )
            table_rows_count = (await session.execute(tbl_stmt)).scalar() or 0

            summaries.append(
                DocumentStatusSummary(
                    title=doc.title,
                    document_id=str(doc.id),
                    version_id=str(ver.id),
                    status=str(ver.status),
                    file_extension=str(ver.file_extension or ""),
                    pages_count=pages_count,
                    chunks_count=chunks_count,
                    embeddings_count=embedded_count,
                    visual_readings_count=vis_count,
                    table_rows_count=table_rows_count,
                )
            )

    return assistant_name, summaries


def main() -> None:
    """Run knowledge base status CLI."""
    load_dotenv()
    parser = argparse.ArgumentParser(description="Inspect Knowledge Base document status.")
    parser.add_argument(
        "--assistant-id",
        type=str,
        default=None,
        help="Optional assistant UUID (defaults to primary assistant)",
    )
    args = parser.parse_args()

    asst_id = UUID(args.assistant_id) if args.assistant_id else None
    asst_name, doc_summaries = asyncio.run(inspect_knowledge_base(asst_id))
    report = format_kb_status_report(asst_name, doc_summaries)
    print(report)


if __name__ == "__main__":
    main()
