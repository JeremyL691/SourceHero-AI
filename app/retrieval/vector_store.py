"""
Legacy file-based vector store.

Kept only because older tests imported it before the cloud/pgvector migration.
Production now targets Postgres + pgvector (`infra/supabase/schema.sql` +
`app/services/semantic_index.py`). This module is not wired into the current
ingestion or retrieval path and should not be presented as the active index.
It remains for reference and for the four skipped legacy tests in
`tests/test_hybrid_retrieval.py`.
"""


from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import threading

from app.config import settings
from app.openai_models import DEFAULT_EMBEDDING_MODEL

_INDEX_LOCK = threading.RLock()


@dataclass
class LocalIndexStatus:
    backend: str = "openai_embeddings_file_index"
    persisted: bool = False
    enabled: bool = False
    ready: bool = False
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    indexed_chunks: int = 0
    total_chunks: int = 0
    pending_chunks: int = 0
    note: str = "Embeddings are stored locally in the app data directory and searched without an external vector database."


def index_path() -> Path:
    settings.ensure_dirs()
    return settings.vector_dir / "chunk_embeddings.json"


def load_embeddings() -> dict[str, list[float]]:
    with _INDEX_LOCK:
        return _load_embeddings_unlocked()


def _load_embeddings_unlocked() -> dict[str, list[float]]:
    path = index_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    embeddings = payload.get("embeddings", {})
    if not isinstance(embeddings, dict):
        return {}
    return {str(key): value for key, value in embeddings.items() if isinstance(value, list)}


def save_embeddings(embeddings: dict[str, list[float]]) -> None:
    with _INDEX_LOCK:
        _save_embeddings_unlocked(embeddings)


def _save_embeddings_unlocked(embeddings: dict[str, list[float]]) -> None:
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"embedding_model": DEFAULT_EMBEDDING_MODEL, "embeddings": embeddings}
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temp_path, path)


def upsert_embeddings(entries: dict[int, list[float]]) -> int:
    if not entries:
        return 0
    with _INDEX_LOCK:
        embeddings = _load_embeddings_unlocked()
        for chunk_id, vector in entries.items():
            embeddings[str(chunk_id)] = vector
        _save_embeddings_unlocked(embeddings)
    return len(entries)


def delete_embeddings(chunk_ids: list[int]) -> int:
    if not chunk_ids:
        return 0
    with _INDEX_LOCK:
        embeddings = _load_embeddings_unlocked()
        removed = 0
        for chunk_id in chunk_ids:
            key = str(chunk_id)
            if key in embeddings:
                removed += 1
                embeddings.pop(key, None)
        _save_embeddings_unlocked(embeddings)
    return removed


def clear_embeddings() -> None:
    with _INDEX_LOCK:
        _save_embeddings_unlocked({})


def build_status(*, total_chunks: int, enabled: bool) -> LocalIndexStatus:
    embeddings = load_embeddings()
    persisted = index_path().is_file()
    indexed_chunks = len(embeddings)
    return LocalIndexStatus(
        persisted=persisted,
        enabled=enabled,
        ready=bool(enabled and total_chunks > 0 and indexed_chunks >= total_chunks),
        indexed_chunks=indexed_chunks,
        total_chunks=total_chunks,
        pending_chunks=max(total_chunks - indexed_chunks, 0),
    )


def search_similar(
    query_embedding: list[float],
    *,
    top_k: int = 5,
    allowed_chunk_ids: set[int] | None = None,
) -> list[tuple[int, float]]:
    embeddings = load_embeddings()
    if not embeddings:
        return []

    query_norm = _vector_norm(query_embedding)
    if query_norm <= 0:
        return []

    scored: list[tuple[int, float]] = []
    for key, vector in embeddings.items():
        chunk_id = int(key)
        if allowed_chunk_ids is not None and chunk_id not in allowed_chunk_ids:
            continue
        score = _cosine_similarity(query_embedding, vector, query_norm=query_norm)
        if score > 0:
            scored.append((chunk_id, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:top_k]


def _cosine_similarity(left: list[float], right: list[float], *, query_norm: float | None = None) -> float:
    right_norm = _vector_norm(right)
    left_norm = query_norm if query_norm is not None else _vector_norm(left)
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    return dot / (left_norm * right_norm)


def _vector_norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))
