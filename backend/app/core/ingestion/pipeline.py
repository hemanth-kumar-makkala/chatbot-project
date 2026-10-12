"""Ingestion pipeline orchestration service for per-page execution (Steps 4 through 8).

Handles:
- Classification (Step 4)
- Tri-path extraction / readings (Step 5)
- Structured tables (Step 6)
- Text chunking (Step 7)
- Vector embedding (Step 8)
- Idempotency via ON CONFLICT
- Transient error retry classification
- Zero direct database or ORM dependencies (ADR-0003 and boundary checker).
"""

from __future__ import annotations

import socket
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.ingestion.review_policy import evaluate_page_review
from app.core.ingestion.vision_policy import (
    evaluate_vision_reading_outcome,
    select_readings_for_chunking,
    should_trigger_vision_read,
)
from app.ports.chunker import Chunker
from app.ports.document_parser import DocumentParser
from app.ports.document_repository import DocumentRepository
from app.ports.embedding_provider import EmbeddingProvider
from app.ports.errors import ImageProcessingError
from app.ports.image_processor import ImageProcessor
from app.ports.model_provider import ModelProvider
from app.ports.storage import StorageBackend
from app.ports.types import (
    ModelRole,
    PageArtifact,
    PageType,
    ReviewReason,
    StorageKey,
)
from app.ports.types import (
    PageReading as PageReadingDTO,
)


def is_retryable_error(exc: Exception) -> bool:
    """Classify whether an exception is transient (retryable) or permanent.

    Includes network timeouts, connection drops, PostgreSQL deadlock detection (40P01),
    and transaction serialization failures (40001).
    """
    if isinstance(exc, socket.error | TimeoutError | ConnectionError | OSError):
        return True
    if getattr(exc, "connection_invalidated", False):
        return True
    if getattr(exc, "retryable", False) is True:
        return True

    # Check PostgreSQL sqlstate / pgcode (direct driver exception or DBAPIError)
    orig = getattr(exc, "orig", exc)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if code in ("40P01", "40001"):
        return True

    exc_name = type(exc).__name__
    orig_name = type(orig).__name__
    if any(name in ("DeadlockDetected", "SerializationFailure") for name in (exc_name, orig_name)):
        return True

    return exc_name in ("OperationalError", "TransientError", "DatabaseError")


class PagePipelineService:
    """Coordinates execution of Steps 4 through 8 for a given page."""

    def __init__(
        self,
        repository: DocumentRepository,
        parser: DocumentParser,
        chunker: Chunker,
        embedding_provider: EmbeddingProvider,
        model_provider: ModelProvider | None = None,
        image_processor: ImageProcessor | None = None,
        storage: StorageBackend | None = None,
        max_image_dimension: int = 1024,
        max_image_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self.repository = repository
        self.parser = parser
        self.chunker = chunker
        self.embedding_provider = embedding_provider
        self.model_provider = model_provider
        self.image_processor = image_processor
        self.storage = storage
        self.max_image_dimension = max_image_dimension
        self.max_image_bytes = max_image_bytes

    async def process_page(
        self,
        tenant_id: str,
        document_version_id: UUID,
        page_number: int,
    ) -> None:
        """Execute Steps 4 through 8 idempotently for a single page.

        Order per page:
        1. Docling extract, then the review policy (may flag low_ocr_confidence).
        2. If vision_policy says transcribe: prepare the image via ImageProcessor,
           call transcribe_page, and save it with create_page_reading (source vision_ingest).
        3. Apply the vision outcome: success clears only low_ocr_confidence.
           Failure, low or missing confidence, or image too large flags with matching reason.
        4. Chunk using reading selection rule: reliable text as text, vision reading as visual
           with page_reading_id, and low-OCR text never chunked.
        5. Only then record page step as completed.
        """
        # Retrieve existing page record
        page_rec = await self.repository.get_page(
            tenant_id=tenant_id,
            version_id=document_version_id,
            page_number=page_number,
        )
        page_type = page_rec.page_type if page_rec is not None else PageType.DIGITAL_TEXT
        page_id = page_rec.id if page_rec is not None else None

        # Fetch image bytes if available in storage
        image_bytes = b""
        if page_rec and self.storage:
            try:
                path = page_rec.image_key
                if path.startswith(f"{tenant_id}/"):
                    path = path[len(f"{tenant_id}/") :]
                storage_key = StorageKey(tenant_id=UUID(tenant_id), path=path)
                image_bytes = await self.storage.get(storage_key)
            except Exception:
                image_bytes = b""

        # 1. Docling extract
        try:
            artifact = PageArtifact(
                page_number=page_number,
                image_bytes=image_bytes,
                page_type=page_type,
            )
            parsed = await self.parser.extract(artifact)
        except Exception as exc:
            if is_retryable_error(exc):
                raise
            # Permanent failure: flag page for review and record failed step
            await self.repository.flag_page_for_review(
                tenant_id=tenant_id,
                version_id=document_version_id,
                page_number=page_number,
                reason="processing_failed",
            )
            await self.repository.record_job_step(
                tenant_id=tenant_id,
                version_id=document_version_id,
                page_number=page_number,
                step="chunking",
                status="failed",
            )
            return

        # 2. Review policy (may flag low_ocr_confidence)
        needs_review, review_reason = evaluate_page_review(
            page_type=page_type,
            ocr_score=parsed.confidence,
        )
        if needs_review and review_reason:
            await self.repository.flag_page_for_review(
                tenant_id=tenant_id,
                version_id=document_version_id,
                page_number=page_number,
                reason=review_reason,
            )
            prior_needs_review = True
            prior_review_reason: str | None = review_reason
        else:
            prior_needs_review = page_rec.needs_review if page_rec else False
            prior_review_reason = page_rec.review_reason if page_rec else None

        # 3. If vision_policy says transcribe: prepare image, call transcribe_page, save reading
        vision_reading_dto: PageReadingDTO | None = None
        vision_reading_id: UUID | None = None
        should_transcribe = should_trigger_vision_read(
            page_type=page_type,
            ocr_score=parsed.confidence,
        )

        if should_transcribe and self.model_provider is not None:
            prepared_image: bytes | None = None
            try:
                if self.image_processor is not None and image_bytes:
                    prepared_image = self.image_processor.prepare_image_for_vision(
                        image_bytes=image_bytes,
                        max_dimension=self.max_image_dimension,
                        max_bytes=self.max_image_bytes,
                    )
                else:
                    prepared_image = image_bytes
            except ImageProcessingError:
                await self.repository.flag_page_for_review(
                    tenant_id=tenant_id,
                    version_id=document_version_id,
                    page_number=page_number,
                    reason="image_too_large",
                )
                await self.repository.record_job_step(
                    tenant_id=tenant_id,
                    version_id=document_version_id,
                    page_number=page_number,
                    step="chunking",
                    status="completed",
                )
                return

            await self.repository.record_job_step(
                tenant_id=tenant_id,
                version_id=document_version_id,
                page_number=page_number,
                step="vision",
                status="processing",
            )

            try:
                vision_reading_dto = await self.model_provider.transcribe_page(
                    role=ModelRole.VISION,
                    image=prepared_image or b"",
                )
            except Exception as exc:
                if is_retryable_error(exc):
                    raise
                vision_reading_dto = None

            await self.repository.record_job_step(
                tenant_id=tenant_id,
                version_id=document_version_id,
                page_number=page_number,
                step="vision",
                status="completed" if vision_reading_dto is not None else "failed",
            )

            if (
                vision_reading_dto is not None
                and page_id is not None
                and vision_reading_dto.reading.strip()
            ):
                vision_reading_id = await self.repository.create_page_reading(
                    tenant_id=tenant_id,
                    page_id=page_id,
                    source="vision_ingest",
                    text=vision_reading_dto.reading,
                    model_id=vision_reading_dto.model_id,
                    model_version=vision_reading_dto.model_version,
                )

            # 4. Apply vision outcome: success clears only low_ocr_confidence
            outcome = evaluate_vision_reading_outcome(
                page_type=page_type,
                vision_reading=vision_reading_dto,
                prior_needs_review=prior_needs_review,
                prior_review_reason=prior_review_reason,
            )
            if outcome.needs_review:
                assert outcome.review_reason is not None
                await self.repository.flag_page_for_review(
                    tenant_id=tenant_id,
                    version_id=document_version_id,
                    page_number=page_number,
                    reason=cast(ReviewReason, outcome.review_reason),
                )
                is_indexable = False
            else:
                if prior_needs_review:
                    await self.repository.clear_page_review_flag(
                        tenant_id=tenant_id,
                        version_id=document_version_id,
                        page_number=page_number,
                    )
                is_indexable = True
        else:
            is_indexable = not prior_needs_review

        # 5. Chunk using reading selection rule
        if not is_indexable:
            await self.repository.record_job_step(
                tenant_id=tenant_id,
                version_id=document_version_id,
                page_number=page_number,
                step="chunking",
                status="completed",
            )
            return

        selections = select_readings_for_chunking(
            page_type=page_type,
            ocr_text=parsed.text,
            ocr_score=parsed.confidence,
            vision_reading=vision_reading_dto.reading if vision_reading_dto else None,
            vision_reading_id=vision_reading_id,
        )

        chunk_records: list[dict[str, Any]] = []
        for sel in selections:
            chunks = self.chunker.chunk_page(
                document_title="Document",
                page_number=page_number,
                readings=[sel.text],
                language="english",
            )
            if not chunks:
                continue
            chunk_texts = [c.text for c in chunks]
            embeddings = await self.embedding_provider.embed(chunk_texts)
            for i, c in enumerate(chunks):
                chunk_uuid = uuid5(
                    NAMESPACE_URL,
                    f"{tenant_id}:{document_version_id}:{page_number}:{sel.evidence_type}:{i}",
                )
                embedding_val = embeddings[i] if i < len(embeddings) else None
                chunk_records.append(
                    {
                        "id": chunk_uuid,
                        "document_version_id": document_version_id,
                        "page_number": page_number,
                        "page_id": page_id,
                        "text": c.text,
                        "context_prefix": c.context_prefix,
                        "language": c.language,
                        "ts_config": c.ts_config,
                        "embedding": embedding_val,
                        "is_active": False,
                        "chunker_version": "v1",
                        "embedding_model": self.embedding_provider.model_id,
                        "embedding_version": self.embedding_provider.version,
                        "evidence_type": sel.evidence_type,
                        "page_reading_id": sel.page_reading_id,
                    }
                )

        if chunk_records:
            await self.repository.create_chunks_batch(
                tenant_id=tenant_id,
                records=chunk_records,
            )

        # 6. Only then record the page step as completed
        await self.repository.record_job_step(
            tenant_id=tenant_id,
            version_id=document_version_id,
            page_number=page_number,
            step="chunking",
            status="completed",
        )
