"""Integration tests for version activation policies and concurrency serialization."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.adapters.db.postgres_document_repository import PostgresDocumentRepository
from app.core.ingestion.activation import VersionActivationService
from app.db.models import Chunk, Document, DocumentVersion
from app.db.session import worker_tenant_session
from app.ports.types import WorkerContext
from tests.fixtures.database_seeding import (
    seed_chunk,
    seed_document,
    seed_job,
    seed_page,
    seed_version,
)


@pytest.fixture
async def seeded_tenant_and_user() -> tuple[UUID, UUID]:
    """Helper to seed a tenant and user for testing."""
    tenant_id = uuid4()
    user_id = uuid4()
    from app.db.session import unscoped_session

    async with unscoped_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, name) VALUES (:tid, 'Test Tenant')"),
            {"tid": tenant_id},
        )
        await session.execute(
            text("INSERT INTO users (id, email, hashed_password) VALUES (:uid, :email, 'hash')"),
            {"uid": user_id, "email": f"worker-test-{user_id.hex[:8]}@example.com"},
        )
    return tenant_id, user_id


@pytest.mark.asyncio
class TestVersionActivationPolicies:
    """Verifies MECE activation policies (Branches A, B, C) under review conditions."""

    async def test_activation_branch_c_no_flagged_pages(
        self, seeded_tenant_and_user: tuple[UUID, UUID]
    ) -> None:
        """Branch C: Clean document version activates, retires previous, flips chunks."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            # 1. Setup existing active version v1
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Branch C Policy Doc")
            v1_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="ready"
            )
            p1_v1 = await seed_page(session, tenant_id=tenant_id, ver_id=v1_id, page_number=1)
            await seed_chunk(
                session, tenant_id=tenant_id, page_id=p1_v1, ver_id=v1_id, is_active=True
            )

            # Set active_version_id to v1
            await session.execute(
                text("UPDATE documents SET active_version_id = :vid WHERE id = :did"),
                {"vid": v1_id, "did": doc_id},
            )

            # 2. Setup new version v2 with all clean pages (needs_review=False)
            v2_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            p1_v2 = await seed_page(session, tenant_id=tenant_id, ver_id=v2_id, page_number=1)
            p2_v2 = await seed_page(session, tenant_id=tenant_id, ver_id=v2_id, page_number=2)
            await seed_chunk(
                session, tenant_id=tenant_id, page_id=p1_v2, ver_id=v2_id, is_active=False
            )
            await seed_chunk(
                session, tenant_id=tenant_id, page_id=p2_v2, ver_id=v2_id, is_active=False
            )

            # 3. Execute activation
            repo = PostgresDocumentRepository(session)
            service = VersionActivationService(repo)
            final_status = await service.activate_version(
                tenant_id=str(tenant_id), document_version_id=v2_id
            )

            assert final_status == "ready"

            # 4. Assert v2 activated and v1 retired
            doc_row = (
                await session.execute(select(Document).where(Document.id == doc_id))
            ).scalar_one()
            assert doc_row.active_version_id == v2_id

            v2_row = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == v2_id))
            ).scalar_one()
            assert v2_row.status == "ready"

            v1_row = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == v1_id))
            ).scalar_one()
            assert v1_row.status == "retired"

            # 5. Assert chunk is_active states flipped atomically
            v2_chunks = (
                (await session.execute(select(Chunk).where(Chunk.document_version_id == v2_id)))
                .scalars()
                .all()
            )
            assert all(c.is_active for c in v2_chunks)

            v1_chunks = (
                (await session.execute(select(Chunk).where(Chunk.document_version_id == v1_id)))
                .scalars()
                .all()
            )
            assert not any(c.is_active for c in v1_chunks)

    async def test_activation_branch_a_existing_active_version_with_flagged_pages(
        self, seeded_tenant_and_user: tuple[UUID, UUID]
    ) -> None:
        """Branch A: Existing active version remains active; new version held in review."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            # 1. Existing active version v1
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Branch A Policy Doc")
            v1_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="ready"
            )
            p1_v1 = await seed_page(session, tenant_id=tenant_id, ver_id=v1_id, page_number=1)
            await seed_chunk(
                session, tenant_id=tenant_id, page_id=p1_v1, ver_id=v1_id, is_active=True
            )
            await session.execute(
                text("UPDATE documents SET active_version_id = :vid WHERE id = :did"),
                {"vid": v1_id, "did": doc_id},
            )

            # 2. New version v2 with 1 clean page and 1 flagged page
            v2_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            p1_v2 = await seed_page(session, tenant_id=tenant_id, ver_id=v2_id, page_number=1)
            p2_v2 = await seed_page(session, tenant_id=tenant_id, ver_id=v2_id, page_number=2)
            await session.execute(
                text(
                    "UPDATE pages SET needs_review = true, "
                    "review_reason = 'low_ocr_confidence' WHERE id = :pid"
                ),
                {"pid": p2_v2},
            )
            await seed_chunk(
                session, tenant_id=tenant_id, page_id=p1_v2, ver_id=v2_id, is_active=False
            )
            await seed_chunk(
                session, tenant_id=tenant_id, page_id=p2_v2, ver_id=v2_id, is_active=False
            )

            # 3. Execute activation
            repo = PostgresDocumentRepository(session)
            service = VersionActivationService(repo)
            final_status = await service.activate_version(
                tenant_id=str(tenant_id), document_version_id=v2_id
            )

            assert final_status == "needs_review"

            # 4. Assert v1 remains active, v2 NOT active
            doc_row = (
                await session.execute(select(Document).where(Document.id == doc_id))
            ).scalar_one()
            assert doc_row.active_version_id == v1_id

            v1_row = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == v1_id))
            ).scalar_one()
            assert v1_row.status == "ready"

            v2_row = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == v2_id))
            ).scalar_one()
            assert v2_row.status == "needs_review"

            # 5. Assert v2 chunks stay inactive, v1 chunks remain active
            v1_chunks = (
                (await session.execute(select(Chunk).where(Chunk.document_version_id == v1_id)))
                .scalars()
                .all()
            )
            assert all(c.is_active for c in v1_chunks)

            v2_chunks = (
                (await session.execute(select(Chunk).where(Chunk.document_version_id == v2_id)))
                .scalars()
                .all()
            )
            assert not any(c.is_active for c in v2_chunks)

    async def test_activation_branch_b_first_version_with_flagged_pages(
        self, seeded_tenant_and_user: tuple[UUID, UUID]
    ) -> None:
        """Branch B: First version activates; clean chunks active, flagged chunks inactive."""
        tenant_id, user_id = seeded_tenant_and_user
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))

        async with worker_tenant_session(worker_ctx) as session:
            # 1. Document with NO previous active version
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Branch B Policy Doc")
            v1_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )

            # Page 1: clean (needs_review=False)
            p1 = await seed_page(session, tenant_id=tenant_id, ver_id=v1_id, page_number=1)
            c1 = await seed_chunk(
                session, tenant_id=tenant_id, page_id=p1, ver_id=v1_id, is_active=False
            )

            # Page 2: flagged (needs_review=True)
            p2 = await seed_page(session, tenant_id=tenant_id, ver_id=v1_id, page_number=2)
            await session.execute(
                text(
                    "UPDATE pages SET needs_review = true, "
                    "review_reason = 'low_ocr_confidence' WHERE id = :pid"
                ),
                {"pid": p2},
            )
            c2 = await seed_chunk(
                session, tenant_id=tenant_id, page_id=p2, ver_id=v1_id, is_active=False
            )

            # 2. Execute activation
            repo = PostgresDocumentRepository(session)
            service = VersionActivationService(repo)
            final_status = await service.activate_version(
                tenant_id=str(tenant_id), document_version_id=v1_id
            )

            assert final_status == "needs_review"

            # 3. Assert document activates v1 despite review status
            doc_row = (
                await session.execute(select(Document).where(Document.id == doc_id))
            ).scalar_one()
            assert doc_row.active_version_id == v1_id

            v1_row = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == v1_id))
            ).scalar_one()
            assert v1_row.status == "needs_review"

            # 4. Assert clean chunk c1 active, flagged chunk c2 INACTIVE (held out of index)
            c1_row = (await session.execute(select(Chunk).where(Chunk.id == c1))).scalar_one()
            assert c1_row.is_active is True

            c2_row = (await session.execute(select(Chunk).where(Chunk.id == c2))).scalar_one()
            assert c2_row.is_active is False

    async def test_activation_concurrency_race_with_separate_sessions(
        self, seeded_tenant_and_user: tuple[UUID, UUID]
    ) -> None:
        """Concurrency test: Two concurrent last-page completions on SEPARATE DB sessions.

        Row lock ensures only the last finisher triggers activation.
        """
        tenant_id, user_id = seeded_tenant_and_user

        # 1. Setup doc version with 2 pages in 'processing' status
        worker_ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))
        async with worker_tenant_session(worker_ctx) as session:
            doc_id = await seed_document(session, tenant_id=tenant_id, title="Concurrency Doc")
            ver_id = await seed_version(
                session, tenant_id=tenant_id, doc_id=doc_id, user_id=user_id, status="processing"
            )
            p1 = await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=1)
            p2 = await seed_page(session, tenant_id=tenant_id, ver_id=ver_id, page_number=2)
            await seed_chunk(
                session, tenant_id=tenant_id, page_id=p1, ver_id=ver_id, is_active=False
            )
            await seed_chunk(
                session, tenant_id=tenant_id, page_id=p2, ver_id=ver_id, is_active=False
            )

        # 2. Worker 1 and Worker 2 complete their pages concurrently, each on its OWN session
        async def worker_task(page_num: int) -> bool:
            ctx = WorkerContext(tenant_id=str(tenant_id), job_id=str(uuid4()))
            # Transaction 1: Commit page's work (job, job_steps) in its own transaction
            async with worker_tenant_session(ctx) as session:
                await seed_job(
                    session,
                    tenant_id=tenant_id,
                    ver_id=ver_id,
                    job_id=UUID(ctx.job_id),
                    status="processing",
                )
                await session.execute(
                    text(
                        "INSERT INTO job_steps "
                        "(id, tenant_id, job_id, document_version_id, page_number, step, "
                        "processing_version, status) "
                        "VALUES (:id, :tid, :jid, :vid, :pnum, 'chunking', 'v1', 'completed') "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {
                        "id": uuid4(),
                        "tid": tenant_id,
                        "jid": UUID(ctx.job_id),
                        "vid": ver_id,
                        "pnum": page_num,
                    },
                )

            # Transaction 2: Separate short transaction that takes the row locks and activates
            async with worker_tenant_session(ctx) as session:
                repo = PostgresDocumentRepository(session)
                service = VersionActivationService(repo)
                return await service.check_and_activate_if_complete(
                    tenant_id=str(tenant_id),
                    document_version_id=ver_id,
                )

        # Run tasks concurrently on separate sessions
        results = await asyncio.gather(worker_task(1), worker_task(2))

        # Exactly ONE worker must be the finisher that executes activation
        activated_count = sum(1 for res in results if res is True)
        assert activated_count == 1, f"Expected exactly 1 activation, got {activated_count}"

        # Version must now be ready, not stuck in processing
        async with worker_tenant_session(worker_ctx) as session:
            ver_row = (
                await session.execute(select(DocumentVersion).where(DocumentVersion.id == ver_id))
            ).scalar_one()
            assert ver_row.status == "ready"
