"""Widen model_version columns to VARCHAR(255) for SHA-256 and long model digests.

Revision ID: 0011_widen_model_version
Revises: 0010_reupload_after_retire
Create Date: 2026-10-19 23:57:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_widen_model_version"
down_revision: str | None = "0010_reupload_after_retire"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "page_readings",
        "model_version",
        existing_type=sa.String(length=50),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "run_steps",
        "model_version",
        existing_type=sa.String(length=50),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "run_steps",
        "model_version",
        existing_type=sa.String(length=255),
        type_=sa.String(length=50),
        existing_nullable=True,
    )
    op.alter_column(
        "page_readings",
        "model_version",
        existing_type=sa.String(length=255),
        type_=sa.String(length=50),
        existing_nullable=False,
    )
