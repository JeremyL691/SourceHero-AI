"""OpenAI Responses API (`POST {base}/responses`).

Different from Chat Completions in three ways that matter: `instructions` carries the
system prompt, usage lives under `input_tokens`/`output_tokens`, and streaming
delivers typed events rather than `data:` lines.
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


class OpenAIResponsesProvider(_BaseProvider):
    name = "openai_responses"

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
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        rest = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]

        payload: dict[str, Any] = {
            "model": model,
            "input": rest,
            "max_output_tokens": max_tokens,
            "stream": stream,
        }
        if system:
            payload["instructions"] = system
        from app.llm.providers.base import is_json_mode_disabled, is_temperature_disabled

        if not is_temperature_disabled(self.name, model):
            payload["temperature"] = temperature
        if json_mode and not is_json_mode_disabled(self.name, model):
            payload["text"] = {"format": {"type": "json_object"}}
        return payload

    def _url(self, config: ProviderConfig) -> str:
        return f"{config.base_url.rstrip('/')}/responses"

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
        else:  # pragma: no cover
            raise LLMUnavailable("could not satisfy the endpoint's parameter requirements")

        data = resp.json()
        text = _text_of(data)
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
        json_mode: bool = False,
        **_: Any,
    ) -> Iterator[str]:
        import json

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
                    try:
                        event = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    delta = event.get("delta")
                    if event.get("type") == "response.output_text.delta" and delta:
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


def _text_of(data: dict) -> str:
    """Prefer the `output_text` shorthand, else walk `output[].content[]`."""
    shorthand = data.get("output_text")
    if isinstance(shorthand, str) and shorthand:
        return shorthand
    parts: list[str] = []
    for item in data.get("output") or []:
        for block in item.get("content") or []:
            if block.get("type") == "output_text" and block.get("text"):
                parts.append(block["text"])
    return "".join(parts)


__all__ = ["OpenAIResponsesProvider"]
