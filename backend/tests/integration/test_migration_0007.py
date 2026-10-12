"""Integration tests for Migration 0007 (Task 2.5 Step 2).

Validates in an isolated throwaway database:
1. Migration 0007 upgrade -> downgrade -> upgrade lifecycle.
2. Backfill of pre-0007 flagged pages to review_reason = 'legacy_unspecified'.
3. pages.review_reason allowlist check constraint including 'legacy_unspecified'.
4. pages.needs_review requires non-null review_reason check constraint (SQLSTATE 23514).
5. chunks.evidence_type allowlist check constraint (SQLSTATE 23514).
6. chunks visual evidence requires non-null page_reading_id (SQLSTATE 23514).
7. chunks.page_reading_id composite tenant FK enforcement (SQLSTATE 23503).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.adapters.db.postgres_document_repository import PostgresDocumentRepository
from tests.conftest import isolated_throwaway_database


def get_sqlstate(exc: BaseException) -> str | None:
    """Extract 5-character SQLSTATE code from SQLAlchemy DBAPIError or underlying driver."""
    orig: Any = getattr(exc, "orig", exc)
    raw_state: Any = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return str(raw_state) if raw_state is not None else None


@pytest.mark.asyncio
async def test_migration_0007_upgrade_downgrade_lifecycle() -> None:
    """Verify migration 0007 applies, backfills historical rows, and downgrades cleanly."""
    async with isolated_throwaway_database() as throwaway_db:
        # 1. Start at revision 0006
        command.upgrade(throwaway_db.cfg, "0006_procrastinate_schema")

        test_engine = create_async_engine(throwaway_db.url)
        pid = uuid4()
        async with test_engine.begin() as conn:
            tid = uuid4()
            uid = uuid4()
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:tid, 'T1');"),
                {"tid": tid},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) VALUES (:uid, 'u@t1.com', 'h');"
                ),
                {"uid": uid},
            )
            did = uuid4()
            vid = uuid4()
            await conn.execute(
                text("INSERT INTO documents (id, tenant_id, title) VALUES (:did, :tid, 'Doc 1');"),
                {"did": did, "tid": tid},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        id, tenant_id, document_id, sha256, uploaded_by, status
                    )
                    VALUES (:vid, :tid, :did, 'sha1', :uid, 'processing');
                    """
                ),
                {"vid": vid, "tid": tid, "did": did, "uid": uid},
            )
            # Insert historical flagged page without review_reason (at revision 0006)
            await conn.execute(
                text(
                    """
                    INSERT INTO pages (
                        id, tenant_id, document_version_id, page_number,
                        page_sha256, page_type, image_key, needs_review
                    )
                    VALUES (:pid, :tid, :vid, 1, 'psha1', 'scanned', :img, true);
                    """
                ),
                {"pid": pid, "tid": tid, "vid": vid, "img": f"{tid}/img1.png"},
            )

        # 2. Upgrade to 0007 (head)
        command.upgrade(throwaway_db.cfg, "head")

        async with test_engine.connect() as conn:
            # Verify columns exist
            res_pages = await conn.execute(
                text(
                    """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = 'pages' AND column_name = 'review_reason';
                    """
                )
            )
            assert res_pages.fetchone() is not None, "pages.review_reason column missing"

            # Verify historical flagged page backfilled to legacy_unspecified
            res_row = await conn.execute(
                text("SELECT needs_review, review_reason FROM pages WHERE id = :pid"),
                {"pid": pid},
            )
            row = res_row.fetchone()
            assert row is not None
            assert row[0] is True
            assert row[1] == "legacy_unspecified"

        # 3. Downgrade to 0006
        command.downgrade(throwaway_db.cfg, "0006_procrastinate_schema")

        async with test_engine.connect() as conn:
            res_pages_after = await conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'pages' AND column_name = 'review_reason';
                    """
                )
            )
            assert res_pages_after.fetchone() is None, "review_reason must drop on downgrade"

        # 4. Re-upgrade to head
        command.upgrade(throwaway_db.cfg, "head")

        async with test_engine.connect() as conn:
            res_reup = await conn.execute(
                text("SELECT review_reason FROM pages WHERE id = :pid"),
                {"pid": pid},
            )
            assert res_reup.scalar_one() == "legacy_unspecified"

        await test_engine.dispose()


@pytest.mark.asyncio
async def test_migration_0007_pages_review_reason_constraints() -> None:
    """Verify pages.review_reason allowlist and requirement when needs_review is true."""
    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "head")

        test_engine = create_async_engine(throwaway_db.url)
        async with test_engine.begin() as conn:
            tid = uuid4()
            uid = uuid4()
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:tid, 'T1');"),
                {"tid": tid},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) VALUES (:uid, 'u@t1.com', 'h');"
                ),
                {"uid": uid},
            )
            did = uuid4()
            vid = uuid4()
            await conn.execute(
                text("INSERT INTO documents (id, tenant_id, title) VALUES (:did, :tid, 'Doc 1');"),
                {"did": did, "tid": tid},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        id, tenant_id, document_id, sha256, uploaded_by, status
                    )
                    VALUES (:vid, :tid, :did, 'sha1', :uid, 'processing');
                    """
                ),
                {"vid": vid, "tid": tid, "did": did, "uid": uid},
            )

            # 1. Negative: invalid review_reason outside allowlist fails (23514)
            with pytest.raises(DBAPIError) as exc_info:
                async with conn.begin_nested():
                    await conn.execute(
                        text(
                            """
                            INSERT INTO pages (
                                id, tenant_id, document_version_id, page_number,
                                page_sha256, page_type, image_key, needs_review, review_reason
                            )
                            VALUES (
                                :pid, :tid, :vid, 1, 'psha1', 'scanned', :img,
                                true, 'invalid_reason_string'
                            );
                            """
                        ),
                        {"pid": uuid4(), "tid": tid, "vid": vid, "img": f"{tid}/img1.png"},
                    )
            assert get_sqlstate(exc_info.value) == "23514"

            # 2. Negative: needs_review = true with review_reason NULL fails (23514)
            with pytest.raises(DBAPIError) as exc_info:
                async with conn.begin_nested():
                    await conn.execute(
                        text(
                            """
                            INSERT INTO pages (
                                id, tenant_id, document_version_id, page_number,
                                page_sha256, page_type, image_key, needs_review, review_reason
                            )
                            VALUES (
                                :pid, :tid, :vid, 1, 'psha1', 'scanned', :img, true, NULL
                            );
                            """
                        ),
                        {"pid": uuid4(), "tid": tid, "vid": vid, "img": f"{tid}/img1.png"},
                    )
            assert get_sqlstate(exc_info.value) == "23514"

            # 3. Positive: needs_review = false with review_reason NULL succeeds
            pid_clean = uuid4()
            await conn.execute(
                text(
                    """
                    INSERT INTO pages (
                        id, tenant_id, document_version_id, page_number,
                        page_sha256, page_type, image_key, needs_review, review_reason
                    )
                    VALUES (
                        :pid, :tid, :vid, 1, 'psha1', 'scanned', :img, false, NULL
                    );
                    """
                ),
                {"pid": pid_clean, "tid": tid, "vid": vid, "img": f"{tid}/img1.png"},
            )

            # 4. Positive: needs_review = true with low_ocr_confidence succeeds
            pid_flagged = uuid4()
            await conn.execute(
                text(
                    """
                    INSERT INTO pages (
                        id, tenant_id, document_version_id, page_number,
                        page_sha256, page_type, image_key, needs_review, review_reason
                    )
                    VALUES (
                        :pid, :tid, :vid, 2, 'psha2', 'scanned', :img,
                        true, 'low_ocr_confidence'
                    );
                    """
                ),
                {"pid": pid_flagged, "tid": tid, "vid": vid, "img": f"{tid}/img2.png"},
            )

            # 5. Positive: needs_review = true with legacy_unspecified succeeds
            pid_legacy = uuid4()
            await conn.execute(
                text(
                    """
                    INSERT INTO pages (
                        id, tenant_id, document_version_id, page_number,
                        page_sha256, page_type, image_key, needs_review, review_reason
                    )
                    VALUES (
                        :pid, :tid, :vid, 3, 'psha3', 'scanned', :img,
                        true, 'legacy_unspecified'
                    );
                    """
                ),
                {"pid": pid_legacy, "tid": tid, "vid": vid, "img": f"{tid}/img3.png"},
            )

        await test_engine.dispose()


@pytest.mark.asyncio
async def test_migration_0007_chunks_evidence_and_foreign_key_constraints() -> None:
    """Verify chunks evidence_type allowlist, visual requirement, and composite FK."""
    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "head")

        test_engine = create_async_engine(throwaway_db.url)
        async with test_engine.begin() as conn:
            tid = uuid4()
            uid = uuid4()
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:tid, 'T1');"),
                {"tid": tid},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) VALUES (:uid, 'u@t1.com', 'h');"
                ),
                {"uid": uid},
            )
            did = uuid4()
            vid = uuid4()
            await conn.execute(
                text("INSERT INTO documents (id, tenant_id, title) VALUES (:did, :tid, 'Doc 1');"),
                {"did": did, "tid": tid},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        id, tenant_id, document_id, sha256, uploaded_by, status
                    )
                    VALUES (:vid, :tid, :did, 'sha1', :uid, 'processing');
                    """
                ),
                {"vid": vid, "tid": tid, "did": did, "uid": uid},
            )
            pid = uuid4()
            await conn.execute(
                text(
                    """
                    INSERT INTO pages (
                        id, tenant_id, document_version_id, page_number,
                        page_sha256, page_type, image_key, needs_review
                    )
                    VALUES (:pid, :tid, :vid, 1, 'psha1', 'scanned', :img, false);
                    """
                ),
                {"pid": pid, "tid": tid, "vid": vid, "img": f"{tid}/img1.png"},
            )
            rid = uuid4()
            await conn.execute(
                text(
                    """
                    INSERT INTO page_readings (
                        id, tenant_id, page_id, source, text, model_id, model_version
                    )
                    VALUES (
                        :rid, :tid, :pid, 'vision_ingest', 'Transcription', 'mock-vision', '1.0'
                    );
                    """
                ),
                {"rid": rid, "tid": tid, "pid": pid},
            )

            # 1. Negative: invalid evidence_type fails (23514)
            with pytest.raises(DBAPIError) as exc_info:
                async with conn.begin_nested():
                    await conn.execute(
                        text(
                            """
                            INSERT INTO chunks (
                                id, tenant_id, page_id, document_version_id,
                                text, language, ts_config, evidence_type
                            )
                            VALUES (
                                :cid, :tid, :pid, :vid, 'Text',
                                'english', 'english', 'audio_invalid'
                            );
                            """
                        ),
                        {"cid": uuid4(), "tid": tid, "pid": pid, "vid": vid},
                    )
            assert get_sqlstate(exc_info.value) == "23514"

            # 2. Negative: visual evidence without page_reading_id fails (23514)
            with pytest.raises(DBAPIError) as exc_info:
                async with conn.begin_nested():
                    await conn.execute(
                        text(
                            """
                            INSERT INTO chunks (
                                id, tenant_id, page_id, document_version_id,
                                text, language, ts_config, evidence_type, page_reading_id
                            )
                            VALUES (
                                :cid, :tid, :pid, :vid, 'Visual text',
                                'english', 'english', 'visual', NULL
                            );
                            """
                        ),
                        {"cid": uuid4(), "tid": tid, "pid": pid, "vid": vid},
                    )
            assert get_sqlstate(exc_info.value) == "23514"

            # 3. Negative: page_reading_id referencing non-existent reading fails FK (23503)
            with pytest.raises(DBAPIError) as exc_info:
                async with conn.begin_nested():
                    await conn.execute(
                        text(
                            """
                            INSERT INTO chunks (
                                id, tenant_id, page_id, document_version_id,
                                text, language, ts_config, evidence_type, page_reading_id
                            )
                            VALUES (
                                :cid, :tid, :pid, :vid, 'Visual text',
                                'english', 'english', 'visual', :fake_rid
                            );
                            """
                        ),
                        {
                            "cid": uuid4(),
                            "tid": tid,
                            "pid": pid,
                            "vid": vid,
                            "fake_rid": uuid4(),
                        },
                    )
            assert get_sqlstate(exc_info.value) == "23503"

            # 4. Positive: text chunk with page_reading_id NULL succeeds
            await conn.execute(
                text(
                    """
                    INSERT INTO chunks (
                        id, tenant_id, page_id, document_version_id,
                        text, language, ts_config, evidence_type, page_reading_id
                    )
                    VALUES (
                        :cid, :tid, :pid, :vid, 'Text chunk',
                        'english', 'english', 'text', NULL
                    );
                    """
                ),
                {"cid": uuid4(), "tid": tid, "pid": pid, "vid": vid},
            )

            # 5. Positive: visual chunk with valid page_reading_id succeeds
            await conn.execute(
                text(
                    """
                    INSERT INTO chunks (
                        id, tenant_id, page_id, document_version_id,
                        text, language, ts_config, evidence_type, page_reading_id
                    )
                    VALUES (
                        :cid, :tid, :pid, :vid, 'Visual chunk',
                        'english', 'english', 'visual', :rid
                    );
                    """
                ),
                {"cid": uuid4(), "tid": tid, "pid": pid, "vid": vid, "rid": rid},
            )

        await test_engine.dispose()


@pytest.mark.asyncio
async def test_repository_page_reading_and_review_flags() -> None:
    """Verify repo methods: create_page_reading, flag_page_for_review, clear_page_review_flag."""
    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "head")

        test_engine = create_async_engine(throwaway_db.url)
        async with test_engine.begin() as conn:
            tid = uuid4()
            uid = uuid4()
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:tid, 'T1');"),
                {"tid": tid},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) VALUES (:uid, 'u@t1.com', 'h');"
                ),
                {"uid": uid},
            )
            did = uuid4()
            vid = uuid4()
            await conn.execute(
                text("INSERT INTO documents (id, tenant_id, title) VALUES (:did, :tid, 'Doc 1');"),
                {"did": did, "tid": tid},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        id, tenant_id, document_id, sha256, uploaded_by, status
                    )
                    VALUES (:vid, :tid, :did, 'sha1', :uid, 'processing');
                    """
                ),
                {"vid": vid, "tid": tid, "did": did, "uid": uid},
            )
            pid = uuid4()
            await conn.execute(
                text(
                    """
                    INSERT INTO pages (
                        id, tenant_id, document_version_id, page_number,
                        page_sha256, page_type, image_key, needs_review
                    )
                    VALUES (:pid, :tid, :vid, 1, 'psha1', 'scanned', :img, false);
                    """
                ),
                {"pid": pid, "tid": tid, "vid": vid, "img": f"{tid}/img1.png"},
            )

        async with AsyncSession(test_engine) as session:
            repo = PostgresDocumentRepository(session)

            # 1. create_page_reading
            rid1 = await repo.create_page_reading(
                tenant_id=str(tid),
                page_id=pid,
                source="vision_ingest",
                text="Transcribed text",
                model_id="mock-vision",
                model_version="1.0",
            )
            assert rid1 is not None

            # 2. create_page_reading idempotent re-run returns same ID
            rid2 = await repo.create_page_reading(
                tenant_id=str(tid),
                page_id=pid,
                source="vision_ingest",
                text="Transcribed text",
                model_id="mock-vision",
                model_version="1.0",
            )
            assert rid1 == rid2

            # Confirm only 1 reading exists
            count_res = await session.execute(
                text("SELECT count(*) FROM page_readings WHERE page_id = :pid"),
                {"pid": pid},
            )
            assert count_res.scalar_one() == 1

            # 3. flag_page_for_review with reason
            await repo.flag_page_for_review(
                tenant_id=str(tid),
                version_id=vid,
                page_number=1,
                reason="low_ocr_confidence",
            )
            page_res = await session.execute(
                text("SELECT needs_review, review_reason FROM pages WHERE id = :pid"),
                {"pid": pid},
            )
            row = page_res.fetchone()
            assert row is not None
            assert row[0] is True
            assert row[1] == "low_ocr_confidence"

            # 4. clear_page_review_flag
            await repo.clear_page_review_flag(
                tenant_id=str(tid),
                version_id=vid,
                page_number=1,
            )
            page_res_cleared = await session.execute(
                text("SELECT needs_review, review_reason FROM pages WHERE id = :pid"),
                {"pid": pid},
            )
            row_cleared = page_res_cleared.fetchone()
            assert row_cleared is not None
            assert row_cleared[0] is False
            assert row_cleared[1] is None

        await test_engine.dispose()
