"""Built-in AuthProvider using Argon2id password hashing and hashed session tokens in Postgres."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from sqlalchemy import func, select, update

from app.db.models import AuthEvent, Membership, Session, User
from app.db.session import get_app_session_factory, unscoped_session
from app.ports.auth import AuthProvider
from app.ports.errors import AuthenticationError
from app.ports.types import (
    AuthCredentials,
    AuthToken,
    SessionContext,
    UserRole,
)


class BuiltinAuthProvider(AuthProvider):
    """Database-backed session authentication provider."""

    def __init__(
        self,
        idle_timeout_minutes: int = 30,
        absolute_timeout_hours: int = 24,
    ) -> None:
        self.hasher = PasswordHasher()
        self.idle_timeout_minutes = idle_timeout_minutes
        self.absolute_timeout_hours = absolute_timeout_hours
        # Fixed dummy hash for timing attack mitigation on unknown emails
        self._dummy_hash = self.hasher.hash("dummy-fixed-timing-mitigation-secret")

    def hash_password(self, raw_password: str) -> str:
        """Hash a plaintext password using Argon2id."""
        return self.hasher.hash(raw_password)

    def verify_password(self, raw_password: str, hashed_password: str) -> bool:
        """Timing-safe password verification."""
        try:
            return self.hasher.verify(hashed_password, raw_password)
        except (VerifyMismatchError, VerificationError, Exception):
            return False

    def generate_session_token(self) -> str:
        """Generate high-entropy random URL-safe session token."""
        return secrets.token_urlsafe(32)

    def hash_session_token(self, token: str) -> str:
        """Compute SHA-256 hex digest of session token for storage and lookup."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def calculate_absolute_expiry(self, from_time: datetime | None = None) -> datetime:
        base = from_time or datetime.now(UTC)
        return base + timedelta(hours=self.absolute_timeout_hours)

    def is_session_expired(self, expires_at: datetime, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(UTC)
        return current_time >= expires_at

    def is_session_idle(self, last_seen_at: datetime | None, now: datetime | None = None) -> bool:
        if last_seen_at is None:
            return False
        current_time = now or datetime.now(UTC)
        idle_limit = timedelta(minutes=self.idle_timeout_minutes)
        return (current_time - last_seen_at) > idle_limit

    async def authenticate(
        self,
        credentials: AuthCredentials,
        client_ip_hash: str | None = None,
    ) -> AuthToken:
        """Verify user credentials, write to auth_events, and issue session token."""
        async with unscoped_session() as db_session:
            # Query user by email
            stmt = select(User).where(User.email == credentials.username)
            result = await db_session.execute(stmt)
            user = result.scalar_one_or_none()

            if user is None:
                # Timing mitigation: run argon2 verify on dummy hash so unknown email
                # takes comparable time to invalid password verification.
                self.verify_password(credentials.password, self._dummy_hash)
                # Log failure to auth_events (global, append-only)
                event = AuthEvent(
                    user_id=None,
                    outcome="failure",
                    reason_code="INVALID_CREDENTIALS",
                    client_ip_hash=client_ip_hash,
                )
                db_session.add(event)
                await db_session.flush()
                raise AuthenticationError("Invalid email or password")

            # Verify password
            is_valid = self.verify_password(credentials.password, user.hashed_password)
            if not is_valid:
                event = AuthEvent(
                    user_id=user.id,
                    outcome="failure",
                    reason_code="INVALID_CREDENTIALS",
                    client_ip_hash=client_ip_hash,
                )
                db_session.add(event)
                await db_session.flush()
                raise AuthenticationError("Invalid email or password")

            # Create session
            raw_token = self.generate_session_token()
            token_hash = self.hash_session_token(raw_token)
            now = datetime.now(UTC)
            expires_at = self.calculate_absolute_expiry(now)

            session_record = Session(
                user_id=user.id,
                token_hash=token_hash,
                created_at=now,
                expires_at=expires_at,
                last_seen_at=now,
            )
            db_session.add(session_record)

            # Log success to auth_events
            event = AuthEvent(
                user_id=user.id,
                outcome="success",
                reason_code="SUCCESS",
                client_ip_hash=client_ip_hash,
            )
            db_session.add(event)
            await db_session.flush()

            expires_in_secs = int((expires_at - now).total_seconds())
            return AuthToken(token=raw_token, expires_in=expires_in_secs)

    async def session(self, token: str) -> SessionContext:
        """Resolve, validate, and refresh active session from token."""
        token_hash = self.hash_session_token(token)
        now = datetime.now(UTC)

        async with unscoped_session() as db_session:
            stmt = (
                select(Session)
                .where(Session.token_hash == token_hash)
                .where(Session.revoked_at.is_(None))
            )
            result = await db_session.execute(stmt)
            sess = result.scalar_one_or_none()

            if sess is None:
                raise AuthenticationError("Session invalid or revoked")

            if self.is_session_expired(sess.expires_at, now=now):
                raise AuthenticationError("Session expired")

            if self.is_session_idle(sess.last_seen_at, now=now):
                raise AuthenticationError("Session idle timeout")

            # Update last_seen_at
            sess.last_seen_at = now
            await db_session.flush()

            return SessionContext(
                user_id=str(sess.user_id),
                tenant_id="",
                roles=[],
                session_id=str(sess.id),
            )

    async def revoke(self, token: str) -> bool:
        """Revoke a session token upon logout."""
        token_hash = self.hash_session_token(token)
        now = datetime.now(UTC)
        async with unscoped_session() as db_session:
            stmt = (
                update(Session)
                .where(Session.token_hash == token_hash)
                .where(Session.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            res = await db_session.execute(stmt)
            await db_session.flush()
            rowcount = int(getattr(res, "rowcount", 0))
            return rowcount > 0

    async def roles(self, user_id: str, tenant_id: str) -> list[UserRole]:
        """Fetch granted roles for a user within a specific tenant scope."""
        uid = UUID(user_id)
        tid = UUID(tenant_id)
        factory = get_app_session_factory()
        async with factory() as db_session, db_session.begin():
            # Set transaction-local tenant_id so RLS scopes query to tenant
            await db_session.execute(
                select(func.set_config("app.current_tenant_id", str(tid), True))
            )
            stmt = (
                select(Membership.role)
                .where(Membership.user_id == uid)
                .where(Membership.tenant_id == tid)
            )
            result = await db_session.execute(stmt)
            role_str = result.scalar_one_or_none()

            if role_str == "client_admin":
                return [UserRole.CLIENT_ADMIN]
            elif role_str == "staff":
                return [UserRole.STAFF]
            else:
                return []
