"""M7.6: the streaming Ask flow -- event order, per-sentence verdicts, and citations."""

from __future__ import annotations

import json
import uuid

import pytest

from app.verify.answer import parse_citations

pytestmark = pytest.mark.db


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Turn an SSE body into `(event, data)` pairs."""
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                data = line[6:].strip()
        if event:
            events.append((event, json.loads(data) if data else {}))
    return events


# --- citation parsing (pure) ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "clean", "numbers"),
    [
        ("The cell delivers 450 Wh/kg [2].", "The cell delivers 450 Wh/kg.", [2]),
        ("Both agree [1, 3].", "Both agree.", [1, 3]),
        ("No citation here.", "No citation here.", []),
        ("Numbers [10] and [11].", "Numbers and.", [10, 11]),
    ],
)
def test_parse_citations(text, clean, numbers):
    got_clean, got_numbers = parse_citations(text)
    assert got_clean == clean
    assert got_numbers == numbers


# --- the stream ----------------------------------------------------------


@pytest.fixture(scope="module")
def demo_user(engine):
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.core.security import hash_password
    from app.demo.seed import seed_demo
    from app.jobs.handlers import register_all
    from app.models.user import User

    register_all()
    settings = get_settings()
    original = settings.jobs_inline
    settings.jobs_inline = False
    session = Session(bind=engine)
    user_id = uuid.uuid4()
    try:
        session.add(
            User(
                id=user_id,
                email=f"ask{uuid.uuid4().hex[:10]}@test.local",
                password_hash=hash_password("ask-password-123"),
            )
        )
        session.commit()
        seed_demo(session, user_id)
        session.commit()
        from app.demo.seed import drain

        drain(session)
        session.commit()
        yield user_id
        session.rollback()
        from sqlalchemy import text

        for table in (
            "claim_events",
            "claim_relations",
            "claim_clusters",
            "claims",
            "chunks",
            "document_versions",
            "documents",
            "sources",
            "jobs",
            "answers",
            "verifications",
        ):
            session.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": user_id})
        session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        session.commit()
    finally:
        session.close()
        settings.jobs_inline = original


@pytest.fixture
def ask_client(engine, demo_user):
    """A client whose SSE generator opens its own session, as it does in production."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.core.db import get_db
    from app.core.security import create_access_token
    from app.main import create_app

    app = create_app()
    session = Session(bind=engine)
    app.dependency_overrides[get_db] = lambda: session

    # The generator must not borrow the request session: an SSE body runs after the
    # response is returned, by which time that session is gone.
    from app.api import deps

    app.dependency_overrides[deps.get_session_factory] = lambda: lambda: session
    try:
        with TestClient(app) as client:
            client.headers.update({"Authorization": f"Bearer {create_access_token(demo_user)}"})
            yield client
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_ask_streams_events_in_order(ask_client):
    with ask_client.stream(
        "POST", "/v1/ask", json={"question": "What is the energy density of the HX-2?"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        events = _parse_sse(response.read().decode())

    names = [name for name, _ in events]
    assert names[0] == "retrieval"
    assert names[1] == "conflicts"
    assert names[-1] == "done"
    assert "delta" in names
    assert "sentence" in names

    # DOM-4: Disputes event must be non-empty for the demo question
    conflicts_event = next(data for name, data in events if name == "conflicts")
    assert len(conflicts_event.get("disputes", [])) > 0, "conflicts event must not be empty"

    # DOM-4: The first sentence must be emitted before the last delta finishes
    first_sentence_idx = names.index("sentence")
    last_delta_idx = len(names) - 1 - names[::-1].index("delta")
    assert first_sentence_idx < last_delta_idx, "first sentence must be emitted before the last delta"
    assert first_sentence_idx < names.index("done")


def test_ask_stream_llm_contradicted_verdict(ask_client):
    """DOM-4: When LLM outputs a statement contradicting evidence, verdict is contradicted (no circular self-proving)."""
    from app.llm.providers.fake import get_fake_provider

    provider = get_fake_provider()
    # Script a statement that contradicts the retrieved 450 Wh/kg evidence
    provider.script("answer", ["The HX-2 solid-state battery delivers an energy density of 999 Wh/kg [1]."])
    try:
        with ask_client.stream(
            "POST", "/v1/ask", json={"question": "What is the energy density of the HX-2?"}
        ) as response:
            events = _parse_sse(response.read().decode())
        sentences = [data for name, data in events if name == "sentence"]
        assert sentences, "sentences must be emitted"
        # Must be judged contradicted against the retrieved evidence!
        assert sentences[0]["verdict"] == "contradicted"
    finally:
        provider.clear_script("answer")


def test_retrieval_event_carries_hits_with_offsets(ask_client):
    with ask_client.stream(
        "POST", "/v1/ask", json={"question": "What is the energy density of the HX-2?"}
    ) as response:
        events = _parse_sse(response.read().decode())
    retrieval = next(data for name, data in events if name == "retrieval")
    assert retrieval["hits"], "the retrieval event must list the evidence"
    hit = retrieval["hits"][0]
    for key in ("chunk_id", "document_id", "version_id", "title", "text", "char_start", "char_end"):
        assert key in hit, f"retrieval hit is missing {key}"
    assert hit["char_start"] < hit["char_end"]


def test_every_sentence_event_carries_a_verdict(ask_client):
    with ask_client.stream(
        "POST", "/v1/ask", json={"question": "What is the energy density of the HX-2?"}
    ) as response:
        events = _parse_sse(response.read().decode())
    sentences = [data for name, data in events if name == "sentence"]
    assert sentences, "the answer must emit per-sentence events"
    for row in sentences:
        assert row["verdict"] in ("supported", "contradicted", "disputed", "unsupported")
        assert row["text"].strip()
        assert isinstance(row["citations"], list)
        assert isinstance(row["flags"], list)


def test_uncited_sentences_are_flagged(ask_client):
    with ask_client.stream(
        "POST", "/v1/ask", json={"question": "What is the energy density of the HX-2?"}
    ) as response:
        events = _parse_sse(response.read().decode())
    sentences = [data for name, data in events if name == "sentence"]
    # The extractive answer always cites its sources; an uncited sentence would be a
    # defect, so assert the flag is absent rather than merely well-formed.
    assert all("uncited" not in s["flags"] for s in sentences)


def test_done_event_reports_the_answer_id_and_summary(ask_client, engine):
    from sqlalchemy.orm import Session

    from app.models.answer import Answer as AnswerRow

    with ask_client.stream(
        "POST", "/v1/ask", json={"question": "What is the energy density of the HX-2?"}
    ) as response:
        events = _parse_sse(response.read().decode())
    done = next(data for name, data in events if name == "done")
    assert done["answer_id"]
    assert done["method"]
    assert "method" in done

    with Session(bind=engine) as session:
        row = session.get(AnswerRow, uuid.UUID(done["answer_id"]))
        assert row is not None, "the answer must be persisted before `done` is sent"
        assert row.answer_md.strip()


def test_ask_json_mode(ask_client):
    resp = ask_client.post(
        "/v1/ask", params={"stream": "false"}, json={"question": "What is the energy density of the HX-2?"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer_md"].strip()
    assert body["answer_id"]
    for sentence in body["sentences"]:
        assert sentence["verdict"] in ("supported", "contradicted", "disputed", "unsupported")


def test_answer_is_grounded_in_the_retrieved_evidence(ask_client):
    """Every extractive sentence is a quote from the chunk it cites, by construction."""
    resp = ask_client.post(
        "/v1/ask",
        params={"stream": "false"},
        json={"question": "What is the energy density of the HX-2?"},
    )
    body = resp.json()
    quotes = {c["n"]: c["quote"] for c in body["citations"]}
    assert quotes, "the answer must expose the evidence it used"

    for sentence in body["sentences"]:
        assert sentence["citations"], f"sentence {sentence['index']} cites nothing"
        for number in sentence["citations"]:
            assert number in quotes, f"citation [{number}] has no matching evidence entry"
            quote = quotes[number]
            # The sentence is the quote with its citation marker removed, so the
            # sentence's opening words must appear at the start of the quote.
            assert sentence["text"][:40] in quote, (
                f"sentence {sentence['index']} is not drawn from citation [{number}]"
            )


def test_ask_never_leaks_another_tenants_sources(engine, demo_user):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.core.db import get_db
    from app.core.security import create_access_token, hash_password
    from app.main import create_app
    from app.models.user import User

    session = Session(bind=engine)
    other_id = uuid.uuid4()
    session.add(
        User(
            id=other_id,
            email=f"other{uuid.uuid4().hex[:10]}@test.local",
            password_hash=hash_password("other-password-123"),
        )
    )
    session.commit()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    from app.api import deps

    app.dependency_overrides[deps.get_session_factory] = lambda: lambda: session
    try:
        with TestClient(app) as client:
            client.headers.update({"Authorization": f"Bearer {create_access_token(other_id)}"})
            resp = client.post(
                "/v1/ask",
                params={"stream": "false"},
                json={"question": "What is the energy density of the HX-2?"},
            )
        assert resp.status_code == 200
        assert resp.json()["citations"] == [], "another tenant's documents must not appear as evidence"
    finally:
        app.dependency_overrides.clear()
        from sqlalchemy import text

        session.execute(text("DELETE FROM users WHERE id = :u"), {"u": other_id})
        session.commit()
        session.close()


def _answer_count(engine, user_id: uuid.UUID, question: str) -> int:
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from app.models.answer import Answer as AnswerRow

    with Session(bind=engine) as session:
        return session.scalar(
            select(func.count())
            .select_from(AnswerRow)
            .where(AnswerRow.user_id == user_id, AnswerRow.question == question)
        )


def test_ask_error_mid_stream_emits_error_then_a_persisted_fallback(
    ask_client, engine, demo_user, monkeypatch
):
    """LLM-8: a provider failure part-way through is reported, answered extractively, and saved."""
    from app.llm.providers.fake import get_fake_provider

    question = "What is the energy density of the HX-2?"
    before = _answer_count(engine, demo_user, question)

    def dropped_stream(messages, **kwargs):
        yield "The HX-2 delivers 450 Wh/kg [1]. "
        raise RuntimeError("upstream dropped the connection")

    monkeypatch.setattr(get_fake_provider(), "stream", dropped_stream)

    with ask_client.stream("POST", "/v1/ask", json={"question": question}) as response:
        assert response.status_code == 200
        events = _parse_sse(response.read().decode())

    names = [name for name, _ in events]
    assert "error" in names, names
    error_at = names.index("error")
    error = dict(events)["error"]
    assert error["code"] == "unavailable"

    done_at = names.index("done")
    assert names[-1] == "done"
    assert error_at < done_at
    assert any(n == "sentence" for n in names[error_at + 1 : done_at]), "fallback sentences follow the error"

    done = next(data for name, data in events if name == "done")
    assert done["method"] == "heuristic(fallback:unavailable)"
    assert _answer_count(engine, demo_user, question) == before + 1, "the fallback answer must be stored once"


def test_ask_over_the_llm_budget_is_429_with_the_budget_code(ask_client, engine, demo_user, monkeypatch):
    """The JSON answer path must return the budget as HTTP 429, not an unhandled 500."""
    from sqlalchemy.orm import Session

    from app.core import time as clock
    from app.core.config import get_settings
    from app.models.llm import LLMRun

    spent = LLMRun(
        user_id=demo_user,
        task="t",
        provider="fake",
        model="m",
        prompt_tokens=1000,
        completion_tokens=0,
        status="ok",
        created_at=clock.utcnow(),
    )
    with Session(bind=engine) as session:
        session.add(spent)
        session.commit()
    monkeypatch.setattr(get_settings(), "llm_daily_token_budget", 100)

    resp = ask_client.post(
        "/v1/ask", params={"stream": "false"}, json={"question": "What is the energy density of the HX-2?"}
    )

    assert resp.status_code == 429, resp.text
    assert resp.json()["error"]["code"] == "budget_exhausted"


def test_ask_budget_exhaustion_is_an_error_event_with_the_budget_code(
    ask_client, engine, demo_user, monkeypatch
):
    from sqlalchemy.orm import Session

    from app.core import time as clock
    from app.core.config import get_settings
    from app.models.llm import LLMRun

    question = "What is the energy density of the HX-2?"
    spent = LLMRun(
        user_id=demo_user,
        task="t",
        provider="fake",
        model="m",
        prompt_tokens=1000,
        completion_tokens=0,
        status="ok",
        created_at=clock.utcnow(),
    )
    with Session(bind=engine) as session:
        session.add(spent)
        session.commit()
        spent_id = spent.id
    monkeypatch.setattr(get_settings(), "llm_daily_token_budget", 100)
    try:
        with ask_client.stream("POST", "/v1/ask", json={"question": question}) as response:
            events = _parse_sse(response.read().decode())
    finally:
        with Session(bind=engine) as session:
            session.execute(LLMRun.__table__.delete().where(LLMRun.id == spent_id))
            session.commit()

    error = next(data for name, data in events if name == "error")
    assert error["code"] == "budget_exhausted"
    done = next(data for name, data in events if name == "done")
    assert done["method"] == "heuristic(fallback:budget_exhausted)"


def test_ask_never_emits_an_empty_sentence_for_trailing_whitespace(ask_client, monkeypatch):
    """A citation written after the full stop used to become an empty, unsupported sentence.

    The model answers "... tokens. [1, 2]"; the splitter reads the citation as its own span, and
    once the citation is removed nothing is left (K3.6 demo).
    """
    from app.llm.providers.fake import get_fake_provider

    def trailing_whitespace_stream(messages, **kwargs):
        yield "The HX-2 delivers 450 Wh/kg. [1, 2]  \nHowever, the price is $120/kWh. [3]"

    monkeypatch.setattr(get_fake_provider(), "stream", trailing_whitespace_stream)
    with ask_client.stream(
        "POST", "/v1/ask", json={"question": "What is the energy density of the HX-2?"}
    ) as response:
        events = _parse_sse(response.read().decode())
    sentences = [data for name, data in events if name == "sentence"]
    assert sentences, "the answer should carry at least one sentence"
    assert all(s["text"].strip() for s in sentences), [s["text"] for s in sentences]
