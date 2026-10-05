"""Unit tests for BuiltinAuthProvider and Argon2id session authentication."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.adapters.auth.builtin import BuiltinAuthProvider
from app.ports.types import AuthCredentials, UserRole


@pytest.fixture
def auth_provider() -> BuiltinAuthProvider:
    return BuiltinAuthProvider(
        idle_timeout_minutes=30,
        absolute_timeout_hours=24,
    )


class TestBuiltinAuthProviderUnit:
    """Verifies Argon2id password verification, session hashing, timeouts, and roles."""

    def test_argon2id_password_hashing_and_verification(
        self,
        auth_provider: BuiltinAuthProvider,
        runtime_test_password: str,
        runtime_alt_password: str,
    ) -> None:
        """Passwords must be hashed using argon2id and verified timing-safely."""
        hashed = auth_provider.hash_password(runtime_test_password)
        assert hashed.startswith("$argon2id$")
        assert auth_provider.verify_password(runtime_test_password, hashed) is True
        assert auth_provider.verify_password(runtime_alt_password, hashed) is False

    @pytest.mark.asyncio
    async def test_session_token_is_opaque_and_hashed_with_sha256(
        self, auth_provider: BuiltinAuthProvider
    ) -> None:
        """Session tokens must be high-entropy opaque strings hashed before storage."""
        raw_token = auth_provider.generate_session_token()
        assert len(raw_token) >= 32
        token_hash = auth_provider.hash_session_token(raw_token)
        assert len(token_hash) == 64
        # SHA-256 hex digest format
        int(token_hash, 16)
        assert token_hash != raw_token

    def test_session_expiration_calculations(self, auth_provider: BuiltinAuthProvider) -> None:
        """Calculates both absolute expiration and idle timeout limits."""
        now = datetime.now(UTC)
        expires_at = auth_provider.calculate_absolute_expiry(now)
        assert expires_at > now
        assert expires_at - now <= timedelta(hours=24, seconds=5)

        is_idle = auth_provider.is_session_idle(
            last_seen_at=now - timedelta(minutes=31),
            now=now,
        )
        assert is_idle is True

        is_not_idle = auth_provider.is_session_idle(
            last_seen_at=now - timedelta(minutes=15),
            now=now,
        )
        assert is_not_idle is False

    def test_session_is_expired(self, auth_provider: BuiltinAuthProvider) -> None:
        """Verifies session expiration against absolute expiry date."""
        now = datetime.now(UTC)
        assert (
            auth_provider.is_session_expired(
                expires_at=now - timedelta(seconds=1),
                now=now,
            )
            is True
        )
        assert (
            auth_provider.is_session_expired(
                expires_at=now + timedelta(minutes=5),
                now=now,
            )
            is False
        )

    @pytest.mark.asyncio
    async def test_authenticate_success_and_session_resolution(
        self,
        auth_provider: BuiltinAuthProvider,
        runtime_test_password: str,
    ) -> None:
        """Authenticating valid credentials yields session token resolvable via session()."""
        from app.db.models import User
        from app.db.session import unscoped_session

        email = f"user-{uuid4()}@example.com"
        hashed = auth_provider.hash_password(runtime_test_password)

        async with unscoped_session() as db_session:
            user = User(email=email, hashed_password=hashed, is_platform_admin=False)
            db_session.add(user)
            await db_session.flush()
            user_id = str(user.id)

        # Authenticate
        token_obj = await auth_provider.authenticate(
            AuthCredentials(username=email, password=runtime_test_password)
        )
        assert token_obj.token is not None

        # Resolve session
        sess_ctx = await auth_provider.session(token_obj.token)
        assert sess_ctx.user_id == user_id

        # Revoke session (logout)
        revoked = await auth_provider.revoke(token_obj.token)
        assert revoked is True

        # Subsequent session lookup fails
        from app.adapters.auth.builtin import AuthenticationError

        with pytest.raises(AuthenticationError):
            await auth_provider.session(token_obj.token)

    @pytest.mark.asyncio
    async def test_roles_resolution_from_memberships(
        self,
        auth_provider: BuiltinAuthProvider,
        runtime_test_password: str,
    ) -> None:
        """Roles are derived from memberships; non-members get empty list."""
        from app.db.models import Membership, Tenant, User
        from app.db.session import tenant_session, unscoped_session
        from app.ports.types import TenantContext

        async with unscoped_session() as db_session:
            t = Tenant(name="Tenant Test")
            db_session.add(t)
            await db_session.flush()

            u = User(
                email=f"member-{uuid4()}@example.com",
                hashed_password=auth_provider.hash_password(runtime_test_password),
                is_platform_admin=False,
            )
            db_session.add(u)
            await db_session.flush()
            tid = str(t.id)
            uid = str(u.id)

        t_ctx = TenantContext(
            tenant_id=tid,
            user_id=uid,
            role=UserRole.CLIENT_ADMIN,
            session_id="test-session",
        )
        async with tenant_session(t_ctx) as t_sess:
            mem = Membership(tenant_id=t.id, user_id=u.id, role="staff")
            t_sess.add(mem)
            await t_sess.flush()

        roles = await auth_provider.roles(uid, tid)
        assert roles == [UserRole.STAFF]

        # Non-member check
        other_tid = str(uuid4())
        roles_other = await auth_provider.roles(uid, other_tid)
        assert roles_other == []

    @pytest.mark.asyncio
    async def test_dummy_verify_runs_for_unknown_emails(
        self,
        auth_provider: BuiltinAuthProvider,
        monkeypatch: pytest.MonkeyPatch,
        runtime_test_password: str,
    ) -> None:
        """When email is unknown, Argon2 verify runs against dummy hash for timing defense."""
        from unittest.mock import MagicMock

        from app.ports.errors import AuthenticationError

        verify_mock = MagicMock(return_value=False)
        monkeypatch.setattr(auth_provider, "verify_password", verify_mock)

        unknown_email = f"unknown-{uuid4()}@example.com"
        with pytest.raises(AuthenticationError, match="Invalid email or password"):
            await auth_provider.authenticate(
                AuthCredentials(username=unknown_email, password=runtime_test_password)
            )

        assert verify_mock.call_count == 1
        call_password, call_hash = verify_mock.call_args[0]
        assert call_password == runtime_test_password
        assert call_hash == auth_provider._dummy_hash
