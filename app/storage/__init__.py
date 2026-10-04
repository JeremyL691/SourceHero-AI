"""File storage backends: local disk by default, any S3-compatible service otherwise."""

from __future__ import annotations

from abc import ABC, abstractmethod


class FileStorage(ABC):
    @abstractmethod
    def upload(self, key: str, file, content_type: str = "application/octet-stream") -> str: ...

    @abstractmethod
    def download(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str: ...


def get_storage() -> FileStorage:
    """S3-compatible storage when credentials are configured, local disk otherwise.

    The local fallback is for `make dev` and `pytest`; it writes under
    `$DATA_DIR/storage` (gitignored). Any non-local deployment must set S3_*.
    """
    from app.core.config import get_settings
    from app.storage.s3 import S3Storage

    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3Storage()
    from app.storage.local import LocalStorage

    return LocalStorage()


__all__ = ["FileStorage", "get_storage"]
