"""Read-only check for unique key constraint violations prior to Migration 0005.

Checks:
1. page_readings duplicates on (tenant_id, page_id, source, model_id, model_version)
2. tables_data duplicates on (tenant_id, document_version_id, sheet_name, row_index)
   (precursor to (tenant_id, document_version_id, sheet_name, page_id, table_index, row_index))

Per .agents/rules/execution.md Section 8:
- Database credentials are read from .env at runtime via app.db.env_utils.load_dotenv().
- No credentials or connection strings are printed or logged.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.db.env_utils import load_dotenv


def get_db_url() -> str:
    load_dotenv()
    url = os.getenv("ALEMBIC_DATABASE_URL")
    if url:
        return url
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "knowledge_assistant")
    user = os.getenv("POSTGRES_USER", "cka_admin")
    password = os.getenv("POSTGRES_PASSWORD", "")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


def main() -> int:
    db_url = get_db_url()

    print("Connecting to dev DB using credentials from .env...")
    try:
        engine = create_engine(db_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            # Check 1: page_readings duplicates
            q1 = text(
                """
                SELECT tenant_id, page_id, source, model_id, model_version, COUNT(*) as cnt
                FROM page_readings
                GROUP BY tenant_id, page_id, source, model_id, model_version
                HAVING COUNT(*) > 1
                LIMIT 10;
                """
            )
            dups_readings = conn.execute(q1).fetchall()

            count_readings = conn.execute(text("SELECT COUNT(*) FROM page_readings;")).scalar()

            print(f"Total rows in page_readings: {count_readings}")
            if dups_readings:
                print(f"VIOLATIONS FOUND in page_readings: {len(dups_readings)} duplicate groups:")
                for r in dups_readings:
                    print(f"  tenant={r[0]} page={r[1]} src={r[2]} model={r[3]}:{r[4]} cnt={r[5]}")
            else:
                print("No duplicate violations found in page_readings.")

            # Check 2: tables_data duplicates
            q2 = text(
                """
                SELECT tenant_id, document_version_id, sheet_name, row_index, COUNT(*) as cnt
                FROM tables_data
                GROUP BY tenant_id, document_version_id, sheet_name, row_index
                HAVING COUNT(*) > 1
                LIMIT 10;
                """
            )
            dups_tables = conn.execute(q2).fetchall()

            count_tables = conn.execute(text("SELECT COUNT(*) FROM tables_data;")).scalar()

            print(f"Total rows in tables_data: {count_tables}")
            if dups_tables:
                print(f"VIOLATIONS FOUND in tables_data: {len(dups_tables)} duplicate groups:")
                for r in dups_tables:
                    print(f"  tenant={r[0]} version={r[1]} sheet={r[2]} row={r[3]} cnt={r[4]}")
            else:
                print("No duplicate violations found in tables_data.")

            # Check 3: pages columns inspection
            q3 = text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'pages' AND column_name IN ('ocr_confidence', 'needs_review');
                """
            )
            existing_cols = [r[0] for r in conn.execute(q3).fetchall()]
            print(f"Existing migration 0005 target columns on 'pages': {existing_cols}")

            if dups_readings or dups_tables:
                print(
                    "FAILED: Duplicates exist that would block migration 0005 unique constraints."
                )
                return 1

            print(
                "PASSED: Dev DB is clean. Migration 0005 unique constraints can be safely applied."
            )
            return 0

    except OperationalError as exc:
        print(f"Database connection error (dev DB offline or unreachable): {exc.orig or exc}")
        return 2
    except Exception as exc:
        print(f"Error querying dev database: {exc}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
