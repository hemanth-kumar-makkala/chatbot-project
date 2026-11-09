"""FastAPI application factory and middleware configuration."""

from app.observability.tracing_guard import enforce_langsmith_tracing_disabled

enforce_langsmith_tracing_disabled()

import os
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.config import ApiSettings
from app.api.rate_limiter import LoginRateLimiter
from app.api.routes import (
    assistants,
    auth,
    conversations,
    feedback,
    health,
    knowledge,
    questions,
    tenants,
)
from app.observability.logger import set_exception_scrubber
from app.ports.errors import ConfigurationError
from app.wiring import create_auth_provider, create_exception_scrubber


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Create and configure FastAPI application."""
    enforce_langsmith_tracing_disabled()
    if settings is not None:
        app_settings = settings
    else:
        init_kwargs: dict[str, Any] = {}
        if "APP_PROFILE" in os.environ:
            prof = os.environ["APP_PROFILE"].strip()
            if prof != "":
                init_kwargs["profile"] = prof
            else:
                raise ConfigurationError("INVALID_CONFIGURATION: invalid fields: profile")
        else:
            raise ConfigurationError("INVALID_CONFIGURATION: invalid fields: profile")

        if "LOCAL_STORAGE_PATH" in os.environ:
            stor = os.environ["LOCAL_STORAGE_PATH"].strip()
            if stor != "":
                init_kwargs["storage_dir"] = stor
            else:
                raise ConfigurationError("INVALID_CONFIGURATION: invalid fields: storage_dir")
        else:
            raise ConfigurationError("INVALID_CONFIGURATION: invalid fields: storage_dir")

        if "SESSION_UPLOAD_RETENTION_HOURS" in os.environ:
            retention_raw = os.environ["SESSION_UPLOAD_RETENTION_HOURS"].strip()
            if retention_raw != "":
                try:
                    hrs = int(retention_raw)
                    if hrs <= 0:
                        raise ConfigurationError(
                            "INVALID_CONFIGURATION: invalid fields: session_upload_retention_hours"
                        )
                    init_kwargs["session_upload_retention_hours"] = hrs
                except ValueError as err:
                    raise ConfigurationError(
                        "INVALID_CONFIGURATION: invalid fields: session_upload_retention_hours"
                    ) from err
            else:
                raise ConfigurationError(
                    "INVALID_CONFIGURATION: invalid fields: session_upload_retention_hours"
                )
        else:
            raise ConfigurationError(
                "INVALID_CONFIGURATION: invalid fields: session_upload_retention_hours"
            )

        app_settings = ApiSettings(**init_kwargs)

    if (
        app_settings.session_upload_retention_hours is None
        or app_settings.session_upload_retention_hours <= 0
    ):
        raise ConfigurationError(
            "INVALID_CONFIGURATION: invalid fields: session_upload_retention_hours"
        )

    # Invariant: CORS origins and frame-ancestors must never include "*"
    if "*" in app_settings.cors_origins:
        raise ValueError("Wildcard CORS origin '*' is strictly prohibited by security rules.")
    if "*" in app_settings.allowed_frame_ancestors:
        raise ValueError("Wildcard frame-ancestors '*' is strictly prohibited by security rules.")

    app = FastAPI(
        title="Client Knowledge Assistant API",
        version="0.1.0",
        docs_url=None,  # Disabled in production-style hardening
        redoc_url=None,
    )

    frame_ancestors = list(app_settings.allowed_frame_ancestors)

    @app.middleware("http")
    async def add_security_headers(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if frame_ancestors:
            ancestors_str = " ".join(frame_ancestors)
            response.headers.setdefault(
                "Content-Security-Policy", f"frame-ancestors {ancestors_str}"
            )
        else:
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        return response

    # Attach shared state
    scrubber = create_exception_scrubber(app_settings.profile)
    set_exception_scrubber(scrubber)
    app.state.exception_scrubber = scrubber
    app.state.settings = app_settings
    app.state.rate_limiter = LoginRateLimiter(
        max_attempts=app_settings.login_rate_limit_max_attempts,
        window_seconds=app_settings.login_rate_limit_window_seconds,
    )
    app.state.auth_provider = create_auth_provider(
        idle_timeout_minutes=app_settings.idle_timeout_minutes,
        absolute_timeout_hours=app_settings.absolute_timeout_hours,
        provider_type=app_settings.auth_provider_type,
        jwt_secret=(
            app_settings.jwt_secret.get_secret_value()
            if app_settings.jwt_secret is not None
            else None
        ),
        jwt_issuer=app_settings.jwt_issuer,
        jwt_audience=app_settings.jwt_audience,
        api_keys_json=(
            app_settings.api_keys_json.get_secret_value()
            if app_settings.api_keys_json is not None
            else None
        ),
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-CSRF-Token", "X-Request-ID"],
    )

    # Include routers
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(tenants.router)
    app.include_router(assistants.router)
    app.include_router(knowledge.router)
    app.include_router(questions.router)
    app.include_router(conversations.router)
    app.include_router(feedback.router)

    return app
