"""LocalStore — passthrough for local filesystem (no remote ops).

Home Edition's only object-storage backend; documents stay on the
appliance's disk. Presign and remote ops raise NotImplementedError
since there's no remote to talk to.
"""
import os
import shutil
from dataclasses import dataclass
from datetime import datetime


@dataclass
class StashResult:
    """Result of uploading a file to storage."""
    bucket: str
    key: str
    size_bytes: int
    etag: str


@dataclass
class PresignedUrl:
    """A time-limited download URL for a remote object."""
    url: str
    expires_at: datetime


class LocalStore:
    """No-op store for local-only operation."""

    def put(self, local_path: str, key: str) -> StashResult:
        return StashResult(
            bucket="local",
            key=key,
            size_bytes=os.path.getsize(local_path),
            etag="local",
        )

    def get(self, key: str, local_path: str) -> None:
        if key != local_path:
            shutil.copy2(key, local_path)

    def presign(self, key: str, expires_seconds: int = 3600) -> PresignedUrl:
        raise NotImplementedError("LocalStore does not support presigned URLs")

    def delete(self, key: str) -> None:
        if os.path.exists(key):
            os.remove(key)

    def exists(self, key: str) -> bool:
        return os.path.exists(key)
