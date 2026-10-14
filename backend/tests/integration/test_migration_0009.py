"""Integration tests for Migration 0009 (document_versions.file_extension).

Validates in an isolated throwaway database:
1. Migration 0009 upgrade -> downgrade -> upgrade lifecycle.
2. document_versions.file_extension String(20) column allows NULL.
3. ck_document_versions_file_extension CHECK constraint:
   - Allows NULL.
   - Allows all valid extensions: 'pdf', 'png', 'jpeg', 'tiff', 'xlsx', 'csv', 'txt'.
   - Rejects invalid extensions like 'exe', 'docx', 'zip' (SQLSTATE 23514).
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
async def test_migration_0009_upgrade_downgrade_lifecycle() -> None:
    """Verify migration 0009 applies, downgrades, and re-applies cleanly in throwaway DB."""
    async with isolated_throwaway_database() as throwaway_db:
        # 1. Upgrade to 0008 first
        command.upgrade(throwaway_db.cfg, "0008_excluded_and_retired")

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
                    VALUES (:vid, :tid, :did, 'sha0009_1', :uid, 'received');
                    """
                ),
                {"vid": vid, "tid": tid, "did": did, "uid": uid},
            )

        # 2. Upgrade to 0009
        command.upgrade(throwaway_db.cfg, "0009_version_file_extension")

        async with test_engine.begin() as conn:
            # Check document_versions.file_extension column exists and is NULL for pre-existing row
            row = (
                (
                    await conn.execute(
                        text("SELECT file_extension FROM document_versions WHERE id = :vid;"),
                        {"vid": vid},
                    )
                )
                .mappings()
                .one()
            )
            assert row["file_extension"] is None

            # Insert new version with valid extension 'pdf'
            vid2 = uuid4()
            await conn.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        id, tenant_id, document_id, sha256, uploaded_by, status, file_extension
                    )
                    VALUES (:vid, :tid, :did, 'sha0009_2', :uid, 'received', 'pdf');
                    """
                ),
                {"vid": vid2, "tid": tid, "did": did, "uid": uid},
            )
            row2 = (
                (
                    await conn.execute(
                        text("SELECT file_extension FROM document_versions WHERE id = :vid;"),
                        {"vid": vid2},
                    )
                )
                .mappings()
                .one()
            )
            assert row2["file_extension"] == "pdf"

        # 3. Downgrade to 0008
        command.downgrade(throwaway_db.cfg, "0008_excluded_and_retired")

        async with test_engine.begin() as conn:
            with pytest.raises(DBAPIError):
                await conn.execute(text("SELECT file_extension FROM document_versions;"))

        # 4. Re-upgrade to 0009
        command.upgrade(throwaway_db.cfg, "0009_version_file_extension")
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_migration_0009_check_constraint_file_extension() -> None:
    """Verify ck_document_versions_file_extension constraint:
    rejects invalid extensions, allows NULL and all 7 valid extensions.
    """
    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "0009_version_file_extension")

        test_engine = create_async_engine(throwaway_db.url)
        tid = uuid4()
        uid = uuid4()
        did = uuid4()

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

            # 1. Verify invalid extensions are rejected with SQLSTATE 23514
            pass

        for invalid_ext in ("exe", "docx", "zip", "tar", "sh", "PDF"):
            bad_vid = uuid4()
            async with test_engine.begin() as conn:
                with pytest.raises(DBAPIError) as exc_info:
                    await conn.execute(
                        text(
                            """
                            INSERT INTO document_versions (
                                id, tenant_id, document_id, sha256,
                                uploaded_by, status, file_extension
                            )
                            VALUES (:vid, :tid, :did, :sha, :uid, 'received', :ext);
                            """
                        ),
                        {
                            "vid": bad_vid,
                            "tid": tid,
                            "did": did,
                            "sha": f"sha-{bad_vid.hex[:8]}",
                            "uid": uid,
                            "ext": invalid_ext,
                        },
                    )
                assert get_sqlstate(exc_info.value) == "23514"
                assert "ck_document_versions_file_extension" in str(exc_info.value).lower()

        async with test_engine.begin() as conn:
            # 2. Verify all 7 accepted upload validation extensions are allowed
            valid_extensions = ("pdf", "png", "jpeg", "tiff", "xlsx", "csv", "txt")
            for valid_ext in valid_extensions:
                good_vid = uuid4()
                await conn.execute(
                    text(
                        """
                        INSERT INTO document_versions (
                            id, tenant_id, document_id, sha256,
                            uploaded_by, status, file_extension
                        )
                        VALUES (:vid, :tid, :did, :sha, :uid, 'received', :ext);
                        """
                    ),
                    {
                        "vid": good_vid,
                        "tid": tid,
                        "did": did,
                        "sha": f"sha-{good_vid.hex[:8]}",
                        "uid": uid,
                        "ext": valid_ext,
                    },
                )
                saved_ext: str = (
                    await conn.execute(
                        text("SELECT file_extension FROM document_versions WHERE id = :vid;"),
                        {"vid": good_vid},
                    )
                ).scalar_one()
                assert saved_ext == valid_ext

            # 3. Verify NULL is allowed
            null_vid = uuid4()
            await conn.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        id, tenant_id, document_id, sha256,
                        uploaded_by, status, file_extension
                    )
                    VALUES (:vid, :tid, :did, :sha, :uid, 'received', NULL);
                    """
                ),
                {
                    "vid": null_vid,
                    "tid": tid,
                    "did": did,
                    "sha": f"sha-{null_vid.hex[:8]}",
                    "uid": uid,
                },
            )
            saved_null: str | None = (
                await conn.execute(
                    text("SELECT file_extension FROM document_versions WHERE id = :vid;"),
                    {"vid": null_vid},
                )
            ).scalar_one()
            assert saved_null is None

        await test_engine.dispose()
