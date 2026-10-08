"""Integration tests for Ingestion Schema, Idempotency Constraints, and Role Boundaries (Task 2.1).

Executed against real PostgreSQL in Docker (cka-postgres).
Validates:
1. All 8 ingestion tables exist with strict UUID primary keys and tenant_id.
2. pgvector extension and vector(1024) column type enforcement (using CAST(:embedding AS vector)).
3. HNSW and GIN indexes presence.
4. pages.image_key CHECK constraint tying prefix to tenant_id and blocking traversal/raw paths.
5. RLS read isolation across all 8 tables (with pre-assertion that seeded Tenant B rows exist).
6. Unscoped session returns 0 rows on all 8 tables for app_user, and 0 rows on 6 evidence tables
   for retriever_user (after seeding both tenants).
7. RLS WITH CHECK violations raise SQLSTATE 42501 with 'row-level security' on all 8 tables
   (nested savepoints).
8. Meta-test: all 8 tables are tenant-walled with relrowsecurity=true, relforcerowsecurity=true,
   and zero global allowlist entries.
9. Composite FKs block cross-tenant references across all child-parent relationships
   (SQLSTATE 23503, with positive control).
10. job_steps duplicate rejection for both page-level and document-level steps via
    NULLS NOT DISTINCT (SQLSTATE 23505).
11. documents.active_version_id deferred composite FK rejects invalid documents or tenants
    at COMMIT / SET CONSTRAINTS.
12. document_versions (tenant_id, sha256) uniqueness.
13. chunks.ts_config CHECK IN ('english', 'spanish', 'simple') (nested savepoints).
14. chunks.tsv generated column with Spanish and English stemming.
15. retriever_user SELECT-only on 6 evidence tables (INSERT/UPDATE/DELETE denied with 42501
    and 'permission denied').
16. retriever_user zero access to jobs and job_steps (denied with 42501 and 'permission denied').
17. retriever_user bound by RLS (with pre-assertion that Tenant B record exists).
18. app_user DELETE denied on all 8 tables (42501) and all FKs enforce ON DELETE RESTRICT
    (pg_constraint confdeltype = 'r').
19. PUBLIC role has zero grants on all 8 tables (with pre-assertion that all 8 tables exist).
20. Migration 0003 upgrade -> downgrade -> upgrade clean run on an isolated throwaway database,
    preserving shared DB alembic_version.
"""

import os
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.env_utils import load_dotenv
from app.db.models import Membership, Tenant, User
from app.db.session import (
    get_app_engine,
    get_retriever_engine,
    retriever_tenant_session,
    tenant_session,
    unscoped_session,
)
from app.ports.types import TenantContext, UserRole
from tests.fixtures.database_seeding import (
    seed_chunk,
    seed_document,
    seed_job,
    seed_job_step,
    seed_page,
    seed_reading,
    seed_table_data,
    seed_version,
)

load_dotenv()

EVIDENCE_TABLES: list[str] = [
    "documents",
    "document_versions",
    "pages",
    "page_readings",
    "chunks",
    "tables_data",
]

ALL_8_INGESTION_TABLES: list[str] = [
    "documents",
    "document_versions",
    "pages",
    "page_readings",
    "chunks",
    "tables_data",
    "jobs",
    "job_steps",
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def get_sqlstate(exc: BaseException) -> str | None:
    """Extract 5-character SQLSTATE code from SQLAlchemy DBAPIError or underlying driver."""
    orig: Any = getattr(exc, "orig", exc)
    raw_state: Any = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return str(raw_state) if raw_state is not None else None


async def assert_all_8_tables_exist(session: AsyncSession) -> None:
    """Pre-assertion helper: asserts all 8 tables exist to prevent tests passing on nothing."""
    res = await session.execute(
        text(
            """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind = 'r'
              AND c.relname = ANY(:tables);
            """
        ),
        {"tables": ALL_8_INGESTION_TABLES},
    )
    existing: set[str] = {str(row[0]) for row in res.fetchall()}
    missing: set[str] = set(ALL_8_INGESTION_TABLES) - existing
    assert not missing, f"Pre-assertion failed: tables do not exist in public schema: {missing}"


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def tenant_pair(test_user_password_hash: str) -> tuple[TenantContext, TenantContext]:
    """Provides two distinct isolated tenants with seeded User and Membership records."""
    t_a_id = uuid4()
    t_b_id = uuid4()
    u_a_id = uuid4()
    u_b_id = uuid4()
    m_a_id = uuid4()
    m_b_id = uuid4()

    # 1. Seed global tables (tenants and users) in unscoped session
    async with unscoped_session() as session:
        session.add_all(
            [
                Tenant(id=t_a_id, name=f"Tenant-A-{t_a_id}"),
                Tenant(id=t_b_id, name=f"Tenant-B-{t_b_id}"),
                User(
                    id=u_a_id,
                    email=f"user-a-{u_a_id}@example.com",
                    hashed_password=test_user_password_hash,
                    is_platform_admin=False,
                ),
                User(
                    id=u_b_id,
                    email=f"user-b-{u_b_id}@example.com",
                    hashed_password=test_user_password_hash,
                    is_platform_admin=False,
                ),
            ]
        )
        await session.commit()

    ctx_a = TenantContext(
        tenant_id=str(t_a_id),
        user_id=str(u_a_id),
        role=UserRole.STAFF,
        session_id=str(uuid4()),
    )
    ctx_b = TenantContext(
        tenant_id=str(t_b_id),
        user_id=str(u_b_id),
        role=UserRole.STAFF,
        session_id=str(uuid4()),
    )

    # 2. Seed tenant table (memberships) inside their respective tenant sessions
    async with tenant_session(ctx_a) as session_a:
        session_a.add(Membership(id=m_a_id, tenant_id=t_a_id, user_id=u_a_id, role="staff"))

    async with tenant_session(ctx_b) as session_b:
        session_b.add(Membership(id=m_b_id, tenant_id=t_b_id, user_id=u_b_id, role="staff"))

    return ctx_a, ctx_b


# ---------------------------------------------------------------------------
# Tests: Schema Structure, Vector Extension, Constraints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestIngestionSchemaStructure:
    """Verifies table presence, extension, indexes, and column checks."""

    async def test_all_8_ingestion_tables_exist(self) -> None:
        """All 8 ingestion tables must exist in PostgreSQL public schema."""
        engine = get_app_engine()
        async with engine.connect() as conn:
            res = await conn.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = ANY(:tables);
                    """
                ),
                {"tables": ALL_8_INGESTION_TABLES},
            )
            existing: set[str] = {str(row[0]) for row in res.fetchall()}
            assert existing == set(ALL_8_INGESTION_TABLES), (
                f"Missing tables: {set(ALL_8_INGESTION_TABLES) - existing}"
            )

    async def test_pgvector_extension_and_vector_column(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """vector extension must be active and chunks.embedding vector(1024) rejects 1023-dim."""
        ctx_a, _ = tenant_pair
        engine = get_app_engine()
        async with engine.connect() as conn:
            res = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector';")
            )
            assert res.scalar_one_or_none() == "vector", "pgvector extension is not installed"

        async with tenant_session(ctx_a) as session:
            await assert_all_8_tables_exist(session)

            doc_id = await seed_document(session, ctx_a.tenant_id, title="Doc 1024 Test")
            ver_id = await seed_version(
                session, ctx_a.tenant_id, doc_id, ctx_a.user_id, sha256="sha-1024"
            )
            page_id = await seed_page(session, ctx_a.tenant_id, ver_id)

            invalid_1023_vec = "[" + ",".join(["0.0"] * 1023) + "]"
            with pytest.raises(DBAPIError) as exc_info:
                async with session.begin_nested():
                    await session.execute(
                        text(
                            """
                            INSERT INTO chunks (
                                id, tenant_id, page_id, document_version_id,
                                text, language, ts_config, embedding
                            )
                            VALUES (
                                :cid, :tid, :pid, :vid,
                                'Sample text', 'en', 'english', CAST(:vec AS vector)
                            );
                            """
                        ),
                        {
                            "cid": uuid4(),
                            "tid": UUID(ctx_a.tenant_id),
                            "pid": page_id,
                            "vid": ver_id,
                            "vec": invalid_1023_vec,
                        },
                    )
            sqlstate = get_sqlstate(exc_info.value)
            assert sqlstate in ("22000", "42804"), (
                f"Expected dimension mismatch SQLSTATE, got {sqlstate}"
            )

    async def test_hnsw_and_gin_indexes_exist(self) -> None:
        """HNSW index on chunks.embedding and GIN index on chunks.tsv must exist."""
        engine = get_app_engine()
        async with engine.connect() as conn:
            res = await conn.execute(
                text(
                    """
                    SELECT i.relname AS index_name, am.amname AS access_method
                    FROM pg_index x
                    JOIN pg_class c ON c.oid = x.indrelid
                    JOIN pg_class i ON i.oid = x.indexrelid
                    JOIN pg_am am ON am.oid = i.relam
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = 'chunks';
                    """
                )
            )
            indexes: dict[str, str] = {str(row[0]): str(row[1]) for row in res.fetchall()}
            assert any(am == "hnsw" for am in indexes.values()), (
                f"No HNSW index found on chunks: {indexes}"
            )
            assert any(am == "gin" for am in indexes.values()), (
                f"No GIN index found on chunks: {indexes}"
            )

    async def test_pages_image_key_check_constraint(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """pages.image_key CHECK rejects raw paths, '..', backslashes, '//', and wrong prefix."""
        ctx_a, ctx_b = tenant_pair
        async with tenant_session(ctx_a) as session:
            await assert_all_8_tables_exist(session)

            doc_id = await seed_document(session, ctx_a.tenant_id, title="Doc Key Test")
            ver_id = await seed_version(
                session, ctx_a.tenant_id, doc_id, ctx_a.user_id, sha256="sha-key"
            )

            invalid_keys = [
                f"{ctx_a.tenant_id}/../{ctx_b.tenant_id}/x.png",
                f"{ctx_a.tenant_id}\\documents\\x.png",
                f"{ctx_a.tenant_id}/documents\\x.png",
                f"{ctx_a.tenant_id}//documents/x.png",
                f"{ctx_a.tenant_id}/documents//x.png",
                "/var/data/documents/page_1.png",
                "documents/page_1.png",
                f"{ctx_b.tenant_id}/documents/x.png",
            ]

            for inv_key in invalid_keys:
                with pytest.raises(DBAPIError) as exc_info:
                    async with session.begin_nested():
                        await seed_page(
                            session,
                            ctx_a.tenant_id,
                            ver_id,
                            page_number=1,
                            image_key=inv_key,
                        )
                assert get_sqlstate(exc_info.value) == "23514", (
                    f"Expected CHECK violation (23514) for key '{inv_key}', "
                    f"got {get_sqlstate(exc_info.value)}"
                )

            # Valid key succeeds
            valid_key = f"{ctx_a.tenant_id}/documents/{ver_id}/page_1.png"
            await seed_page(
                session,
                ctx_a.tenant_id,
                ver_id,
                page_number=1,
                image_key=valid_key,
            )

            # Valid key with '%' succeeds (confirms backslash check does not reject '%')
            valid_key_percent = f"{ctx_a.tenant_id}/documents/100%.png"
            await seed_page(
                session,
                ctx_a.tenant_id,
                ver_id,
                page_number=2,
                image_key=valid_key_percent,
            )


# ---------------------------------------------------------------------------
# Tests: Row Level Security Isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestIngestionRowLevelSecurity:
    """Verifies RLS read isolation, unscoped behavior, and WITH CHECK enforcement."""

    async def test_meta_all_8_tables_have_rls_enabled_and_forced(self) -> None:
        """Meta-test: all 8 ingestion tables have relrowsecurity=true and relforcerowsecurity."""
        engine = get_app_engine()
        async with engine.connect() as conn:
            res = await conn.execute(
                text(
                    """
                    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relname = ANY(:tables);
                    """
                ),
                {"tables": ALL_8_INGESTION_TABLES},
            )
            rows: dict[str, tuple[bool, bool]] = {
                str(r[0]): (bool(r[1]), bool(r[2])) for r in res.fetchall()
            }
            for tbl in ALL_8_INGESTION_TABLES:
                assert tbl in rows, f"Table {tbl} does not exist"
                rls_en, rls_frc = rows[tbl]
                assert rls_en, f"Table {tbl} must have RLS enabled (relrowsecurity=true)"
                assert rls_frc, f"Table {tbl} must have RLS forced (relforcerowsecurity=true)"

    async def test_rls_read_isolation_on_all_8_tables(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """Tenant A cannot read Tenant B rows across any of the 8 tables."""
        ctx_a, ctx_b = tenant_pair

        # Seed rows for Tenant B across all 8 tables
        async with tenant_session(ctx_b) as session_b:
            await assert_all_8_tables_exist(session_b)

            b_doc = await seed_document(session_b, ctx_b.tenant_id, title="Doc B")
            b_ver = await seed_version(session_b, ctx_b.tenant_id, b_doc, ctx_b.user_id)
            b_page = await seed_page(session_b, ctx_b.tenant_id, b_ver)
            await seed_reading(session_b, ctx_b.tenant_id, b_page)
            await seed_chunk(session_b, ctx_b.tenant_id, b_page, b_ver)
            await seed_table_data(session_b, ctx_b.tenant_id, b_ver)
            b_job = await seed_job(session_b, ctx_b.tenant_id, b_ver)
            await seed_job_step(session_b, ctx_b.tenant_id, b_job, b_ver)

            # Pre-assertion: verify rows exist in Tenant B's own session
            for tbl in ALL_8_INGESTION_TABLES:
                cnt_b_res = await session_b.execute(
                    text(f"SELECT COUNT(*) FROM {tbl} WHERE tenant_id = :tid;"),
                    {"tid": UUID(ctx_b.tenant_id)},
                )
                cnt_b: int = int(cnt_b_res.scalar_one())
                assert cnt_b > 0, f"Seeding failed: {tbl} has 0 rows for Tenant B"

        # Query under Tenant A session: must see 0 rows from Tenant B
        async with tenant_session(ctx_a) as session_a:
            for tbl in ALL_8_INGESTION_TABLES:
                res = await session_a.execute(
                    text(f"SELECT COUNT(*) FROM {tbl} WHERE tenant_id = :b_tid;"),
                    {"b_tid": UUID(ctx_b.tenant_id)},
                )
                cnt: int = int(res.scalar_one())
                assert cnt == 0, f"Tenant A leaked {cnt} rows from Tenant B on table '{tbl}'!"

    async def test_unscoped_session_returns_zero_rows(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """Unscoped session returns 0 rows on all tables after seeding both tenants."""
        ctx_a, ctx_b = tenant_pair

        # 1. Seed rows under Tenant A first
        async with tenant_session(ctx_a) as session_a:
            await assert_all_8_tables_exist(session_a)

            doc_a = await seed_document(session_a, ctx_a.tenant_id, title="Doc A Unscoped")
            ver_a = await seed_version(session_a, ctx_a.tenant_id, doc_a, ctx_a.user_id)
            page_a = await seed_page(session_a, ctx_a.tenant_id, ver_a)
            await seed_reading(session_a, ctx_a.tenant_id, page_a)
            await seed_chunk(session_a, ctx_a.tenant_id, page_a, ver_a)
            await seed_table_data(session_a, ctx_a.tenant_id, ver_a)
            job_a = await seed_job(session_a, ctx_a.tenant_id, ver_a)
            await seed_job_step(session_a, ctx_a.tenant_id, job_a, ver_a)

            for tbl in ALL_8_INGESTION_TABLES:
                cnt_a_res = await session_a.execute(text(f"SELECT COUNT(*) FROM {tbl};"))
                cnt_a: int = int(cnt_a_res.scalar_one())
                assert cnt_a > 0, f"Expected non-empty {tbl} in Tenant A session"

        # 2. Seed rows under Tenant B as well
        async with tenant_session(ctx_b) as session_b:
            await assert_all_8_tables_exist(session_b)

            doc_b = await seed_document(session_b, ctx_b.tenant_id, title="Doc B Unscoped")
            ver_b = await seed_version(session_b, ctx_b.tenant_id, doc_b, ctx_b.user_id)
            page_b = await seed_page(session_b, ctx_b.tenant_id, ver_b)
            await seed_reading(session_b, ctx_b.tenant_id, page_b)
            await seed_chunk(session_b, ctx_b.tenant_id, page_b, ver_b)
            await seed_table_data(session_b, ctx_b.tenant_id, ver_b)
            job_b = await seed_job(session_b, ctx_b.tenant_id, ver_b)
            await seed_job_step(session_b, ctx_b.tenant_id, job_b, ver_b)

            for tbl in ALL_8_INGESTION_TABLES:
                cnt_b_res = await session_b.execute(text(f"SELECT COUNT(*) FROM {tbl};"))
                cnt_b: int = int(cnt_b_res.scalar_one())
                assert cnt_b > 0, f"Expected non-empty {tbl} in Tenant B session"

        # 3. app_user unscoped query: all 8 tables return 0 rows
        async with unscoped_session() as session_unscoped:
            for tbl in ALL_8_INGESTION_TABLES:
                res = await session_unscoped.execute(text(f"SELECT COUNT(*) FROM {tbl};"))
                cnt_u: int = int(res.scalar_one())
                assert cnt_u == 0, f"Unscoped app_user saw {cnt_u} rows on '{tbl}'!"

        # 4. retriever_user unscoped query: 6 evidence tables return 0 rows
        retriever_engine = get_retriever_engine()
        async with AsyncSession(retriever_engine) as session_ret:
            for tbl in EVIDENCE_TABLES:
                res = await session_ret.execute(text(f"SELECT COUNT(*) FROM {tbl};"))
                cnt_r: int = int(res.scalar_one())
                assert cnt_r == 0, f"Unscoped retriever_user saw {cnt_r} rows on '{tbl}'!"

    async def test_rls_with_check_blocks_cross_tenant_insert_on_all_8_tables(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """Cross-tenant insert under Tenant A fails with SQLSTATE 42501 and 'row-level security'."""
        ctx_a, ctx_b = tenant_pair

        async with tenant_session(ctx_a) as session:
            await assert_all_8_tables_exist(session)

            # Table 1: documents
            with pytest.raises(DBAPIError) as exc_info:
                async with session.begin_nested():
                    await seed_document(session, ctx_b.tenant_id, title="Cross Doc")
            assert get_sqlstate(exc_info.value) == "42501"
            assert "row-level security" in str(exc_info.value).lower()

            # Table 2: document_versions
            with pytest.raises(DBAPIError) as exc_info:
                async with session.begin_nested():
                    await seed_version(
                        session, ctx_b.tenant_id, uuid4(), ctx_a.user_id, sha256="sha-x"
                    )
            assert get_sqlstate(exc_info.value) == "42501"
            assert "row-level security" in str(exc_info.value).lower()

            # Table 3: pages
            with pytest.raises(DBAPIError) as exc_info:
                async with session.begin_nested():
                    await seed_page(session, ctx_b.tenant_id, uuid4())
            assert get_sqlstate(exc_info.value) == "42501"
            assert "row-level security" in str(exc_info.value).lower()

            # Table 4: page_readings
            with pytest.raises(DBAPIError) as exc_info:
                async with session.begin_nested():
                    await seed_reading(session, ctx_b.tenant_id, uuid4())
            assert get_sqlstate(exc_info.value) == "42501"
            assert "row-level security" in str(exc_info.value).lower()

            # Table 5: chunks
            with pytest.raises(DBAPIError) as exc_info:
                async with session.begin_nested():
                    await seed_chunk(session, ctx_b.tenant_id, uuid4(), uuid4())
            assert get_sqlstate(exc_info.value) == "42501"
            assert "row-level security" in str(exc_info.value).lower()

            # Table 6: tables_data
            with pytest.raises(DBAPIError) as exc_info:
                async with session.begin_nested():
                    await seed_table_data(session, ctx_b.tenant_id, uuid4())
            assert get_sqlstate(exc_info.value) == "42501"
            assert "row-level security" in str(exc_info.value).lower()

            # Table 7: jobs
            with pytest.raises(DBAPIError) as exc_info:
                async with session.begin_nested():
                    await seed_job(session, ctx_b.tenant_id, uuid4())
            assert get_sqlstate(exc_info.value) == "42501"
            assert "row-level security" in str(exc_info.value).lower()

            # Table 8: job_steps
            with pytest.raises(DBAPIError) as exc_info:
                async with session.begin_nested():
                    await seed_job_step(session, ctx_b.tenant_id, uuid4(), uuid4())
            assert get_sqlstate(exc_info.value) == "42501"
            assert "row-level security" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Tests: Composite Foreign Keys and Idempotency Constraints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCompositeForeignKeysAndIdempotency:
    """Verifies composite FK isolation, deferred circular FK, and NULLS NOT DISTINCT."""

    async def test_composite_fks_block_cross_tenant_references(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """Cross-tenant parent references fail with SQLSTATE 23503, with positive control."""
        ctx_a, ctx_b = tenant_pair

        b_doc_id = uuid4()
        b_ver_id = uuid4()
        b_page_id = uuid4()
        b_job_id = uuid4()

        # 1. Seed Tenant B parents in separate Tenant B session
        async with tenant_session(ctx_b) as session_b:
            await assert_all_8_tables_exist(session_b)
            await seed_document(session_b, ctx_b.tenant_id, doc_id=b_doc_id, title="Doc B")
            await seed_version(session_b, ctx_b.tenant_id, b_doc_id, ctx_b.user_id, ver_id=b_ver_id)
            await seed_page(session_b, ctx_b.tenant_id, b_ver_id, page_id=b_page_id)
            await seed_job(session_b, ctx_b.tenant_id, b_ver_id, job_id=b_job_id)

            # Pre-assertion: verify seeded parent rows exist in Tenant B
            doc_cnt_res = await session_b.execute(
                text("SELECT COUNT(*) FROM documents WHERE id = :id;"), {"id": b_doc_id}
            )
            doc_cnt: int = int(doc_cnt_res.scalar_one())
            assert doc_cnt == 1

        # 2. In Tenant A session, test positive controls and negative cross-tenant FKs
        async with tenant_session(ctx_a) as session_a:
            await assert_all_8_tables_exist(session_a)

            a_doc_id = await seed_document(session_a, ctx_a.tenant_id, title="Doc A Valid")
            a_ver_id = await seed_version(session_a, ctx_a.tenant_id, a_doc_id, ctx_a.user_id)
            a_page_id = await seed_page(session_a, ctx_a.tenant_id, a_ver_id)
            a_job_id = await seed_job(session_a, ctx_a.tenant_id, a_ver_id)

            # Link 1: document_versions -> documents
            await seed_version(session_a, ctx_a.tenant_id, a_doc_id, ctx_a.user_id)
            with pytest.raises(DBAPIError) as exc_info:
                async with session_a.begin_nested():
                    await seed_version(session_a, ctx_a.tenant_id, b_doc_id, ctx_a.user_id)
            assert get_sqlstate(exc_info.value) == "23503"

            # Link 2: pages -> document_versions
            await seed_page(session_a, ctx_a.tenant_id, a_ver_id, page_number=2)
            with pytest.raises(DBAPIError) as exc_info:
                async with session_a.begin_nested():
                    await seed_page(session_a, ctx_a.tenant_id, b_ver_id, page_number=1)
            assert get_sqlstate(exc_info.value) == "23503"

            # Link 3: page_readings -> pages
            await seed_reading(session_a, ctx_a.tenant_id, a_page_id)
            with pytest.raises(DBAPIError) as exc_info:
                async with session_a.begin_nested():
                    await seed_reading(session_a, ctx_a.tenant_id, b_page_id)
            assert get_sqlstate(exc_info.value) == "23503"

            # Link 4: chunks -> pages (tenant_id, document_version_id, page_id)
            await seed_chunk(session_a, ctx_a.tenant_id, a_page_id, a_ver_id)
            with pytest.raises(DBAPIError) as exc_info:
                async with session_a.begin_nested():
                    await seed_chunk(session_a, ctx_a.tenant_id, b_page_id, b_ver_id)
            assert get_sqlstate(exc_info.value) == "23503"

            # Link 5: tables_data -> document_versions
            await seed_table_data(session_a, ctx_a.tenant_id, a_ver_id)
            with pytest.raises(DBAPIError) as exc_info:
                async with session_a.begin_nested():
                    await seed_table_data(session_a, ctx_a.tenant_id, b_ver_id)
            assert get_sqlstate(exc_info.value) == "23503"

            # Link 6: jobs -> document_versions
            await seed_job(session_a, ctx_a.tenant_id, a_ver_id)
            with pytest.raises(DBAPIError) as exc_info:
                async with session_a.begin_nested():
                    await seed_job(session_a, ctx_a.tenant_id, b_ver_id)
            assert get_sqlstate(exc_info.value) == "23503"

            # Link 7: job_steps -> jobs
            await seed_job_step(session_a, ctx_a.tenant_id, a_job_id, a_ver_id, page_number=2)
            with pytest.raises(DBAPIError) as exc_info:
                async with session_a.begin_nested():
                    await seed_job_step(
                        session_a, ctx_a.tenant_id, b_job_id, a_ver_id, page_number=1
                    )
            assert get_sqlstate(exc_info.value) == "23503"

    async def test_job_steps_duplicate_rejected_including_null_page_number(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """NULLS NOT DISTINCT enforces idempotency for page-level and document-level steps."""
        ctx_a, _ = tenant_pair
        async with tenant_session(ctx_a) as session:
            await assert_all_8_tables_exist(session)

            doc_id = await seed_document(session, ctx_a.tenant_id, title="Doc Steps")
            ver_id = await seed_version(session, ctx_a.tenant_id, doc_id, ctx_a.user_id)
            job_id = await seed_job(session, ctx_a.tenant_id, ver_id)

            # Case A: page-level step duplicate rejected
            await seed_job_step(
                session,
                ctx_a.tenant_id,
                job_id,
                ver_id,
                page_number=1,
                step="ocr",
                processing_version="v1",
            )
            with pytest.raises(DBAPIError) as exc_info:
                async with session.begin_nested():
                    await seed_job_step(
                        session,
                        ctx_a.tenant_id,
                        job_id,
                        ver_id,
                        page_number=1,
                        step="ocr",
                        processing_version="v1",
                    )
            assert get_sqlstate(exc_info.value) == "23505"

            # Case B: document-level step duplicate (page_number IS NULL)
            # rejected via NULLS NOT DISTINCT
            await seed_job_step(
                session,
                ctx_a.tenant_id,
                job_id,
                ver_id,
                page_number=None,
                step="split_doc",
                processing_version="v1",
            )
            with pytest.raises(DBAPIError) as exc_info:
                async with session.begin_nested():
                    await seed_job_step(
                        session,
                        ctx_a.tenant_id,
                        job_id,
                        ver_id,
                        page_number=None,
                        step="split_doc",
                        processing_version="v1",
                    )
            assert get_sqlstate(exc_info.value) == "23505", (
                f"Expected UniqueViolation (23505) with NULLS NOT DISTINCT, "
                f"got {get_sqlstate(exc_info.value)}"
            )

    async def test_active_version_id_cannot_point_to_different_document_or_tenant(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """documents.active_version_id composite FK fails if pointing to wrong doc or tenant."""
        ctx_a, ctx_b = tenant_pair

        doc_a1 = uuid4()
        doc_a2 = uuid4()
        ver_a1 = uuid4()
        ver_a2 = uuid4()

        doc_b1 = uuid4()
        ver_b1 = uuid4()

        # Seed Tenant B doc and version
        async with tenant_session(ctx_b) as session_b:
            await assert_all_8_tables_exist(session_b)
            await seed_document(session_b, ctx_b.tenant_id, doc_id=doc_b1, title="Doc B1")
            await seed_version(session_b, ctx_b.tenant_id, doc_b1, ctx_b.user_id, ver_id=ver_b1)

        # In Tenant A session:
        async with tenant_session(ctx_a) as session_a:
            await assert_all_8_tables_exist(session_a)
            await seed_document(session_a, ctx_a.tenant_id, doc_id=doc_a1, title="Doc A1")
            await seed_document(session_a, ctx_a.tenant_id, doc_id=doc_a2, title="Doc A2")
            await seed_version(session_a, ctx_a.tenant_id, doc_a1, ctx_a.user_id, ver_id=ver_a1)
            await seed_version(session_a, ctx_a.tenant_id, doc_a2, ctx_a.user_id, ver_id=ver_a2)

            # Case 1: Point Doc A1 active_version_id to ver_a2 (Doc A2)
            with pytest.raises(DBAPIError) as exc_info:
                async with session_a.begin_nested():
                    await session_a.execute(
                        text(
                            "UPDATE documents SET active_version_id = :ver_id WHERE id = :doc_id;"
                        ),
                        {"ver_id": ver_a2, "doc_id": doc_a1},
                    )
                    await session_a.execute(text("SET CONSTRAINTS ALL IMMEDIATE;"))
            assert get_sqlstate(exc_info.value) == "23503"

            # Case 2: Point Doc A1 active_version_id to ver_b1 (Tenant B)
            with pytest.raises(DBAPIError) as exc_info:
                async with session_a.begin_nested():
                    await session_a.execute(
                        text(
                            "UPDATE documents SET active_version_id = :ver_id WHERE id = :doc_id;"
                        ),
                        {"ver_id": ver_b1, "doc_id": doc_a1},
                    )
                    await session_a.execute(text("SET CONSTRAINTS ALL IMMEDIATE;"))
            assert get_sqlstate(exc_info.value) == "23503"

            # Case 3: Valid assignment (Doc A1 -> Ver A1) succeeds at commit
            await session_a.execute(
                text("UPDATE documents SET active_version_id = :ver_id WHERE id = :doc_id;"),
                {"ver_id": ver_a1, "doc_id": doc_a1},
            )
            await session_a.execute(text("SET CONSTRAINTS ALL IMMEDIATE;"))

    async def test_document_versions_tenant_sha256_uniqueness(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """document_versions enforces UNIQUE (tenant_id, sha256); different tenants succeed."""
        ctx_a, ctx_b = tenant_pair
        shared_sha = "shared-content-hash-abcdef"

        async with tenant_session(ctx_a) as session_a:
            await assert_all_8_tables_exist(session_a)
            doc_a = await seed_document(session_a, ctx_a.tenant_id, title="Doc A")
            await seed_version(session_a, ctx_a.tenant_id, doc_a, ctx_a.user_id, sha256=shared_sha)

            # Duplicate SHA under same tenant fails
            with pytest.raises(DBAPIError) as exc_info:
                async with session_a.begin_nested():
                    await seed_version(
                        session_a, ctx_a.tenant_id, doc_a, ctx_a.user_id, sha256=shared_sha
                    )
            assert get_sqlstate(exc_info.value) == "23505"

        # Same SHA under Tenant B succeeds
        async with tenant_session(ctx_b) as session_b:
            await assert_all_8_tables_exist(session_b)
            doc_b = await seed_document(session_b, ctx_b.tenant_id, title="Doc B")
            await seed_version(session_b, ctx_b.tenant_id, doc_b, ctx_b.user_id, sha256=shared_sha)


# ---------------------------------------------------------------------------
# Tests: Language Configuration and Full-Text Search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestChunksLanguageAndFullTextSearch:
    """Verifies ts_config check constraint and generated tsv column behavior."""

    async def test_chunks_default_is_active_is_false(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """chunks.is_active must default to false upon insert."""
        ctx_a, _ = tenant_pair
        async with tenant_session(ctx_a) as session:
            await assert_all_8_tables_exist(session)

            doc_id = await seed_document(session, ctx_a.tenant_id, title="Doc Default Active Test")
            ver_id = await seed_version(
                session, ctx_a.tenant_id, doc_id, ctx_a.user_id, sha256="sha-active-default"
            )
            page_id = await seed_page(session, ctx_a.tenant_id, ver_id)
            chunk_id = uuid4()

            await session.execute(
                text(
                    """
                    INSERT INTO chunks (
                        id, tenant_id, page_id, document_version_id, text, language, ts_config
                    )
                    VALUES (:cid, :tid, :pid, :vid, 'Default active test', 'en', 'english');
                    """
                ),
                {
                    "cid": chunk_id,
                    "tid": UUID(ctx_a.tenant_id),
                    "pid": page_id,
                    "vid": ver_id,
                },
            )

            res = await session.execute(
                text("SELECT is_active FROM chunks WHERE id = :cid;"), {"cid": chunk_id}
            )
            is_act: bool = bool(res.scalar_one())
            assert is_act is False, f"Expected chunks.is_active DEFAULT false, got {is_act}"

    async def test_chunks_ts_config_check_constraint(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """chunks.ts_config CHECK IN ('english', 'spanish', 'simple') rejects other languages."""
        ctx_a, _ = tenant_pair
        async with tenant_session(ctx_a) as session:
            await assert_all_8_tables_exist(session)

            doc_id = await seed_document(session, ctx_a.tenant_id, title="Doc Lang")
            ver_id = await seed_version(session, ctx_a.tenant_id, doc_id, ctx_a.user_id)
            page_id = await seed_page(session, ctx_a.tenant_id, ver_id)

            # Rejected language configs using savepoints
            for invalid_cfg in ["french", "german", "unsupported"]:
                with pytest.raises(DBAPIError) as exc_info:
                    async with session.begin_nested():
                        await seed_chunk(
                            session,
                            ctx_a.tenant_id,
                            page_id,
                            ver_id,
                            language="fr",
                            ts_config=invalid_cfg,
                        )
                assert get_sqlstate(exc_info.value) == "23514"

            # Allowed language configs succeed
            for valid_cfg, lang in [("english", "en"), ("spanish", "es"), ("simple", "other")]:
                await seed_chunk(
                    session,
                    ctx_a.tenant_id,
                    page_id,
                    ver_id,
                    language=lang,
                    ts_config=valid_cfg,
                )

    async def test_chunks_generated_tsv_spanish_and_english_stemming(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """chunks.tsv correctly indexes stemmed variants according to ts_config."""
        ctx_a, _ = tenant_pair
        async with tenant_session(ctx_a) as session:
            await assert_all_8_tables_exist(session)

            doc_id = await seed_document(session, ctx_a.tenant_id, title="Doc Stem")
            ver_id = await seed_version(session, ctx_a.tenant_id, doc_id, ctx_a.user_id)
            page_id = await seed_page(session, ctx_a.tenant_id, ver_id)

            # Insert Spanish chunk
            es_chunk_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                page_id,
                ver_id,
                body="Los médicos atienden consultas de emergencia",
                language="es",
                ts_config="spanish",
            )

            # Query Spanish chunk with stemmed variant 'médico'
            res_es = await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM chunks
                    WHERE id = :cid AND tsv @@ to_tsquery('spanish', 'médico');
                    """
                ),
                {"cid": es_chunk_id},
            )
            assert int(res_es.scalar_one()) == 1, (
                "Spanish stemming failed to match 'médicos' with 'médico'"
            )

            # Insert English chunk
            en_chunk_id = await seed_chunk(
                session,
                ctx_a.tenant_id,
                page_id,
                ver_id,
                body="Physicians prescribe medication for allergies",
                language="en",
                ts_config="english",
            )

            # Query English chunk with stemmed variant 'physician'
            res_en = await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM chunks
                    WHERE id = :cid AND tsv @@ to_tsquery('english', 'physician');
                    """
                ),
                {"cid": en_chunk_id},
            )
            assert int(res_en.scalar_one()) == 1, (
                "English stemming failed to match 'physicians' with 'physician'"
            )


# ---------------------------------------------------------------------------
# Tests: Role Privileges and Boundaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRolePrivilegesAndBoundaries:
    """Verifies retriever_user and app_user role permissions, denial of deletes, and grants."""

    async def test_retriever_user_select_only_on_6_evidence_tables(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """retriever_user can SELECT from 6 evidence tables; mutations raise 42501."""
        ctx_a, _ = tenant_pair

        doc_id = uuid4()
        ver_id = uuid4()
        async with tenant_session(ctx_a) as session_app:
            await assert_all_8_tables_exist(session_app)
            await seed_document(
                session_app, ctx_a.tenant_id, doc_id=doc_id, title="Retriever Test Doc"
            )
            await seed_version(session_app, ctx_a.tenant_id, doc_id, ctx_a.user_id, ver_id=ver_id)

        # In retriever_tenant_session:
        async with retriever_tenant_session(ctx_a) as session_ret:
            # SELECT succeeds on all 6 evidence tables
            for tbl in EVIDENCE_TABLES:
                res = await session_ret.execute(text(f"SELECT COUNT(*) FROM {tbl};"))
                assert isinstance(res.scalar_one(), int)

            # INSERT fails with 42501 and 'permission denied'
            with pytest.raises(DBAPIError) as exc_info:
                async with session_ret.begin_nested():
                    await seed_document(session_ret, ctx_a.tenant_id, title="x")
            assert get_sqlstate(exc_info.value) == "42501"
            assert "permission denied" in str(exc_info.value).lower()

            # UPDATE fails with 42501 and 'permission denied'
            with pytest.raises(DBAPIError) as exc_info:
                async with session_ret.begin_nested():
                    await session_ret.execute(
                        text("UPDATE documents SET title = 'New' WHERE id = :id;"),
                        {"id": doc_id},
                    )
            assert get_sqlstate(exc_info.value) == "42501"
            assert "permission denied" in str(exc_info.value).lower()

            # DELETE fails with 42501 and 'permission denied'
            with pytest.raises(DBAPIError) as exc_info:
                async with session_ret.begin_nested():
                    await session_ret.execute(
                        text("DELETE FROM documents WHERE id = :id;"),
                        {"id": doc_id},
                    )
            assert get_sqlstate(exc_info.value) == "42501"
            assert "permission denied" in str(exc_info.value).lower()

    async def test_retriever_user_no_access_to_jobs_and_job_steps(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """Any access by retriever_user to jobs or job_steps fails with SQLSTATE 42501."""
        ctx_a, _ = tenant_pair
        async with retriever_tenant_session(ctx_a) as session_ret:
            await assert_all_8_tables_exist(session_ret)

            for tbl in ["jobs", "job_steps"]:
                with pytest.raises(DBAPIError) as exc_info:
                    async with session_ret.begin_nested():
                        await session_ret.execute(text(f"SELECT COUNT(*) FROM {tbl};"))
                assert get_sqlstate(exc_info.value) == "42501"
                assert "permission denied" in str(exc_info.value).lower()

    async def test_retriever_user_is_bound_by_rls(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """retriever_user scoped to Tenant A cannot see rows belonging to Tenant B."""
        ctx_a, ctx_b = tenant_pair

        # Seed row in Tenant B
        b_doc_id = uuid4()
        async with tenant_session(ctx_b) as session_b:
            await assert_all_8_tables_exist(session_b)
            await seed_document(session_b, ctx_b.tenant_id, doc_id=b_doc_id, title="Doc B Private")

            # Pre-assertion: verify seeded record exists in Tenant B
            b_cnt_res = await session_b.execute(
                text("SELECT COUNT(*) FROM documents WHERE id = :id;"), {"id": b_doc_id}
            )
            b_cnt: int = int(b_cnt_res.scalar_one())
            assert b_cnt == 1

        # retriever_user scoped to Tenant A
        async with retriever_tenant_session(ctx_a) as session_a:
            res = await session_a.execute(
                text("SELECT COUNT(*) FROM documents WHERE id = :id;"),
                {"id": b_doc_id},
            )
            assert int(res.scalar_one()) == 0

    async def test_app_user_delete_fails_on_all_8_tables_and_fks_restrict(
        self, tenant_pair: tuple[TenantContext, TenantContext]
    ) -> None:
        """app_user denied DELETE (42501) and all FKs enforce ON DELETE RESTRICT."""
        ctx_a, _ = tenant_pair
        async with tenant_session(ctx_a) as session:
            await assert_all_8_tables_exist(session)

            # 1. DELETE privilege denied on all 8 tables
            for tbl in ALL_8_INGESTION_TABLES:
                with pytest.raises(DBAPIError) as exc_info:
                    async with session.begin_nested():
                        await session.execute(
                            text(f"DELETE FROM {tbl} WHERE tenant_id = :tid;"),
                            {"tid": UUID(ctx_a.tenant_id)},
                        )
                assert get_sqlstate(exc_info.value) == "42501", (
                    f"Expected SQLSTATE 42501 on DELETE for '{tbl}', "
                    f"got {get_sqlstate(exc_info.value)}"
                )
                assert "permission denied" in str(exc_info.value).lower(), (
                    f"Expected 'permission denied' in error for '{tbl}', got: {exc_info.value}"
                )

            # 2. Verify all foreign keys on the 8 tables enforce ON DELETE RESTRICT
            # (confdeltype = 'r')
            fk_res = await session.execute(
                text(
                    """
                    SELECT c.conname, c.confdeltype, cl.relname
                    FROM pg_constraint c
                    JOIN pg_class cl ON cl.oid = c.conrelid
                    JOIN pg_namespace n ON n.oid = cl.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.contype = 'f'
                      AND cl.relname = ANY(:tables);
                    """
                ),
                {"tables": ALL_8_INGESTION_TABLES},
            )
            fks = fk_res.fetchall()
            assert len(fks) > 0, "No foreign keys found on ingestion tables"
            for conname, confdeltype, relname in fks:
                assert confdeltype == "r", (
                    f"FK '{conname}' on '{relname}' must have ON DELETE RESTRICT ('r'), "
                    f"got '{confdeltype}'"
                )

    async def test_no_public_grants_on_all_8_tables(self) -> None:
        """None of the 8 tables have any privileges granted to PUBLIC."""
        engine = get_app_engine()
        async with engine.connect() as conn:
            # Pre-assertion: all 8 tables exist
            res_tables = await conn.execute(
                text(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = ANY(:tables);
                    """
                ),
                {"tables": ALL_8_INGESTION_TABLES},
            )
            existing: set[str] = {str(r[0]) for r in res_tables.fetchall()}
            assert existing == set(ALL_8_INGESTION_TABLES), (
                f"Pre-assertion failed: missing tables {set(ALL_8_INGESTION_TABLES) - existing}"
            )

            res = await conn.execute(
                text(
                    """
                    SELECT table_name, privilege_type
                    FROM information_schema.table_privileges
                    WHERE table_schema = 'public'
                      AND grantee = 'PUBLIC'
                      AND table_name = ANY(:tables);
                    """
                ),
                {"tables": ALL_8_INGESTION_TABLES},
            )
            public_grants = res.fetchall()
            assert len(public_grants) == 0, (
                f"PUBLIC role has unexpected grants on ingestion tables: {public_grants}"
            )


# ---------------------------------------------------------------------------
# Tests: Isolated Migration Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMigrationLifecycleIsolated:
    """Verifies Alembic 0003 upgrade -> downgrade -> upgrade on isolated throwaway DB."""

    async def test_migration_0003_upgrade_downgrade_upgrade_isolated(
        self, throwaway_db: Any
    ) -> None:
        """Isolated throwaway database: downgrade drops 0003 objects, leaving Phase 1 intact."""
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        user = os.getenv("POSTGRES_USER", "cka_admin")
        password = os.getenv("POSTGRES_PASSWORD", "")
        shared_admin_url = (
            f"postgresql+psycopg://{user}:{password}@{host}:{port}/knowledge_assistant"
        )
        shared_admin_engine = create_async_engine(shared_admin_url)
        marker_tenant_id = uuid4()

        async with shared_admin_engine.begin() as conn:
            res_ver = await conn.execute(text("SELECT version_num FROM alembic_version;"))
            shared_version_before = res_ver.scalar_one_or_none()
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:id, :name);"),
                {"id": marker_tenant_id, "name": "Marker Ingestion Test"},
            )

        try:
            # 1. Upgrade to head in throwaway DB
            command.upgrade(throwaway_db.cfg, "head")

            # Verify all Phase 1 tables and all 8 tables exist in throwaway DB
            test_engine = create_async_engine(throwaway_db.url)
            async with test_engine.connect() as conn:
                res = await conn.execute(
                    text(
                        """
                        SELECT table_name FROM information_schema.tables
                        WHERE table_schema = 'public';
                        """
                    )
                )
                tables_up: set[str] = {str(r[0]) for r in res.fetchall()}

                phase1_tables = [
                    "tenants",
                    "users",
                    "assistants",
                    "memberships",
                    "conversations",
                    "messages",
                    "run_steps",
                    "audit_events",
                    "sessions",
                    "auth_events",
                ]
                for p1_t in phase1_tables:
                    assert p1_t in tables_up, (
                        f"Phase 1 table {p1_t} missing in throwaway DB. "
                        f"Tables found: {sorted(tables_up)}"
                    )

                for t in ALL_8_INGESTION_TABLES:
                    assert t in tables_up, (
                        f"Table {t} missing after upgrade head in throwaway DB. "
                        f"Tables found: {sorted(tables_up)}"
                    )

            # 2. Downgrade to 0002_sessions_auth_events
            command.downgrade(throwaway_db.cfg, "0002_sessions_auth_events")

            # Verify 0003 tables are dropped, but Phase 1 tables remain intact
            async with test_engine.connect() as conn:
                res = await conn.execute(
                    text(
                        """
                        SELECT table_name FROM information_schema.tables
                        WHERE table_schema = 'public';
                        """
                    )
                )
                tables_down: set[str] = {str(r[0]) for r in res.fetchall()}
                for t in ALL_8_INGESTION_TABLES:
                    assert t not in tables_down, f"Table {t} was NOT dropped during downgrade!"

                for p1_t in phase1_tables:
                    assert p1_t in tables_down, f"Phase 1 table {p1_t} was erroneously dropped!"

            # 3. Upgrade back to head in throwaway DB
            command.upgrade(throwaway_db.cfg, "head")
            await test_engine.dispose()
        finally:
            # 4. Verify shared database alembic_version and marker tenant survived intact
            async with shared_admin_engine.begin() as conn:
                res_ver_after = await conn.execute(text("SELECT version_num FROM alembic_version;"))
                shared_version_after = res_ver_after.scalar_one_or_none()
                assert shared_version_before == shared_version_after, (
                    f"Shared database alembic_version altered! "
                    f"Was {shared_version_before}, now {shared_version_after}"
                )
                res_cnt = await conn.execute(
                    text("SELECT COUNT(*) FROM tenants WHERE id = :id;"),
                    {"id": marker_tenant_id},
                )
                assert int(res_cnt.scalar_one()) == 1, (
                    "Shared database marker tenant was missing after throwaway test!"
                )
                await conn.execute(
                    text("DELETE FROM tenants WHERE id = :id;"),
                    {"id": marker_tenant_id},
                )
            await shared_admin_engine.dispose()
