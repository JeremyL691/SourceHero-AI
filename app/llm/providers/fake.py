"""The fake provider.

This is what makes the product usable with no LLM credentials at all. With
`LLM_PROVIDER=fake` the *entire* live code path runs -- prompt rendering, the client,
schema validation, quote alignment, caching, budgeting -- and only the final content
originates from the heuristic implementations instead of a model. So development,
tests, e2e and the demo all exercise the same machinery a configured deployment does.

Two ways to get output:

* `script(task, [responses])` -- FIFO canned responses, for tests that need to drive
  parse failures, repair retries or bad citations.
* `FAKE_DEFAULTS[task](request.context)` -- the real heuristic, called with the
  structured inputs the caller put in `context`. Business code must therefore fill
  `context` in properly; the fake never parses a rendered prompt back out.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.core.logging import get_logger
from app.llm.providers.base import ProviderConfig, estimate_tokens
from app.llm.types import Message

log = get_logger(__name__)

STREAM_CHUNK = 24

# task -> callable(request.context) -> dict | str
FAKE_DEFAULTS: dict[str, Callable[[dict], Any]] = {}


def register_default(task: str) -> Callable[[Callable[[dict], Any]], Callable[[dict], Any]]:
    """Decorator: register the heuristic the fake runs for `task`."""

    def wrap(fn: Callable[[dict], Any]) -> Callable[[dict], Any]:
        FAKE_DEFAULTS[task] = fn
        return fn

    return wrap


class FakeProvider:
    """No network. Returns heuristic output, or whatever a test scripted."""

    name = "fake"

    def __init__(self) -> None:
        self._scripts: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def script(self, task: str, responses: list[str]) -> None:
        """Queue canned responses for `task`, returned in order."""
        with self._lock:
            self._scripts[task] = list(responses)

    def clear_script(self, task: str | None = None) -> None:
        with self._lock:
            if task is None:
                self._scripts.clear()
            else:
                self._scripts.pop(task, None)

    def scripted(self, task: str) -> str | None:
        """Pop the next scripted response, or None once the script is exhausted."""
        with self._lock:
            queue = self._scripts.get(task)
            if not queue:
                return None
            return queue.pop(0)

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        config: ProviderConfig,
        task: str = "unknown",
        context: dict | None = None,
        schema: type[BaseModel] | None = None,
        **_: Any,
    ) -> tuple[str, dict]:
        text = self.text_for(task=task, context=context or {}, schema=schema)
        return text, {
            "prompt_tokens": estimate_tokens("\n".join(m.content for m in messages)),
            "completion_tokens": estimate_tokens(text),
        }

    def text_for(self, *, task: str, context: dict, schema: type[BaseModel] | None = None) -> str:
        """The fake's answer for one call: scripted text, or the registered heuristic."""
        scripted = self.scripted(task)
        if scripted is not None:
            return scripted

        default = FAKE_DEFAULTS.get(task)
        if default is None:
            # No heuristic registered for this task yet. Saying so is better than
            # returning empty text that would look like "the model found nothing".
            log.debug("fake provider has no default for task %r", task)
            return json.dumps({"skipped": f"no fake default registered for task {task!r}"})
        result = default(context)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)

    def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        config: ProviderConfig,
        task: str = "unknown",
        context: dict | None = None,
        schema: type[BaseModel] | None = None,
        **_: Any,
    ):
        """Yield the text in small slices, so consumers exercise incremental paths."""
        text = self.text_for(task=task, context=context or {}, schema=schema)
        for i in range(0, len(text), STREAM_CHUNK):
            yield text[i : i + STREAM_CHUNK]

    def list_models(self, config: ProviderConfig) -> list[str]:
        return ["fake-smart", "fake-fast"]


_default_provider = FakeProvider()


def get_fake_provider() -> FakeProvider:
    """The process-wide instance, so `script()` from a test reaches the client."""
    return _default_provider


def reset_fake_provider() -> None:
    _default_provider.clear_script()


__all__ = [
    "FAKE_DEFAULTS",
    "FakeProvider",
    "get_fake_provider",
    "register_default",
    "reset_fake_provider",
]


@register_default("connection_test")
def _connection_test(context: dict) -> str:
    """What the fake provider answers to the settings page's "Test connection".

    A real "OK" rather than the no-default notice: the whole point of that button is
    to tell the operator the wiring works, and "no fake default registered" reads as
    a failure even when everything is fine.
    """
    return "OK"
