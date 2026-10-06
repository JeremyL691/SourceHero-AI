"""M6.8: the LLM layer. Every test here is offline; `respx` intercepts all HTTP."""

from __future__ import annotations

import json
import uuid

import pytest
import respx
from pydantic import BaseModel

from app.core.config import Settings
from app.llm.client import LLMClient, cache_key
from app.llm.credentials.oauth_pkce import _challenge, complete_authorization, start_authorization
from app.llm.credentials.sources import CommandTokenSource, OAuthPKCETokenSource
from app.llm.credentials.store import (
    credential_status,
    delete_credential,
    load_credential,
    save_credential,
)
from app.llm.providers.base import extract_json, strip_reasoning
from app.llm.providers.fake import FakeProvider, get_fake_provider, register_default
from app.llm.types import (
    BudgetExceededError,
    LLMRequest,
    LLMUnavailable,
    Message,
    StructuredOutputError,
)
from app.models.llm import LLMRun, OAuthState, ProviderCredential

pytestmark = pytest.mark.db

BASE = "https://llm.test/v1"


class Fruit(BaseModel):
    name: str
    count: int


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        sourcehero_env="test",
        llm_provider="openai_chat",
        llm_base_url=BASE,
        llm_model_fast="fast-model",
        llm_model_smart="smart-model",
        llm_api_key="sk-test-key",
        llm_preset="",
        llm_daily_token_budget=0,
        llm_json_mode="auto",
    )
    base.update(overrides)
    return Settings(**base)


def _client(db=None, **overrides) -> LLMClient:
    return LLMClient(settings=_settings(**overrides), db=db)


# --- shared parsing helpers ---------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Sure! Here you go: {"a": 1} — hope that helps.', {"a": 1}),
        ('{"text": "a } brace inside a string"}', {"text": "a } brace inside a string"}),
    ],
)
def test_extract_json_tolerates_real_model_behaviour(raw, expected):
    assert extract_json(raw) == expected


def test_extract_json_rejects_nonsense():
    with pytest.raises(StructuredOutputError):
        extract_json("no json here at all")


def test_strip_reasoning_removes_think_blocks():
    assert strip_reasoning("<think>reasoning here</think>The answer.") == "The answer."
    assert strip_reasoning("plain") == "plain"


def test_cache_key_ignores_context_but_not_the_prompt():
    a = LLMRequest(task="t", messages=[Message("user", "x")], context={"k": 1})
    b = LLMRequest(task="t", messages=[Message("user", "x")], context={"k": 2})
    c = LLMRequest(task="t", messages=[Message("user", "y")])
    assert cache_key(a, "p", "m") == cache_key(b, "p", "m"), "context is not part of the prompt"
    assert cache_key(a, "p", "m") != cache_key(c, "p", "m")
    assert cache_key(a, "p", "m") != cache_key(a, "p2", "m")
    assert cache_key(a, "p", "m") != cache_key(a, "p", "m2")


# --- openai_chat ---------------------------------------------------------


@respx.mock
def test_chat_completions_non_streaming():
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx_json(
            {
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            }
        )
    )
    client = _client()
    result = client.complete(LLMRequest(task="t", messages=[Message("user", "hi")]))
    assert result.text == "hello"
    assert (result.prompt_tokens, result.completion_tokens) == (11, 3)
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "fast-model"
    assert body["stream"] is False


@respx.mock
def test_chat_completions_strips_reasoning():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx_json({"choices": [{"message": {"content": "<think>hmm</think>final"}}]})
    )
    result = _client().complete(LLMRequest(task="t", messages=[Message("user", "hi")]))
    assert result.text == "final"


@respx.mock
def test_chat_completions_streaming():
    frames = [
        '{"choices":[{"delta":{"content":"Hel"}}]}',
        '{"choices":[{"delta":{"content":"lo"}}]}',
        "data: [DONE]",
    ]
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx_sse(frames))
    client = _client()
    text = "".join(client.stream(LLMRequest(task="t", messages=[Message("user", "hi")])))
    assert text == "Hello"


@respx.mock
def test_structured_output_is_parsed():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx_json({"choices": [{"message": {"content": '{"name":"apple","count":3}'}}]})
    )
    result = _client().complete(LLMRequest(task="t", messages=[Message("user", "hi")], schema=Fruit))
    assert result.parsed.name == "apple"


@respx.mock
def test_structured_output_repairs_once_then_succeeds():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx_json({"choices": [{"message": {"content": "not json at all"}}]})
        return httpx_json({"choices": [{"message": {"content": '{"name":"pear","count":1}'}}]})

    respx.post(f"{BASE}/chat/completions").mock(side_effect=handler)
    result = _client().complete(LLMRequest(task="t", messages=[Message("user", "hi")], schema=Fruit))
    assert result.parsed.name == "pear"
    assert len(calls) == 2, "one repair round-trip, no more"
    assert "valid" in calls[1].content.decode().lower() or b"JSON" in calls[1].content


@respx.mock
def test_structured_output_gives_up_after_the_repair():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx_json({"choices": [{"message": {"content": "still not json"}}]})
    )
    with pytest.raises(StructuredOutputError):
        _client().complete(LLMRequest(task="t", messages=[Message("user", "hi")], schema=Fruit))


@respx.mock
def test_json_mode_is_dropped_when_the_endpoint_rejects_it():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx_json({"error": "response_format is not supported"}, status=400)
        return httpx_json({"choices": [{"message": {"content": '{"name":"fig","count":2}'}}]})

    respx.post(f"{BASE}/chat/completions").mock(side_effect=handler)
    result = _client().complete(LLMRequest(task="t", messages=[Message("user", "hi")], schema=Fruit))
    assert result.parsed.name == "fig"
    assert b"response_format" not in calls[1].content


@respx.mock
def test_temperature_is_dropped_when_the_endpoint_rejects_it():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx_json({"error": "temperature must be 1"}, status=400)
        return httpx_json({"choices": [{"message": {"content": "ok"}}]})

    respx.post(f"{BASE}/chat/completions").mock(side_effect=handler)
    result = _client().complete(LLMRequest(task="t", messages=[Message("user", "hi")], temperature=0.0))
    assert result.text == "ok"
    assert b"temperature" not in calls[1].content


@respx.mock
def test_429_is_retried():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx_json({"error": "rate limited"}, status=429)
        return httpx_json({"choices": [{"message": {"content": "recovered"}}]})

    respx.post(f"{BASE}/chat/completions").mock(side_effect=handler)
    result = _client().complete(LLMRequest(task="t", messages=[Message("user", "hi")]))
    assert result.text == "recovered"
    assert len(calls) == 3


@respx.mock
def test_401_invalidates_the_token_once_then_retries():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx_json({"error": "401 unauthorized"}, status=401)
        return httpx_json({"choices": [{"message": {"content": "after refresh"}}]})

    respx.post(f"{BASE}/chat/completions").mock(side_effect=handler)
    result = _client().complete(LLMRequest(task="t", messages=[Message("user", "hi")]))
    assert result.text == "after refresh"
    assert len(calls) == 2


@respx.mock
def test_persistent_failure_raises_llm_unavailable():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx_json({"error": "server exploded"}, status=500)
    )
    with pytest.raises(LLMUnavailable):
        _client().complete(LLMRequest(task="t", messages=[Message("user", "hi")]))


@respx.mock
def test_list_models():
    respx.get(f"{BASE}/models").mock(return_value=httpx_json({"data": [{"id": "a"}, {"id": "b"}]}))
    assert _client().list_models() == ["a", "b"]


# --- openai_responses ----------------------------------------------------


@respx.mock
def test_responses_non_streaming():
    route = respx.post(f"{BASE}/responses").mock(
        return_value=httpx_json(
            {
                "output_text": "short-circuit",
                "usage": {"input_tokens": 7, "output_tokens": 2},
            }
        )
    )
    client = _client(llm_provider="openai_responses")
    result = client.complete(LLMRequest(task="t", messages=[Message("system", "S"), Message("user", "U")]))
    assert result.text == "short-circuit"
    assert (result.prompt_tokens, result.completion_tokens) == (7, 2)
    body = json.loads(route.calls[0].request.content)
    # The system prompt moves out of `messages` and into `instructions`.
    assert body["instructions"] == "S"
    assert body["input"] == [{"role": "user", "content": "U"}]


@respx.mock
def test_responses_walks_output_blocks_when_output_text_is_absent():
    respx.post(f"{BASE}/responses").mock(
        return_value=httpx_json({"output": [{"content": [{"type": "output_text", "text": "from blocks"}]}]})
    )
    client = _client(llm_provider="openai_responses")
    result = client.complete(LLMRequest(task="t", messages=[Message("user", "U")]))
    assert result.text == "from blocks"


@respx.mock
def test_responses_streaming():
    frames = [
        '{"type":"response.output_text.delta","delta":"al"}',
        '{"type":"response.output_text.delta","delta":"pha"}',
        "data: [DONE]",
    ]
    respx.post(f"{BASE}/responses").mock(return_value=httpx_sse(frames))
    client = _client(llm_provider="openai_responses")
    text = "".join(client.stream(LLMRequest(task="t", messages=[Message("user", "U")])))
    assert text == "alpha"


# --- anthropic_messages --------------------------------------------------


@respx.mock
def test_anthropic_non_streaming():
    route = respx.post(f"{BASE}/messages").mock(
        return_value=httpx_json(
            {
                "content": [
                    {"type": "text", "text": "part one "},
                    {"type": "tool_use", "id": "x"},
                    {"type": "text", "text": "part two"},
                ],
                "usage": {"input_tokens": 5, "output_tokens": 4},
            }
        )
    )
    client = _client(llm_provider="anthropic_messages", llm_auth_header="x-api-key")
    result = client.complete(LLMRequest(task="t", messages=[Message("system", "SYS"), Message("user", "U")]))
    assert result.text == "part one part two"
    assert (result.prompt_tokens, result.completion_tokens) == (5, 4)
    request = route.calls[0].request
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request.headers["x-api-key"] == "sk-test-key"
    assert json.loads(request.content)["system"] == "SYS"


@respx.mock
def test_anthropic_streaming():
    frames = [
        '{"type":"content_block_delta","delta":{"text":"A"}}',
        '{"type":"content_block_delta","delta":{"text":"B"}}',
    ]
    respx.post(f"{BASE}/messages").mock(return_value=httpx_sse(frames))
    client = _client(llm_provider="anthropic_messages")
    text = "".join(client.stream(LLMRequest(task="t", messages=[Message("user", "U")])))
    assert text == "AB"


# --- cache, budget, logging ----------------------------------------------


@respx.mock
def test_cache_hit_does_not_call_the_provider(db):
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx_json({"choices": [{"message": {"content": "cached"}}]})
    )
    client = _client(db)
    req = LLMRequest(task="t", messages=[Message("user", "same")])
    assert client.complete(req).text == "cached"
    second = client.complete(req)
    assert second.cache_hit is True
    assert second.text == "cached"
    assert route.call_count == 1, "a cache hit must not reach the provider"


@respx.mock
def test_cache_is_off_for_the_fake_provider(db):
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx_json({"choices": [{"message": {"content": "x"}}]})
    )
    client = LLMClient(settings=_settings(llm_provider="fake", llm_base_url=""), db=db)
    assert client._cache_enabled is False, (
        "caching fake output would serve stale answers after a heuristic change"
    )


@respx.mock
def test_budget_exhaustion_raises_before_calling(db):
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx_json({"choices": [{"message": {"content": "x"}}]})
    )
    user_id = uuid.uuid4()
    db.add(
        LLMRun(
            user_id=user_id,
            task="t",
            provider="openai_chat",
            model="m",
            prompt_tokens=1000,
            completion_tokens=0,
            status="ok",
        )
    )
    db.commit()

    client = _client(db, llm_daily_token_budget=500)
    with pytest.raises(BudgetExceededError):
        client.complete(LLMRequest(task="t", messages=[Message("user", "x")], user_id=user_id))
    assert route.call_count == 0, "a budget refusal must not spend a call"


@respx.mock
def test_calls_are_logged(db):
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx_json(
            {
                "choices": [{"message": {"content": "x"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            }
        )
    )
    client = _client(db)
    user_id = uuid.uuid4()
    client.complete(LLMRequest(task="my_task", messages=[Message("user", "x")], user_id=user_id))
    rows = client.db.query(LLMRun).all()
    assert len(rows) == 1
    assert rows[0].task == "my_task"
    assert rows[0].user_id == user_id
    assert (rows[0].prompt_tokens, rows[0].completion_tokens) == (4, 2)
    assert rows[0].status == "ok"


# --- the fake provider ---------------------------------------------------


def test_fake_provider_runs_a_registered_heuristic():
    @register_default("test_task")
    def _default(context):
        return {"echoed": context.get("value")}

    provider = FakeProvider()
    text = provider.text_for(task="test_task", context={"value": 42})
    assert json.loads(text) == {"echoed": 42}


def test_fake_provider_script_takes_priority():
    @register_default("test_task_script")
    def _default(context):
        return {"from": "heuristic"}

    provider = FakeProvider()
    provider.script("test_task_script", ["scripted"])
    assert provider.text_for(task="test_task_script", context={}) == "scripted"
    # Exhausted script falls back to the heuristic.
    assert json.loads(provider.text_for(task="test_task_script", context={})) == {"from": "heuristic"}


def test_fake_provider_scripts_in_fifo_order():
    provider = FakeProvider()
    provider.script("t", ["one", "two"])
    assert provider.text_for(task="t", context={}) == "one"
    assert provider.text_for(task="t", context={}) == "two"


def test_fake_provider_reports_a_missing_default_instead_of_empty_text():
    text = get_fake_provider().text_for(task="never_registered", context={})
    assert "no fake default" in text


def test_fake_provider_streams_in_small_slices():
    provider = FakeProvider()
    provider.script("t", ["x" * 100])
    chunks = list(provider.stream([], model="m", config=None, task="t", context={}))
    assert "".join(chunks) == "x" * 100
    assert len(chunks) > 1, "streaming must yield incrementally, not one blob"


def test_fake_client_never_needs_a_network(db):
    """The fake runs the whole client path -- prompt, budget, cache, schema check."""

    @register_default("fake_smoke")
    def _default(context):
        return {"name": "apple", "count": 3}

    client = LLMClient(settings=_settings(llm_provider="fake", llm_base_url="", llm_model_fast="f"), db=db)
    assert client.mode == "fake"
    result = client.complete(LLMRequest(task="fake_smoke", messages=[Message("user", "q")], schema=Fruit))
    assert result.provider == "fake"
    assert result.parsed.name == "apple"


# --- credentials ---------------------------------------------------------


def test_credential_roundtrip_is_encrypted(db):
    save_credential(db, "opencode-go", "api_key", {"value": "sk-super-secret"})
    assert load_credential(db, "opencode-go", "api_key") == {"value": "sk-super-secret"}
    row = db.query(ProviderCredential).one()
    assert b"sk-super-secret" not in row.encrypted_payload, "the secret must not be stored in the clear"


def test_credential_is_replaced_not_accumulated(db):
    save_credential(db, "p", "api_key", {"value": "one"})
    save_credential(db, "p", "api_key", {"value": "two"})
    assert load_credential(db, "p", "api_key") == {"value": "two"}
    assert delete_credential(db, "p") == 1
    assert load_credential(db, "p", "api_key") is None


def test_credential_status_reports_presence_only(db):
    save_credential(db, "p", "api_key", {"value": "hidden"})
    status = credential_status(db, "p")
    assert status["has_api_key"] is True
    assert "value" not in status and "hidden" not in json.dumps(status)


def test_command_token_source_runs_and_caches():
    source = CommandTokenSource(command=f"{__import__('sys').executable} -c \"print('minted')\"")
    assert source.get_token() == "minted"
    assert source.get_token() == "minted", "cached within the TTL"
    source.invalidate()
    assert source.get_token() == "minted"


def test_command_token_source_handles_a_broken_command():
    assert CommandTokenSource(command="no-such-binary-xyz").get_token() is None


@respx.mock
def test_oauth_token_source_refreshes_before_expiry(db):
    from datetime import UTC, datetime, timedelta

    settings = _settings(llm_auth="oauth", oauth_token_url="https://oauth.test/token")
    save_credential(
        db,
        "custom",
        "oauth",
        {
            "access_token": "old",
            "refresh_token": "r1",
            "expires_at": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
    )
    route = respx.post("https://oauth.test/token").mock(
        return_value=httpx_json({"access_token": "new", "expires_in": 3600})
    )
    source = OAuthPKCETokenSource(db=db, provider="custom", settings=settings)
    assert source.get_token() == "new", "a token expiring inside the margin must refresh"
    assert route.call_count == 1
    assert load_credential(db, "custom", "oauth")["access_token"] == "new"


@respx.mock
def test_oauth_token_source_keeps_a_valid_token(db):
    from datetime import UTC, datetime, timedelta

    settings = _settings(llm_auth="oauth", oauth_token_url="https://oauth.test/token")
    save_credential(
        db,
        "custom",
        "oauth",
        {
            "access_token": "still-good",
            "refresh_token": "r1",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    route = respx.post("https://oauth.test/token").mock(return_value=httpx_json({}))
    source = OAuthPKCETokenSource(db=db, provider="custom", settings=settings)
    assert source.get_token() == "still-good"
    assert route.call_count == 0, "a valid token must not be refreshed"


def test_challenge_is_rfc7636_base64url_sha256():
    # The example verifier/challenge pair from RFC 7636 appendix B.
    assert _challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == (
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_oauth_start_requires_configuration(db):
    from app.core.errors import ConflictError

    with pytest.raises(ConflictError) as excinfo:
        start_authorization(db, _settings())
    assert "oauth_authorize_url" in excinfo.value.details["missing"]


def test_oauth_start_builds_a_correct_authorize_url(db):
    settings = _settings(
        oauth_authorize_url="https://oauth.test/authorize",
        oauth_token_url="https://oauth.test/token",
        oauth_client_id="cid",
        oauth_redirect_uri="http://127.0.0.1:8788/v1/settings/llm/oauth/callback",
        oauth_scopes="read write",
    )
    url = start_authorization(db, settings, provider="custom")
    assert "response_type=code" in url
    assert "code_challenge_method=S256" in url
    assert "client_id=cid" in url
    assert "scope=read+write" in url
    row = db.query(OAuthState).one()
    assert "code_challenge=" + _challenge(row.code_verifier) in url


@respx.mock
def test_oauth_callback_validates_state_and_stores_the_token(db):
    settings = _settings(
        oauth_authorize_url="https://oauth.test/authorize",
        oauth_token_url="https://oauth.test/token",
        oauth_client_id="cid",
        oauth_redirect_uri="http://127.0.0.1:8788/v1/settings/llm/oauth/callback",
    )
    start_authorization(db, settings, provider="custom")
    state = db.query(OAuthState).one().state

    route = respx.post("https://oauth.test/token").mock(
        return_value=httpx_json({"access_token": "tok", "expires_in": 3600})
    )
    result = complete_authorization(db, settings, code="the-code", state=state, provider="custom")
    assert result["connected"] is True
    assert load_credential(db, "custom", "oauth")["access_token"] == "tok"
    assert route.call_count == 1

    # Single use: replaying the same state must fail with a domain error.
    from app.core.errors import DomainValidationError

    with pytest.raises(DomainValidationError, match="state"):
        complete_authorization(db, settings, code="the-code", state=state, provider="custom")


@respx.mock
def test_oauth_callback_rejects_an_unknown_state(db):
    from app.core.errors import DomainValidationError

    settings = _settings(oauth_token_url="https://oauth.test/token")
    with pytest.raises(DomainValidationError):
        complete_authorization(db, settings, code="c", state="never-issued", provider="custom")


# --- settings API --------------------------------------------------------


def test_settings_endpoints_require_admin(make_client, db):
    plain = make_client(email=f"plain{uuid.uuid4().hex[:10]}@test.local")
    admin = make_client(email=f"admin{uuid.uuid4().hex[:10]}@test.local", admin=True)
    for path in ("/v1/settings/llm", "/v1/usage"):
        assert plain.get(path).status_code == 403
        assert admin.get(path).status_code == 200


def test_settings_response_is_redacted(admin_client, db):
    save_credential(db, "custom", "api_key", {"value": "sk-secret-value"})
    body = admin_client.get("/v1/settings/llm").json()
    assert "sk-secret-value" not in json.dumps(body)
    assert "provider" in body and "presets" in body


def test_settings_can_be_updated_and_cleared(admin_client, db):
    resp = admin_client.put(
        "/v1/settings/llm", json={"provider": "openai_chat", "model_fast": "custom-model"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["model_fast"] == "custom-model"

    assert admin_client.delete("/v1/settings/llm").json()["model_fast"] != "custom-model"


def test_settings_reject_unknown_keys(admin_client):
    resp = admin_client.put("/v1/settings/llm", json={"nonsense": 1})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unknown_setting"


def test_credentials_endpoint_stores_and_removes(admin_client):
    resp = admin_client.post(
        "/v1/settings/llm/credentials", json={"kind": "api_key", "value": "sk-live", "provider": "p"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_api_key"] is True
    assert "sk-live" not in json.dumps(resp.json())

    removed = admin_client.delete("/v1/settings/llm/credentials", params={"provider": "p"})
    assert removed.json()["has_api_key"] is False


def test_oauth_start_returns_409_with_the_missing_fields(admin_client):
    """The settings page needs to tell the operator what to configure."""
    resp = admin_client.get("/v1/settings/llm/oauth/start")
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert "missing" in body["error"]["details"]
    assert "oauth_authorize_url" in body["error"]["details"]["missing"]


def test_test_connection_succeeds_with_the_fake_provider(admin_client):
    @register_default("connection_test")
    def _default(context):
        return "OK"

    resp = admin_client.post("/v1/settings/llm/test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "fake"
    assert body["tiers"]["fast"]["ok"] is True
    assert body["tiers"]["smart"]["ok"] is True


def test_usage_aggregates_by_day_and_task(admin_client, db):
    from datetime import UTC, datetime

    from app.models.llm import LLMRun

    for task, prompt in (("a", 10), ("a", 20), ("b", 5)):
        db.add(
            LLMRun(
                task=task,
                provider="fake",
                model="m",
                prompt_tokens=prompt,
                completion_tokens=1,
                cache_hit=task == "a",
                status="ok",
                created_at=datetime.now(UTC),
            )
        )
    db.commit()

    body = admin_client.get("/v1/usage", params={"days": 7}).json()
    assert body["totals"]["calls"] == 3
    assert body["totals"]["prompt_tokens"] == 35
    assert body["totals"]["cache_hits"] == 2
    assert body["by_task"]["a"]["calls"] == 2
    assert body["by_day"]


def test_streaming_think_stripper_preserves_spaces():
    from app.llm.providers.base import strip_thinking_stream

    chunks = ["Hello wor", "ld <thi", "nk>x</think> ok"]
    out = "".join(strip_thinking_stream(iter(chunks)))
    assert out == "Hello world  ok"


def test_business_transaction_rollback_does_not_affect_llm_calls(fresh_db):
    from app.core.db import SessionLocal
    from app.llm.client import get_llm
    from app.models.llm import OAuthState

    state = OAuthState(
        state="rollback_test_state",
        code_verifier="v",
        provider="test",
    )
    fresh_db.add(state)
    fresh_db.flush()

    client = get_llm(fresh_db)
    client.complete(LLMRequest(task="rollback_test_task", messages=[Message("user", "hi")]))

    fresh_db.rollback()

    assert fresh_db.query(OAuthState).filter_by(state="rollback_test_state").first() is None
    with SessionLocal() as s:
        assert s.query(LLMRun).filter_by(task="rollback_test_task").count() == 1


def test_settings_provider_change_takes_effect_on_next_call(admin_client, db):
    from app.llm.client import get_llm

    resp = admin_client.put("/v1/settings/llm", json={"provider": "fake"})
    assert resp.status_code == 200

    c = get_llm(db)
    assert c.provider_name == "fake"


@respx.mock
def test_repair_tokens_accumulate(db):
    respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[
            httpx_json(
                {
                    "choices": [{"message": {"content": "not json"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
            ),
            httpx_json(
                {
                    "choices": [{"message": {"content": '{"name": "apple", "count": 2}'}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10},
                }
            ),
        ]
    )
    client = _client(db)
    res = client.complete(LLMRequest(task="t", messages=[Message("user", "x")], schema=Fruit))
    assert res.parsed.name == "apple"
    assert res.prompt_tokens == 30
    assert res.completion_tokens == 15


@respx.mock
def test_4xx_not_retried(db):
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx_json({"error": "bad request"}, status=400)
    )
    client = _client(db)
    with pytest.raises(LLMUnavailable):
        client.complete(LLMRequest(task="t", messages=[Message("user", "x")]))
    assert route.call_count == 1


@respx.mock
def test_cache_eviction_on_schema_failure(db):
    from app.core.db import SessionLocal
    from app.models.llm import LLMCache

    req = LLMRequest(task="t", messages=[Message("user", "x")], schema=Fruit)
    k = cache_key(req, "openai_chat", "fast-model")
    with SessionLocal() as s:
        s.add(LLMCache(key=k, task="t", model="fast-model", response={"text": "bad schema json"}))
        s.commit()

    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx_json(
            {
                "choices": [{"message": {"content": '{"name": "banana", "count": 5}'}}],
                "usage": {"prompt_tokens": 6, "completion_tokens": 3},
            }
        )
    )
    client = _client(db)
    res = client.complete(req)
    assert res.parsed.name == "banana"
    assert route.call_count == 1


# --- helpers -------------------------------------------------------------


def httpx_json(payload: dict, status: int = 200):
    import httpx

    return httpx.Response(status, json=payload)


def httpx_sse(frames: list[str]):
    """Build an SSE body from raw payload lines (without a `data:` prefix)."""
    import httpx

    body = "".join(f"data: {f}\n\n" for f in frames)
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


@pytest.fixture
def admin_client(make_client):
    return make_client(email=f"admin{uuid.uuid4().hex[:10]}@test.local", admin=True)


@pytest.fixture(autouse=True)
def _clear_fake_scripts():
    from app.core.db import SessionLocal
    from app.models.llm import LLMCache, LLMRun

    get_fake_provider().clear_script()
    try:
        with SessionLocal() as s:
            s.query(LLMRun).delete()
            s.query(LLMCache).delete()
            s.commit()
    except Exception:
        pass
    yield
    get_fake_provider().clear_script()
    try:
        with SessionLocal() as s:
            s.query(LLMRun).delete()
            s.query(LLMCache).delete()
            s.commit()
    except Exception:
        pass
