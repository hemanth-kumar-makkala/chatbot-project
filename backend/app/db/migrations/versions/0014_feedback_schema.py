"""Add feedback table with tenant isolation and RLS.

Revision ID: 0014_feedback_schema
Revises: 0013_rate_limit_indexes
Create Date: 2026-11-04 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_feedback_schema"
down_revision: str | None = "0013_rate_limit_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create feedback table
    op.create_table(
        "feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rating IN ('up', 'down')", name="ck_feedback_rating"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "message_id"],
            ["messages.tenant_id", "messages.id"],
            name="fk_feedback_message",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_feedback_tenant_id"),
        sa.UniqueConstraint("tenant_id", "message_id", "user_id", name="uq_feedback_message_user"),
    )

    # 2. Enable and Force Row Level Security (RLS) on feedback table
    op.execute("ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE feedback FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_policy ON feedback
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
        """
    )

    # 3. Grant permissions to app_user (Invariant 2, 3)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON feedback TO app_user;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("feedback")

