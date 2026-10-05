"""Add sessions and auth_events global tables with role permissions.

Revision ID: 0002_sessions_auth_events
Revises: 0001_initial_schema
Create Date: 2026-10-05 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_sessions_auth_events"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create sessions table
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    # 2. Create auth_events table (global, append-only, zero PHI)
    op.create_table(
        "auth_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("outcome", sa.String(50), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("client_ip_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_auth_events_user_id", "auth_events", ["user_id"])
    op.create_index("ix_auth_events_created_at", "auth_events", ["created_at"])

    # 3. Role Grants for app_user
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON sessions TO app_user;")
    # App role may INSERT only on auth_events (never UPDATE or DELETE)
    op.execute("GRANT SELECT, INSERT ON auth_events TO app_user;")

    # 4. Lookup helper function for pre-tenant assistant routing
    op.execute(
        """
        CREATE OR REPLACE FUNCTION lookup_assistant_tenant(p_assistant_id uuid)
        RETURNS uuid
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT tenant_id FROM public.assistants WHERE id = p_assistant_id LIMIT 1;
        $$;
        REVOKE ALL ON FUNCTION lookup_assistant_tenant(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION lookup_assistant_tenant(uuid) TO app_user;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS lookup_assistant_tenant(uuid);")
    op.execute("REVOKE ALL PRIVILEGES ON sessions, auth_events FROM app_user;")
    op.drop_table("auth_events")
    op.drop_table("sessions")
