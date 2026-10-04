"""URL canonicalization and document identity.

`identity_key` is what makes a document the same document across fetches:
`url:<canonical>` for links, `hash:<sha256>` for pasted text, `file:<sha256>` for
uploads. Two URLs that differ only in tracking parameters, default port, fragment or
trailing slash resolve to one document, so re-capturing the same article does not
duplicate it.
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Analytics parameters that never change what the page *is*.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "igshid"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonicalize_url(url: str) -> str:
    """A stable form of `url` for deduplication and identity.

    Lowercases the host, drops the default port, the fragment, tracking parameters,
    and sorts what remains. A trailing slash is removed only when the path is not
    just `/` -- `example.com/` and `example.com` are the same page, but
    `example.com/docs/` and `example.com/docs` may not be.
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not host:
        # Not a real absolute URL (or a relative one): return it untouched rather
        # than mangling it into something worse.
        return url.strip()

    netloc = host
    port = parts.port
    if port is not None and _DEFAULT_PORTS.get(scheme) != port:
        netloc = f"{host}:{port}"
    if parts.username:
        credentials = parts.username
        if parts.password:
            credentials += f":{parts.password}"
        netloc = f"{credentials}@{netloc}"

    path = parts.path
    path = "" if path == "/" else path.rstrip("/") or "/"

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.lower().startswith(_TRACKING_PREFIXES) or k.lower() in _TRACKING_KEYS)
    ]
    query = urlencode(sorted(kept))

    return urlunsplit((scheme, netloc, path, query, ""))


def identity_key(
    *, url: str | None = None, clean_text: str | None = None, raw_bytes: bytes | None = None
) -> str:
    """The deduplication key for one piece of material.

    A URL wins when there is one; otherwise the content itself is the identity.
    """
    if url:
        return f"url:{canonicalize_url(url)}"
    if raw_bytes is not None:
        return f"file:{hashlib.sha256(raw_bytes).hexdigest()}"
    if clean_text is not None:
        return f"hash:{hashlib.sha256(clean_text.encode('utf-8')).hexdigest()}"
    raise ValueError("identity_key needs a url, raw bytes or text")


__all__ = ["canonicalize_url", "identity_key"]
