"""Authentication and request protection.

- local: a fixed developer identity, allowed only in the local deployment profile
  (the gateway publishes only on 127.0.0.1). Never accepted by the remote profile.
- cloudflare_access: every request must carry a Cloudflare Access JWT
  (`Cf-Access-Jwt-Assertion` header). Its signature is verified against the team's
  published signing keys, plus issuer, audience, expiry and an explicit email
  allowlist. A raw email header or an unverified token is never trusted, and
  traffic on the Docker network is not treated as proof of identity.

State-changing requests also require a custom header and a matching Origin, so a
cross-site page cannot submit them (CSRF protection; the UI and API share one origin).
"""

from __future__ import annotations

import threading

import jwt
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db import session_scope
from app.errors import AppError, Forbidden, Unauthorized
from app.models import User

CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "projections-assistant"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class AccessVerifier:
    def __init__(self, team_domain: str, audience: str, allowed: set[str], jwks_client=None):
        self.issuer = team_domain.rstrip("/")
        self.audience = audience
        self.allowed = allowed
        self._jwks = jwks_client or jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs", cache_keys=True,
                                                    lifespan=3600)
        self._lock = threading.Lock()

    def verify(self, token: str) -> str:
        try:
            with self._lock:
                key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                                options={"require": ["exp", "iat", "iss", "aud"]}, leeway=30)
        except jwt.ExpiredSignatureError as exc:
            raise Unauthorized("Your sign-in has expired. Reload the page to sign in again.") from exc
        except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
            raise Unauthorized("Sign-in could not be verified.") from exc
        email = str(claims.get("email", "")).strip().lower()
        if not email or email not in self.allowed:
            raise Forbidden("This account is not invited to use this application.")
        return email


def _get_or_create_user(email: str) -> User:
    for _ in range(2):
        try:
            with session_scope() as session:
                user = session.scalars(select(User).where(User.email == email)).first()
                if user is None:
                    user = User(email=email)
                    session.add(user)
                    session.flush()
                session.expunge(user)
                return user
        except IntegrityError:  # a concurrent first request created the same user; read it back
            continue
    raise AppError("user_unavailable", "Could not load your account. Please reload the page.", status_code=503)


class Authenticator:
    def __init__(self, settings: Settings, verifier: AccessVerifier | None = None):
        self.settings = settings
        if settings.auth_mode == "cloudflare_access":
            self.verifier = verifier or AccessVerifier(settings.cf_access_team_domain, settings.cf_access_audience,
                                                       settings.allowed_email_set)
        else:
            if settings.deployment_profile != "local":
                raise RuntimeError("local authentication is refused outside the local profile")
            self.verifier = None

    def user_for(self, request: Request) -> User:
        if self.verifier is None:
            return _get_or_create_user(self.settings.local_dev_user_email.lower())
        token = request.headers.get("cf-access-jwt-assertion")
        if not token:
            raise Unauthorized()
        return _get_or_create_user(self.verifier.verify(token))

    def check_csrf(self, request: Request) -> None:
        if request.method in SAFE_METHODS:
            return
        if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            raise AppError("csrf_rejected", "This request was blocked for security reasons. Reload the page.",
                           status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/") != self.settings.allowed_origin.rstrip("/"):
            raise AppError("csrf_rejected", "This request came from an unexpected site and was blocked.",
                           status_code=403)

    def is_owner(self, user: User) -> bool:
        return bool(self.settings.effective_owner_email) and user.email == self.settings.effective_owner_email
