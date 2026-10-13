"""Integration tests for database-level guard on clear_page_review_flag."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.adapters.db.postgres_document_repository import PostgresDocumentRepository
from app.db.session import unscoped_session, worker_tenant_session
from app.ports.errors import InvalidPageStateError
from app.ports.types import WorkerContext
from tests.fixtures.database_seeding import (
    seed_document,
    seed_page,
    seed_version,
)


@pytest.fixture
async def seeded_tenant_and_user() -> tuple[UUID, UUID]:
    """Helper to seed a tenant and user for testing."""
    tenant_id = uuid4()
    user_id = uuid4()

    async with unscoped_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, name) VALUES (:tid, 'Guard Test Tenant')"),
            {"tid": tenant_id},
        )
        await session.execute(
            text("INSERT INTO users (id, email, hashed_password) VALUES (:uid, :email, 'hash')"),
            {"uid": user_id, "email": f"guard-test-{user_id.hex[:8]}@example.com"},
        )
    return tenant_id, user_id


@pytest.mark.asyncio
async def test_clear_page_review_flag_low_ocr_confidence_success(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """Page flagged low_ocr_confidence:
    clear_page_review_flag sets needs_review False and review_reason NULL.
    """
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Test Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        page_id = await seed_page(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            page_number=1,
            needs_review=True,
            review_reason="low_ocr_confidence",
        )
        repo = PostgresDocumentRepository(session)

        await repo.clear_page_review_flag(
            tenant_id=str(tenant_id),
            version_id=ver_id,
            page_number=1,
        )

        res = await session.execute(
            text("SELECT needs_review, review_reason FROM pages WHERE id = :pid"),
            {"pid": page_id},
        )
        row = res.one()
        assert row.needs_review is False
        assert row.review_reason is None


@pytest.mark.asyncio
async def test_clear_page_review_flag_legacy_unspecified_rejected(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """Page flagged legacy_unspecified:
    clear_page_review_flag raises typed error and row is unchanged.
    """
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Test Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        page_id = await seed_page(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            page_number=1,
            needs_review=True,
            review_reason="legacy_unspecified",
        )
        repo = PostgresDocumentRepository(session)

        with pytest.raises(InvalidPageStateError):
            await repo.clear_page_review_flag(
                tenant_id=str(tenant_id),
                version_id=ver_id,
                page_number=1,
            )

        res = await session.execute(
            text("SELECT needs_review, review_reason FROM pages WHERE id = :pid"),
            {"pid": page_id},
        )
        row = res.one()
        assert row.needs_review is True
        assert row.review_reason == "legacy_unspecified"


@pytest.mark.asyncio
async def test_clear_page_review_flag_vision_low_confidence_rejected(
    seeded_tenant_and_user: tuple[UUID, UUID],
) -> None:
    """Page flagged vision_low_confidence:
    clear_page_review_flag raises typed error and row is unchanged.
    """
    tenant_id, user_id = seeded_tenant_and_user
    worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

    async with worker_tenant_session(worker_ctx) as session:
        doc_id = await seed_document(session, tenant_id=tenant_id, title="Test Doc")
        ver_id = await seed_version(
            session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
        )
        page_id = await seed_page(
            session,
            tenant_id=tenant_id,
            ver_id=ver_id,
            page_number=1,
            needs_review=True,
            review_reason="vision_low_confidence",
        )
        repo = PostgresDocumentRepository(session)

        with pytest.raises(InvalidPageStateError):
            await repo.clear_page_review_flag(
                tenant_id=str(tenant_id),
                version_id=ver_id,
                page_number=1,
            )

        res = await session.execute(
            text("SELECT needs_review, review_reason FROM pages WHERE id = :pid"),
            {"pid": page_id},
        )
        row = res.one()
        assert row.needs_review is True
        assert row.review_reason == "vision_low_confidence"
