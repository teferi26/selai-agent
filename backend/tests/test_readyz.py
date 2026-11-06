"""Item 196 — /readyz reports one boolean per dependency, honestly.

"Conectado" used to mean "the keys exist". A revoked Meta token or a dead
Redis kept the badge green while nothing worked. This endpoint separates
"configured" from "answering", never raises, and leaves the dependency-free
/healthz process liveness endpoint untouched.

The tests build a mini app around `health.router` instead of importing
app.main: this file is about the endpoint, not the whole router table.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import health
from app.core.settings import settings
from app.db.session import get_db

EXPECTED_KEYS = {
    "postgres",
    "redis",
    "celery",
    "meta_configured",
    "vapi_configured",
    "stripe_configured",
}


class FakeSession:
    """Answers SELECT 1, or refuses to, on demand."""

    def __init__(self, ok: bool = True, hang: bool = False):
        self.ok = ok
        self.hang = hang
        self.invalidated = 0

    async def execute(self, stmt):
        if self.hang:
            await asyncio.sleep(10)
        if not self.ok:
            raise RuntimeError("could not connect to postgres")
        return None

    async def invalidate(self):
        self.invalidated += 1


@pytest.fixture
def client(monkeypatch):
    mini = FastAPI()
    mini.include_router(health.router)

    state = {"db": FakeSession()}

    async def _override():
        yield state["db"]

    mini.dependency_overrides[get_db] = _override

    # Neither of these may reach the network from a unit test.
    async def _redis_ok():
        return True

    async def _celery_ok():
        return True

    monkeypatch.setattr(health, "_check_redis", _redis_ok)
    monkeypatch.setattr(health, "_check_celery", _celery_ok)

    return TestClient(mini), state


# ---------------------------------------------------------------------------
# Shape and status codes
# ---------------------------------------------------------------------------


def test_healthz_stays_trivial(client):
    """Liveness must not touch a dependency or trigger restart cascades."""
    tc, state = client
    state["db"] = FakeSession(ok=False)
    resp = tc.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_reports_every_dependency(client):
    tc, _ = client
    resp = tc.get("/readyz")
    assert resp.status_code == 200
    assert set(resp.json()) == EXPECTED_KEYS


def test_readyz_is_503_when_postgres_is_down(client):
    tc, state = client
    state["db"] = FakeSession(ok=False)
    resp = tc.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["postgres"] is False
    # The rest of the report still arrives — that is the point of the endpoint.
    assert set(resp.json()) == EXPECTED_KEYS


def test_postgres_down_does_not_raise(client):
    """A monitoring endpoint that 500s tells you nothing."""
    tc, state = client
    state["db"] = FakeSession(ok=False)
    assert tc.get("/readyz").status_code == 503


def test_a_hanging_postgres_times_out_instead_of_hanging(client, monkeypatch):
    tc, state = client
    monkeypatch.setattr(health, "PG_TIMEOUT", 0.05)
    state["db"] = FakeSession(hang=True)
    resp = tc.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["postgres"] is False


def test_a_timed_out_connection_is_dropped_and_not_returned_to_the_pool(
    client, monkeypatch
):
    """The timeout only bounds the query. Handing the cancelled connection
    back to the pool afterwards is worse than the hang it avoided: the
    session's teardown issues a ROLLBACK over the same unresponsive socket,
    with no timeout of its own, so /readyz blocks *after* the check returned
    — and the next request inherits the poisoned connection."""
    tc, state = client
    monkeypatch.setattr(health, "PG_TIMEOUT", 0.05)
    session = FakeSession(hang=True)
    state["db"] = session

    assert tc.get("/readyz").status_code == 503
    assert session.invalidated == 1


def test_a_session_that_cannot_be_invalidated_still_answers(client, monkeypatch):
    """A half-open socket can swallow the close too; the readiness report has
    to come back either way."""

    class Stubborn(FakeSession):
        async def invalidate(self):
            raise OSError("connection already gone")

    tc, state = client
    state["db"] = Stubborn(ok=False)
    resp = tc.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["postgres"] is False


def test_a_healthy_check_leaves_the_session_alone(client):
    """Invalidating on the happy path would throw away a good connection on
    every probe."""
    tc, state = client
    assert tc.get("/readyz").status_code == 200
    assert state["db"].invalidated == 0


def test_celery_can_be_unknown(client, monkeypatch):
    """`null` means "we could not ask", which is not the same as "no worker"
    — the neighbouring `redis` boolean is what tells them apart."""

    async def _unknown():
        return None

    monkeypatch.setattr(health, "_check_celery", _unknown)
    tc, _ = client
    body = tc.get("/readyz").json()
    assert body["celery"] is None
    assert body["postgres"] is True


def test_redis_down_is_reported_without_failing_readiness(client, monkeypatch):
    async def _down():
        return False

    monkeypatch.setattr(health, "_check_redis", _down)
    tc, _ = client
    resp = tc.get("/readyz")
    # Postgres is the only hard dependency of the API process.
    assert resp.status_code == 200
    assert resp.json()["redis"] is False


def test_celery_is_not_pinged_through_a_dead_broker(client, monkeypatch):
    """`control.ping` blocks in a thread that cannot be cancelled; asking it
    to talk through a broker we just failed to reach is how /readyz starts
    leaking threads during an outage."""

    async def _down():
        return False

    async def _must_not_run():
        raise AssertionError("celery must not be pinged with the broker down")

    monkeypatch.setattr(health, "_check_redis", _down)
    monkeypatch.setattr(health, "_check_celery", _must_not_run)

    tc, _ = client
    body = tc.get("/readyz").json()
    assert body["redis"] is False
    assert body["celery"] is None


# ---------------------------------------------------------------------------
# "configured" flags
# ---------------------------------------------------------------------------


def test_configured_flags_follow_the_secrets(client, monkeypatch):
    from pydantic import SecretStr

    tc, _ = client
    monkeypatch.setattr(settings, "whatsapp_access_token", SecretStr("tok"))
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "123")
    monkeypatch.setattr(settings, "vapi_api_key", SecretStr(""))
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk"))
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr(""))

    body = tc.get("/readyz").json()
    assert body["meta_configured"] is True
    assert body["vapi_configured"] is False
    # Half-configured Stripe (key but no webhook secret) is not configured:
    # the webhook is where the money state actually changes.
    assert body["stripe_configured"] is False


def test_meta_needs_both_token_and_phone_number(client, monkeypatch):
    from pydantic import SecretStr

    tc, _ = client
    monkeypatch.setattr(settings, "whatsapp_access_token", SecretStr("tok"))
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "")
    assert tc.get("/readyz").json()["meta_configured"] is False


# ---------------------------------------------------------------------------
# The individual checks swallow their own failures
# ---------------------------------------------------------------------------


async def test_check_redis_returns_false_when_it_cannot_connect(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("nope")

    monkeypatch.setattr("redis.asyncio.from_url", _boom)
    assert await health._check_redis() is False


async def test_check_redis_is_false_without_a_url(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    assert await health._check_redis() is False


async def test_check_celery_returns_none_when_the_broker_is_unreachable(monkeypatch):
    from app.workers.celery_app import celery_app

    def _boom(*a, **k):
        raise OSError("broker unreachable")

    monkeypatch.setattr(celery_app.control, "ping", _boom)
    assert await health._check_celery() is None


async def test_check_celery_is_false_when_no_worker_answers(monkeypatch):
    from app.workers.celery_app import celery_app

    monkeypatch.setattr(celery_app.control, "ping", lambda *a, **k: [])
    assert await health._check_celery() is False


async def test_check_celery_is_true_when_a_worker_answers(monkeypatch):
    from app.workers.celery_app import celery_app

    monkeypatch.setattr(celery_app.control, "ping", lambda *a, **k: [{"worker@1": "pong"}])
    assert await health._check_celery() is True
