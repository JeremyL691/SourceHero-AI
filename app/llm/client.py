"""The LLM client: budget -> cache -> call -> validate -> log.

Every business caller goes through here, and every caller must catch `LLMError` and
fall back to its heuristic. The client never raises into a request handler by design:
an unreachable provider should degrade the product, not break it.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.llm.credentials.sources import make_token_source
from app.llm.providers.base import ProviderConfig, estimate_tokens, extract_json
from app.llm.providers.fake import FakeProvider, get_fake_provider
from app.llm.router import effective_llm_settings
from app.llm.types import (
    BudgetExceededError,
    LLMError,
    LLMRequest,
    LLMResult,
    LLMUnavailable,
    Message,
    StructuredOutputError,
)

log = get_logger(__name__)

RETRY_BACKOFF = (0.5, 1.5, 3.0)


def cache_key(req: LLMRequest, provider: str, model: str) -> str:
    """Everything that can change the answer, and nothing else.

    `context` is deliberately excluded: it is the caller's framing of the same input,
    not part of the prompt that was sent.
    """
    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "task": req.task,
            "prompt_version": req.prompt_version,
            "schema": req.schema.__name__ if req.schema else None,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LLMClient:
    def __init__(self, settings: Settings | None = None, db: Session | None = None) -> None:
        self.settings = settings or get_settings()
        self.db = db
        self.effective = effective_llm_settings(db, self.settings)
        self._provider = self._build_provider()
        self._semaphore = threading.BoundedSemaphore(max(1, self.settings.llm_max_concurrency))
        self._cache_enabled = self.effective.provider != "fake"

    # --- construction ------------------------------------------------------

    def _build_provider(self):
        name = self.effective.provider
        if name == "fake":
            return get_fake_provider()
        if name == "openai_chat":
            from app.llm.providers.openai_chat import OpenAIChatProvider

            return OpenAIChatProvider()
        if name == "openai_responses":
            from app.llm.providers.openai_responses import OpenAIResponsesProvider

            return OpenAIResponsesProvider()
        if name == "anthropic_messages":
            from app.llm.providers.anthropic_messages import AnthropicMessagesProvider

            return AnthropicMessagesProvider()
        raise LLMUnavailable(f"unknown provider {name!r}", code="bad_provider")

    @property
    def mode(self) -> str:
        return "fake" if self.effective.provider == "fake" else "live"

    def _model_for(self, req: LLMRequest) -> str:
        return req.model_for_tier(self.effective)

    def model_for(self, req: LLMRequest) -> str:
        """The model this request would use. Public so the settings page can name it."""
        return self._model_for(req)

    def _config(self) -> ProviderConfig:
        return ProviderConfig(
            base_url=self.effective.base_url,
            api_key=self.settings.llm_api_key,
            model_fast=self.effective.model_fast,
            model_smart=self.effective.model_smart,
            auth_header=self.effective.auth_header,
            json_mode=self.effective.json_mode,
            timeout=self.effective.timeout,
            extra_headers=self.effective.extra_headers,
            token_source=make_token_source(self.settings, self.db),
        )

    # --- budget and cache --------------------------------------------------

    def _tokens_used_today(self, user_id: uuid.UUID | None) -> int:
        """Sum of today's calls. `user_id=None` means the instance-wide budget."""
        if self.db is None:
            return 0
        from sqlalchemy import func, select

        from app.models.llm import LLMRun

        start_of_day = clock.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = select(func.coalesce(func.sum(LLMRun.prompt_tokens + LLMRun.completion_tokens), 0)).where(
            LLMRun.created_at >= start_of_day
        )
        if user_id is not None:
            stmt = stmt.where(LLMRun.user_id == user_id)
        return int(db_scalar(self.db, stmt) or 0)

    def check_budget(self, user_id: uuid.UUID | None) -> None:
        budget = self.effective.daily_token_budget
        if budget <= 0:
            return
        used = self._tokens_used_today(user_id)
        if used >= budget:
            raise BudgetExceededError(
                f"daily token budget exhausted ({used}/{budget}); raise it in Settings or wait for tomorrow",
                code="budget_exhausted",
            )

    def _cache_get(self, key: str) -> str | None:
        # Disabled wholesale for the fake provider: heuristic code changes between
        # runs, and serving a stale cached answer would mask that change.
        if not self._cache_enabled or self.db is None:
            return None
        from sqlalchemy import select

        from app.models.llm import LLMCache

        row = self.db.execute(select(LLMCache).where(LLMCache.key == key)).scalar_one_or_none()
        return row.response.get("text") if row is not None else None

    def _cache_put(self, key: str, task: str, model: str, text: str) -> None:
        if not self._cache_enabled or self.db is None:
            return
        from sqlalchemy import select

        from app.models.llm import LLMCache

        row = self.db.execute(select(LLMCache).where(LLMCache.key == key)).scalar_one_or_none()
        if row is None:
            self.db.add(LLMCache(key=key, task=task, model=model, response={"text": text}))
        else:
            row.response = {"text": text}
        self.db.commit()

    # --- logging -----------------------------------------------------------

    def _log(
        self,
        *,
        task: str,
        provider: str,
        model: str,
        user_id: uuid.UUID | None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
        cache_hit: bool = False,
        status: str = "ok",
        error: str | None = None,
    ) -> None:
        if self.db is None:
            return
        from app.models.llm import LLMRun

        self.db.add(
            LLMRun(
                user_id=user_id,
                task=task,
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                cache_hit=cache_hit,
                status=status,
                error=error[:2000] if error else None,
            )
        )
        self.db.commit()

    # --- the main path -----------------------------------------------------

    def complete(self, req: LLMRequest) -> LLMResult:
        """Budget, cache, call with retries, validate, log. Raises `LLMError`."""
        model = self._model_for(req)
        started = time.perf_counter()

        if req.cache:
            cached = self._cache_get(cache_key(req, self.effective.provider, model))
            if cached is not None:
                return LLMResult(
                    text=cached,
                    parsed=_validate(req.schema, cached, attempts=0),
                    provider=self.effective.provider,
                    model=model,
                    cache_hit=True,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )

        try:
            self.check_budget(req.user_id)
        except BudgetExceededError as exc:
            self._log(
                task=req.task,
                provider=self.effective.provider,
                model=model,
                user_id=req.user_id,
                status="budget",
                error=str(exc),
            )
            raise

        with self._semaphore:
            text, usage = self._call_with_retries(req, model)

        try:
            parsed = _validate(req.schema, text, attempts=1)
        except StructuredOutputError:
            # One repair round-trip: tell the model exactly what was wrong.
            text, usage = self._call_with_retries(req, model, repair_of=text)
            parsed = _validate(req.schema, text, attempts=2)

        if req.cache:
            self._cache_put(cache_key(req, self.effective.provider, model), req.task, model, text)

        self._log(
            task=req.task,
            provider=self.effective.provider,
            model=model,
            user_id=req.user_id,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return LLMResult(
            text=text,
            parsed=parsed,
            provider=self.effective.provider,
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def _call_with_retries(
        self, req: LLMRequest, model: str, *, repair_of: str | None = None
    ) -> tuple[str, dict]:
        messages = list(req.messages)
        if repair_of is not None:
            messages = [
                *messages,
                Message("assistant", repair_of[:2000]),
                Message(
                    "user",
                    "That reply was not valid. Return ONLY the JSON object, "
                    "with no prose and no code fences.",
                ),
            ]

        last_error: Exception | None = None
        for attempt in range(len(RETRY_BACKOFF) + 1):
            try:
                return self._provider.complete(
                    messages,
                    model=model,
                    config=self._config(),
                    temperature=req.temperature,
                    max_tokens=req.max_tokens,
                    json_mode=req.schema is not None and self.effective.json_mode != "prompt",
                    task=req.task,
                    context=req.context,
                    schema=req.schema,
                )
            except LLMUnavailable as exc:
                last_error = exc
                # A rejected credential is worth one forced refresh before giving up.
                if "401" in str(exc) and attempt == 0:
                    token_source = make_token_source(self.settings, self.db)
                    if token_source is not None:
                        token_source.invalidate()
                    continue
                if attempt < len(RETRY_BACKOFF):
                    time.sleep(RETRY_BACKOFF[attempt])
                    continue
                self._log(
                    task=req.task,
                    provider=self.effective.provider,
                    model=model,
                    user_id=req.user_id,
                    status="error",
                    error=str(exc),
                )
                raise
            except Exception as exc:  # noqa: BLE001 - normalize into LLMError
                self._log(
                    task=req.task,
                    provider=self.effective.provider,
                    model=model,
                    user_id=req.user_id,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc

        raise LLMUnavailable(str(last_error) if last_error else "call failed")

    def stream(self, req: LLMRequest) -> Iterator[str]:
        """Yield text deltas. Usage is logged once the stream is exhausted."""
        model = self._model_for(req)
        self.check_budget(req.user_id)
        started = time.perf_counter()
        produced = 0
        try:
            with self._semaphore:
                for delta in self._provider.stream(
                    req.messages,
                    model=model,
                    config=self._config(),
                    temperature=req.temperature,
                    max_tokens=req.max_tokens,
                    json_mode=False,
                    task=req.task,
                    context=req.context,
                ):
                    produced += len(delta)
                    yield delta
        except LLMError:
            self._log(
                task=req.task,
                provider=self.effective.provider,
                model=model,
                user_id=req.user_id,
                status="error",
            )
            raise
        except Exception as exc:  # noqa: BLE001
            self._log(
                task=req.task,
                provider=self.effective.provider,
                model=model,
                user_id=req.user_id,
                status="error",
                error=str(exc),
            )
            raise LLMUnavailable(str(exc)) from exc
        self._log(
            task=req.task,
            provider=self.effective.provider,
            model=model,
            user_id=req.user_id,
            completion_tokens=estimate_tokens("x" * produced),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def list_models(self) -> list[str]:
        return self._provider.list_models(self._config())


def _validate(schema, text: str, *, attempts: int):
    if schema is None:
        return None
    try:
        return schema.model_validate(extract_json(text))
    except StructuredOutputError:
        raise
    except Exception as exc:  # noqa: BLE001 - pydantic validation error
        raise StructuredOutputError(
            f"response did not validate against {schema.__name__} after {attempts} attempt(s): {exc}"
        ) from exc


def db_scalar(db, stmt):
    return db.execute(stmt).scalar()


_client: LLMClient | None = None
_client_lock = threading.Lock()


def get_llm(db: Session | None = None) -> LLMClient:
    """The process client. Call `invalidate_llm()` after a settings change."""
    global _client
    with _client_lock:
        if _client is None:
            _client = LLMClient(db=db)
        elif db is not None:
            _client.db = db
        return _client


def invalidate_llm() -> None:
    global _client
    with _client_lock:
        _client = None


def reset_llm() -> None:
    invalidate_llm()
    get_fake_provider().clear_script()


__all__ = [
    "FakeProvider",
    "LLMClient",
    "cache_key",
    "get_llm",
    "invalidate_llm",
    "reset_llm",
]
