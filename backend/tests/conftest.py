"""Pytest configuration and event loop setup."""

import asyncio
import os
import sys

# psycopg on Windows requires WindowsSelectorEventLoopPolicy for async mode
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import secrets

import pytest

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
