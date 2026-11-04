"""Add composite indexes on audit_events for question rate-limit queries.

Revision ID: 0013_rate_limit_indexes
Revises: 0012_session_documents
Create Date: 2026-11-04 03:16:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_rate_limit_indexes"
down_revision: str | None = "0012_session_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create composite indexes on audit_events for tenant and user rate-limit queries."""
    op.create_index(
        "ix_audit_events_tenant_event_created",
        "audit_events",
        ["tenant_id", "event_type", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_user_event_created",
        "audit_events",
        ["user_id", "event_type", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop composite rate-limit indexes from audit_events."""
    op.drop_index("ix_audit_events_user_event_created", table_name="audit_events")
    op.drop_index("ix_audit_events_tenant_event_created", table_name="audit_events")
