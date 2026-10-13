"""Integration tests for Migration 0008 (Task 2.8a Step 2).

Validates in an isolated throwaway database:
1. Migration 0008 upgrade -> downgrade -> upgrade lifecycle.
2. documents.is_retired boolean column defaults to false, nullable=False.
3. pages.is_excluded boolean column defaults to false, nullable=False.
4. pages check constraint ck_pages_excluded_needs_review:
   - Rejects rows with is_excluded = true AND needs_review = true (SQLSTATE 23514).
   - Allows is_excluded = true AND needs_review = false.
   - Allows is_excluded = false AND needs_review = true.
   - Allows is_excluded = false AND needs_review = false.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import isolated_throwaway_database


def get_sqlstate(exc: BaseException) -> str | None:
    """Extract 5-character SQLSTATE code from SQLAlchemy DBAPIError or underlying driver."""
    orig: Any = getattr(exc, "orig", exc)
    raw_state: Any = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return str(raw_state) if raw_state is not None else None


@pytest.mark.asyncio
async def test_migration_0008_upgrade_downgrade_lifecycle() -> None:
    """Verify migration 0008 applies, downgrades, and re-applies cleanly in throwaway DB."""
    async with isolated_throwaway_database() as throwaway_db:
        # 1. Upgrade to 0007 first
        command.upgrade(throwaway_db.cfg, "0007_chunks_reading_evidence")

        test_engine = create_async_engine(throwaway_db.url)
        tid = uuid4()
        uid = uuid4()
        did = uuid4()
        vid = uuid4()

        async with test_engine.begin() as conn:
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
                    VALUES (:vid, :tid, :did, 'sha0008_1', :uid, 'processing');
                    """
                ),
                {"vid": vid, "tid": tid, "did": did, "uid": uid},
            )

        # 2. Upgrade to 0008
        command.upgrade(throwaway_db.cfg, "0008_excluded_and_retired")

        async with test_engine.begin() as conn:
            # Check documents.is_retired column exists and defaults to false
            doc_row = (
                (
                    await conn.execute(
                        text("SELECT is_retired FROM documents WHERE id = :did;"),
                        {"did": did},
                    )
                )
                .mappings()
                .one()
            )
            assert doc_row["is_retired"] is False

            # Insert page and verify is_excluded defaults to false
            pid = uuid4()
            await conn.execute(
                text(
                    """
                    INSERT INTO pages (
                        id, tenant_id, document_version_id, page_number,
                        page_sha256, page_type, image_key, needs_review
                    )
                    VALUES (:pid, :tid, :vid, 1, 'psha1', 'digital_text', :img, false);
                    """
                ),
                {"pid": pid, "tid": tid, "vid": vid, "img": f"{tid}/p1.png"},
            )
            page_row = (
                (
                    await conn.execute(
                        text("SELECT is_excluded FROM pages WHERE id = :pid;"),
                        {"pid": pid},
                    )
                )
                .mappings()
                .one()
            )
            assert page_row["is_excluded"] is False

        # 3. Downgrade to 0007
        command.downgrade(throwaway_db.cfg, "0007_chunks_reading_evidence")

        async with test_engine.begin() as conn:
            # Confirm is_retired no longer exists on documents
            with pytest.raises(DBAPIError):
                await conn.execute(text("SELECT is_retired FROM documents;"))

            # Confirm is_excluded no longer exists on pages
            with pytest.raises(DBAPIError):
                await conn.execute(text("SELECT is_excluded FROM pages;"))

        # 4. Re-upgrade to 0008
        command.upgrade(throwaway_db.cfg, "0008_excluded_and_retired")
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_migration_0008_check_constraint_ck_pages_excluded_needs_review() -> None:
    """Verify ck_pages_excluded_needs_review constraint:
    rejects is_excluded=true AND needs_review=true.
    """
    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "0008_excluded_and_retired")

        test_engine = create_async_engine(throwaway_db.url)
        tid = uuid4()
        uid = uuid4()
        did = uuid4()
        vid = uuid4()

        async with test_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:tid, 'T1');"), {"tid": tid}
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) VALUES (:uid, 'u@t1.com', 'h');"
                ),
                {"uid": uid},
            )
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
                    VALUES (:vid, :tid, :did, 'sha0008_2', :uid, 'processing');
                    """
                ),
                {"vid": vid, "tid": tid, "did": did, "uid": uid},
            )

        # Test 1: is_excluded=true AND needs_review=true must FAIL (SQLSTATE 23514)
        async with test_engine.begin() as conn:
            pid_invalid = uuid4()
            with pytest.raises(DBAPIError) as exc_info:
                await conn.execute(
                    text(
                        """
                        INSERT INTO pages (
                            id, tenant_id, document_version_id, page_number,
                            page_sha256, page_type, image_key, needs_review,
                            review_reason, is_excluded
                        )
                        VALUES (
                            :pid, :tid, :vid, 1, 'psha1', 'digital_text', :img,
                            true, 'low_ocr_confidence', true
                        );
                        """
                    ),
                    {"pid": pid_invalid, "tid": tid, "vid": vid, "img": f"{tid}/p1.png"},
                )
            assert get_sqlstate(exc_info.value) == "23514"
            assert "ck_pages_excluded_needs_review" in str(exc_info.value).lower()

        async with test_engine.begin() as conn:
            # Test 2: is_excluded=true AND needs_review=false must SUCCEED
            pid_excluded = uuid4()
            await conn.execute(
                text(
                    """
                    INSERT INTO pages (
                        id, tenant_id, document_version_id, page_number,
                        page_sha256, page_type, image_key, needs_review,
                        review_reason, is_excluded
                    )
                    VALUES (:pid, :tid, :vid, 2, 'psha2', 'digital_text', :img, false, NULL, true);
                    """
                ),
                {"pid": pid_excluded, "tid": tid, "vid": vid, "img": f"{tid}/p2.png"},
            )

            # Test 3: is_excluded=false AND needs_review=true must SUCCEED
            pid_flagged = uuid4()
            await conn.execute(
                text(
                    """
                    INSERT INTO pages (
                        id, tenant_id, document_version_id, page_number,
                        page_sha256, page_type, image_key, needs_review,
                        review_reason, is_excluded
                    )
                    VALUES (
                        :pid, :tid, :vid, 3, 'psha3', 'digital_text', :img,
                        true, 'low_ocr_confidence', false
                    );
                    """
                ),
                {"pid": pid_flagged, "tid": tid, "vid": vid, "img": f"{tid}/p3.png"},
            )

            # Test 4: UPDATE to is_excluded=true while needs_review=true must FAIL (SQLSTATE 23514)
            with pytest.raises(DBAPIError) as exc_update:
                await conn.execute(
                    text("UPDATE pages SET is_excluded = true WHERE id = :pid;"),
                    {"pid": pid_flagged},
                )
            assert get_sqlstate(exc_update.value) == "23514"

        await test_engine.dispose()
