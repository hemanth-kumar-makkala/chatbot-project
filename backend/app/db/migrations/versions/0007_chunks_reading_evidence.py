"""Add review reason, chunks reading evidence, and composite lineage foreign key.

Pages flagged before 0007 with needs_review = true and NULL review_reason are migrated to
review_reason = 'legacy_unspecified'. The constraint ck_pages_needs_review_reason is added
as a fully valid check constraint. Vision reading does not auto-clear legacy_unspecified flags.

Revision ID: 0007_chunks_reading_evidence
Revises: 0006_procrastinate_schema
Create Date: 2026-10-12 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_chunks_reading_evidence"
down_revision: str | None = "0006_procrastinate_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add review_reason to pages
    op.add_column("pages", sa.Column("review_reason", sa.String(50), nullable=True))

    # 2. Add CHECK constraint allowlist on pages.review_reason
    op.execute(
        """
        ALTER TABLE pages
        ADD CONSTRAINT ck_pages_review_reason
        CHECK (
            review_reason IS NULL OR review_reason IN (
                'low_ocr_confidence',
                'vision_failed',
                'vision_low_confidence',
                'vision_confidence_missing',
                'image_too_large',
                'processing_failed',
                'legacy_unspecified'
            )
        );
        """
    )

    # 3. Backfill existing flagged rows before enforcing non-null check constraint
    op.execute(
        """
        UPDATE pages
        SET review_reason = 'legacy_unspecified'
        WHERE needs_review = true AND review_reason IS NULL;
        """
    )

    # 4. Add fully valid CHECK requiring review_reason when needs_review is true
    op.execute(
        """
        ALTER TABLE pages
        ADD CONSTRAINT ck_pages_needs_review_reason
        CHECK (needs_review = false OR review_reason IS NOT NULL);
        """
    )

    # 5. Add page_reading_id and evidence_type to chunks
    op.add_column(
        "chunks",
        sa.Column("page_reading_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "chunks",
        sa.Column(
            "evidence_type",
            sa.String(50),
            server_default=sa.text("'text'"),
            nullable=False,
        ),
    )

    # 6. Composite foreign key tying chunks to page_readings within same tenant (Invariant 5)
    op.create_foreign_key(
        "fk_chunks_page_reading",
        "chunks",
        "page_readings",
        ["tenant_id", "page_reading_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    # 7. Add CHECK constraint allowlist on chunks.evidence_type
    op.execute(
        """
        ALTER TABLE chunks
        ADD CONSTRAINT ck_chunks_evidence_type
        CHECK (evidence_type IN ('text', 'visual', 'table'));
        """
    )

    # 8. Add CHECK constraint: visual chunks must link to a page_reading_id
    op.execute(
        """
        ALTER TABLE chunks
        ADD CONSTRAINT ck_chunks_visual_requires_reading
        CHECK (evidence_type != 'visual' OR page_reading_id IS NOT NULL);
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE chunks DROP CONSTRAINT IF EXISTS ck_chunks_visual_requires_reading;")
    op.execute("ALTER TABLE chunks DROP CONSTRAINT IF EXISTS ck_chunks_evidence_type;")
    op.drop_constraint("fk_chunks_page_reading", "chunks", type_="foreignkey")
    op.drop_column("chunks", "evidence_type")
    op.drop_column("chunks", "page_reading_id")
    op.execute("ALTER TABLE pages DROP CONSTRAINT IF EXISTS ck_pages_needs_review_reason;")
    op.execute("ALTER TABLE pages DROP CONSTRAINT IF EXISTS ck_pages_review_reason;")
    op.execute(
        """
        UPDATE pages
        SET review_reason = NULL
        WHERE review_reason = 'legacy_unspecified';
        """
    )
    op.drop_column("pages", "review_reason")
