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
from app.llm.types import LLMUnavailable, Message

log = get_logger(__name__)


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
        **_: Any,
    ) -> tuple[str, dict]:
        payload = self._payload(
            messages,
            model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
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
                    if chunk == "[DONE]":
                        break
                    import json

                    try:
                        parsed = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue  # keep-alive or partial frame
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
