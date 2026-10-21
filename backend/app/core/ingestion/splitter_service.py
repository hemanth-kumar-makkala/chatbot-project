"""Document splitter orchestration service (Task 2.3).

Orchestrates document page splitting, classification, storage, database recording,
and error handling (temporary resume vs permanent cleanup) across pure port interfaces.
Zero vendor imports, zero SQLAlchemy imports (ADR-0003 & Rule 5).
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from app.core.ingestion.splitter_router import should_split_document
from app.ports.document_repository import DocumentRepository
from app.ports.document_splitter import DocumentSplitter
from app.ports.errors import (
    CorruptDocumentError,
    DecompressionBombError,
    EncryptedDocumentError,
    PageLimitExceededError,
    PortError,
)
from app.ports.storage import StorageBackend
from app.ports.types import StorageKey, TenantContext


class SplitExecutionResult(BaseModel):
    """Result summary of splitting a document version."""

    total_pages: int
    pages_processed: int
    skipped: bool = False


class DocumentSplitterService:
    """Orchestrates tenant-scoped document splitting and page-first persistence."""

    def __init__(
        self,
        document_repo: DocumentRepository,
        storage: StorageBackend,
        splitter: DocumentSplitter,
    ) -> None:
        self.document_repo = document_repo
        self.storage = storage
        self.splitter = splitter

    async def split_document_version(
        self,
        ctx: TenantContext,
        version_id: UUID,
        file_bytes: bytes,
        filename: str,
        fail_after_page: int | None = None,
        fail_permanent_after_page: int | None = None,
    ) -> SplitExecutionResult:
        """Split a document into individual page artifacts and persist images and metadata.

        Enforces:
        - Non-paged documents (.txt, .csv, .xlsx) skip page splitting.
        - Preflight validation catches permanent failures before writing any page rows.
        - Temporary failures preserve completed pages so reruns resume idempotently (Invariant 11).
        - Mid-run permanent failures set version to failed and delete all version images.
        - Step progress and errors are recorded in job_steps.
        """
        ext = Path(filename).suffix.lower()

        # 1. Routing check: Skip non-paged documents
        if not should_split_document(ext):
            await self.document_repo.record_job_step(
                tenant_id=ctx.tenant_id,
                version_id=version_id,
                page_number=None,
                step="split_page",
                status="skipped",
            )
            return SplitExecutionResult(total_pages=0, pages_processed=0, skipped=True)

        # 2. Preflight split & validation (catches encrypted, corrupt, oversized before writing)
        try:
            page_artifacts = await self.splitter.split(file_bytes, extension=ext)
        except (
            EncryptedDocumentError,
            CorruptDocumentError,
            DecompressionBombError,
            PageLimitExceededError,
        ) as exc:
            # Permanent preflight failure: Set version failed, record job_step, re-raise
            await self.document_repo.update_version_status(
                tenant_id=ctx.tenant_id,
                version_id=version_id,
                status="failed",
            )
            await self.document_repo.record_job_step(
                tenant_id=ctx.tenant_id,
                version_id=version_id,
                page_number=None,
                step="split_page",
                status="failed",
            )
            raise exc

        total_pages = len(page_artifacts)
        existing_pages = set(
            await self.document_repo.get_page_numbers(
                tenant_id=ctx.tenant_id,
                version_id=version_id,
            )
        )

        stored_keys: list[StorageKey] = []
        processed_count = len(existing_pages)

        # 3. Process each page artifact
        for artifact in page_artifacts:
            p_num = artifact.page_number

            # Testing hook: Injected transient crash (Test 16)
            if fail_after_page is not None and p_num > fail_after_page:
                raise RuntimeError("Simulated transient worker crash")

            # Testing hook: Injected mid-run corrupt page error (Test 17)
            if fail_permanent_after_page is not None and p_num > fail_permanent_after_page:
                # Mid-run permanent error: Clean up all stored images, mark version failed
                await self._handle_permanent_failure(
                    ctx=ctx,
                    version_id=version_id,
                    p_num=p_num,
                    total_pages=total_pages,
                )
                raise CorruptDocumentError("Mid-run corrupted page encountered")

            # Invariant 11: Idempotent resume: skip pages already committed
            if p_num in existing_pages:
                continue

            try:
                # Store rendered PNG in tenant storage
                storage_key = StorageKey(
                    tenant_id=UUID(ctx.tenant_id),
                    path=f"pages/{version_id}/{p_num}.png",
                )
                await self.storage.put(
                    key=storage_key,
                    data=artifact.image_bytes,
                    content_type="image/png",
                )
                stored_keys.append(storage_key)

                if artifact.text_content:
                    txt_key = StorageKey(
                        tenant_id=UUID(ctx.tenant_id),
                        path=f"pages/{version_id}/{p_num}.txt",
                    )
                    await self.storage.put(
                        key=txt_key,
                        data=artifact.text_content.encode("utf-8"),
                        content_type="text/plain",
                    )
                    stored_keys.append(txt_key)

                # Insert page record in database
                await self.document_repo.create_page(
                    tenant_id=ctx.tenant_id,
                    version_id=version_id,
                    page_number=p_num,
                    page_sha256=artifact.page_sha256,
                    page_type=artifact.page_type.value,
                    image_key=storage_key.full_key,
                )

                # Record successful step in job_steps
                await self.document_repo.record_job_step(
                    tenant_id=ctx.tenant_id,
                    version_id=version_id,
                    page_number=p_num,
                    step="split_page",
                    status="completed",
                )
                processed_count += 1

            except PortError:
                # Permanent failure during page persistence
                await self._handle_permanent_failure(
                    ctx=ctx,
                    version_id=version_id,
                    p_num=p_num,
                    total_pages=total_pages,
                )
                raise
            except Exception:
                # Transient error: do not mark version failed, allow retry to resume
                raise

        return SplitExecutionResult(
            total_pages=total_pages,
            pages_processed=processed_count,
            skipped=False,
        )

    async def _handle_permanent_failure(
        self,
        ctx: TenantContext,
        version_id: UUID,
        p_num: int,
        total_pages: int,
    ) -> None:
        """Handle mid-run permanent failure: update status to failed and clean up storage images."""
        # 1. Update version to failed
        await self.document_repo.update_version_status(
            tenant_id=ctx.tenant_id,
            version_id=version_id,
            status="failed",
        )
        # 2. Record error in job_steps
        await self.document_repo.record_job_step(
            tenant_id=ctx.tenant_id,
            version_id=version_id,
            page_number=p_num,
            step="split_page",
            status="failed",
        )
        # 3. Clean up any stored images for this version (app_user has no DELETE on DB tables)
        for p in range(1, total_pages + 1):
            key = StorageKey(
                tenant_id=UUID(ctx.tenant_id),
                path=f"pages/{version_id}/{p}.png",
            )
            await self.storage.delete(key)
