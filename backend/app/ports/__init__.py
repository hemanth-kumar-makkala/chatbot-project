"""Pure abstract port interfaces (Layer 5).

Contracts between the fixed engine (Layer 4) and swappable adapters (Layer 6).
All I/O operations are async coroutines.
Contains zero vendor dependencies and no concrete logic.
"""

from app.ports.auth import AuthProvider
from app.ports.document_parser import DocumentParser
from app.ports.embedding_provider import EmbeddingProvider
from app.ports.errors import (
    AuthenticationError,
    EmptyOutput,
    MalformedOutput,
    ParseError,
    PortError,
    ProviderTimeout,
    ProviderUnavailable,
    QueueError,
    StorageError,
)
from app.ports.job_queue import JobQueue
from app.ports.model_provider import ModelProvider
from app.ports.reranker import Reranker
from app.ports.retriever import Retriever
from app.ports.storage import StorageBackend
from app.ports.tracer import Tracer
from app.ports.types import (
    AuthCredentials,
    AuthToken,
    ChatMessage,
    EvidenceId,
    EvidenceType,
    ExtractedFields,
    ExtractedFieldValue,
    JobStatus,
    ModelResponse,
    ModelRole,
    PageArtifact,
    PageReading,
    ParsedPageContent,
    ReadState,
    RerankCandidate,
    RerankResult,
    RetrievalFilters,
    SessionContext,
    StepEndPayload,
    StepErrorPayload,
    StepStartPayload,
    StorageKey,
    TaskPayload,
    TenantContext,
    UserRole,
)

__all__ = [
    # 9 Abstract Ports
    "AuthProvider",
    "DocumentParser",
    "EmbeddingProvider",
    "JobQueue",
    "ModelProvider",
    "Reranker",
    "Retriever",
    "StorageBackend",
    "Tracer",
    # Shared Errors
    "PortError",
    "ProviderTimeout",
    "ProviderUnavailable",
    "MalformedOutput",
    "EmptyOutput",
    "AuthenticationError",
    "StorageError",
    "ParseError",
    "QueueError",
    # Core Port Types
    "UserRole",
    "TenantContext",
    "EvidenceType",
    "EvidenceId",
    "ModelRole",
    "ChatMessage",
    "ModelResponse",
    "PageReading",
    "ReadState",
    "ExtractedFieldValue",
    "ExtractedFields",
    "PageArtifact",
    "ParsedPageContent",
    "RerankCandidate",
    "RerankResult",
    "RetrievalFilters",
    "StorageKey",
    "JobStatus",
    "TaskPayload",
    "AuthCredentials",
    "AuthToken",
    "SessionContext",
    "StepStartPayload",
    "StepEndPayload",
    "StepErrorPayload",
]
