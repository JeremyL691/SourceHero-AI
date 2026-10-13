"""Anthropic Messages API (`POST {base}/messages`).

The system prompt is a top-level field rather than a message, the version header is
required, and usage is `input_tokens`/`output_tokens`.
"""

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
from app.llm.types import LLMUnavailable, Message

log = get_logger(__name__)

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicMessagesProvider(_BaseProvider):
    name = "anthropic_messages"

    def _headers(self, config: ProviderConfig) -> dict[str, str]:
        headers = super()._headers(config)
        headers["anthropic-version"] = ANTHROPIC_VERSION
        return headers

    def _payload(
        self,
        messages: list[Message],
        model: str,
        *,
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> dict:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages if m.role != "system"],
            "stream": stream,
        }
        if system:
            payload["system"] = system
        from app.llm.providers.base import is_temperature_disabled

        if not is_temperature_disabled(self.name, model):
            payload["temperature"] = temperature
        return payload

    def _url(self, config: ProviderConfig) -> str:
        return f"{config.base_url.rstrip('/')}/messages"

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        config: ProviderConfig,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        json_mode: bool = False,  # unused: no JSON mode in this protocol
        **_: Any,
    ) -> tuple[str, dict]:
        payload = self._payload(
            messages,
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        for _attempt in range(2):
            resp = self._post(self._url(config), payload, config)
            if resp.status_code < 400:
                break
            body = getattr(resp, "text", "")
            if self._degrade(resp.status_code, body, payload, model=model):
                continue
            raise LLMUnavailable(f"HTTP {resp.status_code}: {body[:400]}", status_code=resp.status_code)
        else:  # pragma: no cover
            raise LLMUnavailable("could not satisfy the endpoint's parameter requirements")

        data = resp.json()
        parts = [block.get("text", "") for block in data.get("content") or [] if block.get("type") == "text"]
        text = "".join(parts)
        usage = data.get("usage") or {}
        return strip_reasoning(text), {
            "prompt_tokens": int(
                usage.get("input_tokens") or estimate_tokens("\n".join(m.content for m in messages))
            ),
            "completion_tokens": int(usage.get("output_tokens") or estimate_tokens(text)),
        }

    def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        config: ProviderConfig,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        json_mode: bool = False,  # unused
        **_: Any,
    ) -> Iterator[str]:
        import json

        from app.llm.providers.base import strip_thinking_stream

        payload = self._payload(
            messages,
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        def _raw_chunks():
            client = self._http(config)
            with client.stream(
                "POST", self._url(config), json=payload, headers=self._headers(config)
            ) as resp:
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", errors="replace")
                    raise LLMUnavailable(
                        f"HTTP {resp.status_code}: {body[:400]}", status_code=resp.status_code
                    )
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    try:
                        event = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "content_block_delta":
                        delta = (event.get("delta") or {}).get("text")
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


__all__ = ["ANTHROPIC_VERSION", "AnthropicMessagesProvider"]
