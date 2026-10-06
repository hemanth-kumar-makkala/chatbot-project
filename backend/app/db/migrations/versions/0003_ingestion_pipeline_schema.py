"""Ingestion schema, composite foreign keys, RLS policies, and role grants.

Revision ID: 0003_ingestion_pipeline_schema
Revises: 0002_sessions_auth_events
Create Date: 2026-10-06 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_ingestion_pipeline_schema"
down_revision: str | None = "0002_sessions_auth_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INGESTION_TABLES: list[str] = [
    "documents",
    "document_versions",
    "pages",
    "page_readings",
    "chunks",
    "tables_data",
    "jobs",
    "job_steps",
]

EVIDENCE_TABLES: list[str] = [
    "documents",
    "document_versions",
    "pages",
    "page_readings",
    "chunks",
    "tables_data",
]


def upgrade() -> None:
    # 0. pgvector Extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 1. documents table
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id"),
    )

    # 2. document_versions table
    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_document_versions_document",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_document_versions_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "document_id", "id", name="uq_document_versions_tenant_doc_id"
        ),
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_document_versions_tenant_sha256"),
    )

    # Circular composite FK: documents.active_version_id -> document_versions
    op.create_foreign_key(
        "fk_documents_active_version",
        "documents",
        "document_versions",
        ["tenant_id", "id", "active_version_id"],
        ["tenant_id", "document_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )

    # 3. pages table
    op.create_table(
        "pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("page_sha256", sa.String(length=64), nullable=False),
        sa.Column("page_type", sa.String(length=50), nullable=False),
        sa.Column("image_key", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
            name="fk_pages_document_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pages_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "document_version_id", "id", name="uq_pages_tenant_version_page"
        ),
        sa.UniqueConstraint(
            "tenant_id", "document_version_id", "page_number", name="uq_pages_version_page_num"
        ),
        sa.CheckConstraint(
            "image_key LIKE (tenant_id::text || '/%') "
            "AND image_key NOT LIKE '%..%' "
            "AND position(chr(92) in image_key) = 0 "
            "AND image_key NOT LIKE '%//%' "
            "AND image_key NOT LIKE (tenant_id::text || '//%')",
            name="ck_pages_image_key",
        ),
    )

    # 4. page_readings table
    op.create_table(
        "page_readings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "page_id"],
            ["pages.tenant_id", "pages.id"],
            name="fk_page_readings_page",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_page_readings_tenant_id"),
    )

    # 5. chunks table (vector + generated tsv)
    op.execute(
        """
        CREATE TABLE chunks (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            page_id UUID NOT NULL,
            document_version_id UUID NOT NULL,
            text TEXT NOT NULL,
            context_prefix TEXT,
            language VARCHAR(50) NOT NULL,
            ts_config VARCHAR(50) NOT NULL,
            tsv TSVECTOR GENERATED ALWAYS AS (
                CASE
                    WHEN ts_config = 'english' THEN to_tsvector('english'::regconfig, text)
                    WHEN ts_config = 'spanish' THEN to_tsvector('spanish'::regconfig, text)
                    ELSE to_tsvector('simple'::regconfig, text)
                END
            ) STORED,
            embedding vector(1024),
            is_active BOOLEAN NOT NULL DEFAULT false,
            chunker_version VARCHAR(50),
            embedding_model VARCHAR(100),
            embedding_version VARCHAR(50),
            parser_version VARCHAR(50),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            CONSTRAINT uq_chunks_tenant_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_chunks_page FOREIGN KEY (tenant_id, document_version_id, page_id)
                REFERENCES pages (tenant_id, document_version_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_chunks_ts_config CHECK (ts_config IN ('english', 'spanish', 'simple'))
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);"
    )
    op.execute("CREATE INDEX ix_chunks_tsv_gin ON chunks USING gin (tsv);")

    # 6. tables_data table
    op.create_table(
        "tables_data",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("row_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
            name="fk_tables_data_document_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tables_data_tenant_id"),
    )

    # 7. jobs table
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
            name="fk_jobs_document_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_jobs_tenant_id"),
    )

    # 8. job_steps table (idempotency key with NULLS NOT DISTINCT)
    op.create_table(
        "job_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("step", sa.String(length=50), nullable=False),
        sa.Column("processing_version", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["jobs.tenant_id", "jobs.id"],
            name="fk_job_steps_job",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
            name="fk_job_steps_document_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_job_steps_tenant_id"),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_job_steps_idempotency
        ON job_steps (tenant_id, document_version_id, page_number, step, processing_version)
        NULLS NOT DISTINCT;
        """
    )

    # 9. Row Level Security Policies
    for table in INGESTION_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation_policy ON {table}
                AS PERMISSIVE
                FOR ALL
                TO PUBLIC
                USING (
                    tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                )
                WITH CHECK (
                    tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                );
            """
        )

    # 10. Role Grants
    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON
            {", ".join(INGESTION_TABLES)}
        FROM PUBLIC;
        """
    )
    op.execute(
        f"""
        GRANT SELECT, INSERT, UPDATE ON
            {", ".join(INGESTION_TABLES)}
        TO app_user;
        """
    )
    op.execute(
        f"""
        GRANT SELECT ON
            {", ".join(EVIDENCE_TABLES)}
        TO retriever_user;
        """
    )


def downgrade() -> None:
    # Revoke role grants
    op.execute(
        f"""
        REVOKE ALL PRIVILEGES ON
            {", ".join(INGESTION_TABLES)}
        FROM app_user, retriever_user;
        """
    )

    # Drop policies
    for table in INGESTION_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation_policy ON {table};")

    # Drop circular FK on documents
    op.drop_constraint("fk_documents_active_version", "documents", type_="foreignkey")

    # Drop tables in dependency order
    op.drop_table("job_steps")
    op.drop_table("jobs")
    op.drop_table("tables_data")
    op.drop_table("chunks")
    op.drop_table("page_readings")
    op.drop_table("pages")
    op.drop_table("document_versions")
    op.drop_table("documents")

    # Drop pgvector extension
    op.execute("DROP EXTENSION IF EXISTS vector;")
