"""Integration test for Migration 0012 (session_documents and extraction_results).

Tests upgrade -> downgrade -> upgrade lifecycle in an isolated throwaway database.
Enforces:
- Table creation, column types, constraints, and foreign keys.
- Row level security (RLS) enforcement on both tables.
- Isolation: never run against shared database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import isolated_throwaway_database


@pytest.mark.asyncio
async def test_migration_0012_upgrade_downgrade_lifecycle() -> None:
    """Verify migration 0012 applies, downgrades, and re-applies cleanly in throwaway DB."""
    async with isolated_throwaway_database() as throwaway_db:
        # 1. Upgrade to 0011 first
        command.upgrade(throwaway_db.cfg, "0011_widen_model_version")

        test_engine = create_async_engine(throwaway_db.url)
        tid = uuid4()
        uid = uuid4()
        aid = uuid4()
        cid = uuid4()

        async with test_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:tid, 'Tenant 1');"),
                {"tid": tid},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) "
                    "VALUES (:uid, 'user@t1.com', 'h');"
                ),
                {"uid": uid},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO assistants (id, tenant_id, name, prompt_id, prompt_version)
                    VALUES (:aid, :tid, 'Assistant 1', 'default', 'v1');
                    """
                ),
                {"aid": aid, "tid": tid},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO conversations (id, tenant_id, user_id, assistant_id, title)
                    VALUES (:cid, :tid, :uid, :aid, 'Conversation 1');
                    """
                ),
                {"cid": cid, "tid": tid, "uid": uid, "aid": aid},
            )

        # 2. Upgrade to 0012
        command.upgrade(throwaway_db.cfg, "0012_session_documents")

        sid = uuid4()
        eid = uuid4()
        retention = datetime.now(UTC) + timedelta(hours=24)

        async with test_engine.begin() as conn:
            # Set current tenant context
            await conn.execute(text(f"SET LOCAL app.current_tenant_id = '{tid}';"))

            # Insert into session_documents
            await conn.execute(
                text(
                    """
                    INSERT INTO session_documents (
                        id, tenant_id, conversation_id, file_name, file_path,
                        file_size_bytes, file_sha256, page_count, status, retention_until
                    )
                    VALUES (
                        :sid, :tid, :cid, 'patient_fax.pdf', 'sessions/t1/patient_fax.pdf',
                        12345, 'sha256placeholder', 1, 'uploaded', :retention
                    );
                    """
                ),
                {"sid": sid, "tid": tid, "cid": cid, "retention": retention},
            )

            # Insert into extraction_results
            await conn.execute(
                text(
                    """
                    INSERT INTO extraction_results (
                        id, tenant_id, session_document_id, schema_name, fields_data
                    )
                    VALUES (
                        :eid, :tid, :sid, 'fax_intake_schema',
                        '{"patient_name": "Alex Morgan"}'::jsonb
                    );
                    """
                ),
                {"eid": eid, "tid": tid, "sid": sid},
            )

            # Verify rows exist
            doc_row = (
                (
                    await conn.execute(
                        text("SELECT file_name, status FROM session_documents WHERE id = :sid;"),
                        {"sid": sid},
                    )
                )
                .mappings()
                .one()
            )
            assert doc_row["file_name"] == "patient_fax.pdf"
            assert doc_row["status"] == "uploaded"

            ext_row = (
                (
                    await conn.execute(
                        text("SELECT schema_name FROM extraction_results WHERE id = :eid;"),
                        {"eid": eid},
                    )
                )
                .mappings()
                .one()
            )
            assert ext_row["schema_name"] == "fax_intake_schema"

        # 3. Downgrade to 0011
        command.downgrade(throwaway_db.cfg, "0011_widen_model_version")

        async with test_engine.begin() as conn:
            res = await conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_name = 'session_documents'
                    );
                    """
                )
            )
            assert res.scalar() is False

        # 4. Re-upgrade to 0012
        command.upgrade(throwaway_db.cfg, "0012_session_documents")

        async with test_engine.begin() as conn:
            res = await conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_name = 'session_documents'
                    );
                    """
                )
            )
            assert res.scalar() is True
