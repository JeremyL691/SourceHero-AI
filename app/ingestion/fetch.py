"""Fetching: the only module that touches the network.

Everything here is designed to be interceptable by `respx`, so the test suite never
reaches a real host. In particular robots.txt is fetched with the *same* httpx client
and handed to `urllib.robotparser` as text -- calling `RobotFileParser.read()` would
go through urllib, which respx cannot see, and would silently hit the real internet.
"""

from __future__ import annotations

import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from app.core import time as clock
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# FetchError codes, also stored verbatim in ingestion_runs.error.
CODES = (
    "timeout",
    "dns",
    "tls",
    "http_4xx",
    "http_5xx",
    "too_large",
    "robots_disallowed",
    "connection",
    "unsupported_scheme",
)

RETRY_BACKOFF = (0.5, 1.5)
ROBOTS_TTL_SECONDS = 3600.0
USER_ROLES = ("user-agent", "*")


class FetchError(Exception):
    """A fetch that failed for a reason worth recording verbatim."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str | None
    content: bytes
    fetched_at: datetime


@dataclass
class _RobotsEntry:
    parser: urllib.robotparser.RobotFileParser | None
    fetched_at: float


_client: httpx.Client | None = None
_client_lock = threading.Lock()
_hosts: dict[str, float] = {}
_hosts_lock = threading.Lock()
_robots: dict[str, _RobotsEntry] = {}
_robots_lock = threading.Lock()


def get_client() -> httpx.Client:
    """A module-level client so connections are pooled across calls."""
    global _client
    with _client_lock:
        if _client is None:
            settings = get_settings()
            _client = httpx.Client(
                follow_redirects=True,
                timeout=settings.http_timeout_seconds,
                headers={"User-Agent": settings.user_agent},
                max_redirects=5,
            )
        return _client


def reset_client() -> None:
    """Tests that change settings call this."""
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
        _client = None
    with _hosts_lock:
        _hosts.clear()
    with _robots_lock:
        _robots.clear()


def _throttle(url: str) -> None:
    """Keep at least `per_host_min_interval_seconds` between requests to one host."""
    settings = get_settings()
    interval = settings.per_host_min_interval_seconds
    if interval <= 0:
        return
    host = urlsplit(url).netloc.lower()
    with _hosts_lock:
        last = _hosts.get(host)
        now = time.monotonic()
        wait = 0.0 if last is None else interval - (now - last)
        _hosts[host] = now + max(0.0, wait)
    if wait > 0:
        time.sleep(wait)


def _classify(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        text = str(exc).lower()
        # A DNS failure surfaces as a ConnectError; name it precisely so the UI can
        # say "host not found" instead of "connection failed".
        return (
            "dns"
            if (
                "name or service not known" in text
                or "nodename nor servname" in text
                or "getaddrinfo" in text
            )
            else "connection"
        )
    if isinstance(exc, httpx.TransportError):
        text = str(exc).lower()
        return "tls" if ("ssl" in text or "certificate" in text or "tls" in text) else "connection"
    return "connection"


def sanitize_error(exc: Exception) -> str:
    """Turn any exception into one readable sentence.

    Stored in `ingestion_runs.error` and shown in the UI, so it must never leak a
    stack trace, a local path, or a bearer token from a header.
    """
    if isinstance(exc, FetchError):
        return {
            "timeout": "The request timed out.",
            "dns": "The host could not be found.",
            "tls": "The TLS connection failed.",
            "http_4xx": f"The server refused the request (HTTP {getattr(exc, 'status', '?')}).",
            "http_5xx": "The server had an internal error.",
            "too_large": "The response was larger than the configured limit.",
            "robots_disallowed": "The site's robots.txt does not allow this URL.",
            "connection": "The connection failed.",
            "unsupported_scheme": "Only http and https URLs are supported.",
        }.get(exc.code, "The fetch failed.")
    code = _classify(exc)
    return {
        "timeout": "The request timed out.",
        "dns": "The host could not be found.",
        "tls": "The TLS connection failed.",
        "connection": "The connection failed.",
    }.get(code, "The fetch failed.")


def _robots_allows(url: str) -> bool:
    """Consult robots.txt, caching the verdict per host for an hour.

    A robots.txt we cannot fetch is treated as "allowed": a broken robots file should
    not make the whole product unusable.
    """
    settings = get_settings()
    if not settings.respect_robots:
        return True

    parts = urlsplit(url)
    host = parts.netloc.lower()
    now = time.monotonic()

    with _robots_lock:
        entry = _robots.get(host)
        fresh = entry is not None and (now - entry.fetched_at) < ROBOTS_TTL_SECONDS
        parser = entry.parser if (fresh and entry is not None) else None

    if parser is None:
        robots_url = f"{parts.scheme}://{host}/robots.txt"
        parser = urllib.robotparser.RobotFileParser()
        try:
            _throttle(robots_url)
            resp = get_client().get(robots_url, timeout=5.0)
            if resp.status_code >= 400:
                parser = None
            else:
                # parse(lines), never .read(): .read() uses urllib and would bypass
                # both respx and our own client settings.
                parser.parse(resp.text.splitlines())
        except Exception:  # noqa: BLE001 - unreachable robots.txt means "allowed"
            parser = None
        with _robots_lock:
            _robots[host] = _RobotsEntry(parser=parser, fetched_at=now)

    if parser is None:
        return True
    return parser.can_fetch(settings.user_agent, url) or parser.can_fetch("*", url)


def fetch(url: str, *, allow_robots: bool = True) -> FetchResult:
    """GET `url`, honouring size limits, per-host rate limiting and robots.txt.

    Retries twice on 5xx, timeouts and connection errors with a short backoff. 4xx is
    final -- the server told us no, and asking again will not help.
    """
    settings = get_settings()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError("unsupported_scheme", f"cannot fetch scheme {parts.scheme!r}")

    if allow_robots and not _robots_allows(url):
        raise FetchError("robots_disallowed", f"robots.txt disallows {url}")

    client = get_client()
    max_bytes = settings.http_max_bytes
    last_exc: Exception | None = None

    for attempt in range(len(RETRY_BACKOFF) + 1):
        _throttle(url)
        try:
            with client.stream("GET", url) as resp:
                if resp.status_code >= 500:
                    err = FetchError("http_5xx", f"HTTP {resp.status_code}")
                    err.status = resp.status_code  # type: ignore[attr-defined]
                    if attempt < len(RETRY_BACKOFF):
                        last_exc = err
                        time.sleep(RETRY_BACKOFF[attempt])
                        continue
                    raise err
                if resp.status_code >= 400:
                    err = FetchError("http_4xx", f"HTTP {resp.status_code}")
                    err.status = resp.status_code  # type: ignore[attr-defined]
                    raise err

                chunks: list[bytes] = []
                total = 0
                for part in resp.iter_bytes():
                    total += len(part)
                    if total > max_bytes:
                        raise FetchError("too_large", f"response exceeded {max_bytes} bytes")
                    chunks.append(part)
                return FetchResult(
                    url=url,
                    final_url=str(resp.url),
                    status=resp.status_code,
                    content_type=resp.headers.get("content-type"),
                    content=b"".join(chunks),
                    fetched_at=clock.utcnow(),
                )
        except FetchError:
            raise
        except Exception as exc:  # noqa: BLE001 - classified below
            last_exc = exc
            if attempt < len(RETRY_BACKOFF):
                time.sleep(RETRY_BACKOFF[attempt])
                continue
            raise FetchError(_classify(exc), sanitize_error(exc)) from exc

    raise FetchError("connection", str(last_exc) if last_exc else "fetch failed")


__all__ = [
    "CODES",
    "FetchError",
    "FetchResult",
    "fetch",
    "get_client",
    "reset_client",
    "sanitize_error",
]
