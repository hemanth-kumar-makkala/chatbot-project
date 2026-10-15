"""Shared domain exceptions for port adapters.

Every adapter must raise exceptions inheriting from PortError so that the engine's
fallback rules and recovery mechanisms work identically regardless of underlying vendor.
"""

from typing import Any


class PortError(Exception):
    """Base exception for all port and adapter errors."""

    def __init__(
        self,
        message: str,
        error_code: str = "INTERNAL_PORT_ERROR",
        status_code: int = 500,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}


class ProviderTimeout(PortError):
    """Raised when an external model or service times out."""

    def __init__(
        self,
        message: str = "Provider timed out",
        error_code: str = "PROVIDER_TIMEOUT",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=504,
            retryable=True,
            details=details,
        )


class ProviderUnavailable(PortError):
    """Raised when an external service is offline or unreachable."""

    def __init__(
        self,
        message: str = "Provider service unavailable",
        error_code: str = "PROVIDER_UNAVAILABLE",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=503,
            retryable=True,
            details=details,
        )


class MalformedOutput(PortError):
    """Raised when model or provider returns unparseable or schema-violating output."""

    def __init__(
        self,
        message: str = "Malformed provider output",
        error_code: str = "MALFORMED_OUTPUT",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=502,
            retryable=False,
            details=details,
        )


class EmptyOutput(PortError):
    """Raised when a model returns an empty completion."""

    def __init__(
        self,
        message: str = "Provider returned empty output",
        error_code: str = "EMPTY_OUTPUT",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=502,
            retryable=False,
            details=details,
        )


class AuthenticationError(PortError):
    """Raised when credentials or session validation fails."""

    def __init__(
        self,
        message: str = "Authentication failed",
        error_code: str = "AUTHENTICATION_FAILED",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=401,
            retryable=False,
            details=details,
        )


class StorageError(PortError):
    """Raised when a storage backend read/write fails."""

    def __init__(
        self,
        message: str = "Storage operation failed",
        error_code: str = "STORAGE_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=500,
            retryable=False,
            details=details,
        )


class ParseError(PortError):
    """Raised when document splitting or content extraction fails."""

    def __init__(
        self,
        message: str = "Document parsing failed",
        error_code: str = "PARSE_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=422,
            retryable=False,
            details=details,
        )


class QueueError(PortError):
    """Raised when task enqueueing or polling fails."""

    def __init__(
        self,
        message: str = "Job queue operation failed",
        error_code: str = "QUEUE_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=503,
            retryable=True,
            details=details,
        )


class TransientIngestionError(PortError):
    """Raised at job boundary when a database or network error is transient and retryable."""

    def __init__(
        self,
        message: str = "Transient database or network error",
        error_code: str = "TRANSIENT_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=503,
            retryable=True,
            details=details,
        )


class FileValidationError(PortError):
    """Raised when file format, header, size, or encoding validation fails."""

    def __init__(
        self,
        message: str = "File validation failed",
        error_code: str = "FILE_VALIDATION_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=400,
            retryable=False,
            details=details,
        )


class MalwareDetectedError(PortError):
    """Raised when malware or unsafe signature is detected in an uploaded payload."""

    def __init__(
        self,
        message: str = "Malware detected in payload",
        error_code: str = "MALWARE_DETECTED",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=400,
            retryable=False,
            details=details,
        )


class AuthorizationError(PortError):
    """Raised when an authenticated user lacks permission for an operation."""

    def __init__(
        self,
        message: str = "Permission denied",
        error_code: str = "AUTHORIZATION_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=403,
            retryable=False,
            details=details,
        )


class ConfigurationError(PortError):
    """Raised when system or profile configuration violates architectural rules."""

    def __init__(
        self,
        message: str = "Configuration error",
        error_code: str = "CONFIGURATION_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=500,
            retryable=False,
            details=details,
        )


class DuplicateVersionError(PortError):
    """Raised when concurrent upload attempts to create a duplicate version."""

    def __init__(
        self,
        existing_version: Any,
        message: str = "Duplicate document version already exists",
        error_code: str = "DUPLICATE_VERSION",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=409,
            retryable=False,
            details=details,
        )
        self.existing_version = existing_version


class DocumentNotFoundError(PortError):
    """Raised when an operation targets a document_id that does not exist within the tenant."""

    def __init__(
        self,
        message: str = "Document not found",
        error_code: str = "DOCUMENT_NOT_FOUND",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=404,
            retryable=False,
            details=details,
        )


class DocumentVersionNotFoundError(PortError):
    """Raised when an operation targets a document_version_id that does not exist in tenant."""

    def __init__(
        self,
        message: str = "Document version not found",
        error_code: str = "DOCUMENT_VERSION_NOT_FOUND",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=404,
            retryable=False,
            details=details,
        )


class DocumentRetiredError(PortError):
    """Raised when an operation targets or attempts to activate a retired document."""

    def __init__(
        self,
        message: str = "Document is retired",
        error_code: str = "DOCUMENT_RETIRED",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=400,
            retryable=False,
            details=details,
        )


class EncryptedDocumentError(PortError):
    """Raised when an encrypted/password-protected document cannot be opened."""

    def __init__(
        self,
        message: str = "Document is password protected or encrypted",
        error_code: str = "ERR_PDF_ENCRYPTED",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=422,
            retryable=False,
            details=details,
        )


class CorruptDocumentError(PortError):
    """Raised when a document stream is corrupt or unparseable."""

    def __init__(
        self,
        message: str = "Document stream is corrupt or cannot be parsed",
        error_code: str = "ERR_PDF_CORRUPT",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=422,
            retryable=False,
            details=details,
        )


class DecompressionBombError(PortError):
    """Raised when an image or page exceeds maximum allowable pixel dimensions."""

    def __init__(
        self,
        message: str = "Image or document page exceeds safety pixel limit",
        error_code: str = "ERR_IMAGE_BOMB",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=422,
            retryable=False,
            details=details,
        )


class PageLimitExceededError(PortError):
    """Raised when a document exceeds maximum allowed page count."""

    def __init__(
        self,
        message: str = "Document exceeds maximum allowed page count",
        error_code: str = "ERR_PAGE_LIMIT_EXCEEDED",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=error_code,
            status_code=422,
            retryable=False,
            details=details,
        )


class ImageProcessingError(PortError):
    """Raised when an image cannot be processed or downscaled under size constraints."""

    def __init__(
        self,
        message: str = "Image processing failed or exceeded size limits",
        code: str = "IMAGE_PROCESSING_FAILED",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=code,
            status_code=422,
            retryable=False,
            details=details,
        )
        self.code = code


class NoUsableReadingError(PortError):
    """Raised when accepting a flagged page that has neither a vision reading nor an OCR reading."""

    def __init__(
        self,
        message: str = "No usable reading found for page",
        code: str = "NO_USABLE_READING",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=code,
            status_code=422,
            retryable=False,
            details=details,
        )
        self.code = code


class InvalidPageStateError(PortError):
    """Raised when an accept or exclude review action is requested on an invalid page state."""

    def __init__(
        self,
        message: str = "Page is not in a valid state for requested action",
        code: str = "INVALID_PAGE_STATE",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=code,
            status_code=409,
            retryable=False,
            details=details,
        )
        self.code = code


class InvalidVersionStateError(PortError):
    """Raised when promote is attempted on a version that is not in needs_review status."""

    def __init__(
        self,
        message: str = "Document version is not in a valid state for promotion",
        code: str = "INVALID_VERSION_STATE",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=code,
            status_code=409,
            retryable=False,
            details=details,
        )
        self.code = code


class RetryNotUsefulError(PortError):
    """Raised when retrying a page would not produce a different outcome."""

    def __init__(
        self,
        message: str = "Retry is not useful: reading already exists with current model and version",
        code: str = "RETRY_NOT_USEFUL",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=code,
            status_code=409,
            retryable=False,
            details=details,
        )
        self.code = code


class PageJobConflictError(PortError):
    """Raised when a background job for a page is already queued or running."""

    def __init__(
        self,
        message: str = "A background processing job for this page is already queued or running",
        code: str = "PAGE_JOB_ALREADY_ACTIVE",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=code,
            status_code=409,
            retryable=False,
            details=details,
        )
        self.code = code


class MalformedTaskArgsError(PortError):
    """Raised when background job task arguments are missing or malformed."""

    def __init__(
        self,
        message: str = "Malformed task arguments",
        code: str = "MALFORMED_TASK_ARGS",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=code,
            status_code=400,
            retryable=False,
            details=details,
        )
        self.code = code


class InvalidEvidenceTypeError(PortError):
    """Raised when an unrecognized evidence type is encountered during chunking."""

    def __init__(
        self,
        message: str = "Unknown evidence type encountered during chunking",
        code: str = "INVALID_EVIDENCE_TYPE",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=code,
            status_code=400,
            retryable=False,
            details=details,
        )
        self.code = code


class EmbeddingCountMismatchError(PortError):
    """Raised when the embedding provider returns a different count of vectors than chunks."""

    def __init__(
        self,
        message: str = "Embedding provider returned count mismatching chunk count",
        code: str = "EMBEDDING_COUNT_MISMATCH",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code=code,
            status_code=500,
            retryable=False,
            details=details,
        )
        self.code = code
