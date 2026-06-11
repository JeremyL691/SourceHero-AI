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
    from app.config import settings

    if settings.r2_access_key_id and settings.r2_secret_access_key:
        from app.storage.r2 import R2Storage

        return R2Storage()
    from app.storage.local import LocalStorage

    return LocalStorage()
