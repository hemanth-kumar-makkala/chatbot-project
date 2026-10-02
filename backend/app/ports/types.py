"""Shared data contracts and Pydantic schemas for ports and engine boundaries."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class UserRole(StrEnum):
    """User access roles within a tenant."""

    PLATFORM_ADMIN = "platform_admin"
    CLIENT_ADMIN = "client_admin"
    STAFF = "staff"


class TenantContext(BaseModel):
    """Authenticated request context securely derived from user session."""

    tenant_id: str
    user_id: str
    role: UserRole
    session_id: str


class EvidenceType(StrEnum):
    """Classification of grounded evidence source."""

    TEXT = "text"
    VISUAL = "visual"
    TABLE = "table"


class EvidenceId(BaseModel):
    """Typed identifier pinning an answer claim to exact document version and page."""

    chunk_id: str
    page_id: str
    document_version_id: str
    evidence_type: EvidenceType


class ModelRole(StrEnum):
    """Functional roles mapped to model providers in runtime profiles."""

    ROUTER = "router"
    ANSWER = "answer"
    VISION = "vision"
    EXTRACT = "extract"
    JUDGE = "judge"


class ChatMessage(BaseModel):
    """Representation of an LLM prompt message."""

    role: str
    content: str


class ModelResponse(BaseModel):
    """Standardized completion payload across all model adapters."""

    content: str
    model_id: str
    model_version: str
    structured_output: dict[str, Any] | None = None
    usage_tokens: int | None = None


class PageReading(BaseModel):
    """Multimodal reading from question-time or upload-time vision read."""

    reading: str
    confidence: float
    model_id: str
    model_version: str
    normalized_values: dict[str, Any] = Field(default_factory=dict)


class ReadState(StrEnum):
    """Explicit read state reported by a single extraction or vision read."""

    PRESENT = "present"
    UNREADABLE = "unreadable"
    NOT_PRESENT = "not_present"


class ExtractedFieldValue(BaseModel):
    """Individual extracted field value with explicit single-read state."""

    field_name: str
    value: Any | None = None
    state: ReadState = ReadState.PRESENT
    confidence: float | None = None


class ExtractedFields(BaseModel):
    """Collection of extracted fields from an uploaded document."""

    fields: dict[str, ExtractedFieldValue]
    model_id: str
    model_version: str
    raw_text: str | None = None


class PageArtifact(BaseModel):
    """Extracted single page artifact ready for page-first processing."""

    page_number: int
    image_bytes: bytes
    mime_type: str = "image/png"


class TableData(BaseModel):
    """Structured table extracted from document page."""

    headers: list[str]
    rows: list[list[str]]


class ParsedPageContent(BaseModel):
    """Extracted content from an individual document page."""

    page_number: int
    text: str
    tables: list[TableData] = Field(default_factory=list)
    confidence: float = 1.0


class RerankCandidate(BaseModel):
    """Search candidate item passed into reranker."""

    candidate_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RerankResult(BaseModel):
    """Candidate scored and ranked by reranker."""

    candidate_id: str
    score: float
    rank: int


class RetrievalFilters(BaseModel):
    """Optional search filters applied within tenant partition."""

    document_ids: list[str] | None = None
    evidence_types: list[EvidenceType] | None = None


class StorageKey(BaseModel):
    """Tenant-scoped object storage key."""

    tenant_id: str
    path: str


class JobStatus(StrEnum):
    """Status of asynchronous background jobs."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskPayload(BaseModel):
    """Task specification enqueued into the job queue."""

    task_name: str
    tenant_id: str
    payload: dict[str, Any]


class AuthCredentials(BaseModel):
    """User login credentials."""

    username: str
    password: str


class AuthToken(BaseModel):
    """Issued authentication token."""

    token: str
    expires_in: int


class SessionContext(BaseModel):
    """Session state retrieved from auth token."""

    user_id: str
    tenant_id: str
    roles: list[UserRole]
    session_id: str


class StepStartPayload(BaseModel):
    """Telemetry payload recorded when an execution step begins."""

    request_id: str
    tenant_id: str
    step_name: str
    node_number: int | None = None


class StepEndPayload(BaseModel):
    """Telemetry payload recorded when an execution step completes."""

    request_id: str
    step_name: str
    duration_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    item_count: int | None = None


class StepErrorPayload(BaseModel):
    """Telemetry payload recorded when an execution step fails."""

    request_id: str
    step_name: str
    duration_ms: float
    error_code: str
    error_type: str
