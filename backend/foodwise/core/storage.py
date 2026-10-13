"""Object storage. Keys are always prefixed `tenants/{tenant_id}/` for tenant-owned objects.

The filesystem implementation is used locally and in single-host deployments (a mounted
volume). The interface is small on purpose so an S3-compatible backend can replace it.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from foodwise.config import get_settings


def tenant_key(tenant_id: uuid.UUID | str, *parts: str) -> str:
    safe = [p.replace("..", "").strip("/\\") for p in parts]
    return "/".join(["tenants", str(tenant_id), *safe])


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("storage key escapes the storage root")
        return path

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def local_path(self, key: str) -> Path:
        return self._path(key)


_storage: LocalStorage | None = None


def storage() -> LocalStorage:
    global _storage
    if _storage is None:
        _storage = LocalStorage(get_settings().storage_dir)
    return _storage
