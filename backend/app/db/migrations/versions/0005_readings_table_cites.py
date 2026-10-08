"""Add page review columns, page readings unique key, and table citations.

Revision ID: 0005_readings_table_cites
Revises: 0004_page_type_and_status
Create Date: 2026-10-08 10:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_readings_table_cites"
down_revision: str | None = "0004_page_type_and_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add ocr_confidence and needs_review to pages
    op.add_column("pages", sa.Column("ocr_confidence", sa.Float(), nullable=True))
    op.add_column(
        "pages",
        sa.Column(
            "needs_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # 2. Add idempotency unique key on page_readings
    op.create_unique_constraint(
        "uq_page_readings_idempotency",
        "page_readings",
        ["tenant_id", "page_id", "source", "model_id", "model_version"],
    )

    # 3. Add page_id and table_index to tables_data for page-level citations (Invariant 8)
    op.add_column(
        "tables_data",
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "tables_data",
        sa.Column(
            "table_index",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )

    # 4. Composite foreign key tying tables_data to pages within the same tenant & version
    op.create_foreign_key(
        "fk_tables_data_page",
        "tables_data",
        "pages",
        ["tenant_id", "document_version_id", "page_id"],
        ["tenant_id", "document_version_id", "id"],
        ondelete="RESTRICT",
    )

    # 5. Add idempotency constraint with NULLS NOT DISTINCT for spreadsheet re-runs
    op.execute(
        """
        ALTER TABLE tables_data
        ADD CONSTRAINT uq_tables_data_idempotency
        UNIQUE NULLS NOT DISTINCT (
            tenant_id, document_version_id, sheet_name, page_id, table_index, row_index
        );
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tables_data DROP CONSTRAINT IF EXISTS uq_tables_data_idempotency;")
    op.drop_constraint("fk_tables_data_page", "tables_data", type_="foreignkey")
    op.drop_column("tables_data", "table_index")
    op.drop_column("tables_data", "page_id")
    op.drop_constraint("uq_page_readings_idempotency", "page_readings", type_="unique")
    op.drop_column("pages", "needs_review")
    op.drop_column("pages", "ocr_confidence")
