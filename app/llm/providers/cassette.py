"""Record and replay of LLM traffic (plan K1.4), so evaluations and tests run offline.

A cassette is JSONL, one line per distinct request: `{key, model, created_at, response, usage}`.
`response` is `{"text": ...}` for a completion and also carries `"chunks"` for a stream, so a
replayed stream yields the original deltas. The key is the SHA-256 of the canonical request
(`cassette_key`). Nothing else is written: no headers, no API key, no base URL, so a cassette
is safe to commit.

Modes:
- `replay`: answer from the file only. A request with no recorded answer raises
  `CassetteMissError`. It deliberately does not subclass `LLMUnavailable`, so the client's
  retry and fallback paths cannot turn a stale cassette into a silent heuristic answer.
- `record`: answer from the file when possible; otherwise call the wrapped provider and
  append the answer.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.llm.types import Message

MODES = ("off", "replay", "record")


class CassetteMissError(RuntimeError):
    """A replayed request has no recorded answer. Tests and evaluations must fail on it."""


def cassette_key(
    protocol: str,
    *,
    model: str,
    messages: list[Message],
    schema_name: str | None,
    temperature: float | None,
    json_mode: bool,
) -> str:
    """SHA-256 of the canonical request. Same inputs, same key, on any machine."""
    payload = {
        "protocol": protocol,
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "schema_name": schema_name,
        "temperature": temperature,
        "json_mode": bool(json_mode),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CassetteProvider:
    """Wraps a provider with the same `complete` / `stream` / `list_models` surface."""

    def __init__(self, inner: Any, path: Path, mode: str) -> None:
        if mode not in MODES or mode == "off":
            raise ValueError(f"cassette mode must be 'replay' or 'record', not {mode!r}")
        self._inner = inner
        self._path = Path(path)
        self._mode = mode
        self._lock = threading.Lock()
        self._index: dict[str, dict] | None = None
        self.name = inner.name

    # --- the file ----------------------------------------------------------

    def _rows(self) -> dict[str, dict]:
        with self._lock:
            if self._index is None:
                index: dict[str, dict] = {}
                if self._path.exists():
                    for line in self._path.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            row = json.loads(line)
                            index[row["key"]] = row
                self._index = index
            return self._index

    def _append(self, row: dict) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            if self._index is not None:
                self._index[row["key"]] = row

    def _key(self, messages: list[Message], model: str, kwargs: dict) -> str:
        schema = kwargs.get("schema")
        return cassette_key(
            self._inner.name,
            model=model,
            messages=list(messages),
            schema_name=getattr(schema, "__name__", None) if schema is not None else None,
            temperature=kwargs.get("temperature"),
            json_mode=bool(kwargs.get("json_mode", False)),
        )

    def _miss(self, key: str, model: str) -> CassetteMissError:
        return CassetteMissError(f"no recorded answer for model {model!r} (key {key[:12]}) in {self._path}")

    # --- the provider surface ---------------------------------------------

    def complete(self, messages: list[Message], *, model: str, config: Any, **kwargs) -> tuple[str, dict]:
        key = self._key(messages, model, kwargs)
        row = self._rows().get(key)
        if row is not None:
            return row["response"]["text"], dict(row.get("usage") or {})
        if self._mode == "replay":
            raise self._miss(key, model)
        text, usage = self._inner.complete(messages, model=model, config=config, **kwargs)
        self._append(
            {
                "key": key,
                "model": model,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "response": {"text": text},
                "usage": dict(usage or {}),
            }
        )
        return text, usage

    def stream(self, messages: list[Message], *, model: str, config: Any, **kwargs) -> Iterator[str]:
        key = self._key(messages, model, kwargs)
        row = self._rows().get(key)
        if row is not None:
            response = row["response"]
            yield from response.get("chunks") or [response["text"]]
            return
        if self._mode == "replay":
            raise self._miss(key, model)
        chunks: list[str] = []
        for delta in self._inner.stream(messages, model=model, config=config, **kwargs):
            chunks.append(delta)
            yield delta
        self._append(
            {
                "key": key,
                "model": model,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "response": {"text": "".join(chunks), "chunks": chunks},
                "usage": {},
            }
        )

    def list_models(self, config: Any) -> list[str]:
        return self._inner.list_models(config)
