"""M2.5 onboarding: register -> login -> me -> PAT -> use PAT -> revoke."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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
    assert anon.get("/v1/auth/me", headers=pat_auth).status_code == 401


def test_wrong_password_and_unknown_user_are_indistinguishable(client):
    client.post("/v1/auth/register", json={"email": EMAIL, "password": PASSWORD})
    client.headers.pop("Authorization", None)

    bad_password = client.post("/v1/auth/login", json={"email": EMAIL, "password": "wrong-password-x"})
    no_such_user = client.post(
        "/v1/auth/login", json={"email": "nobody@test.local", "password": "wrong-password-x"}
    )
    assert bad_password.status_code == no_such_user.status_code == 401
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


def test_garbage_token_is_rejected(client):
    for bad in ("nonsense", "sh_pat_not-a-real-token", ""):
        resp = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {bad}"})
        assert resp.status_code in (401, 422), (bad, resp.status_code, resp.text)


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
    assert decode_access_token(create_access_token(uid, token_version=3)) == (uid, 3)


def test_jwt_without_token_version_claim_is_rejected():
    import jwt

    from app.core.config import get_settings
    from app.core.security import decode_access_token

    now = int(datetime.now(UTC).timestamp())
    legacy = jwt.encode(
        {"sub": str(uuid.uuid4()), "iat": now, "exp": now + 600, "typ": "access"},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(legacy)


def test_jwt_lifetime_follows_jwt_ttl_hours(monkeypatch):
    import jwt

    from app.core.config import get_settings
    from app.core.security import create_access_token

    monkeypatch.setattr(get_settings(), "jwt_ttl_hours", 24)
    claims = jwt.decode(create_access_token(uuid.uuid4()), get_settings().jwt_secret, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 24 * 3600


def test_logout_all_invalidates_tokens_issued_before_it(client):
    """A token issued before logout-all stops working; one issued after it works."""
    reg = client.post("/v1/auth/register", json={"email": "logout@test.local", "password": PASSWORD})
    assert reg.status_code == 201, reg.text
    old = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    assert client.get("/v1/auth/me", headers=old).status_code == 200

    # A second session signed in on another device, before the logout.
    login = client.post("/v1/auth/login", json={"email": "logout@test.local", "password": PASSWORD})
    other = {"Authorization": f"Bearer {login.json()['access_token']}"}

    out = client.post("/v1/auth/logout-all", headers=old)
    assert out.status_code == 204, out.text

    for stale in (old, other):
        resp = client.get("/v1/auth/me", headers=stale)
        assert resp.status_code == 401, resp.text
        assert resp.json()["error"]["code"] == "invalid_token"

    fresh = client.post("/v1/auth/login", json={"email": "logout@test.local", "password": PASSWORD})
    assert fresh.status_code == 200, fresh.text
    new = {"Authorization": f"Bearer {fresh.json()['access_token']}"}
    assert client.get("/v1/auth/me", headers=new).status_code == 200


def test_logout_all_needs_authentication(client):
    client.headers.pop("Authorization", None)
    assert client.post("/v1/auth/logout-all").status_code == 401


def test_logout_all_does_not_touch_other_users(client, make_client):
    a = client.post("/v1/auth/register", json={"email": "a-out@test.local", "password": PASSWORD}).json()
    other = make_client(email="b-out@test.local", password=PASSWORD)

    client.post("/v1/auth/logout-all", headers={"Authorization": f"Bearer {a['access_token']}"})
    assert other.get("/v1/auth/me").status_code == 200


def test_auth_401_vs_403_distinction(client, make_client):
    """SEC-5: 401 for unauthenticated/invalid token (with WWW-Authenticate), 403 for insufficient scope/admin."""
    # 1. Unauthenticated -> 401 with WWW-Authenticate
    resp_unauth = client.get("/v1/auth/me")
    assert resp_unauth.status_code == 401, resp_unauth.text
    assert resp_unauth.headers.get("www-authenticate") == "Bearer"

    # 2. Invalid token -> 401 with WWW-Authenticate
    resp_invalid = client.get("/v1/auth/me", headers={"Authorization": "Bearer invalid_garbage_token"})
    assert resp_invalid.status_code == 401, resp_invalid.text
    assert resp_invalid.headers.get("www-authenticate") == "Bearer"

    # 3. Non-admin accessing admin route -> 403
    non_admin = make_client(email="user@test.local", admin=False)
    resp_admin = non_admin.get("/v1/settings/llm")
    assert resp_admin.status_code == 403, resp_admin.text
    assert resp_admin.json()["error"]["code"] == "admin_required"


def test_read_only_admin_pat_cannot_put_settings_llm(client, db):
    """SEC-4: Admin PAT with only 'read' scope cannot call PUT /settings/llm (403)."""
    # First user is admin
    reg = client.post("/v1/auth/register", json={"email": "admin_ro@test.local", "password": PASSWORD}).json()
    jwt_auth = {"Authorization": f"Bearer {reg['access_token']}"}

    # Issue read-only PAT
    pat_res = client.post(
        "/v1/tokens", json={"name": "admin-ro", "scopes": ["read"]}, headers=jwt_auth
    ).json()
    pat_auth = {"Authorization": f"Bearer {pat_res['token']}"}

    # Read endpoint succeeds
    assert client.get("/v1/settings/llm", headers=pat_auth).status_code == 200

    # Write endpoint fails with 403
    resp_put = client.put("/v1/settings/llm", json={"json_mode": True}, headers=pat_auth)
    assert resp_put.status_code == 403, resp_put.text
    assert resp_put.json()["error"]["code"] == "insufficient_scope"


def test_pat_cannot_issue_pat(client):
    """SEC-4: A PAT cannot be used to issue another PAT (only JWT session can)."""
    reg = client.post(
        "/v1/auth/register", json={"email": "pat_issuer@test.local", "password": PASSWORD}
    ).json()
    jwt_auth = {"Authorization": f"Bearer {reg['access_token']}"}

    # Create parent PAT with read and write
    pat_res = client.post(
        "/v1/tokens", json={"name": "parent-pat", "scopes": ["read", "write"]}, headers=jwt_auth
    ).json()
    pat_auth = {"Authorization": f"Bearer {pat_res['token']}"}

    # Try to use parent PAT to create child PAT -> 403
    child_resp = client.post("/v1/tokens", json={"name": "child-pat", "scopes": ["read"]}, headers=pat_auth)
    assert child_resp.status_code == 403, child_resp.text
    assert child_resp.json()["error"]["code"] in ("pat_issuance_forbidden", "pat_cannot_issue_pat")


def test_empty_scope_is_rejected(client):
    """SEC-4: TokenCreateIn rejects empty scopes with 422."""
    reg = client.post(
        "/v1/auth/register", json={"email": "empty_scope@test.local", "password": PASSWORD}
    ).json()
    jwt_auth = {"Authorization": f"Bearer {reg['access_token']}"}

    resp = client.post("/v1/tokens", json={"name": "empty", "scopes": []}, headers=jwt_auth)
    assert resp.status_code == 422, resp.text


def test_concurrent_registration_only_one_admin(db):
    """SEC-6: Concurrent initial registrations result in exactly one admin user."""
    import concurrent.futures

    from app.api.routers.auth import register_user
    from app.core.db import SessionLocal
    from app.models.user import User

    # Empty user table for this test
    db.query(User).delete()
    db.commit()

    emails = [f"concurrent_{i}@test.local" for i in range(5)]

    def do_register(email: str):
        with SessionLocal() as s:
            return register_user(s, email, PASSWORD, None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        users = list(executor.map(do_register, emails))

    admins = [u for u in users if u.is_admin]
    assert len(admins) == 1, f"Expected exactly 1 admin, got {len(admins)}"
