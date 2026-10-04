"""M2.5 onboarding: register -> login -> me -> PAT -> use PAT -> revoke."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.db

EMAIL = "onboarding@test.local"
PASSWORD = "onboarding-password-123"


def test_register_login_me_pat_revoke(client, db):
    # --- register
    resp = client.post("/v1/auth/register", json={"email": EMAIL, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["user"]["email"] == EMAIL
    # Nobody exists yet, so the first account owns the instance.
    assert body["user"]["is_admin"] is True

    # --- duplicate email is a 409, not a 500
    dup = client.post("/v1/auth/register", json={"email": EMAIL, "password": PASSWORD})
    assert dup.status_code == 409, dup.text

    # --- login
    anon = client
    client.headers.pop("Authorization", None)
    login = anon.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    auth = {"Authorization": f"Bearer {token}"}

    # --- me
    me = anon.get("/v1/auth/me", headers=auth)
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL

    # --- create a PAT; the plaintext appears exactly once
    created = anon.post("/v1/tokens", json={"name": "laptop"}, headers=auth)
    assert created.status_code == 201, created.text
    pat_body = created.json()
    plaintext = pat_body["token"]
    assert plaintext.startswith("sh_pat_")
    token_id = pat_body["id"]

    # Listing never returns the plaintext again.
    listed = anon.get("/v1/tokens", headers=auth).json()
    assert len(listed) == 1
    assert listed[0]["token"] is None

    # --- use the PAT
    pat_auth = {"Authorization": f"Bearer {plaintext}"}
    assert anon.get("/v1/auth/me", headers=pat_auth).status_code == 200

    # --- revoke, then the PAT is dead
    assert anon.delete(f"/v1/tokens/{token_id}", headers=auth).status_code == 204
    assert anon.get("/v1/auth/me", headers=pat_auth).status_code == 403


def test_wrong_password_and_unknown_user_are_indistinguishable(client):
    client.post("/v1/auth/register", json={"email": EMAIL, "password": PASSWORD})
    client.headers.pop("Authorization", None)

    bad_password = client.post("/v1/auth/login", json={"email": EMAIL, "password": "wrong-password-x"})
    no_such_user = client.post(
        "/v1/auth/login", json={"email": "nobody@test.local", "password": "wrong-password-x"}
    )
    assert bad_password.status_code == no_such_user.status_code == 403
    # Identical bodies: the endpoint must not be usable to enumerate accounts.
    assert bad_password.json() == no_such_user.json()


def test_short_password_is_rejected(client):
    resp = client.post("/v1/auth/register", json={"email": "short@test.local", "password": "tooshort"})
    assert resp.status_code == 422, resp.text


def test_registration_can_be_disabled(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "allow_registration", False)
    resp = client.post("/v1/auth/register", json={"email": "nope@test.local", "password": PASSWORD})
    assert resp.status_code == 403, resp.text


def test_read_only_token_cannot_write(client, db):

    resp = client.post("/v1/auth/register", json={"email": "ro@test.local", "password": PASSWORD})
    auth = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    client.headers.pop("Authorization", None)

    made = client.post("/v1/tokens", json={"name": "ro", "scopes": ["read"]}, headers=auth)
    assert made.status_code == 201, made.text
    pat = {"Authorization": f"Bearer {made.json()['token']}"}
    assert client.get("/v1/tokens", headers=pat).status_code == 200
    # Writing with a read-scoped token must be refused.
    assert client.post("/v1/tokens", json={"name": "x"}, headers=pat).status_code == 403


def test_login_rate_limit(client):
    """Off by default in tests; the limiter is explicitly enabled here."""
    client.app.state.login_limit_enabled = True
    for _ in range(10):
        client.post("/v1/auth/login", json={"email": "nobody@test.local", "password": "x" * 12})
    resp = client.post("/v1/auth/login", json={"email": "nobody@test.local", "password": "x" * 12})
    assert resp.status_code == 429, resp.text
    client.app.state.login_limit_enabled = False


def test_garbage_token_is_rejected(client):
    for bad in ("nonsense", "sh_pat_not-a-real-token", ""):
        resp = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {bad}"})
        assert resp.status_code in (403, 422), (bad, resp.status_code, resp.text)


def test_token_of_another_user_is_not_even_acknowledged(client, make_client):
    """Revoking someone else's token must 404, not 403 -- a 403 confirms the id exists."""

    a = client.post("/v1/auth/register", json={"email": "a@test.local", "password": PASSWORD}).json()
    made = client.post(
        "/v1/tokens", json={"name": "t"}, headers={"Authorization": f"Bearer {a['access_token']}"}
    ).json()

    other = make_client(email="b@test.local")
    resp = other.delete(f"/v1/tokens/{made['id']}")
    assert resp.status_code == 404, resp.text

    # The failed attempt must not have revoked anything: it still works for its owner.
    still = client.request("GET", "/v1/tokens", headers={"Authorization": f"Bearer {made['token']}"})
    assert still.status_code == 200, still.text


def test_jwt_roundtrip():
    from app.core.security import create_access_token, decode_access_token

    uid = uuid.uuid4()
    assert decode_access_token(create_access_token(uid)) == uid
