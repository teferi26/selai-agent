"""Health / readiness endpoints.

`/healthz` is the process liveness probe: it must stay trivial and never touch
a dependency, or a slow Postgres would make an external runtime restart a
perfectly healthy process.

`/readyz` is the deployment/readiness probe wired into Railway: one boolean
per dependency, short timeouts, and it never raises. "Connected" so far only
meant "the keys exist"; this endpoint at least separates "configured" from
"answering" and prevents a release with an unavailable database going live.
"""
from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings import settings
from app.db.session import get_db

log = get_logger(__name__)
router = APIRouter(tags=["health"])

# Kept short on purpose: a readiness check that hangs is a readiness check
# that lies. Postgres gets a little more room because it is the hard one.
PG_TIMEOUT = 2.0
REDIS_TIMEOUT = 1.0
CELERY_TIMEOUT = 1.5


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


async def _check_postgres(db: AsyncSession) -> bool:
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=PG_TIMEOUT)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("readyz.postgres_down", error=str(exc))
        # The timeout cancelled a statement that was still in flight. Letting
        # `get_db` hand that connection back to the pool is worse than the
        # hang we just avoided: return-to-pool issues a ROLLBACK over the same
        # unresponsive socket, with no timeout of its own, so /readyz blocks
        # in dependency teardown *after* the check returned — and the next
        # request inherits the poisoned connection. `invalidate` drops the
        # socket without talking to the server; the outer wait_for bounds even
        # that, because a half-open TCP connection can swallow a close too.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(db.invalidate(), timeout=PG_TIMEOUT)
        return False


async def _check_redis() -> bool:
    if not settings.redis_url:
        return False
    client = None
    try:
        from redis.asyncio import from_url

        client = from_url(
            settings.redis_url,
            socket_connect_timeout=REDIS_TIMEOUT,
            socket_timeout=REDIS_TIMEOUT,
        )
        await asyncio.wait_for(client.ping(), timeout=REDIS_TIMEOUT)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("readyz.redis_down", error=str(exc))
        return False
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()


async def _check_celery() -> bool | None:
    """True when a worker replied, False when none did, None when unknown.

    `control.ping` is blocking kombu code, so it runs in a thread — and a
    thread cannot be cancelled, which is why the caller only gets here once
    the broker (Redis) has answered. With the broker down the honest answer
    is "we can't tell from here"; the `redis: false` next to it is the real
    signal, not a made-up `celery: false`.
    """
    from app.workers.celery_app import celery_app

    def _ping():
        return celery_app.control.ping(timeout=CELERY_TIMEOUT)

    try:
        replies = await asyncio.wait_for(
            asyncio.to_thread(_ping), timeout=CELERY_TIMEOUT + 1.0
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("readyz.celery_unknown", error=str(exc))
        return None
    return bool(replies)


@router.get("/readyz")
async def readiness(
    response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, bool | None]:
    postgres, redis_ok = await asyncio.gather(_check_postgres(db), _check_redis())
    # Celery's broker *is* that Redis: pinging workers through a broker we
    # just failed to reach would block a thread we cannot cancel.
    celery_ok = await _check_celery() if redis_ok else None

    body: dict[str, bool | None] = {
        "postgres": postgres,
        "redis": redis_ok,
        "celery": celery_ok,
        # "Configured" is not "working" — it only says the credentials exist.
        # Live per-channel verification against Meta/Vapi/Stripe is the other
        # half of item 196 and lives in the channels status endpoint.
        "meta_configured": bool(
            settings.whatsapp_access_token.get_secret_value()
            and settings.whatsapp_phone_number_id
        ),
        "vapi_configured": bool(settings.vapi_api_key.get_secret_value()),
        "stripe_configured": bool(
            settings.stripe_secret_key.get_secret_value()
            and settings.stripe_webhook_secret.get_secret_value()
        ),
    }

    # Postgres is the only dependency the API cannot serve a single request
    # without. Railway uses this response only while promoting a deployment;
    # normal process liveness remains the dependency-free /healthz endpoint.
    if not postgres:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return body
