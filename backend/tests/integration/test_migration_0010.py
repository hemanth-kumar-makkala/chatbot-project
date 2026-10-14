"""Integration tests for Migration 0010 (allow re-upload after retire or failure).

Validates in an isolated throwaway database:
1. Migration 0010 upgrade -> duplicate (tenant_id, sha256) rows allowed in document_versions.
2. Downgrade blocked with a fixed error message when duplicate (tenant_id, sha256) rows exist.
3. Downgrade succeeds when duplicates are removed, restoring uq_document_versions_tenant_sha256.
4. With constraint restored, duplicate (tenant_id, sha256) insertion is rejected
   with SQLSTATE 23505.
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
async def test_migration_0010_duplicates_allowed_and_downgrade_rules() -> None:
    """Verify 0010 allows duplicates and blocks downgrade on duplicates."""
    async with isolated_throwaway_database() as throwaway_db:
        # 1. Upgrade to 0009 first
        command.upgrade(throwaway_db.cfg, "0009_version_file_extension")

        test_engine = create_async_engine(throwaway_db.url)
        tid = uuid4()
        uid = uuid4()
        did1 = uuid4()
        did2 = uuid4()
        vid1 = uuid4()
        vid2 = uuid4()
        shared_sha = (
            "e3b0c44298fc1c149afbf4c8996fb924"  # pragma: allowlist secret
            "27ae41e4649b934ca495991b7852b855"  # pragma: allowlist secret
        )

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
                {"did": did1, "tid": tid},
            )
            await conn.execute(
                text("INSERT INTO documents (id, tenant_id, title) VALUES (:did, :tid, 'Doc 2');"),
                {"did": did2, "tid": tid},
            )

        insert_version_sql = """
            INSERT INTO document_versions
                (id, tenant_id, document_id, sha256, uploaded_by, status, file_extension)
            VALUES
                (:vid, :tid, :did, :sha, :uid, 'received', 'pdf');
        """

        # 2. Under 0009, inserting duplicate (tenant_id, sha256) violates unique constraint
        async with test_engine.begin() as conn:
            await conn.execute(
                text(insert_version_sql),
                {"vid": vid1, "tid": tid, "did": did1, "sha": shared_sha, "uid": uid},
            )

        with pytest.raises(DBAPIError) as exc_info:
            async with test_engine.begin() as conn:
                await conn.execute(
                    text(insert_version_sql),
                    {"vid": vid2, "tid": tid, "did": did2, "sha": shared_sha, "uid": uid},
                )
        assert get_sqlstate(exc_info.value) == "23505"

        # 3. Upgrade to 0010
        command.upgrade(throwaway_db.cfg, "0010_reupload_after_retire")

        # 4. Under 0010, duplicate (tenant_id, sha256) is now ALLOWED
        async with test_engine.begin() as conn:
            await conn.execute(
                text(insert_version_sql),
                {"vid": vid2, "tid": tid, "did": did2, "sha": shared_sha, "uid": uid},
            )

            # Verify both versions exist with identical (tenant_id, sha256)
            res = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM document_versions "
                    "WHERE tenant_id = :tid AND sha256 = :sha;"
                ),
                {"tid": tid, "sha": shared_sha},
            )
            assert res.scalar_one() == 2

        # 5. Downgrade to 0009 must be BLOCKED because duplicates exist
        with pytest.raises(RuntimeError) as downgrade_exc:
            command.downgrade(throwaway_db.cfg, "0009_version_file_extension")
        assert "Cannot downgrade migration 0010: duplicate (tenant_id, sha256) rows exist" in str(
            downgrade_exc.value
        )

        # 6. Delete duplicate version, then downgrade to 0009 must succeed
        async with test_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM document_versions WHERE id = :vid;"),
                {"vid": vid2},
            )

        command.downgrade(throwaway_db.cfg, "0009_version_file_extension")

        # 7. With 0009 restored, unique constraint again rejects duplicate insertion
        with pytest.raises(DBAPIError) as exc_after_downgrade:
            async with test_engine.begin() as conn:
                await conn.execute(
                    text(insert_version_sql),
                    {"vid": vid2, "tid": tid, "did": did2, "sha": shared_sha, "uid": uid},
                )
        assert get_sqlstate(exc_after_downgrade.value) == "23505"

        await test_engine.dispose()
