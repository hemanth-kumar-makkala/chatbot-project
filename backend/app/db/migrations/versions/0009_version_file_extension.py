"""Add document_versions.file_extension and ck_document_versions_file_extension constraint.

Revision ID: 0009_version_file_extension
Revises: 0008_excluded_and_retired
Create Date: 2026-10-14 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_version_file_extension"
down_revision: str | None = "0008_excluded_and_retired"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add file_extension column to document_versions
    op.add_column(
        "document_versions",
        sa.Column("file_extension", sa.String(length=20), nullable=True),
    )

    # 2. Add CHECK constraint enforcing NULL or accepted upload validation extensions
    op.execute(
        """
        ALTER TABLE document_versions
        ADD CONSTRAINT ck_document_versions_file_extension
        CHECK (
            file_extension IS NULL
            OR file_extension IN ('pdf', 'png', 'jpeg', 'tiff', 'xlsx', 'csv', 'txt')
        );
        """
    )


def downgrade() -> None:
    # 1. Drop check constraint on document_versions
    op.execute(
        """
        ALTER TABLE document_versions
        DROP CONSTRAINT IF EXISTS ck_document_versions_file_extension;
        """
    )

    # 2. Drop file_extension column from document_versions
    op.drop_column("document_versions", "file_extension")
