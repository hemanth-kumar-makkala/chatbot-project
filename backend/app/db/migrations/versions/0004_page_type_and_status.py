"""Add check constraints on pages.page_type and document_versions.status.

Revision ID: 0004_page_type_and_version_status_checks
Revises: 0003_ingestion_pipeline_schema
Create Date: 2026-10-06 14:30:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_page_type_and_status"
down_revision: str | None = "0003_ingestion_pipeline_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Normalize legacy 'digital' values from Task 2.1 to 'digital_text'
    op.execute("UPDATE pages SET page_type = 'digital_text' WHERE page_type = 'digital'")
    op.create_check_constraint(
        "ck_pages_page_type",
        "pages",
        "page_type IN ('digital_text', 'scanned', 'image_heavy', 'blank')",
    )
    op.create_check_constraint(
        "ck_document_versions_status",
        "document_versions",
        "status IN ('received', 'processing', 'needs_review', 'ready', 'retired', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_document_versions_status", "document_versions", type_="check")
    op.drop_constraint("ck_pages_page_type", "pages", type_="check")
