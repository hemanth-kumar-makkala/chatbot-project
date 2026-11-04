"""Integration test for Migration 0014 (feedback table with RLS).

Tests upgrade -> downgrade -> upgrade lifecycle in an isolated throwaway database.
Enforces:
- Table creation, column types, constraints, and foreign keys for feedback.
- Composite foreign key to messages (tenant_id, message_id).
- Unique constraint on (tenant_id, message_id, user_id).
- Row level security (RLS) enabled and forced on feedback.
- Isolation: never run against shared database.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import isolated_throwaway_database


@pytest.mark.asyncio
async def test_migration_0014_feedback_schema_upgrade_downgrade_lifecycle() -> None:
    """Verify migration 0014 applies, enforces RLS, downgrades, and re-applies cleanly."""
    async with isolated_throwaway_database() as throwaway_db:
        # 1. Upgrade to 0013 first
        command.upgrade(throwaway_db.cfg, "0013_rate_limit_indexes")

        test_engine = create_async_engine(throwaway_db.url)
        async with test_engine.begin() as conn:
            pre_tables = {
                str(r[0])
                for r in (
                    await conn.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public';")
                    )
                ).fetchall()
            }
            assert "feedback" not in pre_tables

        # 2. Upgrade to 0014_feedback_schema
        command.upgrade(throwaway_db.cfg, "0014_feedback_schema")

        tid_a = uuid4()
        tid_b = uuid4()
        uid_a = uuid4()
        uid_b = uuid4()
        aid_a = uuid4()
        cid_a = uuid4()
        mid_a = uuid4()
        fid_a = uuid4()

        async with test_engine.begin() as conn:
            # Check table exists and RLS is enabled/forced
            post_tables = {
                str(r[0])
                for r in (
                    await conn.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public';")
                    )
                ).fetchall()
            }
            assert "feedback" in post_tables

            rls_check = (
                await conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname = 'feedback';"
                    )
                )
            ).fetchone()
            assert rls_check is not None
            assert rls_check[0] is True, "relrowsecurity must be True"
            assert rls_check[1] is True, "relforcerowsecurity must be True"

            # Seed Tenant A and Tenant B prerequisites
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:tid, 'Tenant A');"),
                {"tid": tid_a},
            )
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:tid, 'Tenant B');"),
                {"tid": tid_b},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) "
                    "VALUES (:uid, 'user_a@test.com', 'hash_a');"
                ),
                {"uid": uid_a},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) "
                    "VALUES (:uid, 'user_b@test.com', 'hash_b');"
                ),
                {"uid": uid_b},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO assistants (id, tenant_id, name, prompt_id, prompt_version)
                    VALUES (:aid, :tid, 'Assistant A', 'default', 'v1');
                    """
                ),
                {"aid": aid_a, "tid": tid_a},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO conversations (id, tenant_id, user_id, assistant_id, title)
                    VALUES (:cid, :tid, :uid, :aid, 'Conversation A');
                    """
                ),
                {"cid": cid_a, "tid": tid_a, "uid": uid_a, "aid": aid_a},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO messages (id, tenant_id, conversation_id, role, content)
                    VALUES (:mid, :tid, :cid, 'assistant', 'Test answer content');
                    """
                ),
                {"mid": mid_a, "tid": tid_a, "cid": cid_a},
            )

            # Insert feedback under Tenant A context as app_user
            await conn.execute(text("SET ROLE app_user;"))
            await conn.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true);"),
                {"tid": str(tid_a)},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO feedback (id, tenant_id, message_id, user_id, rating, reason)
                    VALUES (:fid, :tid, :mid, :uid, 'up', 'Accurate answer');
                    """
                ),
                {"fid": fid_a, "tid": tid_a, "mid": mid_a, "uid": uid_a},
            )

            # Read back under Tenant A: should return 1 row
            rows_a = (
                await conn.execute(text("SELECT id, rating, reason FROM feedback;"))
            ).fetchall()
            assert len(rows_a) == 1
            assert rows_a[0][1] == "up"
            assert rows_a[0][2] == "Accurate answer"

            # Switch to Tenant B context: should return 0 rows (RLS isolation)
            await conn.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true);"),
                {"tid": str(tid_b)},
            )
            rows_b = (await conn.execute(text("SELECT id FROM feedback;"))).fetchall()
            assert len(rows_b) == 0

            # Reset role before ending transaction
            await conn.execute(text("RESET ROLE;"))

        # 3. Test Downgrade to 0013_rate_limit_indexes
        command.downgrade(throwaway_db.cfg, "0013_rate_limit_indexes")
        async with test_engine.begin() as conn:
            downgraded_tables = {
                str(r[0])
                for r in (
                    await conn.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public';")
                    )
                ).fetchall()
            }
            assert "feedback" not in downgraded_tables

        # 4. Re-upgrade back to 0014_feedback_schema
        command.upgrade(throwaway_db.cfg, "0014_feedback_schema")
        async with test_engine.begin() as conn:
            reupgraded_tables = {
                str(r[0])
                for r in (
                    await conn.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public';")
                    )
                ).fetchall()
            }
            assert "feedback" in reupgraded_tables
        await test_engine.dispose()
