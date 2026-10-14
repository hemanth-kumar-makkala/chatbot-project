"""Allow re-upload after document retire or version failure.

Revision ID: 0010_allow_reupload_after_retire_or_failure
Revises: 0009_version_file_extension
Create Date: 2026-10-15 00:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_reupload_after_retire"
down_revision: str | None = "0009_version_file_extension"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop unique constraint uq_document_versions_tenant_sha256
    op.drop_constraint(
        "uq_document_versions_tenant_sha256",
        "document_versions",
        type_="unique",
    )

    # 2. Add non-unique index ix_document_versions_tenant_sha256 on (tenant_id, sha256)
    op.create_index(
        "ix_document_versions_tenant_sha256",
        "document_versions",
        ["tenant_id", "sha256"],
        unique=False,
    )


def downgrade() -> None:
    # 1. Check for duplicate (tenant_id, sha256) rows before restoring unique constraint
    bind = op.get_bind()
    dup_row = bind.execute(
        sa.text(
            """
            SELECT tenant_id, sha256, COUNT(*) AS count
            FROM document_versions
            GROUP BY tenant_id, sha256
            HAVING COUNT(*) > 1
            LIMIT 1;
            """
        )
    ).fetchone()

    if dup_row is not None:
        raise RuntimeError(
            "Cannot downgrade migration 0010: duplicate (tenant_id, sha256) rows exist in "
            "document_versions. Resolve duplicate versions before restoring the unique "
            "constraint uq_document_versions_tenant_sha256."
        )

    # 2. Drop non-unique index
    op.drop_index(
        "ix_document_versions_tenant_sha256",
        table_name="document_versions",
    )

    # 3. Restore unique constraint uq_document_versions_tenant_sha256
    op.create_unique_constraint(
        "uq_document_versions_tenant_sha256",
        "document_versions",
        ["tenant_id", "sha256"],
    )
