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
        monkeypatch: pytest.MonkeyPatch,
        runtime_test_password: str,
    ) -> None:
        """Authenticating valid credentials yields session token resolvable via session()."""
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock

        import app.adapters.auth.builtin as builtin_mod
        from app.db.models import Session, User

        email = f"user-{uuid4()}@example.com"
        hashed = auth_provider.hash_password(runtime_test_password)
        user_uuid = uuid4()
        user = User(id=user_uuid, email=email, hashed_password=hashed, is_platform_admin=False)
        user_id = str(user.id)

        stored_sessions: dict[str, Session] = {}

        class FakeDbSession:
            def add(self, obj: object) -> None:
                if isinstance(obj, Session):
                    if obj.id is None:
                        obj.id = uuid4()
                    stored_sessions[obj.token_hash] = obj

            async def flush(self) -> None:
                pass

            async def execute(self, stmt: object) -> object:
                res = MagicMock()
                stmt_str = str(stmt)
                if "FROM users" in stmt_str:
                    res.scalar_one_or_none.return_value = user
                elif "UPDATE sessions" in stmt_str:
                    updated = 0
                    for s in stored_sessions.values():
                        if s.revoked_at is None:
                            s.revoked_at = datetime.now(UTC)
                            updated += 1
                    res.rowcount = updated
                elif "FROM sessions" in stmt_str:
                    active = next(
                        (s for s in stored_sessions.values() if s.revoked_at is None),
                        None,
                    )
                    res.scalar_one_or_none.return_value = active
                return res

        @asynccontextmanager
        async def fake_unscoped_session():
            yield FakeDbSession()

        monkeypatch.setattr(builtin_mod, "unscoped_session", fake_unscoped_session)

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
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Roles are derived from memberships; non-members get empty list."""
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock

        import app.adapters.auth.builtin as builtin_mod

        tid = str(uuid4())
        uid = str(uuid4())
        current_tid: list[str] = [""]

        class FakeSession:
            @asynccontextmanager
            async def begin(self):
                yield

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def execute(self, stmt: object) -> object:
                res = MagicMock()
                compiled = stmt.compile()  # type: ignore[attr-defined]
                params = compiled.params
                stmt_str = str(compiled)
                if "set_config" in stmt_str:
                    for val in params.values():
                        if val in (tid,):
                            current_tid[0] = str(val)
                        elif isinstance(val, str) and len(val) == 36:
                            current_tid[0] = val
                    res.scalar_one_or_none.return_value = current_tid[0]
                elif "FROM memberships" in stmt_str:
                    if current_tid[0] == tid:
                        res.scalar_one_or_none.return_value = "staff"
                    else:
                        res.scalar_one_or_none.return_value = None
                return res

        monkeypatch.setattr(builtin_mod, "get_app_session_factory", lambda: (lambda: FakeSession()))

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
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock

        import app.adapters.auth.builtin as builtin_mod
        from app.ports.errors import AuthenticationError

        fake_db = MagicMock()
        fake_res = MagicMock()
        fake_res.scalar_one_or_none.return_value = None
        fake_db.execute = AsyncMock(return_value=fake_res)
        fake_db.flush = AsyncMock()

        @asynccontextmanager
        async def fake_unscoped_session():
            yield fake_db

        monkeypatch.setattr(builtin_mod, "unscoped_session", fake_unscoped_session)

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
