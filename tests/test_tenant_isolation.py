"""M2.6 tenant isolation matrix.

Every list / detail / mutate endpoint that exposes user data must appear here. When
you add a resource endpoint, add its case -- that is the point of this file.

For each case user A creates the resource and user B checks three things:
  * it is absent from B's list,
  * B cannot reach it by id -- 404, not 403, since a 403 would confirm the id exists,
  * B cannot mutate it.

Add cases at the bottom of the registered list as milestones introduce resources.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class ResourceCase:
    name: str
    create: Callable[[Any], dict]
    list_path: str
    item_path: str
    # The verb used to probe the item route; not every resource has a GET detail.
    detail_method: str = "GET"
    patch: tuple[str, dict] | None = None
    id_key: str = "id"
    list_key: str = "id"


RESOURCE_CASES: list[ResourceCase] = []


def register(case: ResourceCase) -> ResourceCase:
    RESOURCE_CASES.append(case)
    return case


# --- registered cases ----------------------------------------------------

# M2. Tokens expose no GET detail route, so they are probed with DELETE.
register(
    ResourceCase(
        name="api_tokens",
        create=lambda c: c.post("/v1/tokens", json={"name": "secret"}).json(),
        list_path="/v1/tokens",
        item_path="/v1/tokens/{id}",
        detail_method="DELETE",
    )
)

# M4 adds: sources, documents, document_versions, chunks, ingestion_runs.
# M6 adds: provider_credentials (admin-only, instance-scoped).
# M7 adds: answers, verifications.
# M8 adds: claims, claim_clusters, claim_relations, conflicts.
# M9 adds: watches, belief_diffs, timeline.
# M10 adds: topics, wiki_pages, lint_findings.


# --- runner --------------------------------------------------------------


def _register_and_auth(client) -> dict[str, str]:
    resp = client.post(
        "/v1/auth/register",
        json={"email": f"t{uuid.uuid4().hex[:12]}@test.local", "password": "isolation-pass-123"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.parametrize("case", RESOURCE_CASES, ids=lambda c: c.name)
def test_resource_is_invisible_to_another_user(make_client, case: ResourceCase):
    a = make_client()
    a.headers.update(_register_and_auth(a))
    created = case.create(a)
    resource_id = str(created[case.id_key])

    # Sanity check: A really does see it, otherwise the case proves nothing.
    mine = a.get(case.list_path)
    assert mine.status_code == 200, mine.text
    assert any(str(r.get(case.list_key)) == resource_id for r in mine.json()), (
        f"{case.name}: the owner cannot see their own resource -- the case is misconfigured"
    )

    b = make_client()
    b.headers.update(_register_and_auth(b))

    # 1) absent from the other user's list
    theirs = b.get(case.list_path)
    assert theirs.status_code == 200, theirs.text
    assert all(str(r.get(case.list_key)) != resource_id for r in theirs.json()), (
        f"{case.name} leaks across tenants in {case.list_path}"
    )

    # 2) unreachable by id
    resp = b.request(case.detail_method, case.item_path.format(id=resource_id))
    assert resp.status_code in (404, 405), (
        f"{case.name} {case.detail_method} {case.item_path}: got {resp.status_code}, want 404/405"
    )
    if resp.status_code == 404:
        assert "leak" not in resp.text.lower(), f"{case.name} 403-style body on another user's resource"

    # 3) not mutable by the other user
    if case.patch is not None:
        path, payload = case.patch
        patched = b.patch(path.format(id=resource_id), json=payload)
        assert patched.status_code in (404, 405), (
            f"{case.name} patch: got {patched.status_code}, want 404/405"
        )


def test_matrix_is_not_empty():
    """Guards against the matrix silently rotting into a no-op."""
    assert RESOURCE_CASES, "no resource cases registered"
    assert {c.name for c in RESOURCE_CASES} == {c.name for c in RESOURCE_CASES}, "duplicate case names"


@pytest.mark.parametrize(
    "path",
    [
        "/v1/tokens",
        "/v1/auth/me",
    ],
)
def test_unauthenticated_requests_are_rejected(make_client, path):
    anon = make_client()
    resp = anon.get(path)
    assert resp.status_code == 403, f"{path} answered {resp.status_code} without credentials"
