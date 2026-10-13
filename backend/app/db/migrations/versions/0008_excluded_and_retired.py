"""Add pages.is_excluded, documents.is_retired, and ck_pages_excluded_needs_review constraint.

Revision ID: 0008_excluded_and_retired
Revises: 0007_chunks_reading_evidence
Create Date: 2026-10-13 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_excluded_and_retired"
down_revision: str | None = "0007_chunks_reading_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add is_retired flag to documents
    op.add_column(
        "documents",
        sa.Column("is_retired", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    # 2. Add is_excluded flag to pages
    op.add_column(
        "pages",
        sa.Column("is_excluded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    # 3. Add CHECK constraint enforcing pages cannot be simultaneously excluded and needing review
    op.execute(
        """
        ALTER TABLE pages
        ADD CONSTRAINT ck_pages_excluded_needs_review
        CHECK (NOT (is_excluded = true AND needs_review = true));
        """
    )


def downgrade() -> None:
    # 1. Drop check constraint on pages
    op.execute(
        """
        ALTER TABLE pages
        DROP CONSTRAINT IF EXISTS ck_pages_excluded_needs_review;
        """
    )

    # 2. Drop is_excluded from pages
    op.drop_column("pages", "is_excluded")

    # 3. Drop is_retired from documents
    op.drop_column("documents", "is_retired")
