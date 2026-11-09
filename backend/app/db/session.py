"""Database session management and tenant transaction scoping.

Enforces:
- Single way to get a tenant-scoped session via tenant_session(tenant_ctx: TenantContext).
- Every tenant session executes inside a transaction with SET LOCAL / is_local=true,
  scoping the tenant strictly to the transaction.
- Shared pooled AsyncEngine.
"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

# psycopg on Windows requires WindowsSelectorEventLoopPolicy for async mode
if sys.platform == "win32":
    with suppress(Exception):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.env_utils import load_dotenv
from app.ports.types import TenantContext, WorkerContext


def get_database_url(role: str = "app_user") -> str:
    """Construct async database URL with psycopg driver for a given database role."""
    load_dotenv()
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "knowledge_assistant")

    if role == "cka_admin":
        user = os.getenv("POSTGRES_USER", "cka_admin")
        password = os.getenv("POSTGRES_PASSWORD", "")
    elif role == "retriever_user":
        user = os.getenv("DB_RETRIEVER_USER", "retriever_user")
        password = os.getenv("DB_RETRIEVER_PASSWORD", "")
    else:
        user = os.getenv("DB_APP_USER", "app_user")
        password = os.getenv("DB_APP_PASSWORD", "")

    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


# Shared pooled engines
_app_engine: AsyncEngine | None = None
_app_session_factory: async_sessionmaker[AsyncSession] | None = None

_retriever_engine: AsyncEngine | None = None
_retriever_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_app_engine() -> AsyncEngine:
    global _app_engine, _app_session_factory
    if _app_engine is None:
        url = os.getenv("DATABASE_URL") or get_database_url(role="app_user")
        _app_engine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            connect_args={"connect_timeout": 3},
        )
        _app_session_factory = async_sessionmaker(_app_engine, expire_on_commit=False)
    return _app_engine


def get_app_session_factory() -> async_sessionmaker[AsyncSession]:
    global _app_session_factory
    if _app_session_factory is None:
        get_app_engine()
    assert _app_session_factory is not None
    return _app_session_factory


def get_retriever_engine() -> AsyncEngine:
    global _retriever_engine, _retriever_session_factory
    if _retriever_engine is None:
        url = os.getenv("DATABASE_READONLY_URL") or get_database_url(role="retriever_user")
        _retriever_engine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            connect_args={"connect_timeout": 3},
        )
        _retriever_session_factory = async_sessionmaker(_retriever_engine, expire_on_commit=False)
    return _retriever_engine


def get_retriever_session_factory() -> async_sessionmaker[AsyncSession]:
    global _retriever_session_factory
    if _retriever_session_factory is None:
        get_retriever_engine()
    assert _retriever_session_factory is not None
    return _retriever_session_factory


def reset_session_engines() -> None:
    """Reset cached engines and session factories (used for test isolation)."""
    global _app_engine, _app_session_factory, _retriever_engine, _retriever_session_factory
    _app_engine = None
    _app_session_factory = None
    _retriever_engine = None
    _retriever_session_factory = None


@asynccontextmanager
async def tenant_session(
    tenant_ctx: TenantContext,
) -> AsyncIterator[AsyncSession]:
    """Single authorized entrypoint for user/API requests to query tenant tables.

    Takes a typed TenantContext, begins a transaction, and sets the
    transaction-local tenant setting via set_config(..., is_local=true).
    """
    factory = get_app_session_factory()
    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_ctx.tenant_id)},
        )
        yield session


@asynccontextmanager
async def worker_tenant_session(
    worker_ctx: WorkerContext,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Single authorized entrypoint for background worker jobs to query tenant tables.

    Takes a typed WorkerContext, begins a transaction, and sets the
    transaction-local tenant setting via set_config(..., is_local=true).
    API endpoints can never call this function.
    """
    factory = session_factory or get_app_session_factory()
    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(worker_ctx.tenant_id)},
        )
        yield session


@asynccontextmanager
async def retriever_tenant_session(tenant_ctx: TenantContext) -> AsyncIterator[AsyncSession]:
    """Retriever read-only session scoped to tenant via transaction-local setting."""
    factory = get_retriever_session_factory()
    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_ctx.tenant_id)},
        )
        yield session


@asynccontextmanager
async def unscoped_session() -> AsyncIterator[AsyncSession]:
    """Session for global non-tenant tables only (tenants, users).

    No tenant is set; any query targeting a tenant table will return 0 rows under RLS.
    """
    factory = get_app_session_factory()
    async with factory() as session, session.begin():
        yield session
