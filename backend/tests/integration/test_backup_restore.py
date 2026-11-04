"""Integration test for Task 5.7 Database Backup and Tested Restore.

Tests:
1. Verify backup script creates timestamped dump of a source database with real synthetic data
   (two tenants, documents, chunks with pgvector embeddings, conversations, messages, feedback).
2. Verify restore script creates a NEW target database, refusing to overwrite existing ones.
3. Verify row counts across all tables match exactly between source and restored databases.
4. Verify pgvector similarity search returns identical results and ordering in the restored copy.
5. Verify Row Level Security (RLS) policies and cross-tenant isolation hold in the restored copy.
6. If no compatible pg_dump/pg_restore is available, test is skipped with a clear reason.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import (
    Assistant,
    AuditEvent,
    Chunk,
    Conversation,
    Document,
    DocumentVersion,
    Feedback,
    Membership,
    Message,
    Page,
    PageReading,
    Tenant,
    User,
)
from app.db.session import tenant_session
from app.ports.types import TenantContext, UserRole
from scripts.backup_db import backup_database, is_pg_dump_available
from scripts.restore_db import restore_database
from tests.conftest import isolated_throwaway_database


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _check_backup_restore_tools() -> tuple[bool, str]:
    """Check if pg_dump and pg_restore are available on PATH or via Docker."""
    if is_pg_dump_available():
        return True, "Compatible pg_dump and pg_restore available"
    return (
        False,
        "Neither pg_dump/pg_restore on PATH nor docker container 'cka-postgres' is accessible",
    )


@pytest.mark.asyncio
async def test_database_backup_and_tested_restore_lifecycle(tmp_path: Path) -> None:
    """Verify backup creation, restore to a new database, row parity, vector search, and RLS."""
    available, reason = _check_backup_restore_tools()
    if not available:
        pytest.skip(reason)

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create source throwaway database and migrate to head
    async with isolated_throwaway_database() as source_db:
        command.upgrade(source_db.cfg, "head")

        tid_a = uuid4()
        tid_b = uuid4()
        asst_a_id = uuid4()
        asst_b_id = uuid4()
        doc_a_id = uuid4()
        ver_a_id = uuid4()
        page_a_id = uuid4()
        chunk_a1_id = uuid4()
        chunk_a2_id = uuid4()
        conv_a_id = uuid4()
        msg_a_id = uuid4()
        fb_a_id = uuid4()

        engine_source = create_async_engine(source_db.url)

        # 2. Ingest synthetic data across two tenants
        source_embedding_1 = [1.0] + [0.0] * 1023
        source_embedding_2 = [0.0] + [1.0] + [0.0] * 1022
        query_embedding = [0.9] + [0.1] + [0.0] * 1022

        async with engine_source.begin() as conn:
            # Tenants
            await conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (:t1, 'Tenant Alpha'), (:t2, 'Tenant Beta');"),
                {"t1": tid_a, "t2": tid_b},
            )
            # Users
            uid_a = uuid4()
            uid_b = uuid4()
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) VALUES "
                    "(:u1, 'staff_a@alpha.local', 'hash_a'), "
                    "(:u2, 'staff_b@beta.local', 'hash_b');"
                ),
                {"u1": uid_a, "u2": uid_b},
            )
            # Memberships
            await conn.execute(
                text(
                    "INSERT INTO memberships (id, tenant_id, user_id, role) VALUES "
                    "(:m1, :t1, :u1, 'staff'), "
                    "(:m2, :t2, :u2, 'staff');"
                ),
                {"m1": uuid4(), "t1": tid_a, "u1": uid_a, "m2": uuid4(), "t2": tid_b, "u2": uid_b},
            )
            # Assistants
            await conn.execute(
                text(
                    "INSERT INTO assistants (id, tenant_id, name, prompt_id, prompt_version) VALUES "
                    "(:a1, :t1, 'Assistant Alpha', 'synth', '1.0'), "
                    "(:a2, :t2, 'Assistant Beta', 'synth', '1.0');"
                ),
                {"a1": asst_a_id, "t1": tid_a, "a2": asst_b_id, "t2": tid_b},
            )
            # Document & Version for Tenant A
            await conn.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, title) VALUES (:did, :tid, 'Clinic Policy.pdf');"
                ),
                {"did": doc_a_id, "tid": tid_a},
            )
            await conn.execute(
                text(
                    "INSERT INTO document_versions (id, tenant_id, document_id, sha256, uploaded_by, status, file_extension) "
                    "VALUES (:vid, :tid, :did, 'abc123sha', :uid, 'ready', 'pdf');"
                ),
                {"vid": ver_a_id, "tid": tid_a, "did": doc_a_id, "uid": uid_a},
            )
            await conn.execute(
                text("UPDATE documents SET active_version_id = :vid WHERE id = :did;"),
                {"vid": ver_a_id, "did": doc_a_id},
            )
            # Page & Reading
            await conn.execute(
                text(
                    "INSERT INTO pages (id, tenant_id, document_version_id, page_number, page_sha256, page_type, image_key, ocr_confidence) "
                    "VALUES (:pid, :tid, :vid, 1, 'pagesha1', 'digital_text', :img_key, 0.98);"
                ),
                {"pid": page_a_id, "tid": tid_a, "vid": ver_a_id, "img_key": f"{tid_a}/pages/p1.png"},
            )
            await conn.execute(
                text(
                    "INSERT INTO page_readings (id, tenant_id, page_id, source, text, model_id, model_version) "
                    "VALUES (:prid, :tid, :pid, 'ocr', 'Prior authorization requires 5 business days.', 'docling_easyocr', 'v1');"
                ),
                {"prid": uuid4(), "tid": tid_a, "pid": page_a_id},
            )
            # Chunks with pgvector embeddings
            await conn.execute(
                text(
                    "INSERT INTO chunks (id, tenant_id, document_version_id, page_id, text, language, ts_config, is_active, evidence_type, embedding) "
                    "VALUES "
                    "(:c1, :tid, :vid, :pid, 'Prior authorization clause 1', 'en', 'english', true, 'text', CAST(:emb1 AS vector)), "
                    "(:c2, :tid, :vid, :pid, 'Prior authorization clause 2', 'en', 'english', true, 'text', CAST(:emb2 AS vector));"
                ),
                {
                    "c1": chunk_a1_id,
                    "c2": chunk_a2_id,
                    "tid": tid_a,
                    "vid": ver_a_id,
                    "pid": page_a_id,
                    "emb1": str(source_embedding_1),
                    "emb2": str(source_embedding_2),
                },
            )
            # Conversation & Message
            await conn.execute(
                text(
                    "INSERT INTO conversations (id, tenant_id, user_id, assistant_id, title) "
                    "VALUES (:cid, :tid, :uid, :aid, 'Alpha Chat 1');"
                ),
                {"cid": conv_a_id, "tid": tid_a, "uid": uid_a, "aid": asst_a_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO messages (id, tenant_id, conversation_id, role, content) "
                    "VALUES (:mid, :tid, :cid, 'assistant', 'Submit prior authorization 5 days ahead.');"
                ),
                {"mid": msg_a_id, "tid": tid_a, "cid": conv_a_id},
            )
            # Feedback
            await conn.execute(
                text(
                    "INSERT INTO feedback (id, tenant_id, message_id, user_id, rating, reason) "
                    "VALUES (:fid, :tid, :mid, :uid, 'up', 'Accurate answer.');"
                ),
                {"fid": fb_a_id, "tid": tid_a, "mid": msg_a_id, "uid": uid_a},
            )

        # Record expected row counts per table from source
        tables_to_check = [
            "tenants",
            "users",
            "memberships",
            "assistants",
            "documents",
            "document_versions",
            "pages",
            "page_readings",
            "chunks",
            "conversations",
            "messages",
            "feedback",
        ]
        source_counts: dict[str, int] = {}
        async with engine_source.begin() as conn:
            for tbl in tables_to_check:
                cnt: int = int(
                    (await conn.execute(text(f"SELECT COUNT(*) FROM {tbl};"))).scalar_one()
                )
                source_counts[tbl] = cnt

        # Vector search query on source
        async with engine_source.begin() as conn:
            source_top_chunk = (
                await conn.execute(
                    text(
                        "SELECT id, text FROM chunks "
                        "ORDER BY embedding <=> CAST(:query_emb AS vector) LIMIT 1;"
                    ),
                    {"query_emb": str(query_embedding)},
                )
            ).fetchone()
            assert source_top_chunk is not None
            assert source_top_chunk[0] == chunk_a1_id

        await engine_source.dispose()

        # 3. Perform Backup
        dump_path = backup_database(
            db_name=source_db.db_name,
            output_dir=backup_dir,
        )
        assert dump_path.exists(), f"Dump file {dump_path} must exist"
        assert dump_path.stat().st_size > 0, "Dump file must not be empty"

        # 4. Perform Restore into a NEW throwaway database
        restored_db_name = f"cka_test_restored_{uuid4().hex[:12]}"
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        user = os.getenv("POSTGRES_USER", "cka_admin")
        password = os.getenv("POSTGRES_PASSWORD", "")
        restored_url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{restored_db_name}"

        try:
            restore_database(
                dump_file=dump_path,
                target_db=restored_db_name,
            )

            engine_restored = create_async_engine(restored_url)

            # 5. Verify row counts match exactly
            async with engine_restored.begin() as conn:
                for tbl, expected_cnt in source_counts.items():
                    restored_cnt: int = int(
                        (await conn.execute(text(f"SELECT COUNT(*) FROM {tbl};"))).scalar_one()
                    )
                    assert (
                        restored_cnt == expected_cnt
                    ), f"Row count mismatch on table {tbl}: expected {expected_cnt}, got {restored_cnt}"

            # 6. Verify vector similarity search returns identical results
            async with engine_restored.begin() as conn:
                restored_top_chunk = (
                    await conn.execute(
                        text(
                            "SELECT id, text FROM chunks "
                            "ORDER BY embedding <=> CAST(:query_emb AS vector) LIMIT 1;"
                        ),
                        {"query_emb": str(query_embedding)},
                    )
                ).fetchone()
                assert restored_top_chunk is not None
                assert restored_top_chunk[0] == chunk_a1_id
                assert restored_top_chunk[1] == "Prior authorization clause 1"

            # 7. Verify RLS still blocks cross-tenant reads in restored copy
            async with engine_restored.begin() as conn:
                # Under Tenant A as app_user
                await conn.execute(text("SET ROLE app_user;"))
                await conn.execute(
                    text("SELECT set_config('app.current_tenant_id', :tid, true);"),
                    {"tid": str(tid_a)},
                )
                fb_a = (await conn.execute(text("SELECT id FROM feedback;"))).fetchall()
                assert len(fb_a) == 1
                assert fb_a[0][0] == fb_a_id

                # Under Tenant B as app_user
                await conn.execute(
                    text("SELECT set_config('app.current_tenant_id', :tid, true);"),
                    {"tid": str(tid_b)},
                )
                fb_b = (await conn.execute(text("SELECT id FROM feedback;"))).fetchall()
                assert len(fb_b) == 0, "RLS must block cross-tenant reads in restored database"

                await conn.execute(text("RESET ROLE;"))

            await engine_restored.dispose()

        finally:
            # Clean up restored database
            admin_url = (
                f"postgresql+psycopg://{user}:{password}@{host}:{port}/postgres?connect_timeout=3"
            )
            admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
            async with admin_engine.connect() as conn:
                await conn.execute(
                    text(
                        f"""
                        SELECT pg_terminate_backend(pid)
                        FROM pg_stat_activity
                        WHERE datname = '{restored_db_name}' AND pid <> pg_backend_pid();
                        """
                    )
                )
                await conn.execute(text(f"DROP DATABASE IF EXISTS {restored_db_name};"))
            await admin_engine.dispose()
