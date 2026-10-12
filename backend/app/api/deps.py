"""FastAPI dependency injection providers for auth, tenant context, and security."""

import hashlib
import hmac
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select

from app.api.config import ApiSettings
from app.api.rate_limiter import LoginRateLimiter
from app.db.models import User
from app.db.session import unscoped_session
from app.ports.auth import AuthProvider
from app.ports.errors import AuthenticationError
from app.ports.types import SessionContext, TenantContext, UserRole
from app.wiring import create_auth_provider


def get_api_settings(request: Request) -> ApiSettings:
    """Retrieve application settings from request app state or default."""
    if hasattr(request.app.state, "settings") and request.app.state.settings is not None:
        return cast(ApiSettings, request.app.state.settings)
    return ApiSettings()


def get_auth_provider(request: Request) -> AuthProvider:
    """Retrieve auth provider instance from request app state or default."""
    if hasattr(request.app.state, "auth_provider") and request.app.state.auth_provider is not None:
        return cast(AuthProvider, request.app.state.auth_provider)
    settings = get_api_settings(request)
    provider = create_auth_provider(
        idle_timeout_minutes=settings.idle_timeout_minutes,
        absolute_timeout_hours=settings.absolute_timeout_hours,
    )
    return cast(AuthProvider, provider)


def get_rate_limiter(request: Request) -> LoginRateLimiter:
    """Retrieve login rate limiter from request app state or default."""
    if hasattr(request.app.state, "rate_limiter") and request.app.state.rate_limiter is not None:
        return cast(LoginRateLimiter, request.app.state.rate_limiter)
    settings = get_api_settings(request)
    return LoginRateLimiter(
        max_attempts=settings.login_rate_limit_max_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
    )


def get_client_ip_hash(request: Request, key: str | None = None) -> str:
    """Derive HMAC-SHA256 hex digest of client IP address for telemetry and audit logging.

    Uses secret key from .env (ApiSettings.auth_hmac_key) to prevent rainbow table inversion.
    """
    client_host = request.client.host if request.client else "unknown"
    if key is None:
        settings = get_api_settings(request)
        key = settings.auth_hmac_key
    return hmac.new(key.encode("utf-8"), client_host.encode("utf-8"), hashlib.sha256).hexdigest()


async def validate_csrf(
    request: Request,
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
) -> None:
    """Protect state-changing requests against Cross-Site Request Forgery (CSRF)."""
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        session_cookie = request.cookies.get(settings.cookie_name)
        # If authenticated via browser cookie, enforce CSRF header check
        if session_cookie:
            csrf_header = request.headers.get("X-CSRF-Token")
            csrf_cookie = request.cookies.get(settings.csrf_cookie_name)
            if not csrf_header or not csrf_cookie or csrf_header != csrf_cookie:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="CSRF token verification failed",
                )


async def get_current_session(
    request: Request,
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
) -> SessionContext:
    """Extract and validate session token from httpOnly cookie or test-only Bearer token."""
    token: str | None = request.cookies.get(settings.cookie_name)

    if not token and settings.allow_bearer_auth:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    try:
        session_ctx = await auth_provider.session(token)
        return session_ctx
    except AuthenticationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        ) from None


async def get_tenant_context_for_assistant(
    assistant_id: str,
    session: Annotated[SessionContext, Depends(get_current_session)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
) -> TenantContext:
    """Derive TenantContext strictly from assistant lookup and user membership.

    Invariant 2 & 3: Never accept tenant_id from body, query or headers.
    Refuses with 404 if assistant does not exist or user lacks membership in assistant's tenant.
    """
    try:
        asst_uuid = UUID(assistant_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assistant not found",
        ) from None

    from sqlalchemy import text

    async with unscoped_session() as db_session:
        result = await db_session.execute(
            text("SELECT lookup_assistant_tenant(:asst_id)"),
            {"asst_id": asst_uuid},
        )
        tenant_uuid = result.scalar_one_or_none()
        if tenant_uuid is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")
        tenant_id = str(tenant_uuid)

    # Membership check applies to EVERYONE (including platform admin per rule 2)
    roles = await auth_provider.roles(session.user_id, tenant_id)
    if not roles:
        # Returns 404, not 403, preventing probing (rule 6)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")

    return TenantContext(
        tenant_id=tenant_id,
        user_id=session.user_id,
        role=roles[0],
        session_id=session.session_id,
    )


async def require_platform_admin(
    session: Annotated[SessionContext, Depends(get_current_session)],
) -> SessionContext:
    """Enforce platform admin status from users.is_platform_admin."""
    uid = UUID(session.user_id)
    async with unscoped_session() as db_session:
        stmt = select(User.is_platform_admin).where(User.id == uid)
        result = await db_session.execute(stmt)
        is_admin = result.scalar_one_or_none()
        if not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Platform admin privileges required",
            )
    return session


async def get_client_admin_tenant_context_for_assistant(
    assistant_id: str,
    session: Annotated[SessionContext, Depends(get_current_session)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
) -> TenantContext:
    """Derive TenantContext strictly from assistant lookup and client_admin membership.

    1. Resolves tenant via lookup_assistant_tenant(asst_id) as app_user (SECURITY DEFINER).
    2. If assistant does not exist or user lacks membership in assistant's tenant:
       returns 404 Not Found (anti-probing).
    3. If user has membership but lacks client_admin role:
       returns 403 Forbidden.
    """
    try:
        asst_uuid = UUID(assistant_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assistant not found",
        ) from None

    from sqlalchemy import text

    async with unscoped_session() as db_session:
        result = await db_session.execute(
            text("SELECT lookup_assistant_tenant(:asst_id)"),
            {"asst_id": asst_uuid},
        )
        tenant_uuid = result.scalar_one_or_none()
        if tenant_uuid is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")
        tenant_id = str(tenant_uuid)

    roles = await auth_provider.roles(session.user_id, tenant_id)
    if not roles:
        # Non-member returns 404 Not Found to prevent ID probing
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")

    if UserRole.CLIENT_ADMIN not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client admin privileges required",
        )

    return TenantContext(
        tenant_id=tenant_id,
        user_id=session.user_id,
        role=UserRole.CLIENT_ADMIN,
        session_id=session.session_id,
    )


# Alias for backward compatibility if referenced
get_client_admin_tenant_context = get_client_admin_tenant_context_for_assistant
