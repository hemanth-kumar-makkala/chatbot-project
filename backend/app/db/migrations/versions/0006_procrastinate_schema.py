"""Create procrastinate schema, apply queue tables, and configure role grants.

Revision ID: 0006_procrastinate_schema
Revises: 0005_readings_table_cites
Create Date: 2026-10-08 14:00:00
"""

from collections.abc import Sequence

from alembic import op
from procrastinate.schema import SchemaManager

revision: str = "0006_procrastinate_schema"
down_revision: str | None = "0005_readings_table_cites"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create isolated schema
    op.execute("CREATE SCHEMA IF NOT EXISTS procrastinate;")

    # 2. Set search path and apply Procrastinate queue schema
    schema_sql = SchemaManager(connector=None).get_schema()  # type: ignore[arg-type]
    conn = op.get_bind()
    conn.exec_driver_sql("SET search_path = procrastinate, public;")
    conn.exec_driver_sql(schema_sql.replace("%", "%%"))
    conn.exec_driver_sql("SET search_path = public;")

    # 3. Role grants for worker operations (least privilege: no CREATE on schema, no TRUNCATE)
    op.execute("GRANT USAGE ON SCHEMA procrastinate TO app_user;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA procrastinate TO app_user;"
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA procrastinate TO app_user;")
    op.execute("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA procrastinate TO app_user;")

    # 4. Default privileges for any future objects created in procrastinate
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA procrastinate "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA procrastinate "
        "GRANT USAGE, SELECT ON SEQUENCES TO app_user;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA procrastinate GRANT EXECUTE ON FUNCTIONS TO app_user;"
    )

    # 5. Strictly revoke access from read-only retriever role
    op.execute("REVOKE ALL ON SCHEMA procrastinate FROM retriever_user;")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS procrastinate CASCADE;")
