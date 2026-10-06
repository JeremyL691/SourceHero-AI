"""Provider protocol and shared helpers.

Three wire protocols are supported: OpenAI Chat Completions, OpenAI Responses, and
Anthropic Messages. They differ in endpoint, how system prompts are passed, where the
usage numbers live, and how streaming is framed -- so each gets its own module, with
the parts that are genuinely identical (reasoning-stripping, JSON extraction, auth
headers) here.
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.llm.types import LLMUnavailable, Message

# Models that emit a reasoning preamble we must not treat as the answer.
_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Drop `<think>` blocks and any stray `reasoning_content` echo.

    Some endpoints return the chain of thought alongside the answer; feeding that to a
    JSON parser or to the reader would corrupt both.
    """
    if not text:
        return ""
    cleaned = _THINK.sub("", text).strip()
    return cleaned


def extract_json(text: str) -> dict:
    """Parse a JSON object out of a model reply.

    Tolerates a code fence and leading/trailing prose, because models do both even
    when told not to. Raises `StructuredOutputError` when nothing parses -- the
    caller decides whether to retry with a repair message.
    """
    from app.llm.types import StructuredOutputError

    candidate = strip_reasoning(text)
    if not candidate:
        raise StructuredOutputError("empty response")

    fenced = _FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = None

    if parsed is None:
        # Fall back to the outermost brace-balanced span.
        start = candidate.find("{")
        if start >= 0:
            depth = 0
            in_string = False
            escaped = False
            for i in range(start, len(candidate)):
                ch = candidate[i]
                if in_string:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == '"':
                        in_string = False
                    continue
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(candidate[start : i + 1])
                        except json.JSONDecodeError:
                            break
                        break

    if not isinstance(parsed, dict):
        raise StructuredOutputError(f"response is not a JSON object: {candidate[:200]!r}")
    return parsed


def estimate_tokens(text: str) -> int:
    """Rough token count, used when an endpoint omits usage."""
    if not text:
        return 0
    return max(1, len(text) // 4)


@dataclass
class TokenSource:
    """Supplies bearer tokens; `invalidate` forces the next `get_token` to refresh."""

    def get_token(self) -> str | None:  # pragma: no cover - protocol
        raise NotImplementedError

    def invalidate(self) -> None:  # pragma: no cover - protocol
        return None


@dataclass
class ProviderConfig:
    base_url: str
    api_key: str
    model_fast: str = ""
    model_smart: str = ""
    auth_header: str = "authorization"
    json_mode: str = "auto"
    timeout: float = 120.0
    extra_headers: dict[str, str] = field(default_factory=dict)
    token_source: TokenSource | None = None


class Provider(Protocol):
    name: str

    def complete(
        self, messages: list[Message], *, model: str, config: ProviderConfig, **kwargs
    ) -> tuple[str, dict]:
        """Return `(text, usage)`. Raises `LLMUnavailable` on transport/credential errors."""
        ...

    def stream(self, messages: list[Message], *, model: str, config: ProviderConfig, **kwargs) -> Any:
        """Yield text deltas."""
        ...

    def list_models(self, config: ProviderConfig) -> list[str]: ...


_shared_http_client: Any = None
_shared_client_lock = threading.Lock()


def get_shared_http_client(timeout: float = 120.0) -> Any:
    global _shared_http_client
    with _shared_client_lock:
        if _shared_http_client is None or getattr(_shared_http_client, "is_closed", False):
            import httpx

            _shared_http_client = httpx.Client(timeout=timeout)
        return _shared_http_client


def close_shared_http_client() -> None:
    global _shared_http_client
    with _shared_client_lock:
        if _shared_http_client is not None:
            with contextlib.suppress(Exception):
                _shared_http_client.close()
            _shared_http_client = None


_disabled_json_mode: set[tuple[str, str]] = set()
_disabled_temperature: set[tuple[str, str]] = set()
_degrade_lock = threading.Lock()


def is_json_mode_disabled(provider: str, model: str) -> bool:
    with _degrade_lock:
        return (provider, model) in _disabled_json_mode


def disable_json_mode(provider: str, model: str) -> None:
    with _degrade_lock:
        _disabled_json_mode.add((provider, model))


def is_temperature_disabled(provider: str, model: str) -> bool:
    with _degrade_lock:
        return (provider, model) in _disabled_temperature


def disable_temperature(provider: str, model: str) -> None:
    with _degrade_lock:
        _disabled_temperature.add((provider, model))


def reset_degradation_memory() -> None:
    with _degrade_lock:
        _disabled_json_mode.clear()
        _disabled_temperature.clear()


class StreamingThinkStripper:
    """Strips <think>...</think> tags and their contents across chunks without stripping whitespace."""

    OPEN_TAG = "<think>"
    CLOSE_TAG = "</think>"

    def __init__(self) -> None:
        self.inside_think = False
        self.buffer = ""

    def feed(self, text: str) -> list[str]:
        self.buffer += text
        out: list[str] = []
        open_tag = self.OPEN_TAG
        close_tag = self.CLOSE_TAG

        while self.buffer:
            if not self.inside_think:
                idx = self.buffer.find("<")
                if idx < 0:
                    out.append(self.buffer)
                    self.buffer = ""
                    break
                if idx > 0:
                    out.append(self.buffer[:idx])
                    self.buffer = self.buffer[idx:]

                lower = self.buffer.lower()
                if lower.startswith(open_tag):
                    self.inside_think = True
                    self.buffer = self.buffer[len(open_tag) :]
                    continue
                elif open_tag.startswith(lower):
                    break
                else:
                    out.append(self.buffer[0])
                    self.buffer = self.buffer[1:]
            else:
                lower = self.buffer.lower()
                idx = lower.find(close_tag)
                if idx >= 0:
                    self.inside_think = False
                    self.buffer = self.buffer[idx + len(close_tag) :]
                    continue
                else:
                    matched_prefix_len = 0
                    for length in range(min(len(close_tag) - 1, len(lower)), 0, -1):
                        if close_tag.startswith(lower[-length:]):
                            matched_prefix_len = length
                            break
                    if matched_prefix_len > 0:
                        self.buffer = self.buffer[-matched_prefix_len:]
                    else:
                        self.buffer = ""
                    break
        return out

    def finish(self) -> list[str]:
        out: list[str] = []
        if not self.inside_think and self.buffer:
            out.append(self.buffer)
        self.buffer = ""
        return out


def strip_thinking_stream(chunks: Any) -> Iterator[str]:
    stripper = StreamingThinkStripper()
    for chunk in chunks:
        if not chunk:
            continue
        for piece in stripper.feed(chunk):
            if piece:
                yield piece
    for piece in stripper.finish():
        if piece:
            yield piece


class _BaseProvider:
    """Retry, auth headers, and JSON-mode negotiation shared by the HTTP providers."""

    name = "base"

    def __init__(self, client: Any = None) -> None:
        self._client = client

    # --- transport ---------------------------------------------------------

    def _http(self, config: ProviderConfig):
        if self._client is not None:
            return self._client
        return get_shared_http_client(timeout=config.timeout)

    def _own_client(self) -> bool:
        return self._client is None

    def _token(self, config: ProviderConfig) -> str | None:
        if config.token_source is not None:
            token = config.token_source.get_token()
            if token:
                return token
        return config.api_key or None

    def _headers(self, config: ProviderConfig) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **config.extra_headers}
        token = self._token(config)
        if token:
            if config.auth_header == "x-api-key":
                headers["x-api-key"] = token
            else:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _post(self, url: str, payload: dict, config: ProviderConfig, *, stream: bool = False):
        import httpx

        client = self._http(config)
        headers = self._headers(config)
        try:
            return client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc

    # --- JSON mode / temperature negotiation --------------------------------

    def _is_json_mode_error(self, status_code: int, body: str) -> bool:
        lowered = (body or "").lower()
        return status_code == 400 and ("response_format" in lowered or "text.format" in lowered)

    def _is_temperature_error(self, status_code: int, body: str) -> bool:
        return status_code == 400 and "temperature" in (body or "").lower()

    def _degrade(self, status_code: int, body: str, payload: dict, *, model: str = "") -> bool:
        """Strip an unsupported parameter and retry. True if `payload` was changed."""
        prov = self.name
        if not is_json_mode_disabled(prov, model) and self._is_json_mode_error(status_code, body):
            payload.pop("response_format", None)
            payload.pop("text", None)
            disable_json_mode(prov, model)
            return True
        if not is_temperature_disabled(prov, model) and self._is_temperature_error(status_code, body):
            payload.pop("temperature", None)
            disable_temperature(prov, model)
            return True
        return False


__all__ = [
    "Provider",
    "ProviderConfig",
    "StreamingThinkStripper",
    "TokenSource",
    "close_shared_http_client",
    "disable_json_mode",
    "disable_temperature",
    "estimate_tokens",
    "extract_json",
    "get_shared_http_client",
    "is_json_mode_disabled",
    "is_temperature_disabled",
    "reset_degradation_memory",
    "strip_reasoning",
    "strip_thinking_stream",
]
