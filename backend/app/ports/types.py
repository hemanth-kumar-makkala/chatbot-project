"""Shared data contracts and Pydantic schemas for ports and engine boundaries."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


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


class WorkerContext(BaseModel):
    """Internal system/worker context for background ingestion jobs.

    Not a user entity; scoped strictly to tenant_id for pipeline execution (ADR-0007).
    """

    tenant_id: str
    job_id: str


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
    confidence: float | None = None
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


class PageType(StrEnum):
    """Classification of page layout and content structure."""

    DIGITAL_TEXT = "digital_text"
    SCANNED = "scanned"
    IMAGE_HEAVY = "image_heavy"
    BLANK = "blank"


ReviewReason = Literal[
    "low_ocr_confidence",
    "vision_failed",
    "vision_low_confidence",
    "vision_confidence_missing",
    "image_too_large",
    "processing_failed",
    "legacy_unspecified",
]


class PageArtifact(BaseModel):
    """Extracted single page artifact ready for page-first processing."""

    page_number: int
    image_bytes: bytes
    mime_type: str = "image/png"
    page_sha256: str = ""
    page_type: PageType = PageType.DIGITAL_TEXT
    char_count: int = 0
    has_text_layer: bool = False
    image_area_ratio: float = 0.0
    width_pts: float | None = None
    height_pts: float | None = None


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

    tenant_id: UUID
    path: str

    @field_validator("path")
    @classmethod
    def validate_safe_path(cls, v: str) -> str:
        if ".." in v:
            raise ValueError("StorageKey path must not contain '..'")
        if "\\" in v:
            raise ValueError("StorageKey path must not contain backslashes")
        if v.startswith("/"):
            raise ValueError("StorageKey path must not start with leading slash")
        if "//" in v:
            raise ValueError("StorageKey path must not contain double slashes")
        return v

    @property
    def full_key(self) -> str:
        return f"{self.tenant_id}/{self.path}"


class DocumentRecord(BaseModel):
    """Domain record representing an uploaded document entity."""

    id: UUID
    tenant_id: str
    title: str
    active_version_id: UUID | None = None
    created_at: datetime
    is_retired: bool = False


class DocumentVersionRecord(BaseModel):
    """Domain record representing an immutable document version."""

    id: UUID
    tenant_id: str
    document_id: UUID
    sha256: str
    uploaded_by: str
    status: str
    created_at: datetime


class VersionStatusInfo(BaseModel):
    """Derived UI and lifecycle status for a single document version."""

    version_id: str
    ui_status: str
    raw_status: str
    flagged_pages_count: int
    total_pages_count: int
    created_at: datetime


class DocumentStatusInfo(BaseModel):
    """Status summary for a document and its version history within a tenant."""

    document_id: str
    title: str
    active_version_id: str | None = None
    created_at: datetime
    is_retired: bool = False
    versions: list[VersionStatusInfo]


class PageRecord(BaseModel):
    """Domain record representing a single document page evidence row."""

    id: UUID
    tenant_id: str
    document_version_id: UUID
    page_number: int
    page_sha256: str
    page_type: PageType
    image_key: str
    ocr_confidence: float | None = None
    needs_review: bool = False
    review_reason: ReviewReason | None = None
    is_excluded: bool = False


class ReviewReadingInfo(BaseModel):
    """Information about a page reading for administrative review."""

    id: str
    source: str
    text: str
    model_id: str
    model_version: str
    created_at: datetime


class ReviewPageInfo(BaseModel):
    """Summary of a flagged page for administrative review inspection."""

    page_id: str
    page_number: int
    review_reason: str | None = None
    ocr_confidence: float | None = None
    page_type: str
    is_excluded: bool = False
    has_image: bool = True
    readings: list[ReviewReadingInfo] = []


class UploadPayload(BaseModel):
    """Input payload for a document upload."""

    filename: str
    content: bytes
    document_id: UUID | None = None


class ValidatedFile(BaseModel):
    """Validated file metadata and sanitized payload."""

    mime_type: str
    extension: str
    sha256: str
    size_bytes: int
    content: bytes


class UploadResult(BaseModel):
    """Result of processing a document upload."""

    document_id: UUID
    document_version_id: UUID
    sha256: str
    title: str
    is_duplicate: bool
    existing_document_id: UUID | None = None


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
