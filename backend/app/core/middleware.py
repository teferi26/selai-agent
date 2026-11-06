"""Security headers + rate limiting middleware."""
from __future__ import annotations

import contextlib
import os
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)

# How long to stay on the in-memory fallback after Redis fails, before
# trying it again. Long enough that a Redis outage doesn't add a failed
# connection to every request, short enough to self-heal in a minute.
REDIS_FAILURE_COOLDOWN_SECONDS = 60

# Ceiling on the in-memory fallback map. The key is the client address, which
# the caller controls behind a proxy, so an unbounded dict is a memory leak
# with a knob on it. Eviction is least-recently-seen, so whoever keeps
# knocking stays tracked (and stays blocked) however many addresses they burn.
MAX_TRACKED_BUCKETS = 10_000


async def _aclose(client) -> None:
    """Best-effort close of a redis-py async client, whatever its version.

    Never raises: this runs on the failure path of a component whose whole
    contract is "must not break a request".
    """
    if client is None:
        return
    closer = getattr(client, "aclose", None) or getattr(client, "close", None)
    if closer is None:
        return
    with contextlib.suppress(Exception):
        result = closer()
        if result is not None:
            await result


def _configured_proxy_hops() -> int:
    """How many reverse proxies sit in front of us (Railway edge, CDN…).

    0 (the default) means "don't read X-Forwarded-For at all": the peer
    address is the client. Any other value must match the deployment exactly
    — see `_client_ip`.

    Read from the environment rather than `app.core.settings` on purpose: it
    is deployment topology, not product configuration, and it must be
    impossible to get wrong by half. Move it to Settings when that file is
    next touched.
    """
    try:
        return max(0, int(os.getenv("TRUSTED_PROXY_HOPS", "0") or 0))
    except ValueError:
        return 0


SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP request budget, shared across processes via Redis.

    The counter lives in Redis (`INCR` + `EXPIRE` on a per-window key) so the
    limit is the *configured* number and not N times it: the app runs with
    `uvicorn --workers 2` and several Railway replicas, and an in-process
    deque also resets on every deploy, which made the old limiter decorative.

    The window is approximate on purpose: keys are bucketed by
    `floor(now / window_seconds)`, so a caller can spend two budgets across a
    window boundary. That costs one line of Lua-free code and is the standard
    trade for a protection whose job is to stop floods, not to meter.

    Redis is never allowed to break a request: if it is not configured, not
    reachable or errors mid-flight, the middleware falls back to the
    in-memory deque for `REDIS_FAILURE_COOLDOWN_SECONDS` and logs once. A
    limiter that returns 500 is worse than a limiter that is briefly
    per-process.

    Default: 120 req / 60 s per (IP, route prefix). Signed third-party
    webhooks are exempt because they get bursts from providers (Meta, Vapi,
    Twilio) and are authenticated by signature anyway.

    The public web chat (`/webhooks/web/`) is **not** exempt: it is
    anonymous, unsigned, runs the LLM and creates leads, so it gets its own
    tighter per-IP budget (`web_chat_max_per_window`). `widget.js` keeps the
    default budget — it is a static asset with no cost, and a whole office
    behind one NAT IP must still be able to load it.

    "Per IP" is only true if we can see the IP: behind a reverse proxy every
    request arrives from the proxy and the whole platform would share a
    single bucket (20 web-chat messages a minute for *everyone*). Set
    `TRUSTED_PROXY_HOPS` to the number of proxies in front of the app to read
    the client address from `X-Forwarded-For` instead — counting from the
    right, which is the only end a client cannot forge.
    """

    def __init__(
        self,
        app,
        *,
        max_per_window: int = 120,
        window_seconds: int = 60,
        web_chat_max_per_window: int = 20,
        trusted_proxy_hops: int | None = None,
        redis_url: str | None = None,
        backend: str | None = None,
    ):
        super().__init__(app)
        self.max_per_window = max_per_window
        self.window_seconds = window_seconds
        self.web_chat_max_per_window = web_chat_max_per_window
        self.trusted_proxy_hops = (
            _configured_proxy_hops() if trusted_proxy_hops is None else trusted_proxy_hops
        )
        self._buckets: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()

        self.backend = backend or settings.rate_limit_backend
        self.redis_url = settings.redis_url if redis_url is None else redis_url
        # Built lazily on the first request that needs it, inside the running
        # loop — creating it at import time binds it to the wrong loop.
        self._redis = None
        self._redis_disabled_until = 0.0

    # -- Redis backend ------------------------------------------------------

    def _redis_wanted(self) -> bool:
        """Whether this deployment should count in Redis at all.

        "auto" means Redis everywhere except development, so the test suite
        and a laptop without Redis behave deterministically instead of
        depending on whether something is listening on 6379.
        """
        if self.backend == "memory" or not self.redis_url:
            return False
        if self.backend == "redis":
            return True
        return settings.app_env != "development"

    async def _mark_redis_down(self, exc: Exception) -> None:
        client, self._redis = self._redis, None
        self._redis_disabled_until = time.time() + REDIS_FAILURE_COOLDOWN_SECONDS
        log.warning("ratelimit.redis_unavailable.falling_back", error=str(exc))
        # Dropping the reference is not closing it: redis-py keeps a
        # ConnectionPool with live sockets behind that client. During a long
        # outage we build a new one every cooldown, so without this the
        # limiter leaks a pool a minute in every web process — a Redis blip
        # turning into a file-descriptor leak.
        await _aclose(client)

    async def _redis_client(self):
        if self._redis is not None:
            return self._redis
        from redis.asyncio import from_url  # imported here: optional at import time

        self._redis = from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        )
        return self._redis

    async def _redis_exceeded(self, key: tuple[str, str], limit: int) -> bool | None:
        """True/False when Redis answered, None when it could not be used."""
        if not self._redis_wanted() or time.time() < self._redis_disabled_until:
            return None
        ip, bucket = key
        window = int(time.time() // self.window_seconds)
        redis_key = f"ratelimit:{bucket}:{ip}:{window}"
        try:
            client = await self._redis_client()
            pipe = client.pipeline()
            pipe.incr(redis_key)
            # Twice the window: the key outlives its own bucket by one window
            # so a clock skew between replicas can't resurrect a stale count.
            pipe.expire(redis_key, self.window_seconds * 2)
            count, _ = await pipe.execute()
            return int(count) > limit
        except Exception as exc:  # noqa: BLE001 — never 500 because of the limiter
            await self._mark_redis_down(exc)
            return None

    def _memory_exceeded(self, key: tuple[str, str], limit: int) -> bool:
        """Per-process fallback: the original deque, with a bounded map."""
        now = time.time()
        window = self._buckets.get(key)
        if window is None:
            window = deque()
            self._buckets[key] = window
        self._buckets.move_to_end(key)
        while len(self._buckets) > MAX_TRACKED_BUCKETS:
            self._buckets.popitem(last=False)

        cutoff = now - self.window_seconds
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= limit:
            return True
        window.append(now)
        return False

    def reset(self) -> None:
        """Drop all local state. Used by the test suite between tests."""
        self._buckets.clear()
        self._redis = None
        self._redis_disabled_until = 0.0

    EXEMPT_PREFIXES = (
        # Signed third-party webhooks only — /webhooks/web/ is deliberately
        # absent (public, anonymous, unsigned).
        "/webhooks/whatsapp",
        "/webhooks/email",
        "/webhooks/sms",
        "/webhooks/voice",
        "/webhooks/google-business",
        "/voice/",          # Vapi LLM endpoint, very high cardinality
        "/healthz",
        "/readyz",
    )

    WEB_CHAT_PREFIX = "/webhooks/web/"
    WEB_CHAT_STATIC = "/webhooks/web/widget.js"

    def _client_ip(self, request: Request) -> str:
        """The address the budget is charged to.

        With `trusted_proxy_hops == 0` this is the peer address, full stop.
        Otherwise it is the entry `hops` places from the **right** of
        X-Forwarded-For: each proxy appends the address it received the
        request from, so the tail is written by our own infrastructure while
        anything the client puts in the header stays on the left, unable to
        shift the entry we read. Taking the leftmost value instead — the
        usual mistake — hands every attacker an unlimited supply of fresh
        rate-limit buckets.
        """
        peer = request.client.host if request.client else "unknown"
        hops = self.trusted_proxy_hops
        if hops <= 0:
            return peer

        # Proxies may split the chain over several headers; join them in
        # order, like uvicorn does, before counting from the right.
        chain = [
            part.strip()
            for value in request.headers.getlist("x-forwarded-for")
            for part in value.split(",")
            if part.strip()
        ]
        if not chain:
            return peer
        return chain[max(0, len(chain) - hops)]

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path

        if path.startswith(self.WEB_CHAT_PREFIX) and path != self.WEB_CHAT_STATIC:
            limit = self.web_chat_max_per_window
            bucket = "webchat"
        elif any(path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return await call_next(request)
        else:
            limit = self.max_per_window
            bucket = path.split("/", 4)[1] if path.startswith("/") else "/"

        key = (self._client_ip(request), bucket)
        exceeded = await self._redis_exceeded(key, limit)
        if exceeded is None:
            exceeded = self._memory_exceeded(key, limit)
        if exceeded:
            return Response(content="rate_limited", status_code=429)
        return await call_next(request)
