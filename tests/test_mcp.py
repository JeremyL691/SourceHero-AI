"""M11.4: the tools themselves, plus MCP over HTTP and over stdio."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.security import hash_password
from app.mcp.tools import TOOLS

pytestmark = pytest.mark.db

REPO_ROOT = Path(__file__).resolve().parents[1]

# Sessions the MCP protocol needs for a modern handshake.
PROTOCOL_VERSION = "2025-06-18"


# --- the tools, called directly ------------------------------------------


@pytest.fixture(scope="module")
def agent(engine):
    """A user whose corpus is the demo corpus, with a PAT."""
    import hashlib

    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.demo.seed import drain, seed_demo
    from app.jobs.handlers import register_all
    from app.models.user import ApiToken, User

    register_all()
    settings = get_settings()
    original = settings.jobs_inline
    settings.jobs_inline = False
    session = Session(bind=engine)
    user_id = uuid.uuid4()
    raw = "sh_pat_" + uuid.uuid4().hex + uuid.uuid4().hex
    try:
        session.add(
            User(
                id=user_id,
                email=f"agent{uuid.uuid4().hex[:10]}@test.local",
                password_hash=hash_password("agent-password-123"),
            )
        )
        session.flush()
        session.add(
            ApiToken(
                user_id=user_id,
                name="agent",
                prefix=raw[:12],
                token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                scopes=["read", "write"],
            )
        )
        session.commit()
        seed_demo(session, user_id)
        session.commit()
        drain(session)
        session.commit()

        from app.api.deps import CurrentUser

        user = CurrentUser(
            id=user_id,
            email="agent@test.local",
            is_admin=False,
            scopes=frozenset({"read", "write"}),
            via="pat",
        )
        yield session, user, raw

        session.rollback()
        # The wiki join tables carry no user_id of their own; reach them via pages.
        for join_table in ("wiki_page_claims", "wiki_page_revisions"):
            session.execute(
                text(
                    f"DELETE FROM {join_table} WHERE page_id IN "
                    "(SELECT id FROM wiki_pages WHERE user_id = :u)"
                ),
                {"u": user_id},
            )
        for table in (
            "lint_findings",
            "wiki_pages",
            "topics",
            "claim_events",
            "claim_relations",
            "claim_clusters",
            "claims",
            "chunks",
            "document_versions",
            "documents",
            "sources",
            "jobs",
        ):
            session.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": user_id})
        session.execute(text("DELETE FROM api_tokens WHERE user_id = :u"), {"u": user_id})
        session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        session.commit()
    finally:
        session.close()
        settings.jobs_inline = original


def test_every_tool_is_registered():
    assert len(TOOLS) >= 9
    for name, fn in TOOLS.items():
        assert fn.__doc__, f"{name} has no docstring; agents read it as the tool spec"


def test_search_sources_returns_traceable_hits(agent):
    session, user, _ = agent
    result = TOOLS["search_sources"](session, user, query="energy density of the HX-2", k=5)
    assert result["count"] > 0
    hit = result["hits"][0]
    for key in ("title", "text", "char_start", "char_end", "document_id", "scores"):
        assert key in hit, f"a search hit is missing {key}"
    assert hit["char_start"] < hit["char_end"]


def test_search_sources_finds_the_chinese_document(agent):
    session, user, _ = agent
    result = TOOLS["search_sources"](session, user, query="HX-2 电池的能量密度", k=5)
    assert result["count"] > 0


def test_get_passage_returns_surrounding_context(agent):
    session, user, _ = agent
    hit = TOOLS["search_sources"](session, user, query="energy density", k=1)["hits"][0]
    passage = TOOLS["get_passage"](session, user, chunk_id=hit["chunk_id"], context_chars=200)
    assert passage["passage"] == hit["text"] or passage["passage"] in passage["context"]
    assert passage["context_char_start"] <= passage["passage_char_start"]
    assert passage["context_char_end"] >= passage["passage_char_end"]


def test_ask_returns_a_verdict_per_sentence(agent):
    session, user, _ = agent
    result = TOOLS["ask"](session, user, question="What is the energy density of the HX-2?")
    assert result["answer_md"]
    assert result["sentences"]
    for sentence in result["sentences"]:
        assert sentence["verdict"] in ("supported", "contradicted", "disputed", "unsupported")


def test_verify_matches_the_planned_verdicts(agent):
    session, user, _ = agent
    result = TOOLS["verify"](session, user, text="Orchid-7 was trained on 2 trillion tokens.")
    assert result["sentences"][0]["verdict"] == "supported"


def test_list_conflicts_returns_two_sided_camps(agent):
    session, user, _ = agent
    result = TOOLS["list_conflicts"](session, user, limit=10)
    assert result["count"] >= 4
    with_camps = [c for c in result["conflicts"] if c.get("camps")]
    assert with_camps, "at least one conflict must show its camps"
    camps = with_camps[0]["camps"]["camps"]
    assert len(camps) == 2
    for camp in camps:
        assert camp["claims"], "a camp must quote at least one claim"
        assert camp["claims"][0]["quote"]


def test_what_changed_returns_a_digest(agent):
    session, user, _ = agent
    result = TOOLS["what_changed"](session, user, since="2026-05-01", until="2026-09-01")
    assert "What changed" in result["digest"]
    assert result["stats"]["superseded"] >= 1


def test_what_changed_clamps_a_long_window(agent):
    session, user, _ = agent
    result = TOOLS["what_changed"](session, user, since="2020-01-01")
    from datetime import datetime as _dt

    start = _dt.fromisoformat(result["period"]["start"])
    end = _dt.fromisoformat(result["period"]["end"])
    assert (end - start).days <= 90


def test_what_changed_validates_its_arguments(agent):
    from app.core.errors import DomainValidationError

    session, user, _ = agent
    with pytest.raises(DomainValidationError):
        TOOLS["what_changed"](session, user, since="2026-09-01", until="2026-05-01")


def test_wiki_tools(agent):
    session, user, _ = agent
    found = TOOLS["search_wiki"](session, user, query="energy")
    assert found["count"] >= 1
    page = TOOLS["read_wiki"](session, user, slug=found["pages"][0]["slug"])
    assert page["body_md"]
    assert page["claim_ids"], "a page must record which claims it was built from"


def test_capture_creates_a_note(agent):
    from app.demo.seed import drain
    from app.retrieval.hybrid import hybrid_search

    session, user, _ = agent
    result = TOOLS["capture"](session, user, url_or_text="A note captured by the agent layer.")
    assert result["source_type"] == "note"
    assert result["job_id"]
    drain(session)
    search_res = hybrid_search(session, user.id, "captured by the agent layer", k=5)
    assert any("captured by the agent layer" in h.text for h in search_res.hits)


def test_tools_are_tenant_scoped(agent):
    from app.api.deps import CurrentUser
    from app.core.errors import NotFoundError

    session, _, _ = agent
    stranger = CurrentUser(
        id=uuid.uuid4(),
        email="stranger@test.local",
        is_admin=False,
        scopes=frozenset({"read", "write"}),
        via="pat",
    )
    assert TOOLS["search_sources"](session, stranger, query="energy density")["count"] == 0
    assert TOOLS["list_conflicts"](session, stranger)["count"] == 0
    with pytest.raises(NotFoundError):
        TOOLS["get_passage"](session, stranger, chunk_id=1)


def test_capture_needs_the_write_scope(agent):
    from app.api.deps import CurrentUser
    from app.core.errors import PermissionDeniedError

    session, _, _ = agent
    reader = CurrentUser(
        id=uuid.uuid4(),
        email="reader@test.local",
        is_admin=False,
        scopes=frozenset({"read"}),
        via="pat",
    )
    with pytest.raises(PermissionDeniedError):
        TOOLS["capture"](session, reader, url_or_text="should not be allowed")


# --- MCP over Streamable HTTP --------------------------------------------


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def mcp_server(engine, agent):
    """A real uvicorn server on a random port, with the demo user's PAT."""
    import uvicorn

    from app.main import create_app

    session, _, raw = agent
    # Committed data, because the server runs on its own connections.
    session.commit()

    port = _free_port()
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    import httpx2 as httpx

    for _ in range(120):
        try:
            httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1)
            break
        except Exception:  # noqa: BLE001 - the server may not be listening yet
            time.sleep(0.25)

    yield f"http://127.0.0.1:{port}", raw

    server.should_exit = True
    thread.join(timeout=10)


def test_mcp_requires_a_token(mcp_server):
    import httpx2 as httpx

    base, _ = mcp_server
    resp = httpx.post(
        f"{base}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        timeout=15,
    )
    assert resp.status_code == 401, resp.text


def test_mcp_endpoint_is_not_double_prefixed(mcp_server):
    """`/mcp` is the endpoint; `/mcp/mcp` must not exist."""
    import httpx2 as httpx

    base, raw = mcp_server
    headers = {
        "Authorization": f"Bearer {raw}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    ok = httpx.post(f"{base}/mcp", json=payload, headers=headers, timeout=15)
    assert ok.status_code == 200, ok.text
    assert "result" in ok.json()

    doubled = httpx.post(f"{base}/mcp/mcp", json=payload, headers=headers, timeout=15)
    assert doubled.status_code == 404, "the endpoint must be /mcp, not /mcp/mcp"


def test_mcp_lists_and_calls_a_tool(mcp_server):
    """Full round trip: initialize -> list_tools -> call search_sources."""
    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    base, raw = mcp_server

    async def run() -> dict:
        # mcp 2.x takes an httpx client rather than a `headers=` kwarg, so the PAT
        # rides on the client's default headers.
        import httpx2 as httpx

        async with (
            httpx.AsyncClient(headers={"Authorization": f"Bearer {raw}"}, timeout=30) as http,
            streamable_http_client(f"{base}/mcp", http_client=http, terminate_on_close=False) as (
                read,
                write,
                *_,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            assert len(names) >= 9, names
            result = await session.call_tool(
                "search_sources", {"query": "energy density of the HX-2", "k": 3}
            )
            return {"names": names, "result": result}

    outcome = asyncio.run(run())
    assert "search_sources" in outcome["names"]
    payload = _tool_payload(outcome["result"])
    assert payload.get("count", 0) > 0, payload
    assert payload["hits"][0]["text"]


def _tool_payload(result) -> dict:
    """Pull the JSON out of an MCP tool result (content blocks or structured)."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured.get("result"):
        return structured["result"]
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    return {}


def test_mcp_tool_error_is_reported_as_data(mcp_server):
    """A bad argument reaches the agent as a payload, not as a dropped connection."""
    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    base, raw = mcp_server

    async def run():
        import httpx2 as httpx

        async with (
            httpx.AsyncClient(headers={"Authorization": f"Bearer {raw}"}, timeout=30) as http,
            streamable_http_client(f"{base}/mcp", http_client=http, terminate_on_close=False) as (
                read,
                write,
                *_,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await session.call_tool("read_wiki", {"slug": "no-such-page"})

    payload = _tool_payload(asyncio.run(run()))
    assert "error" in payload, payload


# --- MCP over stdio ------------------------------------------------------


def test_stdio_entry_requires_a_token():
    env = {k: v for k, v in os.environ.items() if k != "SOURCEHERO_TOKEN"}
    proc = subprocess.run(
        [sys.executable, "-m", "app.mcp"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=60,
    )
    assert proc.returncode == 2
    assert "SOURCEHERO_TOKEN" in proc.stderr


def test_stdio_rejects_an_unknown_token():
    env = {**os.environ, "SOURCEHERO_TOKEN": "sh_pat_" + uuid.uuid4().hex + uuid.uuid4().hex}
    proc = subprocess.run(
        [sys.executable, "-m", "app.mcp"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=60,
    )
    assert proc.returncode == 2
    assert "invalid" in proc.stderr.lower()


@pytest.mark.slow
def test_stdio_lists_tools_over_the_protocol(engine, agent):
    """A real client launching `python -m app.mcp` and listing the tools."""
    import asyncio

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    session, _, raw = agent
    session.commit()

    env = {
        **os.environ,
        "SOURCEHERO_TOKEN": raw,
        "DATABASE_URL": session.get_bind().url.render_as_string(hide_password=False),
    }

    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "app.mcp"], env=env, cwd=str(REPO_ROOT)
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            return await client.list_tools()

    tools = asyncio.run(run())
    names = [t.name for t in tools.tools]
    assert len(names) >= 9, names
    assert "search_sources" in names
