"""Local filesystem storage adapter implementing StorageBackend port."""

from pathlib import Path

from app.ports.storage import StorageBackend
from app.ports.types import StorageKey


class LocalStorageBackend(StorageBackend):
    """Local filesystem storage adapter storing objects partitioned by tenant."""

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir)

    def _resolve_path(self, key: StorageKey) -> Path:
        """Resolve tenant storage key to local filesystem path."""
        target_path = self.base_dir / str(key.tenant_id) / key.path
        return target_path

    async def put(
        self,
        key: StorageKey,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Store binary data under the tenant-scoped storage key."""
        del content_type
        target = self._resolve_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target)

    async def get(self, key: StorageKey) -> bytes:
        """Retrieve binary data stored at the tenant-scoped storage key."""
        target = self._resolve_path(key)
        if not target.exists():
            raise FileNotFoundError(f"Storage object not found: {key.full_key}")
        return target.read_bytes()

    async def signed_url(self, key: StorageKey, ttl_seconds: int = 3600) -> str:
        """Generate a local access URL or path."""
        del ttl_seconds
        target = self._resolve_path(key)
        return f"file:///{target.as_posix()}"

    async def delete(self, key: StorageKey) -> None:
        """Permanently delete data at the tenant-scoped storage key."""
        target = self._resolve_path(key)
        if target.exists():
            target.unlink()
