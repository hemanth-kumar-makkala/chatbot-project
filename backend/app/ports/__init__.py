"""Pure abstract port interfaces (Layer 5).

Contracts between the fixed engine (Layer 4) and swappable adapters (Layer 6).
All I/O operations are async coroutines.
Contains zero vendor dependencies and no concrete logic.
"""

from app.ports.audit import AuditLogger
from app.ports.auth import AuthProvider
from app.ports.document_parser import DocumentParser
from app.ports.document_repository import DocumentRepository
from app.ports.document_splitter import DocumentSplitter
from app.ports.embedding_provider import EmbeddingProvider
from app.ports.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    CorruptDocumentError,
    DecompressionBombError,
    DocumentNotFoundError,
    DuplicateVersionError,
    EmptyOutput,
    EncryptedDocumentError,
    FileValidationError,
    MalformedOutput,
    MalwareDetectedError,
    PageLimitExceededError,
    ParseError,
    PortError,
    ProviderTimeout,
    ProviderUnavailable,
    QueueError,
    StorageError,
    TransientIngestionError,
)
from app.ports.job_queue import JobQueue
from app.ports.malware_scanner import MalwareScanner
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
    PageType,
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
    # Abstract Ports
    "AuditLogger",
    "AuthProvider",
    "DocumentParser",
    "DocumentRepository",
    "DocumentSplitter",
    "EmbeddingProvider",
    "JobQueue",
    "MalwareScanner",
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
    "AuthorizationError",
    "ConfigurationError",
    "CorruptDocumentError",
    "DecompressionBombError",
    "DocumentNotFoundError",
    "DuplicateVersionError",
    "EncryptedDocumentError",
    "FileValidationError",
    "MalwareDetectedError",
    "PageLimitExceededError",
    "StorageError",
    "ParseError",
    "QueueError",
    "TransientIngestionError",
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
    "PageType",
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
