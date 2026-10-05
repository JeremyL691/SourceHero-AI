from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings


def get_local_storage_dir() -> Path:
    return Path(get_settings().data_dir) / "local_storage"


class _LocalStorageDirProxy:
    """Compatibility proxy so that LOCAL_STORAGE_DIR / key continues to work."""

    def __truediv__(self, other: str) -> Path:
        return get_local_storage_dir() / other

    def mkdir(self, *args, **kwargs) -> None:
        get_local_storage_dir().mkdir(*args, **kwargs)


LOCAL_STORAGE_DIR = _LocalStorageDirProxy()


class LocalStorage:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or get_local_storage_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def upload(self, key: str, file, content_type: str = "application/octet-stream") -> str:
        target = self.base_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as f:
            f.write(file.read())
        return f"/local-storage/{key}"

    def download(self, key: str) -> bytes:
        path = self.base_dir / key
        if not path.exists():
            raise FileNotFoundError(f"File not found: {key}")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self.base_dir / key
        if path.exists():
            path.unlink()

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return f"/local-storage/{key}"
