"""Embeddings.

The default embedder is `HashingEmbedder`: a deterministic signed hashing vectorizer.
No model download, identical output on every machine, which is what makes the test
suite, the e2e run and the demo reproducible. `fastembed` is the recommended
production choice and is opt-in.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Protocol, runtime_checkable

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger
from app.indexing.tokenize import search_tokens

log = get_logger(__name__)


@runtime_checkable
class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


class HashingEmbedder:
    """Signed hashing vectorizer over token unigrams and bigrams.

    * a feature maps to a bucket via `blake2b` (stable across runs and machines,
      unlike Python's salted `hash`),
    * the high bit of the digest is the sign, which keeps unrelated texts near zero
      cosine similarity instead of always-positive,
    * bigrams are down-weighted to 0.5 so word order nudges but does not dominate.

    Two texts sharing no vocabulary land around 0; texts sharing words land high.
    It cannot match a paraphrase with different words -- that is the trade for a
    default that needs no download. `EMBEDDING_PROVIDER=fastembed` fixes that.
    """

    def __init__(self, dim: int | None = None):
        self.dim = dim or get_settings().embedding_dim
        self.name = "hashing-v1"
        self.status = "hashing-v1"

    def _features(self, text: str) -> list[tuple[str, float]]:
        tokens = search_tokens(text)
        feats = [(t, 1.0) for t in tokens]
        feats += [(f"{a}_{b}", 0.5) for a, b in zip(tokens, tokens[1:], strict=False)]
        return feats

    def _vector(self, text: str) -> list[float]:
        acc = np.zeros(self.dim, dtype=np.float64)
        for feature, weight in self._features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            h = int.from_bytes(digest, "big")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            acc[idx] += sign * weight
        return _normalize(acc).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FastEmbedEmbedder:
    """Local ONNX sentence embeddings. Loaded lazily; the process-global cache keeps
    the model resident across calls."""

    name = "fastembed-v1"

    _instances: dict[tuple, FastEmbedEmbedder] = {}
    _lock = threading.Lock()

    def __init__(self, model: str, dim: int, cache_dir: str):
        self.model_name = model
        self.dim = dim
        self.cache_dir = cache_dir
        self._encoder = None
        self.name = f"fastembed:{model}"
        self.status = self.name

    @classmethod
    def get(cls, model: str, dim: int, cache_dir: str) -> FastEmbedEmbedder:
        key = (model, dim, cache_dir)
        with cls._lock:
            inst = cls._instances.get(key)
            if inst is None:
                inst = cls(model, dim, cache_dir)
                cls._instances[key] = inst
            return inst

    def _load(self):
        if self._encoder is None:
            from fastembed import TextEmbedding

            self._encoder = TextEmbedding(model_name=self.model_name, cache_dir=self.cache_dir)
            probe = np.asarray(next(iter(self._encoder.embed(["dimension probe"]))))
            if probe.shape[0] != self.dim:
                raise ValueError(
                    f"{self.model_name} produces {probe.shape[0]}-dim vectors but "
                    f"EMBEDDING_DIM is {self.dim}. Change EMBEDDING_DIM rather than "
                    "silently truncating -- a wrong dimension corrupts every stored vector."
                )
        return self._encoder

    def _embed(self, texts: list[str]) -> list[list[float]]:
        encoder = self._load()
        return [np.asarray(v).tolist() for v in encoder.embed(texts, batch_size=64)]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        encoder = self._load()
        # Some models are asymmetric; their query encoder is the right one for queries.
        query_embed = getattr(encoder, "query_embed", None)
        if query_embed is not None:
            return np.asarray(next(iter(query_embed.embed([text])))).tolist()
        return self._embed([text])[0]


class OpenAICompatEmbedder:
    """POST {base}/embeddings. Off by default -- OpenCode Go does not serve embeddings."""

    name = "openai_compat"

    def __init__(self, base_url: str, model: str, dim: int, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim
        self.api_key = api_key
        self.status = f"openai_compat:{model}"

    def _post(self, texts: list[str]) -> list[list[float]]:
        import httpx

        resp = httpx.post(
            f"{self.base_url}/embeddings",
            json={"model": self.model, "input": texts},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        vectors = [item["embedding"] for item in sorted(data, key=lambda d: d.get("index", 0))]
        if vectors and len(vectors[0]) != self.dim:
            raise ValueError(
                f"{self.model} returns {len(vectors[0])}-dim vectors but EMBEDDING_DIM is {self.dim}"
            )
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._post(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._post([text])[0]


_embedder: Embedder | None = None
_embedder_status: str = ""
_lock = threading.Lock()


def get_embedder() -> Embedder:
    """Build (once) the configured embedder.

    If fastembed cannot initialise we fall back to hashing and say so, rather than
    leaving the app unusable: a working lexical+vector search beats none. `/readyz`
    reports the fallback so it is never silent.
    """
    global _embedder, _embedder_status
    with _lock:
        if _embedder is not None:
            return _embedder
        settings = get_settings()
        provider = settings.embedding_provider
        if provider == "fastembed":
            try:
                inst = FastEmbedEmbedder.get(
                    settings.embedding_model, settings.embedding_dim, settings.embedding_cache_dir
                )
                inst.embed_query("warm up")
                _embedder = inst
                _embedder_status = inst.status
            except Exception as exc:  # noqa: BLE001 - degrade, do not crash
                log.warning("fastembed unavailable (%s); falling back to hashing", exc)
                _embedder = HashingEmbedder(settings.embedding_dim)
                _embedder_status = f"hashing-v1 (fallback: {type(exc).__name__}: {exc})"
        elif provider == "openai_compat":
            _embedder = OpenAICompatEmbedder(
                settings.llm_base_url,
                settings.embedding_model,
                settings.embedding_dim,
                settings.llm_api_key,
            )
            _embedder_status = _embedder.status
        else:
            _embedder = HashingEmbedder(settings.embedding_dim)
            _embedder_status = "hashing-v1"
        return _embedder


def get_embedder_status() -> str:
    return _embedder_status or "not initialized"


def reset_embedder() -> None:
    """Tests that switch EMBEDDING_PROVIDER call this."""
    global _embedder, _embedder_status
    with _lock:
        _embedder = None
        _embedder_status = ""


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    va, vb = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va.dot(vb) / denom) if denom else 0.0


def unit(vec: np.ndarray) -> np.ndarray:
    return _normalize(vec)


__all__ = [
    "Embedder",
    "FastEmbedEmbedder",
    "HashingEmbedder",
    "OpenAICompatEmbedder",
    "cosine",
    "get_embedder",
    "get_embedder_status",
    "reset_embedder",
    "unit",
]
