"""Integration test for Migration 0013 (rate-limit indexes on audit_events).

Tests upgrade -> downgrade -> upgrade lifecycle in an isolated throwaway database.
Enforces:
- Index creation covering (tenant_id, event_type, created_at) and (user_id, event_type, created_at).
- EXPLAIN plan verification on the rate-limit queries showing Index Scan / Index Only Scan
  using the new composite indexes.
- Isolation: never run against the shared database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import isolated_throwaway_database


@pytest.mark.asyncio
async def test_migration_0013_rate_limit_indexes_upgrade_downgrade_and_explain() -> None:
    """Verify migration 0013 creates both composite indexes and improves EXPLAIN plan."""
    async with isolated_throwaway_database() as throwaway_db:
        # 1. Upgrade to 0012 first
        command.upgrade(throwaway_db.cfg, "0012_session_documents")

        test_engine = create_async_engine(throwaway_db.url)
        async with test_engine.begin() as conn:
            pre_indexes = {
                str(r[0])
                for r in (
                    await conn.execute(
                        text("SELECT indexname FROM pg_indexes WHERE tablename = 'audit_events';")
                    )
                ).fetchall()
            }
            assert "ix_audit_events_tenant_event_created" not in pre_indexes
            assert "ix_audit_events_user_event_created" not in pre_indexes

        # 2. Upgrade to 0013_rate_limit_indexes
        command.upgrade(throwaway_db.cfg, "0013_rate_limit_indexes")

        tid = uuid4()
        uid = uuid4()
        cutoff = datetime.now(UTC) - timedelta(seconds=60)

        async with test_engine.begin() as conn:
            post_rows = (
                await conn.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE tablename = 'audit_events';"
                    )
                )
            ).fetchall()
            post_indexes = {str(r[0]): str(r[1]) for r in post_rows}

            assert "ix_audit_events_tenant_event_created" in post_indexes
            assert (
                "(tenant_id, event_type, created_at)"
                in post_indexes["ix_audit_events_tenant_event_created"]
            )
            assert "ix_audit_events_user_event_created" in post_indexes
            assert (
                "(user_id, event_type, created_at)"
                in post_indexes["ix_audit_events_user_event_created"]
            )

            # Seed sample rows and disable seqscan so planner deterministically shows index usage
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:tid, 'Rate Limit Tenant');"),
                {"tid": tid},
            )
            await conn.execute(text("SET LOCAL enable_seqscan = off;"))

            explain_tenant = [
                str(r[0])
                for r in (
                    await conn.execute(
                        text(
                            "EXPLAIN SELECT count(id) FROM audit_events "
                            "WHERE tenant_id = :tid AND event_type = 'question' "
                            "AND created_at >= :cutoff"
                        ),
                        {"tid": tid, "cutoff": cutoff},
                    )
                ).fetchall()
            ]
            explain_tenant_str = "\n".join(explain_tenant)
            assert "ix_audit_events_tenant_event_created" in explain_tenant_str

            explain_user = [
                str(r[0])
                for r in (
                    await conn.execute(
                        text(
                            "EXPLAIN SELECT count(id) FROM audit_events "
                            "WHERE user_id = :uid AND event_type = 'question' "
                            "AND created_at >= :cutoff"
                        ),
                        {"uid": uid, "cutoff": cutoff},
                    )
                ).fetchall()
            ]
            explain_user_str = "\n".join(explain_user)
            assert "ix_audit_events_user_event_created" in explain_user_str

        # 3. Downgrade back to 0012
        command.downgrade(throwaway_db.cfg, "0012_session_documents")

        async with test_engine.begin() as conn:
            downgraded_indexes = {
                str(r[0])
                for r in (
                    await conn.execute(
                        text("SELECT indexname FROM pg_indexes WHERE tablename = 'audit_events';")
                    )
                ).fetchall()
            }
            assert "ix_audit_events_tenant_event_created" not in downgraded_indexes
            assert "ix_audit_events_user_event_created" not in downgraded_indexes

        # 4. Re-upgrade to 0013_rate_limit_indexes
        command.upgrade(throwaway_db.cfg, "0013_rate_limit_indexes")

        async with test_engine.begin() as conn:
            reupgraded_indexes = {
                str(r[0])
                for r in (
                    await conn.execute(
                        text("SELECT indexname FROM pg_indexes WHERE tablename = 'audit_events';")
                    )
                ).fetchall()
            }
            assert "ix_audit_events_tenant_event_created" in reupgraded_indexes
            assert "ix_audit_events_user_event_created" in reupgraded_indexes

        await test_engine.dispose()


@pytest.mark.asyncio
async def test_db_rate_limit_enforces_per_user_and_per_tenant_across_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rate limit queries PostgreSQL audit_events so limits survive process restarts."""
    import asyncio

    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.api.rate_limiter import check_question_rate_limit
    from app.core.guards.input_guards import QUESTION_RATE_LIMIT_MESSAGE
    from app.db.models import Assistant, AuditEvent, Tenant, User
    from app.db.session import get_app_engine, reset_session_engines, tenant_session
    from app.ports.types import TenantContext, UserRole
    from tests.conftest import make_test_password

    async with isolated_throwaway_database() as throwaway_db:
        await asyncio.to_thread(command.upgrade, throwaway_db.cfg, "head")

        monkeypatch.setenv("DATABASE_URL", throwaway_db.url)
        reset_session_engines()

        engine = create_async_engine(throwaway_db.url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        tenant_id = uuid4()
        user1_id = uuid4()
        user2_id = uuid4()
        asst_id = uuid4()

        try:
            async with session_factory() as admin_sess, admin_sess.begin():
                admin_sess.add(Tenant(id=tenant_id, name="Rate Limit Clinic"))
                await admin_sess.flush()
                admin_sess.add_all(
                    [
                        User(
                            id=user1_id,
                            email="u1@clinic.test",
                            hashed_password=make_test_password(),
                        ),
                        User(
                            id=user2_id,
                            email="u2@clinic.test",
                            hashed_password=make_test_password(),
                        ),
                        Assistant(
                            id=asst_id,
                            tenant_id=tenant_id,
                            name="Clinic Assistant",
                            prompt_id="default",
                            prompt_version="1.0",
                        ),
                    ]
                )

            t_ctx_u1 = TenantContext(
                tenant_id=str(tenant_id),
                user_id=str(user1_id),
                role=UserRole.STAFF,
                session_id="sess-u1",
            )
            t_ctx_u2 = TenantContext(
                tenant_id=str(tenant_id),
                user_id=str(user2_id),
                role=UserRole.STAFF,
                session_id="sess-u2",
            )

            async with tenant_session(t_ctx_u1) as db_sess:
                for i in range(3):
                    await check_question_rate_limit(
                        db_session=db_sess,
                        tenant_uuid=tenant_id,
                        user_uuid=user1_id,
                        user_max_requests=3,
                        tenant_max_requests=5,
                        window_seconds=60,
                    )
                    db_sess.add(
                        AuditEvent(
                            tenant_id=tenant_id,
                            user_id=user1_id,
                            event_type="question",
                            action="question.answered",
                            resource_id=f"req-u1-{i}",
                        )
                    )
                    await db_sess.flush()

            await get_app_engine().dispose()
            reset_session_engines()

            async with tenant_session(t_ctx_u1) as db_sess_restart:
                with pytest.raises(HTTPException) as exc_info:
                    await check_question_rate_limit(
                        db_session=db_sess_restart,
                        tenant_uuid=tenant_id,
                        user_uuid=user1_id,
                        user_max_requests=3,
                        tenant_max_requests=5,
                        window_seconds=60,
                    )
                assert exc_info.value.status_code == 429
                assert exc_info.value.detail == QUESTION_RATE_LIMIT_MESSAGE

            async with tenant_session(t_ctx_u2) as db_sess_u2:
                for i in range(2):
                    await check_question_rate_limit(
                        db_session=db_sess_u2,
                        tenant_uuid=tenant_id,
                        user_uuid=user2_id,
                        user_max_requests=3,
                        tenant_max_requests=5,
                        window_seconds=60,
                    )
                    db_sess_u2.add(
                        AuditEvent(
                            tenant_id=tenant_id,
                            user_id=user2_id,
                            event_type="question",
                            action="question.answered",
                            resource_id=f"req-u2-{i}",
                        )
                    )
                    await db_sess_u2.flush()

                with pytest.raises(HTTPException) as tenant_exc_info:
                    await check_question_rate_limit(
                        db_session=db_sess_u2,
                        tenant_uuid=tenant_id,
                        user_uuid=user2_id,
                        user_max_requests=3,
                        tenant_max_requests=5,
                        window_seconds=60,
                    )
                assert tenant_exc_info.value.status_code == 429
                assert tenant_exc_info.value.detail == QUESTION_RATE_LIMIT_MESSAGE
        finally:
            await get_app_engine().dispose()
            reset_session_engines()
            await engine.dispose()
