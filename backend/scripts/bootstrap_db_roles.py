"""Bootstrap PostgreSQL database roles for development and staging.

Creates or updates app_user (read-write) and retriever_user (read-only) roles
with passwords supplied from environment variables.
Never hardcodes passwords or credentials in migrations or repository files.
"""

import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

# Add backend to sys.path if run directly as script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.env_utils import load_dotenv


def get_env_var(name: str, default: str | None = None) -> str:
    val = os.getenv(name, default)
    if val is None or val == "":
        print(f"Error: Required environment variable '{name}' is not set.", file=sys.stderr)
        sys.exit(1)
    return val


def bootstrap_roles() -> None:
    load_dotenv()
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    db = os.getenv("POSTGRES_DB", "knowledge_assistant")
    admin_user = os.getenv("POSTGRES_USER", "cka_admin")
    admin_password = get_env_var("POSTGRES_PASSWORD")

    app_user = os.getenv("DB_APP_USER", "app_user")
    app_password = get_env_var("DB_APP_PASSWORD")

    retriever_user = os.getenv("DB_RETRIEVER_USER", "retriever_user")
    retriever_password = get_env_var("DB_RETRIEVER_PASSWORD")

    conn_str = f"host={host} port={port} dbname={db} user={admin_user} password={admin_password}"
    print(f"Connecting to PostgreSQL at {host}:{port}/{db} as {admin_user}...")

    create_role_sql = (
        "CREATE ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
    )
    alter_role_sql = (
        "ALTER ROLE {} WITH PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
    )

    with psycopg.connect(conn_str, autocommit=True) as conn, conn.cursor() as cur:
        # Check and create/update app_user
        cur.execute(sql.SQL("SELECT 1 FROM pg_roles WHERE rolname = %s"), (app_user,))
        if cur.fetchone() is None:
            cur.execute(
                sql.SQL(create_role_sql).format(sql.Identifier(app_user), sql.Literal(app_password))
            )
        else:
            cur.execute(
                sql.SQL(alter_role_sql).format(sql.Identifier(app_user), sql.Literal(app_password))
            )
        print(f"Role '{app_user}' verified (NOSUPERUSER, NOBYPASSRLS).")

        # Check and create/update retriever_user
        cur.execute(sql.SQL("SELECT 1 FROM pg_roles WHERE rolname = %s"), (retriever_user,))
        if cur.fetchone() is None:
            cur.execute(
                sql.SQL(create_role_sql).format(
                    sql.Identifier(retriever_user), sql.Literal(retriever_password)
                )
            )
        else:
            cur.execute(
                sql.SQL(alter_role_sql).format(
                    sql.Identifier(retriever_user), sql.Literal(retriever_password)
                )
            )
        print(f"Role '{retriever_user}' verified (NOSUPERUSER, NOBYPASSRLS).")

    print("PostgreSQL roles bootstrapped successfully.")


if __name__ == "__main__":
    bootstrap_roles()
