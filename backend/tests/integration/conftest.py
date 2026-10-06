"""Integration test fixtures and database persistence verification."""

import os
from collections.abc import Iterator
from uuid import UUID

import psycopg
import pytest

from app.db.env_utils import load_dotenv

FIXED_SUITE_MARKER_TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")


@pytest.fixture(scope="session", autouse=True)
def verify_shared_database_persistence_across_suite() -> Iterator[UUID]:
    """Ensures that running integration tests never wipes the shared database.

    Fails loudly if PostgreSQL cannot be reached.
    """
    load_dotenv()
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "cka_admin")
    password = os.getenv("POSTGRES_PASSWORD", "")
    db_name = os.getenv("POSTGRES_DB", "knowledge_assistant")

    marker_id = FIXED_SUITE_MARKER_TENANT_ID
    conn_str = f"host={host} port={port} user={user} password={password} dbname={db_name}"

    # Setup: connect and insert fixed marker tenant row (must fail loudly if DB down)
    with psycopg.connect(conn_str, autocommit=True) as conn, conn.cursor() as cur:
        # Upsert fixed marker tenant to handle previous test run artifacts cleanly
        cur.execute(
            """
            INSERT INTO tenants (id, name)
            VALUES (%s, %s)
            ON CONFLICT (id) DO NOTHING;
            """,
            (marker_id, "Fixed Integration Suite Marker"),
        )

    yield marker_id

    # Teardown: assert marker tenant is still present in shared DB, then clean up
    with psycopg.connect(conn_str, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM tenants WHERE id = %s;", (marker_id,))
        row = cur.fetchone()
        cnt = row[0] if row else 0
        assert cnt == 1, (
            f"CRITICAL: Shared database '{db_name}' was wiped during integration tests! "
            f"Marker tenant {marker_id} was lost."
        )
        cur.execute("DELETE FROM tenants WHERE id = %s;", (marker_id,))
