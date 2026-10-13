"""Optional cross-encoder reranking.

Off by default. The plan makes `RERANK_PROVIDER=fastembed` the opt-in path; if the
model cannot be loaded, reranking is disabled with a logged reason rather than
failing the search -- a missing optional model must not take retrieval down.
"""

from __future__ import annotations

import threading

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_encoder = None
_unavailable_reason: str | None = None
_lock = threading.Lock()


def get_encoder():
    """Load the cross-encoder once, or return None with the reason logged."""
    global _encoder, _unavailable_reason
    with _lock:
        if _encoder is not None or _unavailable_reason is not None:
            return _encoder
        settings = get_settings()
        if settings.rerank_provider == "none":
            _unavailable_reason = "rerank_provider is none"
            return None
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            _encoder = TextCrossEncoder(
                model_name=settings.rerank_model, cache_dir=settings.embedding_cache_dir
            )
        except Exception as exc:  # noqa: BLE001 - degrade, do not fail the search
            _unavailable_reason = f"{type(exc).__name__}: {exc}"
            log.warning("rerank disabled (%s)", _unavailable_reason)
            _encoder = None
        return _encoder


def rerank(query: str, documents: list[str]) -> list[float]:
    """Relevance scores for `documents`, or [] when reranking is unavailable.

    Returns [] rather than zeros so the caller can tell "no reranker" from
    "everything scored zero".
    """
    if not documents:
        return []
    encoder = get_encoder()
    if encoder is None:
        return []
    try:
        results = list(encoder.rerank(query, documents))
        return [float(getattr(r, "score", r)) for r in results]
    except Exception:  # noqa: BLE001 - never let reranking break retrieval
        log.warning("rerank failed; falling back to the fused order", exc_info=True)
        return []


def status() -> str:
    return "rerank" if get_encoder() is not None else (_unavailable_reason or "not loaded")


def reset() -> None:
    global _encoder, _unavailable_reason
    with _lock:
        _encoder = None
        _unavailable_reason = None


__all__ = ["get_encoder", "rerank", "reset", "status"]
