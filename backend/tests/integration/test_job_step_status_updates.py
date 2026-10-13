"""Integration tests verifying record_job_step updates status on conflict."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.adapters.chunking.mock import MockChunker
from app.adapters.db.postgres_document_repository import PostgresDocumentRepository
from app.adapters.embedding.mock import MockEmbeddingProvider
from app.adapters.images.pillow import PillowImageProcessor
from app.adapters.models.mock import MockModelProvider
from app.adapters.storage.local import LocalStorageBackend
from app.core.ingestion.pipeline import PagePipelineService
from app.db.models import JobStep
from app.db.session import unscoped_session, worker_tenant_session
from app.ports.types import (
    ModelRole,
    StorageKey,
    WorkerContext,
)
from app.ports.types import (
    PageReading as PageReadingDTO,
)
from tests.fixtures.database_seeding import (
    seed_document,
    seed_page,
    seed_version,
)
from tests.integration.test_upload_vision import (
    LowOcrParser,
    _create_sample_png_bytes,
)


@pytest.fixture
async def seeded_tenant_and_user() -> tuple[UUID, UUID]:
    """Helper to seed a tenant and user for testing."""
    tenant_id = uuid4()
    user_id = uuid4()

    async with unscoped_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, name) VALUES (:tid, 'Job Step Update Tenant')"),
            {"tid": tenant_id},
        )
        await session.execute(
            text("INSERT INTO users (id, email, hashed_password) VALUES (:uid, :email, 'hash')"),
            {"uid": user_id, "email": f"jobstep-test-{user_id.hex[:8]}@example.com"},
        )
    return tenant_id, user_id


@pytest.mark.asyncio
async def test_record_job_step_vision_processing_to_completed_updates_row(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """record_job_step(step='vision', status='processing') then 'completed' ends 'completed'."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Vision Test Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)

        repo = PostgresDocumentRepository(session)

        # First record: processing
        await repo.record_job_step(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
            step="vision",
            status="processing",
            processing_version="v1",
        )

        # Second record on same page and processing_version: completed
        await repo.record_job_step(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
            step="vision",
            status="completed",
            processing_version="v1",
        )

        rows = (
            (
                await session.execute(
                    select(JobStep).where(
                        JobStep.tenant_id == tenant_id,
                        JobStep.document_version_id == ver_id,
                        JobStep.page_number == 1,
                        JobStep.step == "vision",
                    )
                )
            )
            .scalars()
            .all()
        )

        assert len(rows) == 1, f"Expected exactly 1 row, got {len(rows)}"
        assert rows[0].status == "completed", f"Expected 'completed', got '{rows[0].status}'"


@pytest.mark.asyncio
async def test_record_job_step_chunking_failed_to_completed_updates_row(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """record_job_step(step='chunking', status='failed') then 'completed' ends 'completed'."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Chunking Test Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)

        repo = PostgresDocumentRepository(session)

        # First record: failed
        await repo.record_job_step(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
            step="chunking",
            status="failed",
            processing_version="v1",
        )

        # Second record on same page and processing_version: completed
        await repo.record_job_step(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
            step="chunking",
            status="completed",
            processing_version="v1",
        )

        rows = (
            (
                await session.execute(
                    select(JobStep).where(
                        JobStep.tenant_id == tenant_id,
                        JobStep.document_version_id == ver_id,
                        JobStep.page_number == 1,
                        JobStep.step == "chunking",
                    )
                )
            )
            .scalars()
            .all()
        )

        assert len(rows) == 1, f"Expected exactly 1 row, got {len(rows)}"
        assert rows[0].status == "completed", f"Expected 'completed', got '{rows[0].status}'"


@pytest.mark.asyncio
async def test_page_triggering_vision_can_be_accepted_after_processing(
    seeded_tenant_and_user: tuple[UUID, UUID],
    tmp_path: Any,
) -> None:
    """Regression: page triggering vision finishes and accept does not return 409."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Regression Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        await seed_page(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            page_number=1,
            page_type="scanned",
            needs_review=True,
            review_reason="low_ocr_confidence",
        )

        storage = LocalStorageBackend(base_dir=str(tmp_path))
        storage_key = StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/p1.png")
        await storage.put(storage_key, _create_sample_png_bytes(), "image/png")

        repo = PostgresDocumentRepository(session)
        parser = LowOcrParser(confidence=0.45)
        chunker = MockChunker()
        embedder = MockEmbeddingProvider()
        image_processor = PillowImageProcessor()

        class RecoveringVisionProvider(MockModelProvider):
            async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReadingDTO:
                _ = (role, image)
                return PageReadingDTO(
                    reading="Clean vision transcription text.",
                    confidence=0.95,
                    model_id="mock-vision",
                    model_version="1.0",
                )

        pipeline = PagePipelineService(
            repository=repo,
            parser=parser,
            chunker=chunker,
            embedding_provider=embedder,
            model_provider=RecoveringVisionProvider(),
            image_processor=image_processor,
            storage=storage,
        )

        # Run page through real pipeline path
        await pipeline.process_page(
            tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
        )

        # If the page is not flagged after processing, flag it in test setup first
        await repo.flag_page_for_review(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
            reason="low_ocr_confidence",
        )

        # Call accept on that page - must not return 409 PAGE_JOB_ALREADY_ACTIVE
        page_rec, auto_promoted = await repo.accept_page_review(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
        )
        assert page_rec.needs_review is False
