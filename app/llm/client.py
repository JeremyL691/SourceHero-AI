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
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.llm.credentials.sources import make_token_source
from app.llm.providers.base import (
    ProviderConfig,
    estimate_tokens,
    extract_json,
    strip_thinking_stream,
)
from app.llm.providers.cassette import CassetteMissError
from app.llm.providers.fake import FakeProvider, get_fake_provider
from app.llm.router import EffectiveLLM, effective_llm_settings, enforce_free_models
from app.llm.types import (
    BudgetExceededError,
    LLMError,
    LLMRequest,
    LLMResult,
    LLMUnavailable,
    Message,
    QuotaExhaustedError,
    StructuredOutputError,
)

log = get_logger(__name__)

RETRY_BACKOFF = (0.5, 1.5, 3.0)

_process_semaphore: threading.BoundedSemaphore | None = None
_semaphore_lock = threading.Lock()


def _get_semaphore(max_concurrency: int) -> threading.BoundedSemaphore:
    global _process_semaphore
    with _semaphore_lock:
        if _process_semaphore is None:
            _process_semaphore = threading.BoundedSemaphore(max(1, max_concurrency))
        return _process_semaphore


def cache_key(req: LLMRequest, provider: str, model: str) -> str:
    """Everything that can change the answer, and nothing else.

    `context` is deliberately excluded: it is the caller's framing of the same input,
    not part of the prompt that was sent.
    """
    schema_hash = None
    if req.schema is not None:
        try:
            schema_json = json.dumps(req.schema.model_json_schema(), sort_keys=True)
            schema_hash = hashlib.sha256(schema_json.encode("utf-8")).hexdigest()
        except Exception:
            schema_hash = req.schema.__name__

    messages_payload = json.dumps(
        [{"role": m.role, "content": m.content} for m in req.messages],
        sort_keys=True,
        ensure_ascii=False,
    )
    messages_hash = hashlib.sha256(messages_payload.encode("utf-8")).hexdigest()

    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "task": req.task,
            "prompt_version": req.prompt_version,
            "temperature": req.temperature,
            "schema_hash": schema_hash,
            "messages_hash": messages_hash,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# A provider that answered "free tier spent for today" is not called again until the
# next UTC midnight. Process-local on purpose: a restart simply asks once more.
_quota_lock = threading.Lock()
_quota_until: dict[str, datetime] = {}


def _next_utc_midnight() -> datetime:
    now = clock.utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def _mark_quota_exhausted(key: str) -> None:
    until = _next_utc_midnight()
    with _quota_lock:
        _quota_until[key] = until
    log.warning("free-tier quota exhausted for %s; falling back until %s UTC", key, until.isoformat())


def _quota_exhausted_until(key: str) -> datetime | None:
    with _quota_lock:
        until = _quota_until.get(key)
        if until is None:
            return None
        if clock.utcnow() >= until:
            del _quota_until[key]
            return None
        return until


def reset_quota_state() -> None:
    with _quota_lock:
        _quota_until.clear()


def _failure_status(exc: BaseException) -> str:
    return "quota" if isinstance(exc, QuotaExhaustedError) else "error"


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


class LLMClient:
    def __init__(
        self,
        settings: Settings | None = None,
        db: Session | None = None,
        user_id: uuid.UUID | None = None,
        effective: EffectiveLLM | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.db = db
        self.user_id = user_id
        self.effective = effective if effective is not None else effective_llm_settings(db, self.settings)
        enforce_free_models(self.effective, allow_paid=self.settings.llm_allow_paid)
        self._provider = self._build_provider()
        self._semaphore = _get_semaphore(self.effective.max_concurrency)
        self._cache_enabled = self.effective.provider != "fake"

    # --- construction ------------------------------------------------------

    def _build_provider(self):
        provider = self._raw_provider()
        mode = self.settings.llm_cassette_mode
        if mode == "off":
            return provider
        if not self.settings.llm_cassette_path:
            raise ValueError("LLM_CASSETTE_PATH must be set when LLM_CASSETTE_MODE is replay or record")
        from pathlib import Path

        from app.llm.providers.cassette import CassetteProvider

        return CassetteProvider(provider, Path(self.settings.llm_cassette_path), mode)

    def _raw_provider(self):
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

    @property
    def provider_name(self) -> str:
        return self.effective.provider

    def _model_for(self, req: LLMRequest) -> str:
        return req.model_for_tier(self.effective)

    def model_for(self, req: LLMRequest) -> str:
        """The model this request would use. Public so the settings page can name it."""
        return self._model_for(req)

    def _config(self) -> ProviderConfig:
        api_key = self.settings.llm_api_key
        if not api_key and self.effective.preset == "openrouter":
            api_key = self.settings.openrouter_api_key
        return ProviderConfig(
            base_url=self.effective.base_url,
            api_key=api_key,
            model_fast=self.effective.model_fast,
            model_smart=self.effective.model_smart,
            auth_header=self.effective.auth_header,
            json_mode=self.effective.json_mode,
            timeout=self.effective.timeout,
            extra_headers=self.effective.extra_headers,
            token_source=make_token_source(self.settings, self.db, effective=self.effective),
        )

    # --- budget and cache --------------------------------------------------

    def _tokens_used_today(self, user_id: uuid.UUID | None) -> int:
        """Sum of today's calls. Filtered by user_id if provided."""
        if self.db is None:
            return 0
        from app.models.llm import LLMRun

        start_of_day = clock.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = select(func.coalesce(func.sum(LLMRun.prompt_tokens + LLMRun.completion_tokens), 0)).where(
            LLMRun.created_at >= start_of_day
        )
        if user_id is not None:
            stmt = stmt.where(LLMRun.user_id == user_id)

        try:
            return int(self.db.scalar(stmt) or 0)
        except Exception as exc:
            log.warning("failed to query tokens used today: %s", exc)
            return 0

    def check_budget(self, user_id: uuid.UUID | None = None) -> None:
        if self.db is None:
            return
        target_user = user_id if user_id is not None else self.user_id
        budget = self.effective.daily_token_budget
        if budget <= 0:
            return
        used = self._tokens_used_today(target_user)
        if used >= budget:
            raise BudgetExceededError(
                f"daily token budget exhausted ({used}/{budget}); raise it in Settings or wait for tomorrow",
                code="budget_exhausted",
            )

    def _cache_get(self, key: str) -> str | None:
        if not self._cache_enabled or self.db is None:
            return None
        from app.core.db import SessionLocal
        from app.models.llm import LLMCache

        try:
            with SessionLocal() as s:
                row = s.execute(select(LLMCache).where(LLMCache.key == key)).scalar_one_or_none()
                return row.response.get("text") if row is not None else None
        except Exception as exc:
            log.warning("failed to get LLM cache: %s", exc)
            return None

    def _cache_put(self, key: str, task: str, model: str, text: str) -> None:
        if not self._cache_enabled or self.db is None:
            return
        from app.core.db import SessionLocal
        from app.models.llm import LLMCache

        try:
            with SessionLocal() as s:
                row = s.execute(select(LLMCache).where(LLMCache.key == key)).scalar_one_or_none()
                if row is None:
                    s.add(LLMCache(key=key, task=task, model=model, response={"text": text}))
                else:
                    row.response = {"text": text}
                s.commit()
        except Exception as exc:
            log.warning("failed to put LLM cache: %s", exc)

    def _cache_delete(self, key: str) -> None:
        if not self._cache_enabled or self.db is None:
            return
        from app.core.db import SessionLocal
        from app.models.llm import LLMCache

        try:
            with SessionLocal() as s:
                s.execute(delete(LLMCache).where(LLMCache.key == key))
                s.commit()
        except Exception as exc:
            log.warning("failed to delete LLM cache: %s", exc)

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
        from app.core.db import SessionLocal
        from app.models.llm import LLMRun

        try:
            with SessionLocal() as s:
                s.add(
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
                s.commit()
        except Exception as exc:
            log.warning("failed to log LLM run: %s", exc)

    # --- reservations: the budget check and the row are one critical section ----

    def _quota_key(self) -> str:
        return self.effective.base_url or self.effective.provider

    def _raise_if_quota_exhausted(self) -> None:
        until = _quota_exhausted_until(self._quota_key())
        if until is not None:
            raise QuotaExhaustedError(
                f"free-tier quota for {self.effective.provider} is spent until {until:%Y-%m-%d %H:%M} UTC"
            )

    def _requests_today(self) -> int:
        from app.models.llm import LLMRun

        start_of_day = clock.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = (
            select(func.count())
            .select_from(LLMRun)
            .where(LLMRun.created_at >= start_of_day, LLMRun.status != "budget")
        )
        return int(self.db.scalar(stmt) or 0)

    def _check_request_budget(self) -> None:
        budget = self.effective.request_budget
        if budget <= 0 or self.db is None:
            return
        used = self._requests_today()
        if used >= budget:
            raise BudgetExceededError(
                f"daily LLM request budget exhausted ({used}/{budget}); raise it in Settings or wait for tomorrow",
                code="budget_exhausted",
            )

    def _reserve(self, *, task: str, model: str, user_id: uuid.UUID | None) -> int | None:
        """Check today's request and token budgets, then write a `pending` row, atomically.

        One advisory lock serializes reservations across the whole database, so two callers
        cannot both see the last free slot. The counts are read through the caller's session
        (it sees its own rows, as before); the row is committed on a fresh connection, which
        releases the lock. Returns the row id to finish, or None when there is no database.
        """
        if self.db is None:
            return None
        from sqlalchemy import text

        from app.core.db import SessionLocal
        from app.models.llm import LLMRun

        try:
            with SessionLocal() as s:
                s.execute(text("SELECT pg_advisory_xact_lock(hashtext('llm_budget'))"))
                self._check_request_budget()
                self.check_budget(user_id)
                row = LLMRun(
                    user_id=user_id,
                    task=task,
                    provider=self.effective.provider,
                    model=model,
                    status="pending",
                )
                s.add(row)
                s.flush()
                row_id = int(row.id)
                s.commit()
                return row_id
        except BudgetExceededError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed; callers fall back to heuristics
            raise LLMUnavailable(f"could not reserve an LLM request: {exc}") from exc

    def _finish(
        self,
        row_id: int | None,
        *,
        status: str,
        model: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
        error: str | None = None,
    ) -> None:
        if row_id is None:
            return
        from app.core.db import SessionLocal
        from app.models.llm import LLMRun

        values: dict = {
            "status": status,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": latency_ms,
            "error": error[:2000] if error else None,
        }
        if model:
            values["model"] = model
        try:
            with SessionLocal() as s:
                s.execute(update(LLMRun).where(LLMRun.id == row_id).values(**values))
                s.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to finish LLM run: %s", exc)

    # --- the main path -----------------------------------------------------

    def complete(self, req: LLMRequest) -> LLMResult:
        """Budget, cache, call with retries, validate, log. Raises `LLMError`."""
        model = self._model_for(req)
        started = time.perf_counter()
        target_user_id = req.user_id if req.user_id is not None else self.user_id

        if req.cache:
            ckey = cache_key(req, self.effective.provider, model)
            cached = self._cache_get(ckey)
            if cached is not None:
                try:
                    parsed = _validate(req.schema, cached, attempts=0)
                    return LLMResult(
                        text=cached,
                        parsed=parsed,
                        provider=self.effective.provider,
                        model=model,
                        cache_hit=True,
                        latency_ms=_elapsed_ms(started),
                    )
                except StructuredOutputError:
                    log.warning("cached response for %s failed validation, evicting", ckey)
                    self._cache_delete(ckey)

        self._raise_if_quota_exhausted()
        try:
            row_id = self._reserve(task=req.task, model=model, user_id=target_user_id)
        except BudgetExceededError as exc:
            self._log(
                task=req.task,
                provider=self.effective.provider,
                model=model,
                user_id=target_user_id,
                status="budget",
                error=str(exc),
            )
            raise

        prompt_tokens = completion_tokens = 0
        used_model = model
        try:
            with self._semaphore:
                text, usage = self._call_with_retries(req, model, user_id=target_user_id)
            used_model = str(usage.get("model") or model)
            prompt_tokens = int(usage.get("prompt_tokens", 0))
            completion_tokens = int(usage.get("completion_tokens", 0))

            try:
                parsed = _validate(req.schema, text, attempts=1)
            except StructuredOutputError:
                # One repair round-trip: tell the model exactly what was wrong.
                text, repair_usage = self._call_with_retries(
                    req, used_model, repair_of=text, user_id=target_user_id
                )
                used_model = str(repair_usage.get("model") or used_model)
                prompt_tokens += int(repair_usage.get("prompt_tokens", 0))
                completion_tokens += int(repair_usage.get("completion_tokens", 0))
                parsed = _validate(req.schema, text, attempts=2)
        except BaseException as exc:  # every exit must finish the reserved row, then re-raise
            self._finish(
                row_id,
                status=_failure_status(exc),
                model=used_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=_elapsed_ms(started),
                error=str(exc),
            )
            raise

        if req.cache:
            self._cache_put(cache_key(req, self.effective.provider, model), req.task, model, text)

        latency_ms = _elapsed_ms(started)
        self._finish(
            row_id,
            status="ok",
            model=used_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
        return LLMResult(
            text=text,
            parsed=parsed,
            provider=self.effective.provider,
            model=used_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )

    def _call_with_retries(
        self,
        req: LLMRequest,
        model: str,
        *,
        repair_of: str | None = None,
        user_id: uuid.UUID | None = None,
    ) -> tuple[str, dict]:
        """Call the provider with retries. Returns `(text, usage)`; usage carries the model used."""
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

        # Free-tier fallbacks, used in order when the current model is retired (404/410)
        # or has failed three times in a row with a 5xx.
        fallbacks = list(self.effective.fallback_models)
        current = model
        consecutive_5xx = 0
        last_error: Exception | None = None
        for attempt in range(len(RETRY_BACKOFF) + 1):
            try:
                text, usage = self._provider.complete(
                    messages,
                    model=current,
                    config=self._config(),
                    temperature=req.temperature,
                    max_tokens=req.max_tokens,
                    json_mode=req.schema is not None and self.effective.json_mode != "prompt",
                    task=req.task,
                    context=req.context,
                    schema=req.schema,
                    extra_body=self.effective.extra_body,
                )
                return text, {**usage, "model": current}
            except QuotaExhaustedError:
                _mark_quota_exhausted(self._quota_key())
                raise  # final for today: never retried, never switched to a fallback model
            except LLMUnavailable as exc:
                last_error = exc
                status_code = getattr(exc, "status_code", None)
                if status_code is not None and 500 <= status_code < 600:
                    consecutive_5xx += 1
                else:
                    consecutive_5xx = 0
                if status_code == 401 and attempt == 0:
                    token_source = self._config().token_source
                    if token_source is not None:
                        token_source.invalidate()
                    continue
                if fallbacks and (status_code in (404, 410) or consecutive_5xx >= 3):
                    next_model = fallbacks.pop(0)
                    log.warning(
                        "LLM model %s returned HTTP %s; falling back to %s",
                        current,
                        status_code,
                        next_model,
                    )
                    current, consecutive_5xx = next_model, 0
                    continue
                if (
                    status_code is not None
                    and 400 <= status_code < 500
                    and status_code not in (408, 409, 429)
                ):
                    raise
                if attempt < len(RETRY_BACKOFF):
                    time.sleep(RETRY_BACKOFF[attempt])
                    continue
                raise
            except CassetteMissError:
                raise  # a stale cassette must fail loudly, never become a silent fallback (K1.4)
            except Exception as exc:  # noqa: BLE001 - normalize into LLMError
                raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc

        raise LLMUnavailable(str(last_error) if last_error else "call failed")

    def stream(self, req: LLMRequest) -> Iterator[str]:
        """Yield text deltas. The call's row is finished when the stream ends, however it ends."""
        model = self._model_for(req)
        target_user_id = req.user_id if req.user_id is not None else self.user_id
        self._raise_if_quota_exhausted()
        row_id = self._reserve(task=req.task, model=model, user_id=target_user_id)
        started = time.perf_counter()
        produced = 0
        usage: dict[str, int] = {}
        outcome, error = "error", "stream closed before completion"

        def _raw():
            nonlocal produced
            for delta in self._provider.stream(
                req.messages,
                model=model,
                config=self._config(),
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                json_mode=False,
                task=req.task,
                context=req.context,
                schema=req.schema,
                extra_body=self.effective.extra_body,
                usage_out=usage,
            ):
                produced += len(delta)
                yield delta

        try:
            with self._semaphore:
                yield from strip_thinking_stream(_raw())
            outcome, error = "ok", None
        except LLMError as exc:
            outcome, error = _failure_status(exc), str(exc)
            if isinstance(exc, QuotaExhaustedError):
                _mark_quota_exhausted(self._quota_key())
            raise
        except CassetteMissError:
            outcome, error = "error", "cassette miss"
            raise  # K1.4: a miss must not become a silent fallback
        except Exception as exc:  # noqa: BLE001
            outcome, error = "error", str(exc)
            raise LLMUnavailable(str(exc)) from exc
        finally:
            if "prompt_tokens" in usage:
                prompt_tokens = usage["prompt_tokens"]
            else:
                # The endpoint reported no usage: estimate from the characters sent, and say so.
                prompt_tokens = estimate_tokens("".join(m.content for m in req.messages))
                log.info(
                    "llm usage estimated=true task=%s provider=%s prompt_tokens=%d",
                    req.task,
                    self.effective.provider,
                    prompt_tokens,
                )
            self._finish(
                row_id,
                status=outcome,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=estimate_tokens("x" * produced),
                latency_ms=_elapsed_ms(started),
                error=error,
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


def get_llm(
    db: Session | None = None,
    *,
    user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> LLMClient:
    """Get an LLMClient with fresh effective settings and credentials.

    No session is held on a process singleton.
    """
    settings = settings or get_settings()
    effective = effective_llm_settings(db, settings)
    return LLMClient(settings=settings, db=db, user_id=user_id, effective=effective)


def invalidate_llm() -> None:
    from app.llm.prompts import reset_cache
    from app.llm.providers.base import reset_degradation_memory
    from app.llm.router import bump_settings_version

    bump_settings_version()
    reset_degradation_memory()
    reset_cache()


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
    "reset_quota_state",
]
