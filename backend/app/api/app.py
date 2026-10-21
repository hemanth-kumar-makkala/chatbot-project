"""FastAPI application factory and middleware configuration."""

from app.observability.tracing_guard import enforce_langsmith_tracing_disabled

enforce_langsmith_tracing_disabled()

import os
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.config import ApiSettings
from app.api.rate_limiter import LoginRateLimiter
from app.api.routes import auth, conversations, health, knowledge, questions
from app.ports.errors import ConfigurationError
from app.wiring import create_auth_provider


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

        app_settings = ApiSettings(**init_kwargs)

    # Invariant: CORS origins must never include "*"
    if "*" in app_settings.cors_origins:
        raise ValueError("Wildcard CORS origin '*' is strictly prohibited by security rules.")

    app = FastAPI(
        title="Client Knowledge Assistant API",
        version="0.1.0",
        docs_url=None,  # Disabled in production-style hardening
        redoc_url=None,
    )

    # Attach shared state
    app.state.settings = app_settings
    app.state.rate_limiter = LoginRateLimiter(
        max_attempts=app_settings.login_rate_limit_max_attempts,
        window_seconds=app_settings.login_rate_limit_window_seconds,
    )
    app.state.auth_provider = create_auth_provider(
        idle_timeout_minutes=app_settings.idle_timeout_minutes,
        absolute_timeout_hours=app_settings.absolute_timeout_hours,
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
    app.include_router(knowledge.router)
    app.include_router(questions.router)
    app.include_router(conversations.router)

    return app
