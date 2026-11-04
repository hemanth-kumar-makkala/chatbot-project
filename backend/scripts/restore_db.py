"""Database restore script restoring a pg_dump file into a NEW database only.

Enforces:
- Restores strictly into a NEW database; never overwrites an existing database.
- Protected database check preventing overwriting primary databases.
- Execution via local pg_restore on PATH or cka-postgres Docker container.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg

from app.db.env_utils import load_dotenv

load_dotenv()

PROTECTED_DATABASES: frozenset[str] = frozenset(
    {"postgres", "knowledge_assistant", "template0", "template1"}
)


def _is_docker_postgres_available() -> bool:
    """Check if docker is available and cka-postgres container is running."""
    if shutil.which("docker") is None:
        return False
    try:
        res = subprocess.run(
            ["docker", "exec", "cka-postgres", "pg_restore", "--version"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


def restore_database(
    dump_file: Path | str,
    target_db: str,
    user: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Restore a pg_dump custom archive into a NEW database.

    Raises:
        ValueError: If target_db is protected, already exists, or dump_file is missing.
        RuntimeError: If neither pg_restore on PATH nor cka-postgres container is available.
        subprocess.CalledProcessError: If pg_restore execution fails.
    """
    dump_path = Path(dump_file).resolve()
    if not dump_path.exists():
        raise ValueError(f"Dump file '{dump_path}' does not exist.")

    normalized_target_db = target_db.strip().lower()
    if normalized_target_db in PROTECTED_DATABASES:
        raise ValueError(
            f"Restoring over protected database '{target_db}' is strictly forbidden. "
            "Restores must target a NEW throwaway database."
        )

    pg_user = user or os.getenv("POSTGRES_USER") or "cka_admin"
    pg_host = host or os.getenv("POSTGRES_HOST") or "localhost"
    pg_port = port or int(os.getenv("POSTGRES_PORT") or "5432")
    pg_pass = os.getenv("POSTGRES_PASSWORD", "")

    # 1. Connect to administrative maintenance database (postgres) to check and create target_db
    conn_info = f"host={pg_host} port={pg_port} user={pg_user} password={pg_pass} dbname=postgres"
    with (
        psycopg.connect(conn_info, autocommit=True) as admin_conn,
        admin_conn.cursor() as cur,
    ):
        cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s;",
            (target_db,),
        )
        if cur.fetchone() is not None:
            raise ValueError(
                f"Target database '{target_db}' already exists. Restores are strictly "
                "permitted into a NEW database only, never over an existing one."
            )

        # Create the fresh target database
        cur.execute(f'CREATE DATABASE "{target_db}";')

    # 2. Ensure pgvector extension exists on the newly created database
    target_conn_info = (
        f"host={pg_host} port={pg_port} user={pg_user} password={pg_pass} dbname={target_db}"
    )
    with (
        psycopg.connect(target_conn_info, autocommit=True) as target_conn,
        target_conn.cursor() as cur,
    ):
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 3. Execute pg_restore
    pg_restore_bin = shutil.which("pg_restore")
    if pg_restore_bin is not None:
        cmd = [
            pg_restore_bin,
            "-U",
            pg_user,
            "-h",
            pg_host,
            "-p",
            str(pg_port),
            "-d",
            target_db,
            str(dump_path),
        ]
        env = os.environ.copy()
        if pg_pass:
            env["PGPASSWORD"] = pg_pass
        proc = subprocess.run(cmd, env=env, capture_output=True, check=False)
        # pg_restore exit code 0 is clean success; code 1 is warnings only; >= 2 is fatal error
        if proc.returncode > 1:
            err_msg = proc.stderr.decode("utf-8", errors="replace")
            raise subprocess.CalledProcessError(
                proc.returncode, cmd, output=proc.stdout, stderr=err_msg
            )
    elif _is_docker_postgres_available():
        cmd = [
            "docker",
            "exec",
            "-i",
            "cka-postgres",
            "pg_restore",
            "-U",
            pg_user,
            "-d",
            target_db,
        ]
        with open(dump_path, "rb") as f:
            proc = subprocess.run(
                cmd,
                stdin=f,
                capture_output=True,
                check=False,
            )
            # pg_restore exit code 0 is clean success; code 1 is warnings only; >= 2 is fatal error
            if proc.returncode > 1:
                err_msg = proc.stderr.decode("utf-8", errors="replace")
                raise subprocess.CalledProcessError(
                    proc.returncode, cmd, output=proc.stdout, stderr=err_msg
                )
    else:
        raise RuntimeError(
            "pg_restore is not available on PATH and docker container 'cka-postgres' "
            "is not accessible."
        )


def main() -> None:
    """CLI entrypoint for database restore script."""
    parser = argparse.ArgumentParser(
        description="Restore pg_dump archive into a NEW database (never overwrites)."
    )
    parser.add_argument(
        "--dump-file",
        "-f",
        dest="dump_file",
        required=True,
        help="Path to the pg_dump archive file.",
    )
    parser.add_argument(
        "--target-db",
        "-d",
        dest="target_db",
        required=True,
        help="Name of the NEW database to create and restore into.",
    )
    parser.add_argument(
        "--user",
        "-u",
        dest="user",
        help="PostgreSQL user (default: POSTGRES_USER or cka_admin).",
    )
    parser.add_argument(
        "--host",
        dest="host",
        help="PostgreSQL host (default: POSTGRES_HOST or localhost).",
    )
    parser.add_argument(
        "--port",
        "-p",
        dest="port",
        type=int,
        help="PostgreSQL port (default: POSTGRES_PORT or 5432).",
    )

    args = parser.parse_args()

    try:
        restore_database(
            dump_file=args.dump_file,
            target_db=args.target_db,
            user=args.user,
            host=args.host,
            port=args.port,
        )
        print(f"SUCCESS: Database restored into new database '{args.target_db}'")
    except Exception as e:
        print(f"ERROR: Restore failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
