"""Shared test fixtures.

The rate limiter keeps in-memory per-IP buckets on the singleton `app`.
Every TestClient request comes from the same "testclient" address, so a
full-suite run exhausts the 120 req/60 s budget and whichever module runs
last starts getting 429s that have nothing to do with what it tests.
Clearing the buckets around each test keeps modules order-independent;
the rate-limit tests themselves fill the buckets within a single test.
"""
from __future__ import annotations

import pytest

from app.core.middleware import RateLimitMiddleware
from app.main import app


def _find_rate_limiter() -> RateLimitMiddleware | None:
    # Starlette builds the stack on the first request, so before any test has
    # made one `app.middleware_stack` is still None and the walk below finds
    # nothing. Build it here and store it, exactly as `Starlette.__call__`
    # would, so the fixture resets the *same* instance the app will serve
    # from — otherwise this fixture is a silent no-op until some other module
    # happens to have run first.
    if app.middleware_stack is None:
        app.middleware_stack = app.build_middleware_stack()

    layer = app.middleware_stack
    while layer is not None:
        if isinstance(layer, RateLimitMiddleware):
            return layer
        layer = getattr(layer, "app", None)
    return None


def _limiter_is_registered() -> bool:
    return any(m.cls is RateLimitMiddleware for m in app.user_middleware)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    limiter = _find_rate_limiter()
    if limiter is None:
        # Registered but unreachable means the walk broke (a Starlette
        # refactor, a new wrapper layer). Failing here is the point: the
        # alternative is every module after the 120th request quietly
        # collecting 429s and nobody knowing why.
        assert not _limiter_is_registered(), (
            "RateLimitMiddleware is installed on app but the fixture can no "
            "longer reach it; the per-test bucket reset is doing nothing"
        )
        yield
        return

    # `reset()` also drops the Redis client + failure cooldown; keep the
    # bucket clear as a fallback for older middleware instances.
    reset = getattr(limiter, "reset", None)
    if callable(reset):
        reset()
    else:
        limiter._buckets.clear()
    yield
