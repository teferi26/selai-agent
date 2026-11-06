"""Item 222 — the rate limiter counts in Redis, and never breaks a request.

The old limiter kept a deque per process. With `uvicorn --workers 2` and
several Railway replicas the real budget was N times the configured one, and
every deploy reset it: decorative protection. These tests cover the two
things that matter — the counter is shared, and Redis failing degrades to
the in-memory deque instead of 500-ing.
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.middleware import RateLimitMiddleware
from app.core.settings import settings


class FakePipeline:
    def __init__(self, store: dict, fail: bool = False):
        self._store = store
        self._fail = fail
        self._ops: list = []

    def incr(self, key: str):
        self._ops.append(("incr", key))
        return self

    def expire(self, key: str, seconds: int):
        self._ops.append(("expire", key, seconds))
        return self

    async def execute(self):
        if self._fail:
            raise ConnectionError("redis is down")
        out = []
        for op in self._ops:
            if op[0] == "incr":
                self._store[op[1]] = self._store.get(op[1], 0) + 1
                out.append(self._store[op[1]])
            else:
                out.append(True)
        return out


class FakeRedis:
    """Shared counter store, like a real Redis shared by several workers."""

    def __init__(self, store: dict | None = None, fail: bool = False):
        self.store = {} if store is None else store
        self.fail = fail
        self.pipelines = 0
        self.closed = 0

    def pipeline(self):
        self.pipelines += 1
        return FakePipeline(self.store, fail=self.fail)

    async def aclose(self):
        self.closed += 1


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("redis.asyncio.from_url", lambda *a, **k: fake)
    return fake


def _app(**kwargs) -> FastAPI:
    mini = FastAPI()
    mini.add_middleware(RateLimitMiddleware, **kwargs)

    @mini.get("/api/v1/whatever")
    async def _api():
        return {"ok": True}

    return mini


def _limiter(app: FastAPI) -> RateLimitMiddleware:
    # The stack is built on startup; build it here when no client has run yet.
    layer = app.middleware_stack or app.build_middleware_stack()
    while layer is not None and not isinstance(layer, RateLimitMiddleware):
        layer = getattr(layer, "app", None)
    assert layer is not None
    return layer


# ---------------------------------------------------------------------------
# The counter is shared across processes
# ---------------------------------------------------------------------------


def test_redis_counts_requests_and_blocks_past_the_limit(fake_redis):
    client = TestClient(_app(max_per_window=3, backend="redis"))
    for _ in range(3):
        assert client.get("/api/v1/whatever").status_code == 200
    assert client.get("/api/v1/whatever").status_code == 429
    assert fake_redis.pipelines == 4


def test_two_processes_share_one_budget(fake_redis):
    """The whole point of item 222: two uvicorn workers (or two Railway
    replicas) must not each get the full budget."""
    one = TestClient(_app(max_per_window=4, backend="redis"))
    two = TestClient(_app(max_per_window=4, backend="redis"))

    for _ in range(2):
        assert one.get("/api/v1/whatever").status_code == 200
    for _ in range(2):
        assert two.get("/api/v1/whatever").status_code == 200

    # Four spent between them: the fifth is refused on either process.
    assert one.get("/api/v1/whatever").status_code == 429
    assert two.get("/api/v1/whatever").status_code == 429


def test_the_key_is_per_ip_and_per_bucket(fake_redis):
    client = TestClient(_app(max_per_window=2, backend="redis"))
    client.get("/api/v1/whatever")
    keys = list(fake_redis.store)
    assert len(keys) == 1
    assert keys[0].startswith("ratelimit:api:testclient:")


def test_redis_keys_expire_so_they_cannot_leak(fake_redis):
    """A key per (ip, bucket, window) with no TTL is an unbounded memory
    leak in the broker everything else depends on."""
    seen: list = []
    original = FakeRedis.pipeline

    def spy(self):
        pipe = original(self)
        seen.append(pipe)
        return pipe

    FakeRedis.pipeline = spy
    try:
        TestClient(_app(max_per_window=2, backend="redis", window_seconds=60)).get(
            "/api/v1/whatever"
        )
    finally:
        FakeRedis.pipeline = original

    expires = [op for pipe in seen for op in pipe._ops if op[0] == "expire"]
    assert expires and all(op[2] >= 60 for op in expires)


def test_the_window_rolls_over(fake_redis, monkeypatch):
    """Budget is a window, not a permanent ban."""
    app = _app(max_per_window=2, backend="redis", window_seconds=60)
    client = TestClient(app)
    now = 1_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)
    for _ in range(2):
        assert client.get("/api/v1/whatever").status_code == 200
    assert client.get("/api/v1/whatever").status_code == 429

    now += 61
    assert client.get("/api/v1/whatever").status_code == 200


# ---------------------------------------------------------------------------
# Redis is never allowed to break a request
# ---------------------------------------------------------------------------


def test_redis_failure_falls_back_to_memory_without_500(monkeypatch):
    monkeypatch.setattr("redis.asyncio.from_url", lambda *a, **k: FakeRedis(fail=True))
    app = _app(max_per_window=2, backend="redis")
    client = TestClient(app)

    # Still served, still limited — just per process for a while.
    assert client.get("/api/v1/whatever").status_code == 200
    assert client.get("/api/v1/whatever").status_code == 200
    assert client.get("/api/v1/whatever").status_code == 429


def test_a_broken_redis_is_not_retried_on_every_request(monkeypatch):
    """Reconnecting per request would add a failed TCP connect to every
    single call for the whole outage."""
    fake = FakeRedis(fail=True)
    calls = {"n": 0}

    def _from_url(*a, **k):
        calls["n"] += 1
        return fake

    monkeypatch.setattr("redis.asyncio.from_url", _from_url)
    app = _app(max_per_window=10, backend="redis")
    client = TestClient(app)
    for _ in range(5):
        client.get("/api/v1/whatever")

    assert calls["n"] == 1
    assert _limiter(app)._redis_disabled_until > time.time()


def test_the_failed_client_is_closed_and_not_just_dropped(monkeypatch):
    """Setting `self._redis = None` does not free anything: redis-py keeps a
    ConnectionPool with live sockets behind that client. During an outage the
    limiter rebuilds one every cooldown, so a leak of one pool a minute per
    web process is how a Redis blip becomes a file-descriptor leak."""
    clients: list[FakeRedis] = []

    def _from_url(*a, **k):
        client = FakeRedis(fail=True)
        clients.append(client)
        return client

    monkeypatch.setattr("redis.asyncio.from_url", _from_url)
    app = _app(max_per_window=10, backend="redis")
    client = TestClient(app)
    client.get("/api/v1/whatever")

    assert len(clients) == 1
    assert clients[0].closed == 1
    assert _limiter(app)._redis is None


def test_a_client_that_cannot_be_closed_does_not_break_the_request(monkeypatch):
    """The close runs on the failure path of a component whose whole contract
    is "never break a request"."""

    class Stubborn(FakeRedis):
        async def aclose(self):
            raise OSError("socket already gone")

    monkeypatch.setattr("redis.asyncio.from_url", lambda *a, **k: Stubborn(fail=True))
    client = TestClient(_app(max_per_window=5, backend="redis"))
    assert client.get("/api/v1/whatever").status_code == 200


def test_unreachable_redis_url_does_not_break_the_api():
    """No fake at all: a real connection attempt to a dead port."""
    app = _app(max_per_window=5, backend="redis", redis_url="redis://127.0.0.1:1/0")
    client = TestClient(app)
    assert client.get("/api/v1/whatever").status_code == 200


# ---------------------------------------------------------------------------
# Which backend a deployment gets
# ---------------------------------------------------------------------------


def test_auto_uses_memory_in_development_and_redis_elsewhere(monkeypatch):
    limiter = _limiter(_app(backend="auto", redis_url="redis://x:6379/0"))

    monkeypatch.setattr(settings, "app_env", "development")
    assert limiter._redis_wanted() is False

    monkeypatch.setattr(settings, "app_env", "production")
    assert limiter._redis_wanted() is True


def test_memory_backend_never_touches_redis(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("must not connect to Redis")

    monkeypatch.setattr("redis.asyncio.from_url", _boom)
    client = TestClient(_app(max_per_window=2, backend="memory"))
    for _ in range(2):
        assert client.get("/api/v1/whatever").status_code == 200
    assert client.get("/api/v1/whatever").status_code == 429


def test_no_redis_url_means_memory():
    assert _limiter(_app(backend="redis", redis_url=""))._redis_wanted() is False


def test_the_memory_fallback_map_stays_bounded():
    """During a Redis outage the deque is the only limiter left, and its key
    is the client address — unbounded growth would turn an outage into an
    OOM. Eviction is least-recently-seen, so an abuser cannot evict itself."""
    from app.core.middleware import MAX_TRACKED_BUCKETS

    limiter = _limiter(_app(max_per_window=2, backend="memory"))
    abuser = ("198.51.100.7", "api")
    for _ in range(2):
        assert limiter._memory_exceeded(abuser, 2) is False
    assert limiter._memory_exceeded(abuser, 2) is True

    for i in range(MAX_TRACKED_BUCKETS + 500):
        limiter._memory_exceeded((f"flood-{i}", "api"), 2)
        if i % 2_000 == 0:
            assert limiter._memory_exceeded(abuser, 2) is True

    assert len(limiter._buckets) <= MAX_TRACKED_BUCKETS
    assert limiter._memory_exceeded(abuser, 2) is True


def test_reset_clears_local_state(fake_redis):
    app = _app(max_per_window=1, backend="redis")
    TestClient(app).get("/api/v1/whatever")
    limiter = _limiter(app)
    assert limiter._redis is not None
    limiter.reset()
    assert limiter._redis is None
    assert limiter._buckets == {}
    assert limiter._redis_disabled_until == 0.0
