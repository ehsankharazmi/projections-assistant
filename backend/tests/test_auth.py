"""Cloudflare Access verification and remote-profile configuration (mocked signing keys)."""

from __future__ import annotations

import datetime as dt

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import AccessVerifier
from app.config import Settings
from tests.conftest import H

TEAM = "https://example-team.cloudflareaccess.com"
AUD = "test-audience-tag"
PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWKS:
    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": PRIVATE.public_key()})()


def token(email="owner@example.com", aud=AUD, iss=TEAM, exp_minutes=10, key=PRIVATE):
    now = dt.datetime.now(dt.UTC)
    return jwt.encode({"email": email, "aud": aud, "iss": iss, "iat": now, "exp": now + dt.timedelta(minutes=exp_minutes)},
                      key, algorithm="RS256")


REMOTE = dict(auth_mode="cloudflare_access", deployment_profile="remote", cf_access_team_domain=TEAM,
              cf_access_audience=AUD, allowed_emails="owner@example.com,tester@example.com",
              owner_email="owner@example.com", allowed_origin="https://assistant.example.com")


@pytest.fixture
def remote_client(make_client):
    return make_client(access_verifier=AccessVerifier(TEAM, AUD, {"owner@example.com", "tester@example.com"},
                                                      jwks_client=FakeJWKS()), **REMOTE)


def test_valid_token_and_per_user_isolation(remote_client):
    owner = {"Cf-Access-Jwt-Assertion": token()}
    tester = {"Cf-Access-Jwt-Assertion": token("tester@example.com")}
    assert remote_client.get("/api/me", headers=owner).json()["is_owner"] is True
    assert remote_client.get("/api/me", headers=tester).json()["is_owner"] is False
    wb = remote_client.post("/api/workbooks/demo", headers={**H, **owner}).json()["workbook"]
    for path in (f"/api/workbooks/{wb['id']}", f"/api/workbooks/{wb['id']}/versions",
                 f"/api/workbooks/{wb['id']}/export?version_id={wb['active_version']['id']}"):
        assert remote_client.get(path, headers=tester).status_code == 404
    assert remote_client.get("/api/admin/usage", headers=tester).status_code == 403


@pytest.mark.parametrize("bad,status", [
    (None, 401),
    (token(exp_minutes=-5), 401),
    (token(aud="wrong"), 401),
    (token(iss="https://evil.cloudflareaccess.com"), 401),
    (token(key=OTHER), 401),
    ("not-a-jwt", 401),
    (token("stranger@example.com"), 403),
])
def test_rejected_identities(remote_client, bad, status):
    headers = {"Cf-Access-Jwt-Assertion": bad} if bad else {}
    for path in ("/api/me", "/api/workbooks", "/api/jobs/job_00000000000000000000"):
        assert remote_client.get(path, headers=headers).status_code == status
    # A raw email header is never trusted.
    assert remote_client.get("/api/me", headers={"Cf-Access-Authenticated-User-Email": "owner@example.com"}
                             ).status_code == 401


def test_remote_profile_refuses_local_auth_and_requires_access_config():
    with pytest.raises(ValueError, match="refuses local authentication"):
        Settings(deployment_profile="remote", auth_mode="local", _env_file=None)
    with pytest.raises(ValueError, match="CF_ACCESS"):
        Settings(deployment_profile="remote", auth_mode="cloudflare_access", _env_file=None)
    with pytest.raises(ValueError, match="https"):
        Settings(**{**REMOTE, "cf_access_team_domain": "http://x.cloudflareaccess.com"}, _env_file=None)


def test_origin_must_match_in_remote_profile(remote_client):
    owner = {"Cf-Access-Jwt-Assertion": token(), **H}
    assert remote_client.post("/api/workbooks/demo", headers={**owner, "Origin": "http://127.0.0.1:8080"}
                              ).status_code == 403
    assert remote_client.post("/api/workbooks/demo", headers={**owner, "Origin": "https://assistant.example.com"}
                              ).status_code == 201


def test_concurrent_first_requests_create_one_user(make_client):
    """A fresh page load fires several requests at once; each must succeed with the same user."""
    from concurrent.futures import ThreadPoolExecutor
    client = make_client()
    with ThreadPoolExecutor(8) as pool:
        statuses = list(pool.map(lambda _: client.get("/api/me").status_code, range(16)))
    assert statuses == [200] * 16
    from sqlalchemy import func, select

    from app.db import session_scope
    from app.models import User
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
