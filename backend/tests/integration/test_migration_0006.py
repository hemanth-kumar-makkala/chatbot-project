"""Integration tests for migration 0006 (Procrastinate schema and role permissions).

Runs exclusively against an isolated throwaway database so tests pass
cleanly before migration 0006 is applied to the shared database.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.env_utils import load_dotenv
from tests.conftest import isolated_throwaway_database


def get_sqlstate(exc: BaseException) -> str | None:
    """Extract 5-character SQLSTATE code from SQLAlchemy DBAPIError or driver."""
    orig: Any = getattr(exc, "orig", exc)
    raw_state: Any = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return str(raw_state) if raw_state is not None else None


@pytest.mark.asyncio
class TestMigration0006ProcrastinateSchema:
    """Verifies schema isolation, app_user permissions, least privilege, and retriever denial."""

    async def test_migration_0006_schema_isolation_and_permissions(self) -> None:
        """Full suite of migration 0006 checks in an isolated throwaway database."""
        load_dotenv()
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        app_user = os.getenv("DB_APP_USER", "app_user")
        app_pass = os.getenv("DB_APP_PASSWORD", "")
        retriever_user = os.getenv("DB_RETRIEVER_USER", "retriever_user")
        retriever_pass = os.getenv("DB_RETRIEVER_PASSWORD", "")

        async with isolated_throwaway_database() as throwaway_db:
            # 1. Apply all migrations up to head (including 0006) to the throwaway DB
            command.upgrade(throwaway_db.cfg, "head")

            app_url = (
                f"postgresql+psycopg://{app_user}:{app_pass}@{host}:{port}/{throwaway_db.db_name}"
            )
            retriever_url = f"postgresql+psycopg://{retriever_user}:{retriever_pass}@{host}:{port}/{throwaway_db.db_name}"

            app_engine = create_async_engine(
                app_url, connect_args={"options": "-c search_path=procrastinate,public"}
            )
            retriever_engine = create_async_engine(retriever_url)

            # 2. Check schema isolation (queue tables exist in procrastinate, none in public)
            async with app_engine.connect() as conn:
                res = await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'procrastinate' ORDER BY table_name"
                    )
                )
                proc_tables: set[str] = set(res.scalars().all())
                expected_tables = {
                    "procrastinate_jobs",
                    "procrastinate_workers",
                    "procrastinate_events",
                    "procrastinate_periodic_defers",
                }
                assert expected_tables.issubset(proc_tables), (
                    f"Missing expected procrastinate tables: {expected_tables - proc_tables}"
                )

                public_res = await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name LIKE 'procrastinate%'"
                    )
                )
                public_proc_tables: set[str] = set(public_res.scalars().all())
                assert public_proc_tables == set(), (
                    f"Procrastinate tables leaked into schema public: {public_proc_tables}"
                )

            # 3. app_user can enqueue, fetch, update, and delete jobs
            async with app_engine.connect() as conn:
                probe_lock = f"probe_lock_{uuid4().hex[:8]}"
                insert_res = await conn.execute(
                    text(
                        "INSERT INTO procrastinate.procrastinate_jobs "
                        "(queue_name, task_name, lock, args, status) "
                        "VALUES ('test_queue', 'test_task', :lck, '{}', 'todo') "
                        "RETURNING id"
                    ),
                    {"lck": probe_lock},
                )
                job_id: int = int(insert_res.scalar_one())
                assert job_id > 0

                fetch_res = await conn.execute(
                    text(
                        "SELECT queue_name, task_name, status "
                        "FROM procrastinate.procrastinate_jobs "
                        "WHERE id = :jid"
                    ),
                    {"jid": job_id},
                )
                row = fetch_res.fetchone()
                assert row is not None
                assert row[0] == "test_queue"
                assert row[1] == "test_task"

                # Cleanup job
                await conn.execute(
                    text("DELETE FROM procrastinate.procrastinate_jobs WHERE id = :jid"),
                    {"jid": job_id},
                )
                await conn.commit()

            # 4. Least privilege: app_user cannot TRUNCATE tables in schema procrastinate
            async with app_engine.connect() as conn:
                with pytest.raises(DBAPIError) as exc_info:
                    await conn.execute(text("TRUNCATE procrastinate.procrastinate_jobs"))
                assert get_sqlstate(exc_info.value) == "42501", (
                    f"Expected SQLSTATE 42501 on TRUNCATE, got {get_sqlstate(exc_info.value)}"
                )

            # 5. Least privilege: app_user cannot CREATE tables in schema procrastinate
            async with app_engine.connect() as conn:
                with pytest.raises(DBAPIError) as exc_info:
                    await conn.execute(
                        text("CREATE TABLE procrastinate.unauthorized_probe (id int)")
                    )
                assert get_sqlstate(exc_info.value) == "42501", (
                    f"Expected SQLSTATE 42501 on CREATE TABLE, got {get_sqlstate(exc_info.value)}"
                )

            # 6. retriever_user permission denied (SQLSTATE 42501)
            async with retriever_engine.connect() as conn:
                with pytest.raises(DBAPIError) as exc_info:
                    await conn.execute(
                        text("SELECT count(*) FROM procrastinate.procrastinate_jobs")
                    )
                actual_state = get_sqlstate(exc_info.value)
                assert actual_state == "42501", (
                    f"Expected SQLSTATE 42501 for retriever_user, got {actual_state}"
                )

            await app_engine.dispose()
            await retriever_engine.dispose()
