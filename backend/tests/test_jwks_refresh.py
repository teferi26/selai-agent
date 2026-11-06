"""Item 226 — an unknown `kid` refreshes the JWKS immediately.

When Clerk rotates its signing keys, every token starts arriving with a
`kid` we have never seen. With a 1-hour cache and no refresh path that means
up to 60 minutes of 401 for every dashboard and app user: a self-inflicted
total outage. The fix refreshes once, on the spot — behind a lock so a burst
costs one fetch, and with a floor so a stream of junk `kid`s cannot turn an
unauthenticated route into a hammer aimed at Clerk.
"""
from __future__ import annotations

import asyncio
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core import auth as auth_module
from app.core.settings import settings
from app.db.session import get_db

JWKS_URL = "https://clerk.example.test/.well-known/jwks.json"
ISSUER = "https://clerk.example.test"
ALLOWED_AZP = "https://www.selai.es"


_TEST_KEYS: dict[tuple[str, str], tuple[bytes, dict]] = {}


def _b64uint(value: int) -> str:
    size = max(1, (value.bit_length() + 7) // 8)
    import base64

    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode()


def _rsa_material(kid: str, label: str) -> tuple[bytes, dict]:
    cache_key = (kid, label)
    if cache_key not in _TEST_KEYS:
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = private.public_key().public_numbers()
        pem = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        _TEST_KEYS[cache_key] = (
            pem,
            {
                "kty": "RSA",
                "kid": kid,
                "alg": "RS256",
                "use": "sig",
                "n": _b64uint(public.n),
                "e": _b64uint(public.e),
            },
        )
    return _TEST_KEYS[cache_key]


def _rsa_jwk(kid: str, label: str) -> dict:
    return _rsa_material(kid, label)[1]


def _token(kid: str, label: str, *, omit: set[str] | None = None, **claims) -> str:
    now = int(time.time())
    payload = {
        "sub": "user_123",
        "email": "owner@inmo.test",
        "iss": ISSUER,
        "iat": now - 1,
        "nbf": now - 1,
        "exp": now + 60,
        **claims,
    }
    for claim in omit or set():
        payload.pop(claim, None)
    private_pem, _jwk = _rsa_material(kid, label)
    return jwt.encode(
        payload, private_pem, algorithm="RS256", headers={"kid": kid}
    )


@pytest.fixture(autouse=True)
def _clean_jwks_cache(monkeypatch):
    monkeypatch.setattr(settings, "clerk_jwks_url", JWKS_URL)
    monkeypatch.setattr(settings, "clerk_issuer", "")
    monkeypatch.setattr(settings, "clerk_audience", "")
    monkeypatch.setattr(
        settings,
        "clerk_authorized_parties",
        ["https://selai.es", "https://www.selai.es"],
    )
    auth_module._jwks_cache = None
    auth_module._jwks_cached_at = 0
    auth_module._jwks_last_attempt_at = 0
    auth_module._jwks_lock = None
    yield
    auth_module._jwks_cache = None
    auth_module._jwks_cached_at = 0
    auth_module._jwks_last_attempt_at = 0
    auth_module._jwks_lock = None


class Clerk:
    """Stands in for the JWKS endpoint, counting fetches."""

    def __init__(self, *keys: dict):
        self.keys = list(keys)
        self.fetches = 0
        self.fail = False
        self.delay = 0.0

    async def fetch(self, url: str) -> dict:
        self.fetches += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("clerk is down")
        return {"keys": list(self.keys)}


@pytest.fixture
def clerk(monkeypatch):
    c = Clerk(_rsa_jwk("old-key", "s3cret-old"))
    monkeypatch.setattr(auth_module, "_fetch_jwks", c.fetch)
    return c


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


async def test_the_cache_is_used_within_the_ttl(clerk):
    await auth_module._get_jwks()
    await auth_module._get_jwks()
    assert clerk.fetches == 1


def _age_the_last_attempt() -> None:
    """Pretend the last fetch happened long enough ago to clear the floor."""
    auth_module._jwks_last_attempt_at -= auth_module.JWKS_MIN_REFRESH_INTERVAL + 1


async def test_forcing_refetches_a_cache_that_is_still_inside_its_ttl(clerk):
    """This is the whole point: the TTL says "fresh", the unknown kid says
    "no it isn't", and the kid wins."""
    await auth_module._get_jwks()
    _age_the_last_attempt()
    clerk.keys.append(_rsa_jwk("new-key", "s3cret-new"))

    jwks = await auth_module._get_jwks(force=True)
    assert clerk.fetches == 2
    assert auth_module._key_for_kid(jwks, "new-key") is not None


async def test_a_forced_refresh_is_rate_limited(clerk):
    """Otherwise a token with a random kid is a free request to Clerk, from
    an endpoint that needs no authentication to reach. The floor counts from
    the last fetch of any kind: keys pulled a second ago have nothing newer
    to offer, whoever asks."""
    await auth_module._get_jwks()
    for _ in range(20):
        await auth_module._get_jwks(force=True)
    assert clerk.fetches == 1


async def test_the_floor_expires(clerk):
    await auth_module._get_jwks()
    _age_the_last_attempt()
    await auth_module._get_jwks(force=True)
    assert clerk.fetches == 2


async def test_a_burst_of_unknown_kids_costs_one_fetch(clerk):
    """Stampede guard: a real rotation hits every in-flight request at once."""
    await auth_module._get_jwks()
    _age_the_last_attempt()
    clerk.delay = 0.05

    await asyncio.gather(*[auth_module._get_jwks(force=True) for _ in range(10)])
    assert clerk.fetches == 2


async def test_a_failed_refresh_keeps_serving_the_old_keys(clerk):
    """Clerk being down must not invalidate the users we can still verify."""
    await auth_module._get_jwks()
    clerk.fail = True
    jwks = await auth_module._get_jwks(force=True)
    assert auth_module._key_for_kid(jwks, "old-key") is not None


async def test_a_failed_first_fetch_propagates(clerk):
    """Nothing cached and Clerk unreachable: there is no honest answer."""
    clerk.fail = True
    with pytest.raises(RuntimeError):
        await auth_module._get_jwks()


# ---------------------------------------------------------------------------
# Stampede: the floor has to cover the ordinary refresh too, not only `force`
# ---------------------------------------------------------------------------


async def test_an_expired_cache_and_a_dead_clerk_cost_one_fetch(clerk):
    """The hole the `force`-only floor left open.

    A failed fetch does not move `_jwks_cached_at`, so once the TTL has
    expired every request thinks the cache is stale and takes the lock. With
    Clerk unreachable each one then pays its own 10-second timeout, one after
    another: a hundred in-flight requests became a hundred calls to an
    endpoint already in trouble and roughly a quarter of an hour of frozen
    API, for a rotation that had not even happened.
    """
    await auth_module._get_jwks()
    clerk.fail = True
    auth_module._jwks_cached_at -= auth_module.JWKS_TTL + 1  # cache now stale
    _age_the_last_attempt()

    results = await asyncio.gather(
        *[auth_module._get_jwks() for _ in range(25)]
    )

    assert clerk.fetches == 2  # the warm-up plus exactly one retry
    # And every one of those requests was still served the keys we have.
    assert all(auth_module._key_for_kid(r, "old-key") is not None for r in results)


async def test_the_stale_cache_keeps_serving_while_the_floor_holds(clerk):
    """Stale keys still verify every token signed before the rotation. Going
    blind because we could not refresh would be the worse failure."""
    await auth_module._get_jwks()
    clerk.fail = True
    auth_module._jwks_cached_at -= auth_module.JWKS_TTL + 1

    jwks = await auth_module._get_jwks()
    assert auth_module._key_for_kid(jwks, "old-key") is not None
    assert clerk.fetches == 1  # blocked by the floor, not retried


async def test_nothing_cached_and_the_floor_active_raises_instead_of_queueing(clerk):
    """Cold start against a dead Clerk: with no keys to serve, queueing
    behind another timeout only deepens the outage."""
    clerk.fail = True
    with pytest.raises(RuntimeError):
        await auth_module._get_jwks()

    with pytest.raises(auth_module.JWKSUnavailableError):
        await auth_module._get_jwks()
    assert clerk.fetches == 1


async def test_no_jwks_url_short_circuits(clerk, monkeypatch):
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    assert await auth_module._get_jwks() == {}
    assert clerk.fetches == 0


# ---------------------------------------------------------------------------
# End to end through require_auth
# ---------------------------------------------------------------------------


class FakeResult:
    def scalar_one_or_none(self):
        return None


class FakeSession:
    async def execute(self, stmt):
        return FakeResult()

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def client():
    mini = FastAPI()

    @mini.get("/whoami")
    async def _whoami(auth=Depends(auth_module.require_auth)):
        return {"sub": auth.clerk_user_id}

    async def _override():
        yield FakeSession()

    mini.dependency_overrides[get_db] = _override
    return TestClient(mini, raise_server_exceptions=False)


def test_a_rotated_key_is_accepted_after_one_refresh(client, clerk):
    """The rotation scenario end to end: the key the token was signed with
    only exists in the *next* JWKS fetch."""
    rotated = _rsa_jwk("new-key", "s3cret-new")
    token = _token("new-key", "s3cret-new")

    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401  # nothing cached yet, key genuinely unknown

    clerk.keys.append(rotated)
    auth_module._jwks_last_attempt_at = 0  # the floor is not what we test here

    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["sub"] == "user_123"


def test_a_genuinely_unknown_kid_is_still_401(client, clerk):
    token = _token("forged-key", "whatever")
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert "unknown_kid" in resp.text


def test_an_unknown_kid_does_not_hammer_clerk(client, clerk):
    """Twenty forged tokens, one refresh: the floor holds under attack."""
    token = _token("forged-key", "whatever")
    for _ in range(20):
        client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert clerk.fetches <= 2


def test_a_valid_token_needs_no_refresh(client, clerk):
    clerk.keys.append(_rsa_jwk("live-key", "s3cret-live"))
    token = _token("live-key", "s3cret-live")
    assert client.get("/whoami", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    before = clerk.fetches
    assert client.get("/whoami", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert clerk.fetches == before


def test_the_token_cannot_choose_a_symmetric_algorithm(client, clerk):
    """Algorithm policy is RS256 even when the header asks for HS256.

    Rejected before the JWKS request as well: obviously invalid input should
    not spend a network call to Clerk.
    """
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "user_123",
            "iss": ISSUER,
            "iat": now - 1,
            "nbf": now - 1,
            "exp": now + 60,
        },
        "attacker-controlled-secret-with-32-bytes",
        algorithm="HS256",
        headers={"kid": "old-key"},
    )
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert clerk.fetches == 0


def test_a_token_from_another_clerk_instance_is_rejected(client, clerk):
    clerk.keys.append(_rsa_jwk("live-key", "s3cret-live"))
    token = _token("live-key", "s3cret-live", iss="https://other.clerk.accounts.dev")
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert "invalid_token" in resp.text


@pytest.mark.parametrize("missing_claim", ["iss", "sub", "iat", "nbf", "exp"])
def test_required_session_claims_cannot_be_omitted(client, clerk, missing_claim):
    clerk.keys.append(_rsa_jwk("live-key", "s3cret-live"))
    token = _token("live-key", "s3cret-live", omit={missing_claim})
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_an_empty_subject_is_not_an_identity(client, clerk):
    clerk.keys.append(_rsa_jwk("live-key", "s3cret-live"))
    token = _token("live-key", "s3cret-live", sub="")
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_a_browser_token_needs_an_authorized_party(client, clerk):
    clerk.keys.append(_rsa_jwk("live-key", "s3cret-live"))
    token = _token("live-key", "s3cret-live", azp="https://malicious.example")
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_an_allowed_browser_origin_and_a_native_token_are_accepted(client, clerk):
    clerk.keys.append(_rsa_jwk("live-key", "s3cret-live"))
    browser = _token("live-key", "s3cret-live", azp=ALLOWED_AZP)
    native = _token("live-key", "s3cret-live")  # Clerk may omit azp without Origin.
    assert client.get("/whoami", headers={"Authorization": f"Bearer {browser}"}).status_code == 200
    assert client.get("/whoami", headers={"Authorization": f"Bearer {native}"}).status_code == 200


def test_audience_is_exact_when_configured(client, clerk, monkeypatch):
    clerk.keys.append(_rsa_jwk("live-key", "s3cret-live"))
    monkeypatch.setattr(settings, "clerk_audience", "https://api.selai.es")

    missing = _token("live-key", "s3cret-live")
    wrong = _token("live-key", "s3cret-live", aud="https://other-api.example")
    right = _token("live-key", "s3cret-live", aud="https://api.selai.es")

    assert client.get("/whoami", headers={"Authorization": f"Bearer {missing}"}).status_code == 401
    assert client.get("/whoami", headers={"Authorization": f"Bearer {wrong}"}).status_code == 401
    assert client.get("/whoami", headers={"Authorization": f"Bearer {right}"}).status_code == 200


def test_an_unconfigured_custom_audience_is_not_accepted(client, clerk):
    clerk.keys.append(_rsa_jwk("live-key", "s3cret-live"))
    token = _token("live-key", "s3cret-live", aud="https://api.selai.es")
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_missing_bearer_is_401(client, clerk):
    assert client.get("/whoami").status_code == 401


def test_an_unreachable_clerk_is_503_not_500(client, clerk):
    """Fetching the keys is infrastructure, not credentials. httpx errors are
    are not JWT validation errors, so they used to escape `require_auth` untouched and the
    caller got "our bug" when the honest answer is "come back in a moment" —
    and a 500 is what pages the on-call, while a 503 is what a client
    retries."""
    clerk.fail = True
    token = _token("live-key", "s3cret-live")
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 503
    assert "auth_backend_unavailable" in resp.text


def test_a_dead_clerk_does_not_turn_every_request_into_a_fetch(client, clerk):
    """The same floor, seen from the endpoint: an outage must not multiply
    itself by the number of users pressing refresh."""
    clerk.fail = True
    token = _token("live-key", "s3cret-live")
    for _ in range(15):
        assert (
            client.get("/whoami", headers={"Authorization": f"Bearer {token}"}).status_code
            == 503
        )
    assert clerk.fetches == 1


def test_an_expired_token_is_401_not_a_refresh(client, clerk):
    """Expiry is not a rotation symptom: it must not cost a fetch."""
    clerk.keys.append(_rsa_jwk("live-key", "s3cret-live"))
    token = _token("live-key", "s3cret-live", exp=int(time.time()) - 60)
    client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    before = clerk.fetches
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert clerk.fetches == before
