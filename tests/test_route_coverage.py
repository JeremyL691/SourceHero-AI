"""Guards against newly added routes lacking tenant isolation verification.

Every route declared in FastAPI must either be covered by the RESOURCE_CASES
matrix in tests/test_tenant_isolation.py, or be documented in ROUTE_ALLOWLIST
with an explicit security rationale.
"""

from __future__ import annotations

import re

from app.main import create_app
from tests.test_tenant_isolation import RESOURCE_CASES

ROUTE_ALLOWLIST: dict[str, str] = {
    "/v1/collections/{}/items": "tenant ownership: owned_collection and owned_document are checked before any change; cross-tenant 404 asserted in tests/test_library_api.py",
    "/v1/documents/{}/tags": "tenant ownership: owned_document and owned_tag are checked before any change; cross-tenant 404 asserted in tests/test_library_api.py",
    "/v1/documents/{}/flags": "tenant ownership: owned_document is checked before any change; cross-tenant 404 asserted in tests/test_library_api.py",
    "/healthz": "infrastructure liveness check, unauthenticated and non-tenant",
    "/readyz": "infrastructure readiness check, unauthenticated and non-tenant",
    "/v1/auth/login": "public authentication endpoint (credentials verification)",
    "/v1/auth/register": "public user registration endpoint",
    "/v1/auth/me": "current authenticated user profile endpoint",
    "/metrics": "operator endpoint for Prometheus: 404 unless METRICS_TOKEN is set, bearer-token protected; aggregate counts only, no tenant data (access control asserted in tests/test_metrics.py)",
    "/v1/auth/logout-all": "signs out only the caller's own tokens (token_version); there is no other user's resource to scope",
    "/v1/search": "tested by test_search_and_ask_isolation (cross-tenant leakage assertions)",
    "/v1/ask": "tested by test_search_and_ask_isolation (cross-tenant leakage assertions)",
    "/v1/verify": "action endpoint, stateless or input-based verification",
    "/v1/verify/quick": "action endpoint, stateless quick claim verification",
    "/v1/captures": "creation action endpoint that creates sources and document versions",
    "/v1/captures/parse": "stateless URL parse preview endpoint, no user data returned",
    "/v1/demo/seed": "development demo database seeding endpoint",
    "/v1/documents/{}/claims": "cross-tenant ownership tested via test_mixed_id_document_claims",
    "/v1/documents/{}/diff": "cross-tenant ownership tested via test_mixed_id_document_diff_from_to",
    "/v1/index/reindex": "action endpoint triggering reindexing of current user's versions",
    "/v1/settings/llm": "user-scoped LLM settings endpoint",
    "/v1/settings/llm/credentials": "user-scoped LLM credentials endpoint",
    "/v1/settings/llm/models": "catalog endpoint listing available models for configured provider",
    "/v1/settings/llm/oauth/callback": "OAuth redirect callback endpoint",
    "/v1/settings/llm/oauth/start": "OAuth flow initiation endpoint",
    "/v1/settings/llm/test": "user-scoped LLM credential verification endpoint",
    "/v1/sources/upload": "action endpoint uploading files into user-scoped sources",
    "/v1/usage": "user-scoped usage statistics endpoint",
    "/v1/wiki/export": "tested by test_wiki_export_isolation (tenant export archive check)",
    "/v1/wiki/lint": "user-scoped wiki lint summary report",
    "/v1/wiki/plan": "action endpoint triggering topic replan for current user",
    "/v1/evals/runs": "instance-level evaluation run benchmarks, non-tenant data",
    "/v1/evals/runs/{}": "instance-level evaluation run detail benchmark, non-tenant data",
    "/v1/jobs/summary": "aggregate queue summary for current user jobs (and system jobs for admin)",
}


def _normalize_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def _get_covered_routes() -> set[str]:
    covered = set()
    for case in RESOURCE_CASES:
        if case.list_path:
            covered.add(_normalize_path(case.list_path))
        if case.item_path:
            covered.add(_normalize_path(case.item_path))
        for _method, p in case.detail_paths:
            covered.add(_normalize_path(p))
        for _method, p, _payload in case.mutations:
            covered.add(_normalize_path(p))
        if case.patch:
            covered.add(_normalize_path(case.patch[0]))
    return covered


def test_all_routes_are_covered_or_allowlisted():
    app = create_app()
    openapi = app.openapi()
    all_paths = set(openapi.get("paths", {}).keys())

    covered = _get_covered_routes()

    missing = []
    for path in sorted(all_paths):
        norm = _normalize_path(path)
        if norm not in covered and norm not in ROUTE_ALLOWLIST:
            missing.append(path)

    assert not missing, (
        "The following routes lack tenant isolation coverage or an allowlist entry:\n"
        + "\n".join(f"  - {m}" for m in missing)
        + "\nAdd them to RESOURCE_CASES in tests/test_tenant_isolation.py or document in ROUTE_ALLOWLIST."
    )


def test_allowlist_entries_are_valid():
    app = create_app()
    openapi = app.openapi()
    all_norm_paths = {_normalize_path(p) for p in openapi.get("paths", {})}
    covered = _get_covered_routes()

    for path, reason in ROUTE_ALLOWLIST.items():
        assert path in all_norm_paths, f"ROUTE_ALLOWLIST contains non-existent route: {path}"
        assert len(reason.strip()) >= 15, (
            f"ROUTE_ALLOWLIST entry for {path} must provide a substantial rationale"
        )
        assert path not in covered, f"ROUTE_ALLOWLIST entry for {path} is already covered by RESOURCE_CASES"
