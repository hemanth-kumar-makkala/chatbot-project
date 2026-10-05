"""Port interface for AuthProvider.

Handles credential verification, session validation, and tenant role assignment.
The model never decides access control (Invariant 3); tenant identity is derived
exclusively from verified session tokens.
"""

from abc import ABC, abstractmethod

from app.ports.types import (
    AuthCredentials,
    AuthToken,
    SessionContext,
    UserRole,
)


class AuthenticationError(Exception):
    """Raised when authentication or session validation fails."""

    pass


class AuthProvider(ABC):
    """Abstract port for identity, authentication, and authorization."""

    @abstractmethod
    async def authenticate(
        self,
        credentials: AuthCredentials,
        client_ip_hash: str | None = None,
    ) -> AuthToken:
        """Authenticate user credentials and issue an auth token."""
        ...

    @abstractmethod
    async def session(
        self,
        token: str,
    ) -> SessionContext:
        """Resolve and validate active session from an authentication token."""
        ...

    @abstractmethod
    async def roles(
        self,
        user_id: str,
        tenant_id: str,
    ) -> list[UserRole]:
        """Fetch granted roles for a user within a specific tenant scope."""
        ...

    @abstractmethod
    async def revoke(
        self,
        token: str,
    ) -> bool:
        """Revoke a session token upon user logout."""
        ...
