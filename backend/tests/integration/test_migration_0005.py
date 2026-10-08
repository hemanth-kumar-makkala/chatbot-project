"""Integration tests for Migration 0005 and schema constraints.

Validates in an isolated throwaway database:
1. Migration 0005 upgrade -> downgrade -> upgrade clean run.
2. Duplicate page_readings row rejected (SQLSTATE 23505) with positive controls.
3. Duplicate tables_data row with page_id NULL rejected via NULLS NOT DISTINCT (SQLSTATE 23505)
   with positive controls.
4. Cross-tenant tables_data.page_id reference rejected by composite FK (SQLSTATE 23503)
   with positive control.
5. pages.needs_review defaults to false when omitted, with explicit override positive control.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.conftest import isolated_throwaway_database
from tests.fixtures.database_seeding import (
    seed_document,
    seed_page,
    seed_reading,
    seed_table_data,
    seed_version,
)


def get_sqlstate(exc: BaseException) -> str | None:
    """Extract 5-character SQLSTATE code from SQLAlchemy DBAPIError or underlying driver."""
    orig: Any = getattr(exc, "orig", exc)
    raw_state: Any = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return str(raw_state) if raw_state is not None else None


@pytest.mark.asyncio
async def test_migration_0005_upgrade_downgrade_lifecycle() -> None:
    """Verify migration 0005 applies, downgrades, and re-applies cleanly in isolated DB."""
    async with isolated_throwaway_database() as throwaway_db:
        # 1. Upgrade to head (includes 0005)
        command.upgrade(throwaway_db.cfg, "head")

        test_engine = create_async_engine(throwaway_db.url)
        async with test_engine.connect() as conn:
            # Verify new columns on pages
            res = await conn.execute(
                text(
                    """
                    SELECT column_name, data_type, column_default
                    FROM information_schema.columns
                    WHERE table_name = 'pages'
                      AND column_name IN ('ocr_confidence', 'needs_review');
                    """
                )
            )
            cols = {r[0]: (r[1], r[2]) for r in res.fetchall()}
            assert "ocr_confidence" in cols
            assert "needs_review" in cols
            assert "false" in str(cols["needs_review"][1]).lower()

            # Verify new columns on tables_data
            res_t = await conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'tables_data'
                      AND column_name IN ('page_id', 'table_index');
                    """
                )
            )
            t_cols = {r[0] for r in res_t.fetchall()}
            assert "page_id" in t_cols
            assert "table_index" in t_cols

        # 2. Downgrade to 0004
        command.downgrade(throwaway_db.cfg, "0004_page_type_and_status")

        async with test_engine.connect() as conn:
            res_down = await conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'pages'
                      AND column_name IN ('ocr_confidence', 'needs_review');
                    """
                )
            )
            assert res_down.fetchall() == [], "0005 columns were not dropped on downgrade!"

        # 3. Upgrade back to head
        command.upgrade(throwaway_db.cfg, "head")
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_page_readings_row_rejected_23505() -> None:
    """Duplicate page_readings row with same tenant, page, source, model must raise 23505."""
    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "head")

        engine = create_async_engine(throwaway_db.url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        tenant_id = uuid4()
        user_id = uuid4()

        async with session_factory() as session:
            await session.execute(
                text("INSERT INTO tenants (id, name) VALUES (:t, 'Tenant Readings');"),
                {"t": tenant_id},
            )
            await session.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) VALUES (:u, 'r@test.com', 'h');"
                ),
                {"u": user_id},
            )
            doc_id = await seed_document(session, tenant_id, title="Doc R")
            ver_id = await seed_version(session, tenant_id, doc_id, user_id, status="processing")
            page_id = await seed_page(session, tenant_id, ver_id, page_type="scanned")

            # Positive control 1: insert reading 1 succeeds
            reading_1_id = await seed_reading(
                session,
                tenant_id=tenant_id,
                page_id=page_id,
                source="docling_ocr",
                body="Text 1",
                model_id="easyocr",
                model_version="1.7.2",
            )
            await session.commit()
            assert reading_1_id is not None

            # Positive control 2: reading with different source on same page succeeds
            reading_diff_source_id = await seed_reading(
                session,
                tenant_id=tenant_id,
                page_id=page_id,
                source="tesseract",
                body="Text alternate OCR",
                model_id="easyocr",
                model_version="1.7.2",
            )
            await session.commit()
            assert reading_diff_source_id is not None

            # Negative test: duplicate insert with exact same unique key must raise 23505
            with pytest.raises(DBAPIError) as exc_info:
                await seed_reading(
                    session,
                    tenant_id=tenant_id,
                    page_id=page_id,
                    source="docling_ocr",
                    body="Text duplicate retry",
                    model_id="easyocr",
                    model_version="1.7.2",
                )
                await session.commit()

            assert get_sqlstate(exc_info.value) == "23505"
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_tables_data_null_page_rejected_nulls_not_distinct() -> None:
    """Spreadsheet rows (page_id NULL) must reject duplicates via NULLS NOT DISTINCT (23505)."""
    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "head")

        engine = create_async_engine(throwaway_db.url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        tenant_id = uuid4()
        user_id = uuid4()

        async with session_factory() as session:
            await session.execute(
                text("INSERT INTO tenants (id, name) VALUES (:t, 'Tenant Tables');"),
                {"t": tenant_id},
            )
            await session.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) "
                    "VALUES (:u, 'tb@test.com', 'h');"
                ),
                {"u": user_id},
            )
            doc_id = await seed_document(session, tenant_id, title="Sheet Doc")
            ver_id = await seed_version(session, tenant_id, doc_id, user_id, status="processing")

            # Positive control 1: insert spreadsheet row 1 (page_id is NULL) succeeds
            tbl_1 = await seed_table_data(
                session,
                tenant_id=tenant_id,
                ver_id=ver_id,
                sheet_name="Sheet1",
                row_index=1,
                row_data='{"colA": "val1"}',
            )
            await session.commit()
            assert tbl_1 is not None

            # Positive control 2: row with different sheet_name succeeds
            tbl_sheet2 = await seed_table_data(
                session,
                tenant_id=tenant_id,
                ver_id=ver_id,
                sheet_name="Sheet2",
                row_index=1,
                row_data='{"colA": "val1"}',
            )
            await session.commit()
            assert tbl_sheet2 is not None

            # Negative test: duplicate spreadsheet row with same unique key must raise 23505
            with pytest.raises(DBAPIError) as exc_info:
                await seed_table_data(
                    session,
                    tenant_id=tenant_id,
                    ver_id=ver_id,
                    sheet_name="Sheet1",
                    row_index=1,
                    row_data='{"colA": "val_diff"}',
                )
                await session.commit()

            assert get_sqlstate(exc_info.value) == "23505"
        await engine.dispose()


@pytest.mark.asyncio
async def test_tables_data_cross_tenant_page_id_rejected_23503() -> None:
    """tables_data pointing to another tenant's page_id must raise 23503 (ForeignKeyViolation)."""
    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "head")

        engine = create_async_engine(throwaway_db.url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        tenant_a = uuid4()
        tenant_b = uuid4()
        user_id = uuid4()

        async with session_factory() as session:
            # Seed Tenant A & B
            await session.execute(
                text("INSERT INTO tenants (id, name) VALUES (:t, 'Tenant A');"),
                {"t": tenant_a},
            )
            await session.execute(
                text("INSERT INTO tenants (id, name) VALUES (:t, 'Tenant B');"),
                {"t": tenant_b},
            )
            await session.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) VALUES (:u, 'u@test.com', 'h');"
                ),
                {"u": user_id},
            )

            # Tenant A doc, version, page
            doc_a = await seed_document(session, tenant_a, title="Doc A")
            ver_a = await seed_version(session, tenant_a, doc_a, user_id, status="processing")
            page_a = await seed_page(session, tenant_a, ver_a, page_number=1, page_type="scanned")

            # Tenant B doc, version, page
            doc_b = await seed_document(session, tenant_b, title="Doc B")
            ver_b = await seed_version(session, tenant_b, doc_b, user_id, status="processing")
            page_b = await seed_page(session, tenant_b, ver_b, page_number=1, page_type="scanned")
            await session.commit()

            # Positive control: Tenant B tables_data citing its OWN page succeeds
            await session.execute(
                text(
                    """
                    INSERT INTO tables_data (
                        id, tenant_id, document_version_id, sheet_name, page_id,
                        table_index, row_index, row_data
                    )
                    VALUES (:id, :tb, :vb, 'TableB', :pb, 0, 1, '{"val": 1}'::jsonb);
                    """
                ),
                {"id": uuid4(), "tb": tenant_b, "vb": ver_b, "pb": page_b},
            )
            await session.commit()

            # Negative test: Tenant B tables_data referencing Tenant A's page_id must raise 23503
            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(
                    text(
                        """
                        INSERT INTO tables_data (
                            id, tenant_id, document_version_id, sheet_name, page_id,
                            table_index, row_index, row_data
                        )
                        VALUES (:id, :tb, :vb, 'TableBad', :pa, 0, 1, '{"val": 2}'::jsonb);
                        """
                    ),
                    {"id": uuid4(), "tb": tenant_b, "vb": ver_b, "pa": page_a},
                )
                await session.commit()

            assert get_sqlstate(exc_info.value) == "23503"
        await engine.dispose()


@pytest.mark.asyncio
async def test_pages_needs_review_defaults_to_false() -> None:
    """Inserting into pages without needs_review must default to False."""
    async with isolated_throwaway_database() as throwaway_db:
        command.upgrade(throwaway_db.cfg, "head")

        engine = create_async_engine(throwaway_db.url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        tenant_id = uuid4()
        user_id = uuid4()

        async with session_factory() as session:
            await session.execute(
                text("INSERT INTO tenants (id, name) VALUES (:t, 'Tenant NR');"),
                {"t": tenant_id},
            )
            await session.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) "
                    "VALUES (:u, 'nr@test.com', 'h');"
                ),
                {"u": user_id},
            )
            doc_id = await seed_document(session, tenant_id, title="Doc NR")
            ver_id = await seed_version(session, tenant_id, doc_id, user_id, status="processing")

            # Positive control 1: seed_page uses real schema and omits needs_review & ocr_confidence
            page_id = await seed_page(
                session, tenant_id, ver_id, page_number=1, page_type="digital_text"
            )
            await session.commit()

            res = await session.execute(
                text("SELECT needs_review, ocr_confidence FROM pages WHERE id = :p;"),
                {"p": page_id},
            )
            row = res.fetchone()
            assert row is not None
            needs_review, ocr_confidence = row
            assert needs_review is False
            assert ocr_confidence is None

            # Positive control 2: explicit values persist accurately
            page_explicit_id = uuid4()
            await session.execute(
                text(
                    """
                    INSERT INTO pages (
                        id, tenant_id, document_version_id, page_number, page_sha256,
                        page_type, image_key, needs_review, ocr_confidence
                    )
                    VALUES (
                        :p, :t, :v, 2, 'sha-p2', 'scanned', (:t || '/pages/2.png'), true, 0.62
                    );
                    """
                ),
                {"p": page_explicit_id, "t": tenant_id, "v": ver_id},
            )
            await session.commit()

            res_exp = await session.execute(
                text("SELECT needs_review, ocr_confidence FROM pages WHERE id = :p;"),
                {"p": page_explicit_id},
            )
            row_exp = res_exp.fetchone()
            assert row_exp is not None
            assert row_exp[0] is True
            assert pytest.approx(row_exp[1], 0.01) == 0.62
        await engine.dispose()
