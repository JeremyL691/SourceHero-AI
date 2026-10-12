"""OpenAI Chat Completions (`POST {base}/chat/completions`)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from app.core.logging import get_logger
from app.llm.providers.base import (
    ProviderConfig,
    _BaseProvider,
    estimate_tokens,
    strip_reasoning,
)
from app.llm.types import LLMUnavailable, Message, QuotaExhaustedError

log = get_logger(__name__)

# The operator's extra body may tune a request but never rewrite what the client sent.
_PROTECTED_BODY_KEYS = frozenset({"model", "messages", "stream"})
_QUOTA_MARKERS = ("free-models-per-day", "daily limit")


def _http_error(status_code: int, body: str) -> LLMUnavailable:
    """A 429 that says the free-tier day is spent is final; every other error keeps its retry path."""
    message = f"HTTP {status_code}: {body[:400]}"
    lowered = (body or "").lower()
    if status_code == 429 and any(marker in lowered for marker in _QUOTA_MARKERS):
        return QuotaExhaustedError(message, status_code=status_code)
    return LLMUnavailable(message, status_code=status_code)


class OpenAIChatProvider(_BaseProvider):
    """Works with OpenAI itself and anything that speaks the same dialect
    (OpenCode Go, Ollama, vLLM, LM Studio)."""

    name = "openai_chat"

    def _payload(
        self,
        messages: list[Message],
        model: str,
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        stream: bool,
        extra_body: dict[str, Any] | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        from app.llm.providers.base import (
            is_json_mode_disabled,
            is_temperature_disabled,
        )

        if not is_temperature_disabled(self.name, model):
            payload["temperature"] = temperature
        if json_mode and not is_json_mode_disabled(self.name, model):
            payload["response_format"] = {"type": "json_object"}
        for key, value in (extra_body or {}).items():
            if key not in _PROTECTED_BODY_KEYS:
                payload[key] = value
        return payload

    def _url(self, config: ProviderConfig) -> str:
        return f"{config.base_url.rstrip('/')}/chat/completions"

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        config: ProviderConfig,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        json_mode: bool = False,
        extra_body: dict[str, Any] | None = None,
        **_: Any,
    ) -> tuple[str, dict]:
        payload = self._payload(
            messages,
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            stream=False,
            extra_body=extra_body,
        )
        for _attempt in range(2):
            resp = self._post(self._url(config), payload, config)
            if resp.status_code < 400:
                break
            body = getattr(resp, "text", "")
            if self._degrade(resp.status_code, body, payload, model=model):
                continue
            raise _http_error(resp.status_code, body)
        else:  # pragma: no cover - degrade always returns True or raises
            raise LLMUnavailable("could not satisfy the endpoint's parameter requirements")

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise LLMUnavailable("response contained no choices")
        text = strip_reasoning(choices[0].get("message", {}).get("content") or "")
        usage = data.get("usage") or {}
        return text, {
            "prompt_tokens": int(
                usage.get("prompt_tokens") or estimate_tokens("\n".join(m.content for m in messages))
            ),
            "completion_tokens": int(usage.get("completion_tokens") or estimate_tokens(text)),
        }

    def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        config: ProviderConfig,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        json_mode: bool = False,
        extra_body: dict[str, Any] | None = None,
        usage_out: dict[str, int] | None = None,
        **_: Any,
    ) -> Iterator[str]:
        from app.llm.providers.base import strip_thinking_stream

        payload = self._payload(
            messages,
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            stream=True,
            extra_body=extra_body,
        )

        def _raw_chunks():
            client = self._http(config)
            with client.stream(
                "POST", self._url(config), json=payload, headers=self._headers(config)
            ) as resp:
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", errors="replace")
                    raise _http_error(resp.status_code, body)
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    import json

                    try:
                        parsed = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue  # keep-alive or partial frame
                    # With include_usage the usage arrives in a final frame with no choices.
                    if usage_out is not None and parsed.get("usage"):
                        reported = parsed["usage"]
                        if reported.get("prompt_tokens") is not None:
                            usage_out["prompt_tokens"] = int(reported["prompt_tokens"])
                        if reported.get("completion_tokens") is not None:
                            usage_out["completion_tokens"] = int(reported["completion_tokens"])
                    for choice in parsed.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield delta

        yield from strip_thinking_stream(_raw_chunks())

    def list_models(self, config: ProviderConfig) -> list[str]:
        client = self._http(config)
        try:
            resp = client.get(f"{config.base_url.rstrip('/')}/models", headers=self._headers(config))
        except Exception as exc:  # noqa: BLE001
            raise LLMUnavailable(f"could not list models: {exc}") from exc
        if resp.status_code >= 400:
            raise LLMUnavailable(f"HTTP {resp.status_code}")
        return [str(item.get("id")) for item in (resp.json().get("data") or []) if item.get("id")]


__all__ = ["OpenAIChatProvider"]
