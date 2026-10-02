"""Pytest configuration and event loop setup."""

import asyncio
import os
import sys

# psycopg on Windows requires WindowsSelectorEventLoopPolicy for async mode
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest

from app.db.env_utils import load_dotenv

load_dotenv()


@pytest.fixture(scope="session")
def test_user_password_hash() -> str:
    """Fixture providing user password hash from environment variable."""
    return os.getenv("TEST_USER_PASSWORD_HASH", "")
