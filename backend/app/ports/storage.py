"""Port interface for StorageBackend.

Provides tenant-partitioned object storage for original documents, page images,
and short-lived signed URLs for frontend display.
"""

from abc import ABC, abstractmethod

from app.ports.types import StorageKey


class StorageBackend(ABC):
    """Abstract port for file and object storage."""

    @abstractmethod
    async def put(
        self,
        key: StorageKey,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Store binary data under the tenant-scoped storage key."""
        ...

    @abstractmethod
    async def get(
        self,
        key: StorageKey,
    ) -> bytes:
        """Retrieve binary data stored at the tenant-scoped storage key."""
        ...

    @abstractmethod
    async def signed_url(
        self,
        key: StorageKey,
        ttl_seconds: int = 3600,
    ) -> str:
        """Generate a short-lived signed access URL for frontend rendering."""
        ...

    @abstractmethod
    async def delete(
        self,
        key: StorageKey,
    ) -> None:
        """Permanently delete data at the tenant-scoped storage key."""
        ...

    @abstractmethod
    async def exists(
        self,
        key: StorageKey,
    ) -> bool:
        """Check if binary data exists under tenant-scoped storage key."""
        ...
