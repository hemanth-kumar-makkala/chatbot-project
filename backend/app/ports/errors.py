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
