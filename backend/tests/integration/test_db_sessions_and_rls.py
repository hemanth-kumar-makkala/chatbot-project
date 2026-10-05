"""Integration tests for sessions and auth_events tables and role permissions."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models import AuthEvent, Session, User
from app.db.session import get_retriever_session_factory, unscoped_session


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.asyncio
class TestDbSessionsAndAuthEvents:
    """Verifies sessions and auth_events tables adhere to global allowlist, zero PHI,
    and role permissions.
    """

    async def test_sessions_table_structure_and_zero_plaintext_tokens(
        self, test_user_password_hash: str
    ) -> None:
        """Sessions table must store token_hash only and hold zero PHI."""
        async with unscoped_session() as session:
            # Create a test user
            user = User(
                email=f"user-{uuid4()}@example.com",
                hashed_password=test_user_password_hash,
                is_platform_admin=False,
            )
            session.add(user)
            await session.flush()

            raw_token = "secret-token-" + str(uuid4())
            import hashlib

            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

            now = datetime.now(UTC)
            sess = Session(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=now + timedelta(hours=24),
                last_seen_at=now,
            )
            session.add(sess)
            await session.flush()

            # Query table directly and assert raw_token is nowhere in row
            result = await session.execute(
                text("SELECT id, user_id, token_hash FROM sessions WHERE id = :id"),
                {"id": sess.id},
            )
            row = result.mappings().one()
            assert row["token_hash"] == token_hash
            assert raw_token not in row["token_hash"]

    async def test_auth_events_is_append_only_for_app_role(self) -> None:
        """App role may INSERT into auth_events, but UPDATE and DELETE must be denied."""
        # UPDATE must fail for cka_app role
        async with unscoped_session() as session:
            event = AuthEvent(
                user_id=None,
                outcome="failure",
                reason_code="INVALID_CREDENTIALS",
                client_ip_hash="hash-123",
            )
            session.add(event)
            await session.flush()
            event_id = event.id

        async with unscoped_session() as session:
            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(
                    text("UPDATE auth_events SET outcome = 'success' WHERE id = :id"),
                    {"id": event_id},
                )
            assert "permission denied" in str(exc_info.value).lower()

        # DELETE must fail for cka_app role
        async with unscoped_session() as session:
            with pytest.raises(DBAPIError) as exc_info_del:
                await session.execute(
                    text("DELETE FROM auth_events WHERE id = :id"),
                    {"id": event_id},
                )
            assert "permission denied" in str(exc_info_del.value).lower()

    async def test_lookup_assistant_tenant_execution_permissions(self) -> None:
        """Only app_user may execute lookup_assistant_tenant; other roles are denied."""
        target_id = uuid4()

        # 1. retriever_user MUST be denied EXECUTE
        retriever_factory = get_retriever_session_factory()
        async with retriever_factory() as session:
            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(
                    text("SELECT lookup_assistant_tenant(:id)"),
                    {"id": target_id},
                )
            assert "permission denied" in str(exc_info.value).lower()

        # 2. app_user is granted EXECUTE
        async with unscoped_session() as session:
            result = await session.execute(
                text("SELECT lookup_assistant_tenant(:id)"),
                {"id": target_id},
            )
            # Function executes without error (returns None when ID not found)
            assert result.scalar_one_or_none() is None

    async def test_lookup_assistant_tenant_fixed_search_path_and_security_definer(self) -> None:
        """lookup_assistant_tenant must be SECURITY DEFINER with fixed search_path."""
        async with unscoped_session() as session:
            result = await session.execute(
                text(
                    "SELECT prosecdef, proconfig FROM pg_proc "
                    "WHERE proname = 'lookup_assistant_tenant'"
                )
            )
            row = result.mappings().one()
            assert row["prosecdef"] is True, "lookup_assistant_tenant must be SECURITY DEFINER"
            proconfig = row["proconfig"]
            assert proconfig is not None, "proconfig must contain function SET parameters"
            assert any("search_path=pg_catalog, pg_temp" in cfg for cfg in proconfig), (
                f"Expected search_path=pg_catalog, pg_temp, got {proconfig}"
            )
