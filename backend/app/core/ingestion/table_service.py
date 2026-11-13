"""Table ingestion service (Task 2.6).

Orchestrates table extraction and persistence through DocumentRepository port.
Zero direct database/ORM imports in core (AGENTS.md Build Rule 4).
Records job_steps with step='table_ingestion', status='completed', page_number=None,
and zero sheet names.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.ports.document_repository import DocumentRepository
from app.ports.errors import FileValidationError
from app.ports.table_extractor import TableExtractor

BATCH_SIZE: int = 1000


class TableIngestionService:
    """Service orchestrating table extraction and persistence into tables_data."""

    def __init__(
        self,
        repository: DocumentRepository,
        table_extractor: TableExtractor,
    ) -> None:
        self.repository = repository
        self.table_extractor = table_extractor

    async def ingest_spreadsheet(
        self,
        tenant_id: UUID,
        document_version_id: UUID,
        file: bytes,
        filename: str,
        mime_type: str,
        job_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Ingest spreadsheet or CSV file into tables_data idempotently."""
        # 1. Transition document version status to processing
        await self.repository.update_version_status(
            str(tenant_id),
            document_version_id,
            "processing",
        )

        # 2. Extract structured tables
        result = await self.table_extractor.extract_tables(file, filename, mime_type)

        # 3. Batch insert rows across sheets
        total_rows = 0
        for sheet in result.sheets:
            batch: list[dict[str, Any]] = []
            for row_idx, row_dict in enumerate(sheet.rows, start=1):
                stored_row_data = dict(row_dict)
                if sheet.caption is not None:
                    stored_row_data["caption"] = sheet.caption
                batch.append(
                    {
                        "id": uuid4(),
                        "tenant_id": tenant_id,
                        "document_version_id": document_version_id,
                        "sheet_name": sheet.sheet_name,
                        "page_id": None,
                        "table_index": 0,
                        "row_index": row_idx,
                        "row_data": stored_row_data,
                    }
                )
                if len(batch) >= BATCH_SIZE:
                    await self.repository.create_table_data_batch(str(tenant_id), batch)
                    batch = []

            if batch:
                await self.repository.create_table_data_batch(str(tenant_id), batch)
            total_rows += len(sheet.rows)

        # 4. Record job step (step='table_ingestion', page_number=None, zero sheet names)
        await self.repository.record_job_step(
            tenant_id=str(tenant_id),
            version_id=document_version_id,
            page_number=None,
            step="table_ingestion",
            status="completed",
            processing_version="v1",
            job_id=job_id,
        )

        # 5. Activate document version and transition status to ready
        await self.repository.execute_version_activation(
            str(tenant_id),
            document_version_id,
            "Branch C",
        )

        return {
            "total_rows": total_rows,
            "sheets_count": len(result.sheets),
            "skipped_hidden_sheets_count": result.skipped_hidden_sheets_count,
        }

    async def ingest_table_file(
        self,
        tenant_id: UUID,
        document_version_id: UUID,
        file: bytes,
        extension: str,
        job_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Ingest spreadsheet or CSV file based on file extension into tables_data."""
        clean_ext = extension.lower().strip().lstrip(".")
        table_mime_map: dict[str, str] = {
            "csv": "text/csv",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        if clean_ext not in table_mime_map:
            raise FileValidationError(f"Unsupported table file extension: {clean_ext}")
        mime_type = table_mime_map[clean_ext]
        return await self.ingest_spreadsheet(
            tenant_id=tenant_id,
            document_version_id=document_version_id,
            file=file,
            filename=f"original.{clean_ext}",
            mime_type=mime_type,
            job_id=job_id,
        )
