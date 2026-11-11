"""FastAPI application configuration and settings."""

import os
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, ValidationError

from app.ports.errors import ConfigurationError

ApiProfile = Literal[
    "mock",
    "office-cpu",
    "desktop-gpu",
    "home-gpu",
    "dev-free-api",
    "cloud",
    "frontier",
    "desktop-api",
]


def _default_cookie_secure() -> bool:
    """Secure cookie defaults to on (True). Only an explicit local-dev setting turns it off."""
    val = os.getenv("COOKIE_SECURE")
    if val is not None:
        return val.strip().lower() not in ("false", "0", "no", "off")
    return True


def _default_cors_origins() -> list[str]:
    """Read comma-separated CORS_ORIGINS from env, defaulting to http://localhost:3000."""
    val = os.getenv("CORS_ORIGINS")
    if val is not None and val.strip():
        origins = [item.strip() for item in val.split(",") if item.strip()]
        if origins:
            return origins
    return ["http://localhost:3000"]


def _default_allow_bearer_auth() -> bool:
    """Read ALLOW_BEARER_AUTH from env, defaulting to False (cookie-only)."""
    val = os.getenv("ALLOW_BEARER_AUTH")
    if val is not None:
        return val.strip().lower() in ("true", "1", "yes", "on")
    return False


def _default_allowed_frame_ancestors() -> list[str]:
    """Read comma-separated ALLOWED_FRAME_ANCESTORS from env, defaulting to empty (DENY)."""
    val = os.getenv("ALLOWED_FRAME_ANCESTORS")
    if val is not None and val.strip():
        return [item.strip() for item in val.split(",") if item.strip()]
    return []


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


DEPRECATED_MODEL_ENV_VARS: tuple[str, ...] = ("ANSWER_MODEL", "CHAT_MODEL", "VISION_MODEL")


def check_deprecated_model_env_vars() -> None:
    """Enforce that model selection comes exclusively from profile YAML.

    Raises ConfigurationError if any legacy model environment override is set.
    """
    for var in DEPRECATED_MODEL_ENV_VARS:
        if os.getenv(var) is not None:
            from app.ports.errors import ConfigurationError

            raise ConfigurationError(
                f"Environment variable '{var}' is no longer supported. "
                "Model configuration must come strictly from the profile YAML.",
                error_code="DEPRECATED_MODEL_ENV_VAR",
            )


AuthProviderType = Literal["builtin", "jwt", "api_key"]


def _default_auth_provider_type() -> AuthProviderType:
    """Auth provider backend setting (AUTH_PROVIDER), defaulting to builtin."""
    val = os.getenv("AUTH_PROVIDER")
    if val is not None and val.strip():
        norm = val.strip().lower()
        if norm in ("builtin", "jwt", "api_key"):
            return norm  # type: ignore[return-value]
        return norm  # type: ignore[return-value]
    return "builtin"


def _optional_secret_env(name: str) -> SecretStr | None:
    """Load an optional hosted-provider secret without assigning a fallback credential."""
    val = os.getenv(name)
    return SecretStr(val) if val is not None and val.strip() else None


def _optional_env(name: str) -> str | None:
    val = os.getenv(name)
    return val.strip() if val is not None and val.strip() else None


class ApiSettings(BaseModel):
    """Configuration settings for the FastAPI application."""

    cors_origins: list[str] = Field(
        default_factory=_default_cors_origins,
        description="Allowed web origins for CORS. Never allow '*'.",
    )
    allowed_frame_ancestors: list[str] = Field(
        default_factory=_default_allowed_frame_ancestors,
        description="Allowed parent frame origins for CSP frame-ancestors. Never allow '*'.",
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
    allow_bearer_auth: bool = Field(
        default_factory=_default_allow_bearer_auth,
        description="Allow Authorization: Bearer tokens when True; defaults to False.",
    )
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
        default="",
        description="Answer-generation model resolved from profile.",
    )
    vision_model: str = Field(
        default="",
        description="Independent vision model selection resolved from profile.",
    )
    ollama_base_url: str | None = Field(default_factory=lambda: _optional_env("OLLAMA_BASE_URL"))
    gemini_base_url: str | None = Field(default_factory=lambda: _optional_env("GEMINI_BASE_URL"))
    gemini_api_key: SecretStr | None = Field(
        default_factory=lambda: _optional_secret_env("GEMINI_API_KEY")
    )
    nvidia_base_url: str | None = Field(default_factory=lambda: _optional_env("NVIDIA_BASE_URL"))
    nvidia_api_key: SecretStr | None = Field(
        default_factory=lambda: _optional_secret_env("NVIDIA_API_KEY")
    )
    inception_base_url: str | None = Field(
        default_factory=lambda: _optional_env("INCEPTION_BASE_URL")
    )
    inception_api_key: SecretStr | None = Field(
        default_factory=lambda: _optional_secret_env("INCEPTION_API_KEY")
    )
    openai_base_url: str | None = Field(default_factory=lambda: _optional_env("OPENAI_BASE_URL"))
    openai_api_key: SecretStr | None = Field(
        default_factory=lambda: _optional_secret_env("OPENAI_API_KEY")
    )
    auth_provider_type: AuthProviderType = Field(
        default_factory=_default_auth_provider_type,
        description="Auth provider backend: builtin, jwt, or api_key (AUTH_PROVIDER).",
    )
    jwt_secret: SecretStr | None = Field(
        default_factory=lambda: _optional_secret_env("JWT_SECRET"),
        description="HS256 secret key for delegated JWT authentication (JWT_SECRET).",
    )
    jwt_issuer: str | None = Field(
        default_factory=lambda: _optional_env("JWT_ISSUER"),
        description="Optional expected issuer claim for delegated JWT authentication.",
    )
    jwt_audience: str | None = Field(
        default_factory=lambda: _optional_env("JWT_AUDIENCE"),
        description="Optional expected audience claim for delegated JWT authentication.",
    )
    api_keys_json: SecretStr | None = Field(
        default_factory=lambda: _optional_secret_env("API_KEYS_JSON"),
        description="JSON array of service API keys for delegated API key authentication.",
    )

    def __init__(self, **data: Any) -> None:
        check_deprecated_model_env_vars()
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

        if self.profile in (
            "mock",
            "office-cpu",
            "desktop-gpu",
            "home-gpu",
            "dev-free-api",
            "desktop-api",
        ):
            resolved = Path(self.storage_dir.strip())
            if not resolved.is_dir():
                raise ConfigurationError(
                    f"LOCAL_STORAGE_PATH directory does not exist: {self.storage_dir}"
                )
