"""Database backup script executing pg_dump locally or via Docker.

Enforces:
- pg_dump execution through local PATH if available, falling back to cka-postgres Docker container.
- Required output folder setting (no default).
- Timestamped output filename.
- Non-destructive execution.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.db.env_utils import load_dotenv

load_dotenv()


def is_pg_dump_available() -> bool:
    """Return True if pg_dump is available on PATH or via cka-postgres Docker container."""
    if shutil.which("pg_dump") is not None:
        return True
    return _is_docker_postgres_available()


def _is_docker_postgres_available() -> bool:
    """Check if docker is available and cka-postgres container is running."""
    if shutil.which("docker") is None:
        return False
    try:
        res = subprocess.run(
            ["docker", "exec", "cka-postgres", "pg_dump", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


def backup_database(
    db_name: str | None = None,
    output_dir: Path | str | None = None,
    user: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> Path:
    """Run pg_dump against the specified database and return the timestamped dump file path.

    Raises:
        ValueError: If output_dir is omitted and no BACKUP_OUTPUT_DIR environment variable is set.
        RuntimeError: If neither pg_dump on PATH nor cka-postgres container is available.
        subprocess.CalledProcessError: If pg_dump execution fails.
    """
    target_db = db_name or os.getenv("POSTGRES_DB") or "knowledge_assistant"
    pg_user = user or os.getenv("POSTGRES_USER") or "cka_admin"
    pg_host = host or os.getenv("POSTGRES_HOST") or "localhost"
    pg_port = port or int(os.getenv("POSTGRES_PORT") or "5432")

    # Required setting: no default
    resolved_output_dir = output_dir or os.getenv("BACKUP_OUTPUT_DIR")
    if not resolved_output_dir or str(resolved_output_dir).strip() == "":
        raise ValueError(
            "BACKUP_OUTPUT_DIR is required and has no default. "
            "Please provide --output-dir or set BACKUP_OUTPUT_DIR."
        )

    out_path = Path(resolved_output_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dump_filename = f"backup_{target_db}_{timestamp}.dump"
    dump_file = out_path / dump_filename

    # Execute pg_dump
    pg_dump_bin = shutil.which("pg_dump")
    if pg_dump_bin is not None:
        cmd = [
            pg_dump_bin,
            "-U",
            pg_user,
            "-h",
            pg_host,
            "-p",
            str(pg_port),
            "-Fc",
            "-f",
            str(dump_file),
            target_db,
        ]
        env = os.environ.copy()
        if "POSTGRES_PASSWORD" in os.environ:
            env["PGPASSWORD"] = os.environ["POSTGRES_PASSWORD"]
        subprocess.run(cmd, check=True, env=env)
    elif _is_docker_postgres_available():
        cmd = [
            "docker",
            "exec",
            "cka-postgres",
            "pg_dump",
            "-U",
            pg_user,
            "-Fc",
            target_db,
        ]
        with open(dump_file, "wb") as f:
            proc = subprocess.run(
                cmd,
                stdout=f,
                stderr=subprocess.PIPE,
                check=False,
            )
            if proc.returncode != 0:
                err_msg = proc.stderr.decode("utf-8", errors="replace")
                dump_file.unlink(missing_ok=True)
                raise subprocess.CalledProcessError(
                    proc.returncode,
                    cmd,
                    output=None,
                    stderr=err_msg,
                )
    else:
        raise RuntimeError(
            "pg_dump is not available on PATH and docker container 'cka-postgres' is not accessible."
        )

    return dump_file


def main() -> None:
    """CLI entrypoint for database backup script."""
    parser = argparse.ArgumentParser(description="Create timestamped PostgreSQL pg_dump backup.")
    parser.add_argument(
        "--output-dir",
        "-o",
        dest="output_dir",
        help="Directory to save timestamped dump file (required, no default).",
    )
    parser.add_argument(
        "--db",
        "-d",
        dest="db",
        help="Database name to back up (default: POSTGRES_DB or knowledge_assistant).",
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
        dump_path = backup_database(
            db_name=args.db,
            output_dir=args.output_dir,
            user=args.user,
            host=args.host,
            port=args.port,
        )
        print(f"SUCCESS: Database backup created at {dump_path}")
    except Exception as e:
        print(f"ERROR: Backup failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

