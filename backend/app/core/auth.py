"""Clerk JWT verification + agency membership enforcement.

Two layers, both exposed as FastAPI dependencies:

1. `require_auth` — verifies the Clerk JWT against the JWKS endpoint and
   resolves the authenticated `User` row (by `users.clerk_user_id`, falling
   back to the email claim, backfilling `clerk_user_id` on first login).
2. `require_agency_member` — for routes namespaced under
   `/api/v1/agencies/{agency_id}/...`: rejects with 403 unless the
   authenticated user belongs to that agency. This is the multi-tenant
   boundary; every agency-scoped router must carry it.

For endpoints that receive `agency_id` in the body instead of the path,
call `ensure_agency_access(auth, agency_id)` inside the handler.

If CLERK_JWKS_URL is empty we fall through to a dev-only "trusted header"
mode (X-Dev-User / X-Dev-Email) that only works in development. When
X-Dev-Email matches a real user, membership is enforced as that user;
otherwise dev mode bypasses membership checks so the local dashboard works
without seeding auth state.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt.exceptions import PyJWTError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import User, UserRole
from app.db.session import get_db

log = get_logger(__name__)
_jwks_cache: dict | None = None
_jwks_cached_at: float = 0  # last *successful* fetch
_jwks_last_attempt_at: float = 0  # last fetch attempt, success or failure
_jwks_lock: asyncio.Lock | None = None
JWKS_TTL = 60 * 60  # 1h
# Floor between two forced refreshes. A token carrying a random `kid` must not
# turn into a request to Clerk: without this, anyone can make us hammer the
# JWKS endpoint from an unauthenticated route.
JWKS_MIN_REFRESH_INTERVAL = 30  # seconds
CLERK_JWT_ALGORITHM = "RS256"
CLERK_CLOCK_SKEW_SECONDS = 5


class JWKSUnavailableError(RuntimeError):
    """Nothing cached and the JWKS endpoint is not answering.

    Distinct from "this token is bad": the caller turns it into a 503, not a
    401, because we cannot tell whether the credential is valid.
    """


def _get_jwks_lock() -> asyncio.Lock:
    """Created lazily so the lock binds to the running loop, not import time."""
    global _jwks_lock
    if _jwks_lock is None:
        _jwks_lock = asyncio.Lock()
    return _jwks_lock


async def _fetch_jwks(url: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as http:
        resp = await http.get(url)
    resp.raise_for_status()
    return resp.json()


async def _get_jwks(*, force: bool = False) -> dict:
    """Clerk's signing keys, cached for `JWKS_TTL`.

    `force=True` is the key-rotation path (item 226): a token signed with a
    `kid` we don't know is the *first* symptom of a rotation, and waiting for
    the 1-hour TTL to expire means every dashboard and app user gets 401 for
    up to an hour. The refresh is serialized behind a lock and rate-limited to
    one fetch per `JWKS_MIN_REFRESH_INTERVAL`, so a burst of requests (real
    rotation) or a burst of junk kids (attacker) both cost exactly one call.

    The floor applies to *every* refresh, not only the forced one. A failed
    fetch does not move `_jwks_cached_at`, so on the ordinary TTL-expiry path
    an unreachable Clerk left each waiting request paying its own 10-second
    fetch, serialized on this lock: 100 in-flight requests became 100 calls to
    an endpoint already in trouble and roughly a quarter of an hour of frozen
    API. Same rule as the forced path — one attempt per interval, everyone
    else gets the keys we already have.
    """
    global _jwks_cache, _jwks_cached_at, _jwks_last_attempt_at
    url = settings.clerk_jwks_url
    if not url:
        return {}

    if _jwks_cache is not None and not force and (time.time() - _jwks_cached_at) < JWKS_TTL:
        return _jwks_cache

    async with _get_jwks_lock():
        # Someone may have refreshed while we waited for the lock.
        now = time.time()
        if _jwks_cache is not None and not force and (now - _jwks_cached_at) < JWKS_TTL:
            return _jwks_cache

        if (now - _jwks_last_attempt_at) < JWKS_MIN_REFRESH_INTERVAL:
            if _jwks_cache is not None:
                return _jwks_cache
            # Nothing to serve and we just tried: queueing behind another
            # 10-second timeout would only deepen the outage.
            raise JWKSUnavailableError("jwks_unavailable")

        _jwks_last_attempt_at = now
        try:
            _jwks_cache = await _fetch_jwks(url)
            _jwks_cached_at = time.time()
        except Exception as exc:  # noqa: BLE001
            log.warning("auth.jwks_fetch_failed", error=str(exc), forced=force)
            if _jwks_cache is None:
                raise
            # Keep serving the stale cache; a real miss still becomes a 401.
        return _jwks_cache


def _key_for_kid(jwks: dict, kid: str | None) -> dict | None:
    return next((k for k in (jwks or {}).get("keys", []) if k.get("kid") == kid), None)


class AuthContext:
    def __init__(
        self,
        *,
        clerk_user_id: str,
        email: str | None,
        raw_claims: dict,
        user: User | None = None,
        dev_bypass: bool = False,
    ):
        self.clerk_user_id = clerk_user_id
        self.email = email
        self.raw_claims = raw_claims
        self.user = user
        self.dev_bypass = dev_bypass

    @property
    def agency_id(self) -> uuid.UUID | None:
        return self.user.agency_id if self.user is not None else None


async def _resolve_user(
    db: AsyncSession,
    clerk_user_id: str | None,
    email: str | None,
    *,
    backfill: bool = False,
) -> User | None:
    user: User | None = None
    if clerk_user_id:
        user = (
            await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
        ).scalar_one_or_none()
    if user is None and email:
        user = (
            await db.execute(select(User).where(func.lower(User.email) == email.lower()))
        ).scalar_one_or_none()
        # First login with a verified JWT: link the Clerk identity to the row.
        if user is not None and backfill and clerk_user_id and user.clerk_user_id is None:
            user.clerk_user_id = clerk_user_id
            await db.commit()
    return user


async def require_auth(
    request: Request, db: AsyncSession = Depends(get_db)
) -> AuthContext:
    if settings.is_dev and not settings.clerk_jwks_url:
        # Dev shortcut: trust X-Dev-User headers. NEVER in prod.
        sub = request.headers.get("X-Dev-User", "dev-user")
        email = request.headers.get("X-Dev-Email")
        user = await _resolve_user(db, None, email) if email else None
        return AuthContext(
            clerk_user_id=sub,
            email=email,
            raw_claims={},
            user=user,
            dev_bypass=user is None,
        )

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing_bearer_token")
    token = auth_header[len("Bearer ") :]

    try:
        unverified_header = jwt.get_unverified_header(token)
    except PyJWTError as exc:
        log.warning("auth.jwt_error", error=str(exc))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token") from exc
    kid = unverified_header.get("kid")
    # The algorithm is policy, never token input.  Using the header value as
    # the allowlist let an attacker choose HS256 and attempt algorithm/key-type
    # confusion against the public JWKS material.
    if unverified_header.get("alg") != CLERK_JWT_ALGORITHM:
        log.warning("auth.jwt_unexpected_algorithm", alg=unverified_header.get("alg"))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token")
    if not isinstance(kid, str) or not kid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token")

    # Fetching the keys is infrastructure, not credentials: when it fails we
    # do not know whether the token is good, and answering 500 (the old
    # behaviour — httpx errors are not JWTError) told the caller "our bug"
    # when the honest answer is "come back in a moment".
    try:
        key = _key_for_kid(await _get_jwks(), kid)
        if key is None:
            # Unknown kid: Clerk most likely rotated its signing keys. Refresh
            # once, right now, instead of 401-ing every user for up to an hour.
            log.info("auth.unknown_kid.refreshing_jwks", kid=kid)
            key = _key_for_kid(await _get_jwks(force=True), kid)
    except Exception as exc:  # noqa: BLE001 — transport failure, not a bad token
        log.warning("auth.jwks_unavailable", error=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "auth_backend_unavailable"
        ) from exc

    if key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown_kid")
    if (
        not isinstance(key, dict)
        or key.get("kty") != "RSA"
        or key.get("alg") not in (None, CLERK_JWT_ALGORITHM)
        or key.get("use") not in (None, "sig")
    ):
        log.warning("auth.jwks_key_rejected", kid=kid)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token")

    try:
        expected_issuer = settings.clerk_expected_issuer
        expected_audience = settings.clerk_audience.strip() or None
        public_key = jwt.PyJWK.from_dict(key, algorithm=CLERK_JWT_ALGORITHM).key
        claims = jwt.decode(
            token,
            public_key,
            algorithms=[CLERK_JWT_ALGORITHM],
            issuer=expected_issuer,
            audience=expected_audience,
            options={
                "verify_aud": expected_audience is not None,
                "require": [
                    "exp",
                    "iat",
                    "nbf",
                    "iss",
                    "sub",
                    *(["aud"] if expected_audience is not None else []),
                ],
            },
            leeway=CLERK_CLOCK_SKEW_SECONDS,
        )
        # Clerk's standard session token intentionally has no audience.  If
        # one appears, it came from a customized token and must not be treated
        # as an unconstrained session token: configure CLERK_AUDIENCE first.
        if expected_audience is None and claims.get("aud") is not None:
            raise PyJWTError("unexpected audience")

        # Clerk documents `azp` as optional for clients with no Origin (native
        # apps).  Browsers do carry it, and accepting an arbitrary value opens
        # the subdomain-cookie/CSRF class `authorizedParties` is designed to
        # close.  Exact origins only; no wildcard or suffix matching.
        azp = claims.get("azp")
        if azp is not None and (
            not isinstance(azp, str) or azp not in settings.clerk_authorized_parties
        ):
            raise PyJWTError("invalid authorized party")
    except PyJWTError as exc:
        log.warning("auth.jwt_error", error=str(exc))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token") from exc

    clerk_user_id = claims.get("sub")
    if not isinstance(clerk_user_id, str) or not clerk_user_id.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token")
    email_claim = claims.get("email")
    email = email_claim if isinstance(email_claim, str) else None
    user = await _resolve_user(db, clerk_user_id, email, backfill=True)
    return AuthContext(
        clerk_user_id=clerk_user_id,
        email=email,
        raw_claims=claims,
        user=user,
    )


def ensure_agency_access(auth: AuthContext, agency_id: uuid.UUID) -> None:
    """Raise 403 unless the authenticated user belongs to `agency_id`."""
    if auth.dev_bypass:
        return
    if (
        auth.user is None
        or not auth.user.is_active
        or auth.user.agency_id != agency_id
    ):
        log.warning(
            "auth.agency_access_denied",
            clerk_user_id=auth.clerk_user_id,
            requested_agency=str(agency_id),
            user_agency=str(auth.agency_id) if auth.agency_id else None,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "agency_access_denied")


async def require_agency_member(
    agency_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> AuthContext:
    """Dependency for routers whose path contains `{agency_id}`."""
    ensure_agency_access(auth, agency_id)
    return auth


def require_role(*roles: UserRole):
    """Dependency factory: 403 unless the caller holds one of `roles`.

    Third layer on top of `require_auth` (who are you) and
    `require_agency_member` (are you in this agency): *what* may you do inside
    it. `UserRole` has existed since the first migration and nothing checked
    it, so any commercial agent could erase a lead irreversibly — unacceptable
    for an owner paying 399 EUR/month (item 175).

    Rejections carry a machine-readable body, `{"code": "role_required", ...}`,
    so the dashboard can hide the action instead of showing a raw error.

    Dev bypass (`X-Dev-*` headers with no Clerk configured) is honoured, like
    `ensure_agency_access`: there is no user row to carry a role, and the
    local dashboard has to work.
    """
    allowed = frozenset(roles)

    async def _dependency(auth: AuthContext = Depends(require_auth)) -> AuthContext:
        if auth.dev_bypass:
            return auth
        user = auth.user
        if user is None or not user.is_active or user.role not in allowed:
            log.warning(
                "auth.role_denied",
                clerk_user_id=auth.clerk_user_id,
                required=[r.value for r in allowed],
                actual=getattr(getattr(user, "role", None), "value", None),
            )
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "role_required",
                    "required": sorted(r.value for r in allowed),
                },
            )
        return auth

    return _dependency


# Convenience aliases for routers
AuthDep = Depends(require_auth)
AgencyMemberDep = Depends(require_agency_member)
# Irreversible, agency-wide actions (GDPR erasure today; subscription
# cancellation and AI/voice settings in the next wave).
OwnerOnlyDep = Depends(require_role(UserRole.OWNER, UserRole.ADMIN))
