"""Provider protocol and shared helpers.

Three wire protocols are supported: OpenAI Chat Completions, OpenAI Responses, and
Anthropic Messages. They differ in endpoint, how system prompts are passed, where the
usage numbers live, and how streaming is framed -- so each gets its own module, with
the parts that are genuinely identical (reasoning-stripping, JSON extraction, auth
headers) here.
"""

from __future__ import annotations

import json
import re
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


class _BaseProvider:
    """Retry, auth headers, and JSON-mode negotiation shared by the HTTP providers."""

    name = "base"

    def __init__(self, client: Any = None) -> None:
        self._client = client
        # Learned per process: some models reject `response_format` or `temperature`.
        # Once a rejection is seen we remember it instead of paying the failed round
        # trip on every subsequent call.
        self._no_json_mode = False
        self._no_temperature = False

    # --- transport ---------------------------------------------------------

    def _http(self, config: ProviderConfig):
        if self._client is not None:
            return self._client
        import httpx

        return httpx.Client(timeout=config.timeout)

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

    def _degrade(self, status_code: int, body: str, payload: dict) -> bool:
        """Strip an unsupported parameter and retry. True if `payload` was changed."""
        if not self._no_json_mode and self._is_json_mode_error(status_code, body):
            payload.pop("response_format", None)
            payload.pop("text", None)
            self._no_json_mode = True
            return True
        if not self._no_temperature and self._is_temperature_error(status_code, body):
            payload.pop("temperature", None)
            self._no_temperature = True
            return True
        return False


__all__ = [
    "Provider",
    "ProviderConfig",
    "TokenSource",
    "estimate_tokens",
    "extract_json",
    "strip_reasoning",
]
