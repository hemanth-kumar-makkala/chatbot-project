"""Integration tests for upload-time vision reading in the page pipeline (Task 2.5 Step 3).

Verifies:
1. Low-OCR page recovered by vision ends up needs_review=False with visual chunks only.
2. Re-run of a recovered page changes nothing (zero duplicate readings, zero duplicate chunks).
3. Image-heavy page with reliable text layer indexes BOTH text and visual chunks.
4. Scanned page with high OCR (>= 0.70) skips vision transcription.
5. Digital text pages skip vision transcription.
6. Empty vision reading flags page with vision_failed and creates 0 chunks.
7. Low vision confidence (< 0.70) flags page with vision_low_confidence and creates 0 chunks.
8. Missing vision confidence (None) flags page with vision_confidence_missing and creates 0 chunks.
9. ImageProcessor failure flags page with image_too_large and creates 0 chunks.
10. Transient vision error is re-raised for worker retry and page step is not marked complete.
"""

from __future__ import annotations

import io
from typing import Any
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import func, select, text

from app.adapters.chunking.mock import MockChunker
from app.adapters.db.postgres_document_repository import PostgresDocumentRepository
from app.adapters.embedding.mock import MockEmbeddingProvider
from app.adapters.images.pillow import PillowImageProcessor
from app.adapters.models.mock import MockModelProvider
from app.adapters.parsing.mock import MockDocumentParser
from app.adapters.storage.local import LocalStorageBackend
from app.core.ingestion.activation import VersionActivationService
from app.core.ingestion.pipeline import PagePipelineService
from app.db.models import AuditEvent, Chunk, Document, DocumentVersion, JobStep, Page, PageReading
from app.db.session import worker_tenant_session
from app.ports.errors import ImageProcessingError
from app.ports.types import (
    ModelRole,
    PageArtifact,
    ParsedPageContent,
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


def _create_sample_png_bytes(width: int = 100, height: int = 100, color: str = "white") -> bytes:
    """Generate minimal valid PNG bytes."""
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color=color)
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
async def seeded_tenant_and_user() -> tuple[UUID, UUID]:
    """Helper to seed a tenant and user for testing."""
    tenant_id = uuid4()
    user_id = uuid4()
    from app.db.session import unscoped_session

    async with unscoped_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, name) VALUES (:tid, 'Upload Vision Tenant')"),
            {"tid": tenant_id},
        )
        await session.execute(
            text("INSERT INTO users (id, email, hashed_password) VALUES (:uid, :email, 'hash')"),
            {"uid": user_id, "email": f"vision-test-{user_id.hex[:8]}@example.com"},
        )
    return tenant_id, user_id


class LowOcrParser(MockDocumentParser):
    """Parser returning low confidence OCR score."""

    def __init__(
        self, confidence: float = 0.45, text_content: str = "Unreadable blurry OCR text"
    ) -> None:
        super().__init__(default_confidence=confidence, default_text=text_content)
        self.confidence = confidence
        self.text_content = text_content

    async def extract(self, page: PageArtifact) -> ParsedPageContent:
        _ = page
        return ParsedPageContent(
            page_number=page.page_number,
            text=self.text_content,
            confidence=self.confidence,
        )


class HighOcrParser(MockDocumentParser):
    """Parser returning high confidence OCR score."""

    async def extract(self, page: PageArtifact) -> ParsedPageContent:
        _ = page
        return ParsedPageContent(
            page_number=page.page_number,
            text="High confidence OCR clinical text",
            confidence=0.95,
        )


@pytest.mark.asyncio
class TestUploadVisionIntegration:
    """Verifies upload-time vision integration in PagePipelineService."""

    async def test_low_ocr_page_recovered_by_vision_ends_needs_review_false_visual_chunks_only(
        self, seeded_tenant_and_user: tuple[UUID, UUID], tmp_path: Any
    ) -> None:
        """Low-OCR page recovered by vision ends up needs_review=false with visual chunks only."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Low OCR Recovery Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            page_id = await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_id,
                page_number=1,
                page_type="scanned",
            )

            # Store test image in storage
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

            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )

            # 1. Page flag cleared
            page_row = (await session.execute(select(Page).where(Page.id == page_id))).scalar_one()
            assert page_row.needs_review is False
            assert page_row.review_reason is None

            # 2. page_readings has exactly 1 row with source='vision_ingest'
            readings = (
                (
                    await session.execute(
                        select(PageReading).where(
                            PageReading.tenant_id == tenant_id, PageReading.page_id == page_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(readings) == 1
            assert readings[0].source == "vision_ingest"
            assert readings[0].text == "Clean vision transcription text."
            vision_reading_id = readings[0].id

            # 3. Chunks: all visual chunks with page_reading_id pointing to vision reading
            chunks = (
                (
                    await session.execute(
                        select(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.page_id == page_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(chunks) > 0
            for c in chunks:
                assert c.evidence_type == "visual"
                assert c.page_reading_id == vision_reading_id
                assert "Clean vision transcription text." in c.text

    async def test_rerun_of_recovered_page_changes_nothing(
        self, seeded_tenant_and_user: tuple[UUID, UUID], tmp_path: Any
    ) -> None:
        """Re-run of a recovered page changes nothing (zero duplicate readings/chunks)."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(
                session, tenant_id=tenant_id, title="Idempotent Vision Doc"
            )
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            page_id = await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_id,
                page_number=1,
                page_type="scanned",
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

            # First run
            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )
            r_count_1 = (
                await session.execute(
                    select(func.count(PageReading.id)).where(PageReading.page_id == page_id)
                )
            ).scalar_one()
            c_count_1 = (
                await session.execute(select(func.count(Chunk.id)).where(Chunk.page_id == page_id))
            ).scalar_one()

            # Second run
            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )
            r_count_2 = (
                await session.execute(
                    select(func.count(PageReading.id)).where(PageReading.page_id == page_id)
                )
            ).scalar_one()
            c_count_2 = (
                await session.execute(select(func.count(Chunk.id)).where(Chunk.page_id == page_id))
            ).scalar_one()

            assert r_count_2 == r_count_1 == 1
            assert c_count_2 == c_count_1 > 0

    async def test_image_heavy_with_reliable_text_indexes_both_text_and_visual(
        self, seeded_tenant_and_user: tuple[UUID, UUID], tmp_path: Any
    ) -> None:
        """Image-heavy page with reliable native text indexes BOTH text and visual chunks."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Image Heavy Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            page_id = await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_id,
                page_number=1,
                page_type="image_heavy",
            )

            storage = LocalStorageBackend(base_dir=str(tmp_path))
            storage_key = StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/p1.png")
            await storage.put(storage_key, _create_sample_png_bytes(), "image/png")

            repo = PostgresDocumentRepository(session)
            parser = HighOcrParser()
            chunker = MockChunker()
            embedder = MockEmbeddingProvider()
            image_processor = PillowImageProcessor()

            class HighConfidenceVisionProvider(MockModelProvider):
                async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReadingDTO:
                    _ = (role, image)
                    return PageReadingDTO(
                        reading="Diagram visual reading text.",
                        confidence=0.95,
                        model_id="mock-vision",
                        model_version="1.0",
                    )

            pipeline = PagePipelineService(
                repository=repo,
                parser=parser,
                chunker=chunker,
                embedding_provider=embedder,
                model_provider=HighConfidenceVisionProvider(),
                image_processor=image_processor,
                storage=storage,
            )

            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )

            chunks = (
                (await session.execute(select(Chunk).where(Chunk.page_id == page_id)))
                .scalars()
                .all()
            )
            evidence_types = {c.evidence_type for c in chunks}
            assert "text" in evidence_types
            assert "visual" in evidence_types

            # Visual chunks have page_reading_id; text chunks do not
            for c in chunks:
                if c.evidence_type == "visual":
                    assert c.page_reading_id is not None
                elif c.evidence_type == "text":
                    assert c.page_reading_id is None

    async def test_scanned_high_ocr_skips_vision(
        self, seeded_tenant_and_user: tuple[UUID, UUID], tmp_path: Any
    ) -> None:
        """Scanned page with high OCR (>= 0.70) skips vision transcription."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="High OCR Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            page_id = await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_id,
                page_number=1,
                page_type="scanned",
            )

            storage = LocalStorageBackend(base_dir=str(tmp_path))
            storage_key = StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/p1.png")
            await storage.put(storage_key, _create_sample_png_bytes(), "image/png")

            repo = PostgresDocumentRepository(session)
            parser = HighOcrParser()
            chunker = MockChunker()
            embedder = MockEmbeddingProvider()
            image_processor = PillowImageProcessor()
            vision = MockModelProvider()

            pipeline = PagePipelineService(
                repository=repo,
                parser=parser,
                chunker=chunker,
                embedding_provider=embedder,
                model_provider=vision,
                image_processor=image_processor,
                storage=storage,
            )

            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )

            # Vision did not run
            r_count = (
                await session.execute(
                    select(func.count(PageReading.id)).where(PageReading.page_id == page_id)
                )
            ).scalar_one()
            assert r_count == 0

            # Only text chunks created
            chunks = (
                (await session.execute(select(Chunk).where(Chunk.page_id == page_id)))
                .scalars()
                .all()
            )
            assert len(chunks) > 0
            for c in chunks:
                assert c.evidence_type == "text"
                assert c.page_reading_id is None

    async def test_digital_text_skips_vision(
        self, seeded_tenant_and_user: tuple[UUID, UUID]
    ) -> None:
        """Digital text pages skip vision transcription."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Digital Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            page_id = await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_id,
                page_number=1,
                page_type="digital_text",
            )

            repo = PostgresDocumentRepository(session)
            parser = MockDocumentParser(default_confidence=1.0)
            chunker = MockChunker()
            embedder = MockEmbeddingProvider()

            pipeline = PagePipelineService(
                repository=repo,
                parser=parser,
                chunker=chunker,
                embedding_provider=embedder,
                model_provider=MockModelProvider(),
                image_processor=PillowImageProcessor(),
            )

            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )

            r_count = (
                await session.execute(
                    select(func.count(PageReading.id)).where(PageReading.page_id == page_id)
                )
            ).scalar_one()
            assert r_count == 0

    async def test_vision_empty_reading_flags_vision_failed(
        self, seeded_tenant_and_user: tuple[UUID, UUID], tmp_path: Any
    ) -> None:
        """Empty vision reading flags page with vision_failed and creates 0 chunks."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Empty Vision Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            page_id = await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_id,
                page_number=1,
                page_type="scanned",
            )

            storage = LocalStorageBackend(base_dir=str(tmp_path))
            storage_key = StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/p1.png")
            await storage.put(storage_key, _create_sample_png_bytes(), "image/png")

            class EmptyVisionProvider(MockModelProvider):
                async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReadingDTO:
                    _ = (role, image)
                    return PageReadingDTO(
                        reading="   ",
                        confidence=0.99,
                        model_id="mock-vision",
                        model_version="1.0",
                    )

            repo = PostgresDocumentRepository(session)
            pipeline = PagePipelineService(
                repository=repo,
                parser=LowOcrParser(confidence=0.45),
                chunker=MockChunker(),
                embedding_provider=MockEmbeddingProvider(),
                model_provider=EmptyVisionProvider(),
                image_processor=PillowImageProcessor(),
                storage=storage,
            )

            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )

            page_row = (await session.execute(select(Page).where(Page.id == page_id))).scalar_one()
            assert page_row.needs_review is True
            assert page_row.review_reason == "vision_failed"

            c_count = (
                await session.execute(select(func.count(Chunk.id)).where(Chunk.page_id == page_id))
            ).scalar_one()
            assert c_count == 0

    async def test_vision_low_confidence_flags_vision_low_confidence(
        self, seeded_tenant_and_user: tuple[UUID, UUID], tmp_path: Any
    ) -> None:
        """Low vision confidence (< 0.70) flags page with vision_low_confidence and 0 chunks."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Low Vision Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            page_id = await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_id,
                page_number=1,
                page_type="scanned",
            )

            storage = LocalStorageBackend(base_dir=str(tmp_path))
            storage_key = StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/p1.png")
            await storage.put(storage_key, _create_sample_png_bytes(), "image/png")

            class LowConfVisionProvider(MockModelProvider):
                async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReadingDTO:
                    _ = (role, image)
                    return PageReadingDTO(
                        reading="Unreliable reading text",
                        confidence=0.55,
                        model_id="mock-vision",
                        model_version="1.0",
                    )

            repo = PostgresDocumentRepository(session)
            pipeline = PagePipelineService(
                repository=repo,
                parser=LowOcrParser(confidence=0.45),
                chunker=MockChunker(),
                embedding_provider=MockEmbeddingProvider(),
                model_provider=LowConfVisionProvider(),
                image_processor=PillowImageProcessor(),
                storage=storage,
            )

            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )

            page_row = (await session.execute(select(Page).where(Page.id == page_id))).scalar_one()
            assert page_row.needs_review is True
            assert page_row.review_reason == "vision_low_confidence"

            c_count = (
                await session.execute(select(func.count(Chunk.id)).where(Chunk.page_id == page_id))
            ).scalar_one()
            assert c_count == 0

    async def test_vision_missing_confidence_flags_vision_confidence_missing(
        self, seeded_tenant_and_user: tuple[UUID, UUID], tmp_path: Any
    ) -> None:
        """Missing vision confidence (None) flags vision_confidence_missing and 0 chunks."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(
                session, tenant_id=tenant_id, title="Missing Conf Vision Doc"
            )
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            page_id = await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_id,
                page_number=1,
                page_type="scanned",
            )

            storage = LocalStorageBackend(base_dir=str(tmp_path))
            storage_key = StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/p1.png")
            await storage.put(storage_key, _create_sample_png_bytes(), "image/png")

            class MissingConfVisionProvider(MockModelProvider):
                async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReadingDTO:
                    _ = (role, image)
                    return PageReadingDTO(
                        reading="Missing confidence text",
                        confidence=None,
                        model_id="mock-vision",
                        model_version="1.0",
                    )

            repo = PostgresDocumentRepository(session)
            pipeline = PagePipelineService(
                repository=repo,
                parser=LowOcrParser(confidence=0.45),
                chunker=MockChunker(),
                embedding_provider=MockEmbeddingProvider(),
                model_provider=MissingConfVisionProvider(),
                image_processor=PillowImageProcessor(),
                storage=storage,
            )

            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )

            page_row = (await session.execute(select(Page).where(Page.id == page_id))).scalar_one()
            assert page_row.needs_review is True
            assert page_row.review_reason == "vision_confidence_missing"

            c_count = (
                await session.execute(select(func.count(Chunk.id)).where(Chunk.page_id == page_id))
            ).scalar_one()
            assert c_count == 0

    async def test_image_processor_too_large_flags_image_too_large(
        self, seeded_tenant_and_user: tuple[UUID, UUID], tmp_path: Any
    ) -> None:
        """ImageProcessor failure flags page with image_too_large and creates 0 chunks."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Corrupt Image Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            page_id = await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_id,
                page_number=1,
                page_type="scanned",
            )

            storage = LocalStorageBackend(base_dir=str(tmp_path))
            storage_key = StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/p1.png")
            await storage.put(storage_key, _create_sample_png_bytes(), "image/png")

            class FailingImageProcessor(PillowImageProcessor):
                def prepare_image_for_vision(self, *args: Any, **kwargs: Any) -> bytes:
                    _ = (args, kwargs)
                    raise ImageProcessingError("Image dimensions exceed 1024 or max bytes 4MB")

            repo = PostgresDocumentRepository(session)
            pipeline = PagePipelineService(
                repository=repo,
                parser=LowOcrParser(confidence=0.45),
                chunker=MockChunker(),
                embedding_provider=MockEmbeddingProvider(),
                model_provider=MockModelProvider(),
                image_processor=FailingImageProcessor(),
                storage=storage,
            )

            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )

            page_row = (await session.execute(select(Page).where(Page.id == page_id))).scalar_one()
            assert page_row.needs_review is True
            assert page_row.review_reason == "image_too_large"

            c_count = (
                await session.execute(select(func.count(Chunk.id)).where(Chunk.page_id == page_id))
            ).scalar_one()
            assert c_count == 0

    async def test_transient_vision_error_follows_retry_strategy(
        self, seeded_tenant_and_user: tuple[UUID, UUID], tmp_path: Any
    ) -> None:
        """Transient vision error is re-raised and page step is not marked complete."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Transient Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            _ = await seed_page(
                session,
                tenant_id=tenant_id,
                ver_id=ver_id,
                page_number=1,
                page_type="scanned",
            )

            storage = LocalStorageBackend(base_dir=str(tmp_path))
            storage_key = StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/p1.png")
            await storage.put(storage_key, _create_sample_png_bytes(), "image/png")

            class TransientVisionProvider(MockModelProvider):
                async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReadingDTO:
                    _ = (role, image)
                    raise ConnectionError(
                        "Transient network failure reaching vision model endpoint"
                    )

            repo = PostgresDocumentRepository(session)
            pipeline = PagePipelineService(
                repository=repo,
                parser=LowOcrParser(confidence=0.45),
                chunker=MockChunker(),
                embedding_provider=MockEmbeddingProvider(),
                model_provider=TransientVisionProvider(),
                image_processor=PillowImageProcessor(),
                storage=storage,
            )

            with pytest.raises(ConnectionError):
                await pipeline.process_page(
                    tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
                )

            # Step must NOT be completed in job_steps
            uncompleted = await repo.get_uncompleted_page_numbers(
                tenant_id=str(tenant_id), version_id=ver_id, step="chunking"
            )
            assert 1 in uncompleted


@pytest.mark.asyncio
async def test_upload_vision_zero_text_leakage_in_job_steps_and_audit(
    seeded_tenant_and_user: tuple[UUID, UUID],
    tmp_path: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Vision transcription text must never leak into job_steps, audit_events,
    or operational logs."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))
    synthetic_marker = "SYNTHETIC_MARKER_NO_LEAK_SECURE_ALPHA_998877"

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Zero Leakage Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        page_id = await seed_page(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            page_number=1,
            page_type="scanned",
        )

        storage = LocalStorageBackend(base_dir=str(tmp_path))
        storage_key = StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/p1.png")
        await storage.put(storage_key, _create_sample_png_bytes(), "image/png")

        class MarkerVisionProvider(MockModelProvider):
            async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReadingDTO:
                _ = (role, image)
                return PageReadingDTO(
                    reading=f"Here is confidential transcribed data: {synthetic_marker}",
                    confidence=0.96,
                    model_id="mock-vision-v1",
                    model_version="1.0.0",
                )

        repo = PostgresDocumentRepository(session)
        pipeline = PagePipelineService(
            repository=repo,
            parser=LowOcrParser(confidence=0.45),
            chunker=MockChunker(),
            embedding_provider=MockEmbeddingProvider(),
            model_provider=MarkerVisionProvider(),
            image_processor=PillowImageProcessor(),
            storage=storage,
        )

        with caplog.at_level("DEBUG"):
            await pipeline.process_page(
                tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
            )

        # 1. Operational logs must never contain the marker text
        assert synthetic_marker not in caplog.text

        # 2. job_steps table must never contain the marker text
        job_steps_res = await session.execute(
            select(JobStep).where(
                JobStep.tenant_id == tenant_id, JobStep.document_version_id == ver_id
            )
        )
        job_steps = job_steps_res.scalars().all()
        assert len(job_steps) > 0
        for step in job_steps:
            row_content = f"{step.step} {step.status} {step.processing_version}"
            assert synthetic_marker not in row_content

        # 3. audit_events table must never contain the marker text
        audit_res = await session.execute(
            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
        )
        audit_events = audit_res.scalars().all()
        for audit in audit_events:
            audit_str = f"{audit.action} {audit.event_type} {audit.resource_id}"
            assert synthetic_marker not in audit_str

        # 4. Invariant: the marker WAS transcribed and stored in page_readings
        readings_res = await session.execute(
            select(PageReading).where(
                PageReading.tenant_id == tenant_id, PageReading.page_id == page_id
            )
        )
        readings = readings_res.scalars().all()
        assert len(readings) == 1
        assert synthetic_marker in readings[0].text


@pytest.mark.asyncio
async def test_upload_vision_permanent_error_flags_review_and_activates_branch_b(
    seeded_tenant_and_user: tuple[UUID, UUID],
    tmp_path: Any,
) -> None:
    """Permanent vision failure on a first version activates Branch B:
    version is needs_review, clean pages active, flagged page has no active chunks."""
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        # First version of document: active_version_id is None
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Branch B Vision Test Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        # Page 1: clean digital page
        page_1_id = await seed_page(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            page_number=1,
            page_type="digital_text",
        )
        # Page 2: scanned page with low OCR that will suffer permanent vision error
        page_2_id = await seed_page(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            page_number=2,
            page_type="scanned",
        )

        storage = LocalStorageBackend(base_dir=str(tmp_path))
        storage_key_2 = StorageKey(tenant_id=tenant_id, path=f"documents/{ver_id}/p2.png")
        await storage.put(storage_key_2, _create_sample_png_bytes(), "image/png")

        class PermanentVisionErrorProvider(MockModelProvider):
            async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReadingDTO:
                _ = (role, image)
                raise RuntimeError("Permanent upstream vision model failure: 400 Bad Request")

        class MultiPageParser(MockDocumentParser):
            async def extract(self, page: PageArtifact) -> ParsedPageContent:
                if page.page_number == 1:
                    return ParsedPageContent(
                        page_number=1,
                        text="High confidence digital text for page 1.",
                        confidence=0.98,
                    )
                return ParsedPageContent(
                    page_number=2,
                    text="Low confidence scanned text for page 2.",
                    confidence=0.35,
                )

        repo = PostgresDocumentRepository(session)
        pipeline = PagePipelineService(
            repository=repo,
            parser=MultiPageParser(),
            chunker=MockChunker(),
            embedding_provider=MockEmbeddingProvider(),
            model_provider=PermanentVisionErrorProvider(),
            image_processor=PillowImageProcessor(),
            storage=storage,
        )

        # Process clean Page 1
        await pipeline.process_page(
            tenant_id=str(tenant_id), document_version_id=ver_id, page_number=1
        )
        # Process failing Page 2
        await pipeline.process_page(
            tenant_id=str(tenant_id), document_version_id=ver_id, page_number=2
        )

        # Verify page states
        p1 = await repo.get_page(str(tenant_id), ver_id, 1)
        p2 = await repo.get_page(str(tenant_id), ver_id, 2)
        assert p1 is not None and not p1.needs_review
        assert p2 is not None and p2.needs_review is True and p2.review_reason == "vision_failed"

        # Now trigger activation service
        activation_service = VersionActivationService(repository=repo)
        activated = await activation_service.check_and_activate_if_complete(
            tenant_id=str(tenant_id), document_version_id=ver_id
        )
        assert activated is True

        # Assert document version status is needs_review
        doc_stmt = select(Document).where(Document.tenant_id == tenant_id, Document.id == doc_id)
        ver_stmt = select(DocumentVersion).where(
            DocumentVersion.tenant_id == tenant_id, DocumentVersion.id == ver_id
        )
        doc_row = (await session.execute(doc_stmt)).scalars().one()
        ver_row = (await session.execute(ver_stmt)).scalars().one()

        assert ver_row.status == "needs_review"
        assert doc_row.active_version_id == ver_id

        # Assert clean page chunks are active
        p1_chunks = (
            (
                await session.execute(
                    select(Chunk).where(
                        Chunk.tenant_id == tenant_id,
                        Chunk.document_version_id == ver_id,
                        Chunk.page_id == page_1_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(p1_chunks) > 0
        assert all(c.is_active is True for c in p1_chunks)

        # Assert flagged page has no active chunks
        p2_active_chunks = (
            (
                await session.execute(
                    select(Chunk).where(
                        Chunk.tenant_id == tenant_id,
                        Chunk.document_version_id == ver_id,
                        Chunk.page_id == page_2_id,
                        Chunk.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(p2_active_chunks) == 0
