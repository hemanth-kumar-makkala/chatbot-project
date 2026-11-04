"""Throwaway ASGI target application for Phase 5 Task 5.6 Locust capacity profiling.

Used exclusively by the @pytest.mark.models test in tests/eval/test_locust_capacity_curve.py
to run uvicorn against an isolated throwaway PostgreSQL database on desktop-gpu.
Configures WindowsSelectorEventLoopPolicy for psycopg async compatibility on Windows and
raises per-user/per-tenant question rate limits so Locust measures the true hardware,
database, and 6 GB GPU (qwen3-vl:4b) capacity curve.
"""

from __future__ import annotations

import asyncio
import os
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

from app.api.app import create_app
from app.api.config import ApiSettings


def build_locust_target_app():
    """Construct the FastAPI app configured for Locust capacity profiling."""
    profile = os.environ.get("APP_PROFILE", "desktop-gpu")
    storage_dir = os.environ.get("LOCAL_STORAGE_PATH", "storage")
    retention_hours = int(os.environ.get("SESSION_UPLOAD_RETENTION_HOURS", "24"))

    settings = ApiSettings(
        profile=profile,
        storage_dir=storage_dir,
        session_upload_retention_hours=retention_hours,
        allow_bearer_auth=True,
        question_rate_limit_user_max_requests=100_000,
        question_rate_limit_tenant_max_requests=100_000,
        question_rate_limit_window_seconds=60,
    )
    return create_app(settings=settings)


app = build_locust_target_app()


async def _serve(host: str, port: int) -> None:
    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        loop="none",
        log_level="warning",
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    target_port = int(os.environ.get("LOCUST_TARGET_PORT", "8099"))
    asyncio.run(_serve("127.0.0.1", target_port))
