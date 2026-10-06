"""Pytest configuration and event loop setup."""

import asyncio
import os
import secrets
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# psycopg on Windows requires WindowsSelectorEventLoopPolicy for async mode
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.env_utils import load_dotenv

load_dotenv()


def make_test_password() -> str:
    """Helper to dynamically generate high-entropy test passwords at runtime."""
    return f"sec_{secrets.token_urlsafe(16)}!Aa1"


@pytest.fixture
def runtime_test_password() -> str:
    """Fixture providing high-entropy plaintext test password at runtime."""
    return make_test_password()


@pytest.fixture
def runtime_alt_password() -> str:
    """Fixture providing alternative runtime test password."""
    return f"alt_{secrets.token_urlsafe(16)}!Bb2"


@pytest.fixture(scope="session")
def test_user_password_hash() -> str:
    """Fixture providing user password hash from environment variable."""
    return os.getenv("TEST_USER_PASSWORD_HASH", "")


@dataclass
class ThrowawayDatabase:
    """Encapsulates isolated throwaway database metadata and Alembic config."""

    db_name: str
    url: str
    cfg: Config


@asynccontextmanager
async def isolated_throwaway_database() -> AsyncIterator[ThrowawayDatabase]:
    """Context manager creating and cleanly destroying an isolated throwaway database."""
    load_dotenv()
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "cka_admin")
    password = os.getenv("POSTGRES_PASSWORD", "")
    throwaway_db_name = f"cka_test_throwaway_{uuid4().hex[:12]}"

    admin_default_url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/postgres"
    admin_engine = create_async_engine(admin_default_url, isolation_level="AUTOCOMMIT")

    # 1. Create throwaway database
    async with admin_engine.connect() as conn:
        await conn.execute(text(f"CREATE DATABASE {throwaway_db_name};"))

    throwaway_url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{throwaway_db_name}"
    backend_dir = Path(__file__).resolve().parent.parent
    alembic_ini_path = backend_dir / "alembic.ini"
    cfg = Config(str(alembic_ini_path))

    old_alembic_url = os.environ.get("ALEMBIC_DATABASE_URL")
    os.environ["ALEMBIC_DATABASE_URL"] = throwaway_url

    try:
        yield ThrowawayDatabase(db_name=throwaway_db_name, url=throwaway_url, cfg=cfg)
    finally:
        if old_alembic_url is not None:
            os.environ["ALEMBIC_DATABASE_URL"] = old_alembic_url
        else:
            os.environ.pop("ALEMBIC_DATABASE_URL", None)

        async with admin_engine.connect() as conn:
            await conn.execute(
                text(
                    f"""
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = '{throwaway_db_name}' AND pid <> pg_backend_pid();
                    """
                )
            )
            await conn.execute(text(f"DROP DATABASE IF EXISTS {throwaway_db_name};"))
        await admin_engine.dispose()


@pytest.fixture
async def throwaway_db() -> AsyncIterator[ThrowawayDatabase]:
    """Fixture providing a fresh, isolated throwaway database for migration testing."""
    async with isolated_throwaway_database() as db:
        yield db
