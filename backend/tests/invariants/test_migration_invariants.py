"""Invariant tests for database migrations.

Enforces:
- Alembic version table constraint: alembic_version.version_num is VARCHAR(32).
  Every migration revision ID must be <= 32 characters to prevent
  DataError / StringDataRightTruncation.
"""

from __future__ import annotations

import ast
from pathlib import Path


def test_all_migration_revision_ids_within_32_characters() -> None:
    """Every migration script in versions/ must define revision with length <= 32."""
    versions_dir = (
        Path(__file__).resolve().parent.parent.parent / "app" / "db" / "migrations" / "versions"
    )
    assert versions_dir.exists(), f"Versions directory not found: {versions_dir}"

    migration_files = sorted(versions_dir.glob("*.py"))
    assert len(migration_files) > 0, "No migration files found to test"

    violations: list[str] = []

    for file_path in migration_files:
        if file_path.name.startswith("__"):
            continue

        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        revision_value: str | None = None

        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "revision"
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str)
                    ):
                        revision_value = stmt.value.value
            elif (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and stmt.target.id == "revision"
                and stmt.value
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                revision_value = stmt.value.value

        assert revision_value is not None, (
            f"Migration {file_path.name} does not define a 'revision' string"
        )

        if len(revision_value) > 32:
            violations.append(
                f"{file_path.name}: revision '{revision_value}' length is "
                f"{len(revision_value)} (> 32 char limit of alembic_version.version_num)"
            )

    assert not violations, "Found migration revision IDs exceeding 32 characters:\n" + "\n".join(
        violations
    )
