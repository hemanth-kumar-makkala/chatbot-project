"""Integration tests for Database Schema, Foreign Keys, and RLS (Task 1.4a).

Executed against real PostgreSQL running in Docker (cka-postgres).
Verifies:
- All 8 Phase 1 tables exist with UUID primary keys.
- Meta-test: every table is either tenant-scoped (tenant_id + RLS enabled & forced) or on the
  explicit global-table allowlist.
- Composite foreign keys (tenant_id, parent_id) -> parent(tenant_id, id) block cross-tenant
  references.
- App and retriever roles own 0 tables, are not superusers, and have NOBYPASSRLS.
- Transaction-local tenant scoping: NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
  returns 0 rows and raises no error when unscoped.
- Tenant isolation: Tenant A cannot read, update, or delete Tenant B rows.
- Cross-transaction leak prevention on pooled connections.
- Role permissions: retriever is read-only; audit_events allows insert-only (no update/delete).
"""

from typing import cast
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models import Assistant, AuditEvent, Conversation, Message, Tenant, User
from app.db.session import (
    get_app_engine,
    retriever_tenant_session,
    tenant_session,
    unscoped_session,
)
from app.ports.types import TenantContext, UserRole

GLOBAL_TABLE_ALLOWLIST = {
    "tenants",
    "users",
    "sessions",
    "auth_events",
    "alembic_version",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestDatabaseSchemaMetadata:
    """Verifies table structures, column definitions, and UUID primary keys."""

    def test_alembic_migrations_upgrade_and_downgrade(self) -> None:
        from pathlib import Path

        from alembic import command
        from alembic.config import Config

        backend_dir = Path(__file__).resolve().parents[2]
        alembic_ini_path = backend_dir / "alembic.ini"
        cfg = Config(str(alembic_ini_path))
        # Downgrade to base and upgrade back to head to verify migration idempotency
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")

    async def test_phase1_tables_exist_with_uuid_primary_keys(self) -> None:
        engine = get_app_engine()
        async with engine.connect() as conn:
            # Query all table primary keys and types
            result = await conn.execute(
                text(
                    """
                    SELECT kcu.table_name, c.column_name, c.data_type, c.udt_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.columns c
                        ON kcu.table_name = c.table_name
                        AND kcu.column_name = c.column_name
                        AND kcu.table_schema = c.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                        AND tc.table_schema = 'public'
                    """
                )
            )
            pks = {row[0]: (row[1], row[3]) for row in result.fetchall()}

            expected_tables = [
                "tenants",
                "users",
                "assistants",
                "memberships",
                "conversations",
                "messages",
                "run_steps",
                "audit_events",
            ]
            for tbl in expected_tables:
                assert tbl in pks, f"Table '{tbl}' missing primary key"
                pk_col, udt_name = pks[tbl]
                assert pk_col == "id", f"Table '{tbl}' PK column is '{pk_col}', expected 'id'"
                assert udt_name == "uuid", f"Table '{tbl}' PK type is '{udt_name}', expected 'uuid'"

    async def test_assistants_uses_prompt_registry_references(self) -> None:
        engine = get_app_engine()
        async with engine.connect() as conn:
            cols = await conn.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'assistants'
                    """
                )
            )
            col_names = {row[0] for row in cols.fetchall()}
            assert "prompt_id" in col_names
            assert "prompt_version" in col_names
            assert "system_prompt" not in col_names, (
                "Assistants table must not contain system_prompt"
            )

    async def test_run_steps_schema_no_content_columns(self) -> None:
        engine = get_app_engine()
        async with engine.connect() as conn:
            cols = await conn.execute(
                text(
                    """
                    SELECT column_name, udt_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'run_steps'
                    """
                )
            )
            col_map = {row[0]: row[1] for row in cols.fetchall()}
            assert "status" in col_map
            assert "model_id" in col_map
            assert "model_version" in col_map
            assert "prompt_id" in col_map
            assert "prompt_version" in col_map
            assert "public_error_ref" in col_map
            assert "evidence_ids" in col_map
            assert col_map["evidence_ids"] == "_uuid", "evidence_ids must be UUID array"

            # Invariant 10: operational logs and telemetry must carry zero prompt/doc content
            banned_content_cols = {"content", "prompt", "message", "query", "answer", "text"}
            assert banned_content_cols.isdisjoint(col_map.keys()), (
                "run_steps must not have content columns"
            )

    async def test_memberships_role_restricted_and_platform_admin_flag(self) -> None:
        engine = get_app_engine()
        async with engine.connect() as conn:
            # Check users table for is_platform_admin flag
            user_cols = await conn.execute(
                text(
                    """
                    SELECT column_name, data_type FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'users'
                    """
                )
            )
            user_col_map = {row[0]: row[1] for row in user_cols.fetchall()}
            assert "is_platform_admin" in user_col_map
            assert user_col_map["is_platform_admin"] == "boolean"

            # Check memberships check constraint
            checks = await conn.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid) FROM pg_constraint
                    WHERE conname = 'ck_memberships_role'
                    """
                )
            )
            clause = checks.scalar()
            assert clause is not None
            assert "client_admin" in clause
            assert "staff" in clause
            assert "platform_admin" not in clause, "platform_admin must not be in memberships.role"


class TestRowLevelSecurityConfiguration:
    """Verifies RLS configuration and PostgreSQL role safety."""

    async def test_meta_all_tables_either_tenant_scoped_or_global_allowlist(self) -> None:
        engine = get_app_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT
                        c.relname as table_name,
                        c.relrowsecurity as rls_enabled,
                        c.relforcerowsecurity as rls_forced,
                        EXISTS (
                            SELECT 1 FROM information_schema.columns col
                            WHERE col.table_schema = 'public'
                              AND col.table_name = c.relname
                              AND col.column_name = 'tenant_id'
                        ) as has_tenant_id
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind = 'r'
                    """
                )
            )
            tables = result.fetchall()

            for tbl_name, rls_enabled, rls_forced, has_tenant_id in tables:
                if tbl_name in GLOBAL_TABLE_ALLOWLIST:
                    assert not has_tenant_id, f"Global table '{tbl_name}' should not have tenant_id"
                else:
                    assert has_tenant_id, f"Table '{tbl_name}' must be tenant-scoped with tenant_id"
                    assert rls_enabled, f"Table '{tbl_name}' must have RLS enabled (relrowsecurity)"
                    assert rls_forced, (
                        f"Table '{tbl_name}' must have RLS forced (relforcerowsecurity)"
                    )

    async def test_postgres_roles_permissions_and_no_bypassrls(self) -> None:
        engine = get_app_engine()
        async with engine.connect() as conn:
            roles_res = await conn.execute(
                text(
                    """
                    SELECT rolname, rolsuper, rolbypassrls
                    FROM pg_roles
                    WHERE rolname IN ('app_user', 'retriever_user')
                    """
                )
            )
            roles = {row[0]: (row[1], row[2]) for row in roles_res.fetchall()}
            assert "app_user" in roles, "app_user role must exist in Postgres"
            assert "retriever_user" in roles, "retriever_user role must exist in Postgres"

            for role_name in ("app_user", "retriever_user"):
                is_super, bypass_rls = roles[role_name]
                assert not is_super, f"Role '{role_name}' must not be superuser"
                assert not bypass_rls, f"Role '{role_name}' must not have BYPASSRLS privilege"

            # Assert roles own zero tables
            owned_res = await conn.execute(
                text(
                    """
                    SELECT count(*) FROM pg_tables
                    WHERE tableowner IN ('app_user', 'retriever_user')
                    """
                )
            )
            owned_count = owned_res.scalar()
            assert owned_count == 0, "app_user and retriever_user must own no tables"


class TestTenantIsolationAndTransactionScoping:
    """Verifies transaction-local tenant scoping and isolation across transactions."""

    async def test_no_tenant_context_returns_zero_rows_without_error(self) -> None:
        async with unscoped_session() as session:
            # Query tenant table without setting tenant_id
            assistants = (await session.execute(sa.select(Assistant))).scalars().all()
            assert assistants == [], "Unscoped query must return 0 rows under RLS"

            conversations = (await session.execute(sa.select(Conversation))).scalars().all()
            assert conversations == [], "Unscoped query must return 0 rows under RLS"

    async def test_tenant_a_cannot_read_or_mutate_tenant_b_rows(self) -> None:
        tenant_a_id = uuid4()
        tenant_b_id = uuid4()
        ctx_a = TenantContext(
            tenant_id=str(tenant_a_id),
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="s1",
        )
        ctx_b = TenantContext(
            tenant_id=str(tenant_b_id),
            user_id="user-2",
            role=UserRole.STAFF,
            session_id="s2",
        )

        # Create tenants via unscoped admin session
        async with unscoped_session() as session:
            session.add_all(
                [
                    Tenant(id=tenant_a_id, name="Tenant A"),
                    Tenant(id=tenant_b_id, name="Tenant B"),
                ]
            )
            await session.commit()

        # Tenant A creates an assistant
        ast_a_id = uuid4()
        async with tenant_session(ctx_a) as session:
            session.add(
                Assistant(
                    id=ast_a_id,
                    tenant_id=tenant_a_id,
                    name="Tenant A Assistant",
                    prompt_id="prompt-1",
                    prompt_version="1.0",
                )
            )

        # Tenant B queries assistants -> must see 0 rows
        async with tenant_session(ctx_b) as session:
            b_results = (await session.execute(sa.select(Assistant))).scalars().all()
            assert len(b_results) == 0, "Tenant B must not see Tenant A's assistant"

            # Tenant B attempts to update Tenant A's assistant
            upd_res = cast(
                sa.CursorResult[object],
                await session.execute(
                    sa.update(Assistant)
                    .where(Assistant.id == ast_a_id)
                    .values(name="Compromised Assistant")
                ),
            )
            assert upd_res.rowcount == 0, "Tenant B must not be able to update Tenant A records"

            # Tenant B attempts to delete Tenant A's assistant
            del_res = cast(
                sa.CursorResult[object],
                await session.execute(sa.delete(Assistant).where(Assistant.id == ast_a_id)),
            )
            assert del_res.rowcount == 0, "Tenant B must not be able to delete Tenant A records"

    async def test_tenant_a_cannot_insert_with_tenant_b_id(self) -> None:
        tenant_a_id = uuid4()
        tenant_b_id = uuid4()
        ctx_a = TenantContext(
            tenant_id=str(tenant_a_id),
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="s1",
        )

        # Create tenants
        async with unscoped_session() as session:
            session.add_all(
                [
                    Tenant(id=tenant_a_id, name="Tenant A"),
                    Tenant(id=tenant_b_id, name="Tenant B"),
                ]
            )
            await session.commit()

        # Tenant A attempts to insert a row with tenant_id = tenant_B -> RLS WITH CHECK violation
        with pytest.raises(DBAPIError) as exc_info:
            async with tenant_session(ctx_a) as session:
                session.add(
                    Assistant(
                        id=uuid4(),
                        tenant_id=tenant_b_id,  # Mismatched tenant
                        name="Spoofed Assistant",
                        prompt_id="prompt-1",
                        prompt_version="1.0",
                    )
                )

        assert "row-level security policy" in str(exc_info.value).lower()

    async def test_tenant_context_does_not_leak_across_transactions(self) -> None:
        """On a reused pooled connection, transaction 1 setting does not leak into transaction 2."""
        tenant_id = uuid4()
        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="s1",
        )

        # Create tenant and assistant
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Tenant Leak Test"))
            await session.commit()

        async with tenant_session(ctx) as session:
            session.add(
                Assistant(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    name="Assistant 1",
                    prompt_id="prompt-1",
                    prompt_version="1.0",
                )
            )

        # Transaction 2 on same engine pool without tenant setting
        async with unscoped_session() as session:
            # Check current_setting is empty
            current_setting = (
                await session.execute(text("SELECT current_setting('app.current_tenant_id', true)"))
            ).scalar()
            assert current_setting in ("", None), "Transaction setting leaked across connections!"

            # Query must return zero rows with no error
            assistants = (await session.execute(sa.select(Assistant))).scalars().all()
            assert len(assistants) == 0

    async def test_cross_tenant_foreign_key_composite_enforcement(
        self, test_user_password_hash: str
    ) -> None:
        """Composite foreign key (tenant_id, parent_id) blocks cross-tenant pointing."""
        tenant_a_id = uuid4()
        tenant_b_id = uuid4()
        ctx_a = TenantContext(
            tenant_id=str(tenant_a_id),
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="s1",
        )
        ctx_b = TenantContext(
            tenant_id=str(tenant_b_id),
            user_id="user-2",
            role=UserRole.STAFF,
            session_id="s2",
        )

        user_id = uuid4()
        conv_b_id = uuid4()
        ast_b_id = uuid4()

        async with unscoped_session() as session:
            session.add_all(
                [
                    Tenant(id=tenant_a_id, name="Tenant A"),
                    Tenant(id=tenant_b_id, name="Tenant B"),
                    User(
                        id=user_id,
                        email=f"user_{uuid4()}@example.com",
                        hashed_password=test_user_password_hash,
                    ),
                ]
            )
            await session.commit()

        # Tenant B creates assistant and conversation
        async with tenant_session(ctx_b) as session:
            session.add(
                Assistant(
                    id=ast_b_id,
                    tenant_id=tenant_b_id,
                    name="Assistant B",
                    prompt_id="p1",
                    prompt_version="1.0",
                )
            )
            session.add(
                Conversation(
                    id=conv_b_id,
                    tenant_id=tenant_b_id,
                    user_id=user_id,
                    assistant_id=ast_b_id,
                    title="Conversation B",
                )
            )

        # Tenant A attempts to insert a Message pointing to Tenant B's conversation
        # Even if RLS were bypassed, composite FK (tenant_id, conversation_id) must reject it!
        with pytest.raises((IntegrityError, DBAPIError)):
            async with tenant_session(ctx_a) as session:
                session.add(
                    Message(
                        id=uuid4(),
                        tenant_id=tenant_a_id,
                        conversation_id=conv_b_id,  # Points to Tenant B's conversation
                        role="user",
                        content="Cross tenant probe",
                    )
                )


class TestRoleTablePermissions:
    """Verifies Postgres privileges for retriever_user and app_user."""

    async def test_retriever_role_cannot_write_anything(self) -> None:
        tenant_id = uuid4()
        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="s1",
        )

        # Create tenant
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Retriever Perms Test"))
            await session.commit()

        # Connecting as retriever_user: SELECT succeeds
        async with retriever_tenant_session(ctx) as session:
            assistants = (await session.execute(sa.select(Assistant))).scalars().all()
            assert isinstance(assistants, list)

            # Write attempt must raise permission denied
            with pytest.raises(DBAPIError) as exc_info:
                session.add(
                    Assistant(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        name="Unauthorized Assistant",
                        prompt_id="p1",
                        prompt_version="1.0",
                    )
                )
                await session.flush()

            assert "permission denied" in str(exc_info.value).lower()

    async def test_audit_events_allows_insert_only(self) -> None:
        tenant_id = uuid4()
        ctx = TenantContext(
            tenant_id=str(tenant_id),
            user_id="user-1",
            role=UserRole.STAFF,
            session_id="s1",
        )
        event_id = uuid4()

        # Create tenant
        async with unscoped_session() as session:
            session.add(Tenant(id=tenant_id, name="Audit Perms Test"))
            await session.commit()

        # app_user: INSERT succeeds
        async with tenant_session(ctx) as session:
            session.add(
                AuditEvent(
                    id=event_id,
                    tenant_id=tenant_id,
                    event_type="AUTH",
                    action="LOGIN",
                )
            )

        # app_user: UPDATE must fail with permission denied
        async with tenant_session(ctx) as session:
            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(
                    sa.update(AuditEvent).where(AuditEvent.id == event_id).values(action="TAMPERED")
                )
            assert "permission denied" in str(exc_info.value).lower()

        # app_user: DELETE must fail with permission denied
        async with tenant_session(ctx) as session:
            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(sa.delete(AuditEvent).where(AuditEvent.id == event_id))
            assert "permission denied" in str(exc_info.value).lower()
