"""FastAPI application configuration and settings."""

import os
from typing import Literal

from pydantic import BaseModel, Field


def _default_cookie_secure() -> bool:
    """Secure cookie defaults to on (True). Only an explicit local-dev setting turns it off."""
    val = os.getenv("COOKIE_SECURE")
    if val is not None:
        return val.strip().lower() not in ("false", "0", "no", "off")
    return True


def _default_auth_hmac_key() -> str:
    """Secret key for client_ip_hash HMAC-SHA256 pseudonymization, loaded from .env."""
    return os.getenv("AUTH_HMAC_KEY", "dev-secret-auth-hmac-key-placeholder")


class ApiSettings(BaseModel):
    """Configuration settings for the FastAPI application."""

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        description="Allowed web origins for CORS. Never allow '*'.",
    )
    cookie_name: str = "session_token"
    csrf_cookie_name: str = "csrf_token"
    cookie_secure: bool = Field(
        default_factory=_default_cookie_secure,
        description="Secure cookie flag. Defaults to True (HTTPS).",
    )
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    idle_timeout_minutes: int = 30
    absolute_timeout_hours: int = 24
    allow_bearer_auth: bool = False  # Strictly cookie-only in standard app; True in test overrides
    login_rate_limit_max_attempts: int = 5
    login_rate_limit_window_seconds: int = 60
    auth_hmac_key: str = Field(
        default_factory=_default_auth_hmac_key,
        description="Secret key for HMAC-SHA256 client IP pseudonymization from .env.",
    )
    max_upload_bytes: int = Field(
        default=50 * 1024 * 1024,
        description="Maximum upload size in bytes (default 50MB).",
    )
