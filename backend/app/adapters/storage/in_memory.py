"""In-memory storage adapter for testing."""

from app.ports.storage import StorageBackend
from app.ports.types import StorageKey


class InMemoryStorageBackend(StorageBackend):
    """In-memory implementation of StorageBackend for fast tests."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def put(
        self,
        key: StorageKey,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        del content_type
        self.files[key.full_key] = data
        return f"memory://{key.full_key}"

    async def get(self, key: StorageKey) -> bytes:
        if key.full_key not in self.files:
            raise FileNotFoundError(f"Key not found: {key.full_key}")
        return self.files[key.full_key]

    async def signed_url(self, key: StorageKey, ttl_seconds: int = 3600) -> str:
        del ttl_seconds
        return f"memory://{key.full_key}"

    async def delete(self, key: StorageKey) -> None:
        self.files.pop(key.full_key, None)

    async def exists(self, key: StorageKey) -> bool:
        return key.full_key in self.files
