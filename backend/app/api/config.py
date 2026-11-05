"""FastAPI application configuration and settings."""

import os
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.ports.errors import ConfigurationError

ApiProfile = Literal[
    "mock",
    "office-cpu",
    "desktop-gpu",
    "home-gpu",
    "dev-free-api",
    "cloud",
    "frontier",
]


def _default_cookie_secure() -> bool:
    """Secure cookie defaults to on (True). Only an explicit local-dev setting turns it off."""
    val = os.getenv("COOKIE_SECURE")
    if val is not None:
        return val.strip().lower() not in ("false", "0", "no", "off")
    return True


def _default_auth_hmac_key() -> str:
    """Secret key for client_ip_hash HMAC-SHA256 pseudonymization, loaded from .env."""
    return os.getenv("AUTH_HMAC_KEY", "dev-secret-auth-hmac-key-placeholder")


def _default_session_upload_retention_hours() -> int | None:
    val = os.getenv("SESSION_UPLOAD_RETENTION_HOURS")
    if val is not None and val.strip():
        try:
            hrs = int(val.strip())
            if hrs > 0:
                return hrs
        except ValueError:
            return None
    return None


def _default_chat_model() -> str:
    """Chat and generation model name setting, default qwen3-vl:4b."""
    val = os.getenv("CHAT_MODEL")
    if val is not None and val.strip():
        return val.strip()
    return "qwen3-vl:4b"


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
    question_rate_limit_user_max_requests: int = 30
    question_rate_limit_tenant_max_requests: int = 120
    question_rate_limit_window_seconds: int = 60
    auth_hmac_key: str = Field(
        default_factory=_default_auth_hmac_key,
        description="Secret key for HMAC-SHA256 client IP pseudonymization from .env.",
    )
    max_upload_bytes: int = Field(
        default=50 * 1024 * 1024,
        description="Maximum upload size in bytes (default 50MB).",
    )
    profile: ApiProfile = Field(
        ...,
        description="Runtime profile (mock, office-cpu, desktop-gpu, etc.).",
    )
    storage_dir: str = Field(
        ...,
        description="Storage directory for local storage backend. Required; no default.",
    )
    session_upload_retention_hours: int | None = Field(
        default_factory=_default_session_upload_retention_hours,
        description="Retention window for chat session uploads in hours. Required; no default.",
    )
    chat_model: str = Field(
        default_factory=_default_chat_model,
        description="Chat and answer generation model name (default: qwen3-vl:4b).",
    )

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            invalid_fields = sorted({str(err["loc"][0]) for err in exc.errors() if err.get("loc")})
            field_str = ", ".join(invalid_fields) if invalid_fields else "unknown"
            raise ConfigurationError(
                f"INVALID_CONFIGURATION: invalid fields: {field_str}"
            ) from None

        if (
            self.session_upload_retention_hours is not None
            and self.session_upload_retention_hours <= 0
        ):
            raise ConfigurationError(
                "INVALID_CONFIGURATION: invalid fields: session_upload_retention_hours"
            ) from None

        if not self.storage_dir or not self.storage_dir.strip():
            raise ConfigurationError("INVALID_CONFIGURATION: invalid fields: storage_dir") from None

        from pathlib import Path

        if self.profile in ("mock", "office-cpu", "desktop-gpu", "home-gpu", "dev-free-api"):
            resolved = Path(self.storage_dir.strip())
            if not resolved.is_dir():
                raise ConfigurationError(
                    f"LOCAL_STORAGE_PATH directory does not exist: {self.storage_dir}"
                )
