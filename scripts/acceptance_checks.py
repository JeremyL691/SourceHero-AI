#!/usr/bin/env python3
"""K9.5 acceptance: HTTP checks of the v2.2 flows against a running API (after make demo and make dev).

Usage: python3 acceptance_checks.py <repo root>. Reads API_PORT from .env and prints one row per check.
Exits 1 if any check fails. Passwords and tokens are never printed.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
EMAIL, PASSWORD = "demo@sourcehero.local", "demo-password-123"  # the Makefile's demo account
EXPECTED = {
    "The HX-2 cell delivers an energy density of 450 Wh/kg.": "disputed",
    "Orchid-7 was trained on 2 trillion tokens.": "supported",
    "Orchid-7 was trained on 5 trillion tokens.": "contradicted",
    "Orchid-7 can write poetry in Latin.": "unsupported",
}


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


BASE = f"http://127.0.0.1:{read_env(ROOT / '.env')['API_PORT']}"


def call(method, path, *, token=None, body=None, data=None, content_type=None):
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = data
    if body is not None:
        payload = json.dumps(body).encode()
        content_type = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(BASE + path, data=payload, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    text = raw.decode("utf-8", errors="replace")
    try:
        return status, (json.loads(text) if text else None)
    except ValueError:
        return status, text


def items(payload):
    if isinstance(payload, dict) and "items" in payload:
        return payload["items"]
    return payload if isinstance(payload, list) else []


def wait_for_job(token: str, job_id: int, timeout: int = 180) -> str:
    """Poll one queued job until it reaches a terminal state, and return that state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, job = call("GET", f"/v1/jobs/{job_id}", token=token)
        if isinstance(job, dict) and job.get("status") in ("succeeded", "failed", "dead", "cancelled"):
            return job["status"]
        time.sleep(3)
    return "timeout"


def multipart(field: str, filename: str, content: bytes, mime: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    )
    return (
        head.encode() + content + f"\r\n--{boundary}--\r\n".encode(),
        f"multipart/form-data; boundary={boundary}",
    )


RESULTS: list[bool] = []


def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as exc:  # a broken check is a failed row, and the rest still run
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)


def main() -> int:
    status, session = call("POST", "/v1/auth/login", body={"email": EMAIL, "password": PASSWORD})
    if status != 200 or not isinstance(session, dict):
        print(f"FAIL  login: HTTP {status}")
        return 1
    token = session["access_token"]
    ctx: dict = {}

    def conflicts():
        status, payload = call("GET", "/v1/conflicts?limit=50", token=token)
        n = len(items(payload))
        return status == 200 and n >= 4, f"HTTP {status}, {n} conflicts (need >= 4)"

    def cluster_two_camps():
        _, clusters = call("GET", "/v1/clusters?limit=100", token=token)
        hits = []
        for cluster in items(clusters):
            _, detail = call("GET", f"/v1/clusters/{cluster['id']}", token=token)
            camps = (detail or {}).get("camps") or {}
            groups = camps.get("camps") or []
            statements = " ".join(c.get("statement", "") for g in groups for c in g.get("claims", []))
            if len(groups) == 2 and all(g.get("claims") for g in groups) and "HX-2" in statements:
                hits.append(cluster["id"])
        return bool(hits), f"clusters whose two camps cover HX-2: {hits}"

    def diffs_superseded_and_stale():
        _, created = call(
            "POST",
            "/v1/diffs",
            token=token,
            body={"start": "2026-05-01", "end": "2026-09-01", "time_axis": "world", "scope_kind": "all"},
        )
        wait_for_job(token, created["job_id"])
        _, diffs = call("GET", "/v1/diffs?limit=5", token=token)
        newest = (items(diffs) or [{}])[0]
        stats = newest.get("stats") or {}
        superseded = next((v for k, v in stats.items() if "supersed" in k), 0)
        stale = next((v for k, v in stats.items() if "stale" in k), 0)
        return superseded >= 2 and stale >= 1, f"stats={stats} (need superseded >= 2, stale >= 1)"

    def wiki_clean_pages():
        _, pages = call("GET", "/v1/wiki/pages?limit=100", token=token)
        clean = [p for p in items(pages) if p.get("status") == "clean"]
        uncited = []
        for page in clean:
            _, detail = call("GET", f"/v1/wiki/pages/{page['slug']}", token=token)
            body = (detail or {}).get("body") or {}
            sentences = list(body.get("summary") or [])
            for section in body.get("sections") or []:
                sentences.extend(section.get("sentences") or [])
            if any(not s.get("claim_ids") for s in sentences):
                uncited.append(page["slug"])
        ctx["wiki_slug"] = clean[0]["slug"] if clean else None
        return len(clean) >= 3 and not uncited, f"{len(clean)} clean pages (need >= 3); uncited: {uncited}"

    def verify_four_examples():
        got = {}
        for sentence in EXPECTED:
            _, result = call("POST", "/v1/verify", token=token, body={"text": sentence})
            got[sentence] = [s.get("verdict") for s in (result or {}).get("sentences", [])]
        ok = all(v and v[0] == EXPECTED[s] for s, v in got.items())
        return ok, "; ".join(f"{EXPECTED[s]}->{v}" for s, v in got.items())

    def ask_every_sentence_has_a_verdict():
        status, answer = call(
            "POST",
            "/v1/ask?stream=false",
            token=token,
            body={
                "question": "What do the sources say about the HX-2 cell and Orchid-7 training data?",
                "k": 8,
            },
        )
        sentences = (answer or {}).get("sentences") or []
        ctx["answer"] = answer
        ok = status == 200 and bool(sentences) and all(s.get("verdict") for s in sentences)
        return ok, f"HTTP {status}, {len(sentences)} sentences, method={(answer or {}).get('method')}"

    def ask_method_is_not_extractive_fallback():
        method = (ctx.get("answer") or {}).get("method")
        return method not in (None, "heuristic(extractive)"), f"method={method}"

    def two_uploads_add_two_documents():
        _, before = call("GET", "/v1/documents?limit=200", token=token)
        n0 = len(items(before))
        statuses = []
        for i in range(2):
            name = f"acceptance-{uuid.uuid4().hex[:8]}.txt"
            unique = uuid.uuid4().hex  # a repeated body deduplicates into the earlier document
            body, ctype = multipart(
                "file",
                name,
                f"Acceptance upload {i} {unique}: the depot stores 12 pallets.".encode(),
                "text/plain",
            )
            status, _ = call("POST", "/v1/sources/upload", token=token, data=body, content_type=ctype)
            statuses.append(status)
        n1 = n0
        deadline = time.time() + 180  # documents are created by the ingestion job, so wait for it
        while time.time() < deadline:
            _, after = call("GET", "/v1/documents?limit=200", token=token)
            n1 = len(items(after))
            if n1 - n0 >= 2:
                break
            time.sleep(3)
        return n1 - n0 == 2 and statuses == [201, 201], f"upload HTTP {statuses}; documents {n0} -> {n1}"

    def capture_becomes_searchable():
        phrase = f"Acceptance capture {uuid.uuid4().hex}"
        call("POST", "/v1/captures", token=token, body={"text": phrase, "title": phrase})
        deadline = time.time() + 120
        while time.time() < deadline:
            _, found = call("POST", "/v1/search", token=token, body={"query": phrase, "k": 5})
            if phrase in json.dumps(found):
                return True, "found by /v1/search"
            time.sleep(3)
        return False, "not found within 120 s"

    def footnote_quotes_match_claims():
        slug = ctx.get("wiki_slug")
        _, detail = call("GET", f"/v1/wiki/pages/{slug}", token=token)
        footnotes = (detail or {}).get("footnotes") or []
        mismatched = []
        for note in footnotes:
            _, claim = call("GET", f"/v1/claims/{note.get('claim_id')}", token=token)
            if (claim or {}).get("quote") != note.get("quote"):
                mismatched.append(note.get("claim_id"))
        return bool(
            footnotes
        ) and not mismatched, f"{len(footnotes)} footnotes; mismatched claims: {mismatched}"

    def tag_filter_returns_only_tagged_documents():
        _, tag = call("POST", "/v1/tags", token=token, body={"name": f"acc-{uuid.uuid4().hex[:6]}"})
        _, docs = call("GET", "/v1/documents?limit=5", token=token)
        doc = items(docs)[0]
        call("POST", f"/v1/documents/{doc['id']}/tags", token=token, body={"tag_id": tag["id"]})
        _, filtered = call("GET", f"/v1/documents?tag_id={tag['id']}&limit=100", token=token)
        ids = [d["id"] for d in items(filtered)]
        return ids == [
            doc["id"]
        ], f"tag {tag['id']} -> {len(ids)} document(s), attached doc returned: {doc['id'] in ids}"

    def schedule_gets_next_run():
        status, sched = call(
            "POST",
            "/v1/schedules",
            token=token,
            body={
                "job_type": "belief_diff",
                "name": f"acc-{uuid.uuid4().hex[:6]}",
                "config": {},
                "hour": 3,
                "minute": 0,
            },
        )
        next_run = (sched or {}).get("next_run_at")
        return status in (200, 201) and bool(next_run), f"HTTP {status}, next_run_at={next_run}"

    def export_has_marker_and_verbatim_quotes():
        answer = ctx.get("answer") or {}
        status, text = call("GET", f"/v1/answers/{answer.get('answer_id')}/export?format=md", token=token)
        body = text if isinstance(text, str) else json.dumps(text)
        # Footnotes read "[^N]: title", the URL and date, then "> quote". Each quote must be a verbatim
        # slice of a cited source document.
        quotes = [line.strip()[2:] for line in body.splitlines() if line.strip().startswith("> ")]
        # The export can quote evidence from documents outside the answer's own citations, so every
        # document in the library counts as a source.
        _, listing = call("GET", "/v1/documents?limit=200", token=token)
        sources = []
        for document in items(listing):
            _, doc = call("GET", f"/v1/documents/{document['id']}", token=token)
            sources.append((doc or {}).get("text") or "")
        verbatim = bool(quotes) and all(any(q in text for text in sources) for q in quotes)
        return status == 200 and "[^1]" in body and verbatim, (
            f"HTTP {status}, [^1] present: {'[^1]' in body}, footnote quotes: {len(quotes)}, all verbatim slices: {verbatim}"
        )

    def opml_import_adds_sources():
        _, before = call("GET", "/v1/sources?limit=200", token=token)
        suffix = uuid.uuid4().hex[:8]
        opml = (
            '<?xml version="1.0"?><opml version="2.0"><body>'
            f'<outline text="A {suffix}" xmlUrl="http://93.184.216.34/{suffix}/a.xml"/>'
            f'<outline text="B {suffix}" xmlUrl="http://93.184.216.34/{suffix}/b.xml"/>'
            "</body></opml>"
        ).encode()
        body, ctype = multipart("file", "feeds.opml", opml, "text/xml")
        _, result = call("POST", "/v1/imports/opml", token=token, data=body, content_type=ctype)
        _, after = call("GET", "/v1/sources?limit=200", token=token)
        created = (result or {}).get("created", 0)
        n0, n1 = len(items(before)), len(items(after))
        return created == 2 and n1 - n0 == created, f"created {created}; sources {n0} -> {n1}"

    def share_is_public_until_revoked():
        slug = ctx.get("wiki_slug")
        _, share = call("POST", "/v1/shares", token=token, body={"kind": "wiki_page", "target_id": slug})
        value = (share or {}).get("token")
        public_before, _ = call("GET", f"/v1/public/shares/{value}")
        call("DELETE", f"/v1/shares/{share['id']}", token=token)
        public_after, _ = call("GET", f"/v1/public/shares/{value}")
        return (
            public_before == 200 and public_after == 404,
            f"public {public_before}, after revoke {public_after}",
        )

    for name, fn in [
        ("conflicts >= 4", conflicts),
        ("cluster HX-2 has two camps", cluster_two_camps),
        ("diffs 2026-05-01..09-01: superseded >= 2, stale >= 1", diffs_superseded_and_stale),
        ("wiki: >= 3 clean pages, every sentence cited", wiki_clean_pages),
        ("verify: four M7.6 examples give four verdicts", verify_four_examples),
        ("ask: every sentence has a verdict", ask_every_sentence_has_a_verdict),
        ("ask: method is not the extractive fallback", ask_method_is_not_extractive_fallback),
        ("two uploads add two documents", two_uploads_add_two_documents),
        ("capture is searchable", capture_becomes_searchable),
        ("wiki footnote quotes equal claim quotes", footnote_quotes_match_claims),
        ("tag filter returns only tagged documents", tag_filter_returns_only_tagged_documents),
        ("schedule has next_run_at", schedule_gets_next_run),
        ("answer export has [^1] and verbatim quotes", export_has_marker_and_verbatim_quotes),
        ("OPML import adds sources", opml_import_adds_sources),
        ("share is public and 404 after revoke", share_is_public_until_revoked),
    ]:
        check(name, fn)

    failed = RESULTS.count(False)
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
