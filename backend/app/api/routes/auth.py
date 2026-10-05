"""Authentication and session management routes."""

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.config import ApiSettings
from app.api.deps import (
    get_api_settings,
    get_auth_provider,
    get_client_ip_hash,
    get_current_session,
    get_rate_limiter,
    validate_csrf,
)
from app.api.rate_limiter import LoginRateLimiter
from app.db.models import User
from app.db.session import unscoped_session
from app.ports.auth import AuthProvider
from app.ports.errors import AuthenticationError
from app.ports.types import AuthCredentials, SessionContext

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    email: str
    status: str = "authenticated"


class UserInfoResponse(BaseModel):
    user_id: str
    email: str
    is_platform_admin: bool


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
    rate_limiter: Annotated[LoginRateLimiter, Depends(get_rate_limiter)],
) -> LoginResponse:
    """Authenticate user with credentials, rate limit failures, and issue httpOnly session cookie.

    Security Rule: Never returns the session token in the JSON response body.
    """
    ip_hash = get_client_ip_hash(request, key=settings.auth_hmac_key)

    # 1. Rate limiting check
    if rate_limiter.is_rate_limited(ip_hash):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please wait before retrying.",
        )

    # 2. Authenticate
    try:
        token_obj = await auth_provider.authenticate(
            credentials=AuthCredentials(username=payload.email, password=payload.password),
            client_ip_hash=ip_hash,
        )
    except AuthenticationError:
        rate_limiter.record_failure(ip_hash)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        ) from None

    # 3. Reset rate limiter on successful authentication
    rate_limiter.reset(ip_hash)

    # 4. Generate random CSRF token
    csrf_token = secrets.token_urlsafe(24)

    # 5. Set session cookie (httpOnly, secure, sameSite)
    response.set_cookie(
        key=settings.cookie_name,
        value=token_obj.token,
        max_age=token_obj.expires_in,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )

    # 6. Set CSRF cookie (readable by frontend script to include in X-CSRF-Token header)
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=token_obj.expires_in,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )

    return LoginResponse(email=payload.email, status="authenticated")


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    settings: Annotated[ApiSettings, Depends(get_api_settings)],
    auth_provider: Annotated[AuthProvider, Depends(get_auth_provider)],
    _csrf: Annotated[None, Depends(validate_csrf)],
) -> dict[str, str]:
    """Revoke session token and clear authentication cookies."""
    token = request.cookies.get(settings.cookie_name)
    if token:
        await auth_provider.revoke(token)

    response.delete_cookie(key=settings.cookie_name, path="/")
    response.delete_cookie(key=settings.csrf_cookie_name, path="/")

    return {"detail": "Logged out successfully"}


@router.get("/me", response_model=UserInfoResponse)
async def get_current_user_info(
    session: Annotated[SessionContext, Depends(get_current_session)],
) -> UserInfoResponse:
    """Retrieve identity of currently authenticated session user."""
    uid = UUID(session.user_id)
    async with unscoped_session() as db_session:
        stmt = select(User).where(User.id == uid)
        result = await db_session.execute(stmt)
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        return UserInfoResponse(
            user_id=str(user.id),
            email=user.email,
            is_platform_admin=user.is_platform_admin,
        )
