from __future__ import annotations

from pathlib import Path

from app.config import settings

LOCAL_STORAGE_DIR = Path("./data/local_storage")


class LocalStorage:
    def __init__(self) -> None:
        LOCAL_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    def upload(self, key: str, file, content_type: str = "application/octet-stream") -> str:
        target = LOCAL_STORAGE_DIR / key
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as f:
            f.write(file.read())
        return f"/local-storage/{key}"

    def download(self, key: str) -> bytes:
        path = LOCAL_STORAGE_DIR / key
        if not path.exists():
            raise FileNotFoundError(f"File not found: {key}")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = LOCAL_STORAGE_DIR / key
        if path.exists():
            path.unlink()

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return f"/local-storage/{key}"
