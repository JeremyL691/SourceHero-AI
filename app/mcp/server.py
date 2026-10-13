"""The MCP server.

Exposed two ways from one implementation:

* **Streamable HTTP** mounted at `/mcp`, authenticated with a PAT.
* **stdio**, for clients that launch the process themselves (`python -m app.mcp`).

The mcp SDK renamed `FastMCP` to `MCPServer` in 2.x and moved the mount path from a
module-level setting to a parameter of `streamable_http_app`. The old
`mcp.server.fastmcp` import no longer exists in the installed version,
so this targets 2.x.

A fresh server is built per `create_app()` call. A module-level singleton would break
the moment the app is constructed twice -- and the MCP session manager can only be
entered once per server instance.
"""

from __future__ import annotations

import contextvars
from inspect import Parameter, Signature
from typing import Any

from app.core.logging import get_logger
from app.core.version import package_version
from app.mcp.tools import TOOLS, WRITE_TOOLS

log = get_logger(__name__)

SERVER_NAME = "SourceHero"
SERVER_INSTRUCTIONS = (
    "Search and question a personal research corpus. Every result is traceable: hits "
    "carry the quoted text and its offsets in the source document, and answers carry "
    "a per-sentence verdict (supported, contradicted, disputed, unsupported). Use "
    "`list_conflicts` to find where sources disagree, and `what_changed` to find what "
    "moved since a date."
)

# Set per-request by the auth middleware; the tool functions read it instead of
# taking a user argument, because MCP tool schemas are derived from the signature.
current_user: contextvars.ContextVar[Any] = contextvars.ContextVar("sourcehero_mcp_user", default=None)


def _bearer(header: str) -> str:
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def _resolve_pat_user(token: str):
    """Turn a personal access token into a `CurrentUser`, or None.

    Shared by the HTTP middleware and the stdio entry point so the two cannot drift:
    a token that works over HTTP must work over stdio, and a revoked one must fail
    in both.
    """
    import hashlib

    from sqlalchemy import select

    from app.api.deps import CurrentUser
    from app.core.db import SessionLocal
    from app.models.user import ApiToken, User

    raw = (token or "").strip()
    if not raw.startswith("sh_pat_"):
        return None

    digest = hashlib.sha256(raw.encode()).hexdigest()
    with SessionLocal() as db:
        row = db.execute(
            select(ApiToken).where(ApiToken.token_hash == digest, ApiToken.revoked_at.is_(None))
        ).scalar_one_or_none()
        if row is None:
            return None
        found = db.execute(select(User).where(User.id == row.user_id)).scalar_one_or_none()
        if found is None:
            return None
        scopes = frozenset(row.scopes or ())
        if not scopes & {"read", "write"}:
            return None
        return CurrentUser(
            id=found.id,
            email=found.email,
            is_admin=found.is_admin,
            scopes=scopes,
            via="pat",
        )


def build_mcp():
    """A fresh MCP server with every tool registered.

    Built per call on purpose: `session_manager.run()` may only be entered once per
    server instance, and the tests construct the app more than once.
    """
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name=SERVER_NAME,
        version=package_version(),
        instructions=SERVER_INSTRUCTIONS,
    )

    def register(name: str, fn):
        def wrapper(**kwargs) -> dict:
            user = current_user.get()
            if user is None:
                return {"error": "no authenticated user in this request", "status": 401}
            if name in WRITE_TOOLS and not user.has("write"):
                return {"error": f"tool {name!r} needs the 'write' scope", "status": 403}
            from app.core.db import SessionLocal
            from app.core.errors import DomainError

            with SessionLocal() as db:
                try:
                    return fn(db, user, **kwargs)
                except DomainError as exc:
                    log.warning("mcp: domain error in %s: %s", name, exc)
                    return {"error": exc.code, "message": exc.message}
                except Exception as exc:  # noqa: BLE001 - errors reach the agent as data
                    log.exception("mcp: unexpected error in %s: %s", name, exc)
                    return {"error": "internal_error", "message": "tool execution failed"}

        wrapper.__name__ = name
        wrapper.__doc__ = fn.__doc__
        # The schema is derived from the wrapper's signature, so it must carry every
        # parameter *with its default* -- see `_apply_signature`.
        _apply_signature(wrapper, name)
        server.tool(name=name, description=fn.__doc__ or "")(wrapper)

    for name, fn in TOOLS.items():
        register(name, fn)
    return server


_STR = str
_INT = int
_BOOL = bool
_OPT_STR = str | None
_OPT_INT = int | None
_OPT_LIST = list[str] | None

# name -> parameter -> (annotation, default). Mirrors each function's keyword-only
# signature so the generated MCP schema is explicit rather than inferred.
_SCHEMAS: dict[str, dict[str, tuple[Any, Any]]] = {
    "search_sources": {
        "query": (_STR, ...),
        "k": (_INT, 8),
        "source_types": (_OPT_LIST, None),
        "date_from": (_OPT_STR, None),
        "date_to": (_OPT_STR, None),
    },
    "get_passage": {"chunk_id": (_INT, ...), "context_chars": (_INT, 400)},
    "ask": {"question": (_STR, ...), "k": (_INT, 8)},
    "verify": {"text": (_STR, ...)},
    "list_conflicts": {"topic": (_OPT_STR, None), "limit": (_INT, 10), "offset": (_INT, 0)},
    "what_changed": {"since": (_STR, ...), "until": (_OPT_STR, None), "topic": (_OPT_STR, None)},
    "read_wiki": {"slug": (_STR, ...)},
    "search_wiki": {"query": (_STR, ...), "limit": (_INT, 10)},
    "capture": {"url_or_text": (_STR, ...), "title": (_OPT_STR, None)},
    "export_brief": {"kind": (_STR, ...), "id": (_STR, ...)},
}


def _apply_signature(wrapper, name: str) -> None:
    """Give the wrapper the full keyword-only signature the MCP schema is built from.

    Every parameter must be present *and carry its default*. The SDK derives the
    schema from this signature, so a parameter left off becomes invisible to the
    agent, and one left without a default becomes mandatory -- which made
    `search_wiki(query=...)` fail asking for `limit`.
    """
    schema = _SCHEMAS[name]
    parameters = [
        Parameter(
            parameter,
            Parameter.KEYWORD_ONLY,
            annotation=annotation,
            default=Parameter.empty if default is ... else default,
        )
        for parameter, (annotation, default) in schema.items()
    ]
    wrapper.__annotations__.update({p: a for p, (a, _) in schema.items()})
    wrapper.__signature__ = Signature(parameters, return_annotation=dict)


def streamable_http_app():
    """The ASGI app to mount, with its endpoint at `/mcp`.

    Mounted at `/` by the caller, with `streamable_http_path="/mcp"` left at its
    default -- mounting a path-defaulted app under `/mcp` would produce `/mcp/mcp`,
    which the probe in `tests/test_mcp.py` guards against.

    Returns `(app, session_manager)`. The manager must be entered with `run()` from
    the *parent's* lifespan: Starlette does not propagate a mounted sub-app's
    lifespan, so without this every request fails with "Task group is not
    initialized".
    """
    server = build_mcp()
    app = server.streamable_http_app(streamable_http_path="/mcp", json_response=True, stateless_http=True)
    return app, server.session_manager


def install_auth_middleware(app) -> None:
    """Require a PAT on `/mcp`, and set the contextvar the tools read."""

    @app.middleware("http")
    async def _auth(request, call_next):
        import asyncio

        if not request.url.path.startswith("/mcp"):
            return await call_next(request)
        user = await asyncio.to_thread(_resolve_pat_user, _bearer(request.headers.get("authorization", "")))
        if user is None:
            from starlette.responses import JSONResponse

            return JSONResponse(
                {"error": "a valid personal access token is required"},
                status_code=401,
            )
        token = current_user.set(user)
        try:
            return await call_next(request)
        finally:
            current_user.reset(token)


__all__ = [
    "SERVER_INSTRUCTIONS",
    "SERVER_NAME",
    "build_mcp",
    "current_user",
    "install_auth_middleware",
    "streamable_http_app",
]
