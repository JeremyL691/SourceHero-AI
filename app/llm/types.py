"""Request/response types for the LLM layer.

Every business caller must catch `LLMError` and fall back to its heuristic, recording
`heuristic(fallback:<code>)` in the result's `method`. A live LLM that errors must
degrade to the heuristic path, never fail the request.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

Tier = Literal["fast", "smart"]
ProviderName = Literal["fake", "openai_chat", "openai_responses", "anthropic_messages"]


class LLMError(Exception):
    """Base for every LLM failure. Callers fall back to heuristics on this."""

    code = "llm_error"

    def __init__(self, message: str = "", *, code: str | None = None):
        super().__init__(message or (code or self.code))
        self.message = message
        if code:
            self.code = code


class LLMUnavailable(LLMError):
    """The provider could not be reached, or refused the credentials."""

    code = "unavailable"


class StructuredOutputError(LLMError):
    """The reply did not validate against the requested schema, even after a repair."""

    code = "invalid_output"


class BudgetExceededError(LLMError):
    """The daily token budget is spent. Distinct from LLMError so callers can surface it."""

    code = "budget"


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class LLMRequest:
    task: str
    messages: list[Message]
    tier: Tier = "fast"
    schema: type[BaseModel] | None = None
    temperature: float = 0.0
    max_tokens: int = 2000
    prompt_version: str = "v1"
    cache: bool = True
    user_id: uuid.UUID | None = None
    # Structured inputs (the chunk, the claim pairs, ...).
    #
    # Deliberately NOT part of the cache key: the same text must hit the same cache
    # entry regardless of how the caller framed the call. `FakeProvider` reads it to
    # run the matching heuristic instead of parsing a rendered prompt back out.
    context: dict = field(default_factory=dict)

    def model_for_tier(self, effective) -> str:
        """Resolve the model for this request's tier.

        Accepts either a `Settings` or an `EffectiveLLM` (which is what the client
        actually holds, since presets and the database can override the env).
        """
        smart = getattr(effective, "model_smart", None) or getattr(effective, "llm_model_smart", "")
        fast = getattr(effective, "model_fast", None) or getattr(effective, "llm_model_fast", "")
        return (smart if self.tier == "smart" else fast) or "default"


@dataclass
class LLMResult:
    text: str
    parsed: BaseModel | None
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    cache_hit: bool = False


__all__ = [
    "BudgetExceededError",
    "LLMError",
    "LLMRequest",
    "LLMResult",
    "LLMUnavailable",
    "Message",
    "ProviderName",
    "StructuredOutputError",
    "Tier",
]
