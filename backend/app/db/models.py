"""SQLAlchemy 2 declarative models for Phase 1.

Enforces:
- UUID primary keys across all tables.
- Strict tenant partitioning on every tenant table.
- Composite foreign keys (tenant_id, parent_id) -> parent(tenant_id, id)
  preventing cross-tenant referencing even outside RLS.
- Platform admin as a flag on users table; memberships restricted to client_admin and staff.
- Assistants reference prompt registry IDs; run_steps carries telemetry only (zero content).
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType


class PGVector(UserDefinedType):
    """PostgreSQL pgvector vector column type."""

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def get_col_spec(self, **_kw: Any) -> str:
        return f"vector({self.dim})"


class Base(DeclarativeBase):
    """Base declarative class for all database entities."""

    pass


class Tenant(Base):
    """Client tenant isolation boundary."""

    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(Base):
    """System user. Cross-tenant platform admin status is a flag on this table."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Assistant(Base):
    """Per-client assistant configuration referencing versioned prompt registry."""

    __tablename__ = "assistants"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_assistants_tenant_id"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_id: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Membership(Base):
    """User membership within a specific client tenant (client_admin or staff)."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_memberships_tenant_id"),
        UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
        CheckConstraint("role IN ('client_admin', 'staff')", name="ck_memberships_role"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Conversation(Base):
    """Chat conversation scoped to a tenant and assistant."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_conversations_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "assistant_id"],
            ["assistants.tenant_id", "assistants.id"],
            name="fk_conversations_assistant",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    assistant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Message(Base):
    """Individual conversation message with cross-tenant composite FK."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_messages_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_messages_conversation",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RunStep(Base):
    """Step execution telemetry store (run_steps). Zero prompt or message content."""

    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_run_steps_tenant_id"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[str] = mapped_column(String(100), nullable=False)
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    node_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    public_error_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prompt_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    evidence_ids: Mapped[list[UUID] | None] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditEvent(Base):
    """Immutable audit trail of compliance actions. Insert-only for application roles."""

    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_audit_events_tenant_id"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Session(Base):
    """User session tracking opaque token hashes with idle and absolute expiry."""

    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthEvent(Base):
    """Global immutable audit log of login/auth attempts (zero PHI). Insert-only for app role."""

    __tablename__ = "auth_events"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    outcome: Mapped[str] = mapped_column(String(50), nullable=False)  # "success" or "failure"
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    client_ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Phase 2 Ingestion Models
# ---------------------------------------------------------------------------


class Document(Base):
    """Client document record representing an uploaded knowledge asset."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "id", "active_version_id"],
            [
                "document_versions.tenant_id",
                "document_versions.document_id",
                "document_versions.id",
            ],
            name="fk_documents_active_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    active_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DocumentVersion(Base):
    """Immutable version of an ingested document."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_document_versions_tenant_id"),
        UniqueConstraint(
            "tenant_id", "document_id", "id", name="uq_document_versions_tenant_doc_id"
        ),
        UniqueConstraint("tenant_id", "sha256", name="uq_document_versions_tenant_sha256"),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_document_versions_document",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('received', 'processing', 'needs_review', 'ready', 'retired', 'failed')",
            name="ck_document_versions_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Page(Base):
    """Individual page evidence unit within a document version."""

    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pages_tenant_id"),
        UniqueConstraint(
            "tenant_id", "document_version_id", "id", name="uq_pages_tenant_version_page"
        ),
        UniqueConstraint(
            "tenant_id", "document_version_id", "page_number", name="uq_pages_version_page_num"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
            name="fk_pages_document_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "page_type IN ('digital_text', 'scanned', 'image_heavy', 'blank')",
            name="ck_pages_page_type",
        ),
        CheckConstraint(
            "image_key LIKE (tenant_id::text || '/%') "
            "AND image_key NOT LIKE '%..%' "
            "AND position(chr(92) in image_key) = 0 "
            "AND image_key NOT LIKE '%//%' "
            "AND image_key NOT LIKE (tenant_id::text || '//%')",
            name="ck_pages_image_key",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    page_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_type: Mapped[str] = mapped_column(String(50), nullable=False)
    image_key: Mapped[str] = mapped_column(String(500), nullable=False)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_review: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa_text("false"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PageReading(Base):
    """Vision model transcription or OCR extraction of a page."""

    __tablename__ = "page_readings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_page_readings_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "page_id",
            "source",
            "model_id",
            "model_version",
            name="uq_page_readings_idempotency",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "page_id"],
            ["pages.tenant_id", "pages.id"],
            name="fk_page_readings_page",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    page_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Chunk(Base):
    """Searchable text chunk with generated tsv and vector embedding."""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_chunks_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id", "page_id"],
            ["pages.tenant_id", "pages.document_version_id", "pages.id"],
            name="fk_chunks_page",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "ts_config IN ('english', 'spanish', 'simple')", name="ck_chunks_ts_config"
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    page_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    context_prefix: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(50), nullable=False)
    ts_config: Mapped[str] = mapped_column(String(50), nullable=False)
    tsv: Mapped[Any | None] = mapped_column(
        TSVECTOR,
        Computed(
            "CASE "
            "WHEN ts_config = 'english' THEN to_tsvector('english'::regconfig, text) "
            "WHEN ts_config = 'spanish' THEN to_tsvector('spanish'::regconfig, text) "
            "ELSE to_tsvector('simple'::regconfig, text) "
            "END",
            persisted=True,
        ),
        nullable=True,
    )
    embedding: Mapped[Any | None] = mapped_column(PGVector(1024), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa_text("false"), nullable=False
    )
    chunker_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TableData(Base):
    """Tabular spreadsheet row data."""

    __tablename__ = "tables_data"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tables_data_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "document_version_id",
            "sheet_name",
            "page_id",
            "table_index",
            "row_index",
            name="uq_tables_data_idempotency",
            postgresql_nulls_not_distinct=True,
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
            name="fk_tables_data_document_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id", "page_id"],
            ["pages.tenant_id", "pages.document_version_id", "pages.id"],
            name="fk_tables_data_page",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    page_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    table_index: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sa_text("0"), nullable=False
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    row_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Job(Base):
    """Background ingestion job tracking execution for a document version."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_jobs_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
            name="fk_jobs_document_version",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sa_text("0"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class JobStep(Base):
    """Granular, idempotent step execution within an ingestion job."""

    __tablename__ = "job_steps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_job_steps_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["jobs.tenant_id", "jobs.id"],
            name="fk_job_steps_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_version_id"],
            ["document_versions.tenant_id", "document_versions.id"],
            name="fk_job_steps_document_version",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_job_steps_idempotency",
            "tenant_id",
            "document_version_id",
            "page_number",
            "step",
            "processing_version",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step: Mapped[str] = mapped_column(String(50), nullable=False)
    processing_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
