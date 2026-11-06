"""Manage outgoing webhook subscriptions per agency.

Item 194 — the three things that made these unverifiable:

- The secret was only ever shown as its first 8 characters, so the CRM on
  the other side could not check the HMAC signature at all. It is now
  returned *in full, once*, in the response to the POST that creates the
  subscription, and never again. That is the same bargain every API key
  ships with, and it is the only one that keeps the secret out of every
  later GET.
- There was no way to try a subscription without waiting for a real lead.
  `POST /{id}/test` sends a signed test event right now and hands back the
  status code the endpoint answered with.
- A subscription auto-disabled after repeated failures died in silence.
  The dispatcher now emails the agency when that happens.
"""
from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AgencyMemberDep
from app.core.logging import get_logger
from app.db.models import WebhookSubscription
from app.db.session import get_db
from app.services.event_dispatcher import (
    SUPPORTED_EVENTS,
    TEST_EVENT,
    deliver_now,
    record_delivery_failure,
    record_delivery_success,
)
from app.services.secret_store import (
    SecretDecryptionError,
    decrypt_secret,
    encrypt_secret,
)
from app.services.url_guard import is_obviously_private

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/webhook-subscriptions",
    tags=["webhook-subscriptions"],
    dependencies=[AgencyMemberDep],
)
log = get_logger(__name__)


class SubscriptionIn(BaseModel):
    target_url: HttpUrl
    events: list[str] = Field(min_length=1)


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    target_url: str
    events: list[str]
    is_active: bool
    secret_preview: str  # only first 8 chars after creation
    secret_unreadable: bool = False
    consecutive_failures: int
    # Populated *only* in the response that creates the subscription. The
    # dashboard shows it once with a copy button and a warning; every later
    # read returns null, because we would rather force a new subscription
    # than leave a signing key readable in an endpoint anyone on the team
    # can call.
    secret_full_once: str | None = None


class WebhookTestOut(BaseModel):
    delivered: bool
    status_code: int | None = None
    error: str | None = None
    is_active: bool
    consecutive_failures: int


def _to_out(
    s: WebhookSubscription, *, secret_full_once: str | None = None
) -> SubscriptionOut:
    try:
        signing_secret = decrypt_secret(s.secret or "")
        secret_unreadable = False
    except SecretDecryptionError:
        signing_secret = ""
        secret_unreadable = True
        log.error("webhook.secret_unreadable", subscription=str(s.id))
    return SubscriptionOut(
        id=s.id,
        target_url=s.target_url,
        events=s.events,
        is_active=s.is_active,
        secret_preview=(signing_secret[:8] + "…") if signing_secret else "no disponible",
        secret_unreadable=secret_unreadable,
        consecutive_failures=s.consecutive_failures,
        secret_full_once=secret_full_once,
    )


@router.get("", response_model=list[SubscriptionOut])
async def list_subscriptions(
    agency_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[SubscriptionOut]:
    rows = (
        await db.execute(
            select(WebhookSubscription).where(WebhookSubscription.agency_id == agency_id)
        )
    ).scalars().all()
    return [_to_out(r) for r in rows]


@router.post("", response_model=SubscriptionOut, status_code=201)
async def create_subscription(
    agency_id: uuid.UUID,
    body: SubscriptionIn,
    db: AsyncSession = Depends(get_db),
) -> SubscriptionOut:
    unknown = [e for e in body.events if e not in SUPPORTED_EVENTS]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown_events: {unknown}")

    # A literal private address is refused here, while the person is still
    # looking at the form. Hostnames are *not* resolved at this point on
    # purpose — a CRM that is not deployed yet, or one behind split-horizon
    # DNS, must still be configurable — so the resolving check lives in
    # `deliver_now`, which is where the request actually leaves the box.
    if is_obviously_private(str(body.target_url)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "blocked_target_url")

    secret = secrets.token_urlsafe(32)
    sub = WebhookSubscription(
        agency_id=agency_id,
        target_url=str(body.target_url),
        events=list(body.events),
        secret=encrypt_secret(secret),
    )
    db.add(sub)
    await db.commit()
    return _to_out(sub, secret_full_once=secret)


@router.post("/{sub_id}/test", response_model=WebhookTestOut)
async def test_subscription(
    agency_id: uuid.UUID,
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> WebhookTestOut:
    """Send one signed test event now and report what came back.

    Delivered inline rather than through Celery on purpose: the whole point
    is that the person configuring the CRM sees the result while they are
    still looking at the screen.

    The outcome counts like a real delivery — a test that fails twenty times
    in a row is an endpoint that does not work, and pretending otherwise
    would keep a dead subscription looking healthy.
    """
    sub = await db.get(WebhookSubscription, sub_id)
    if sub is None or sub.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription_not_found")

    ok, status_code, error = await deliver_now(
        sub,
        TEST_EVENT,
        {
            "message": "Evento de prueba enviado desde selai.",
            "subscription_id": str(sub.id),
        },
    )
    if ok:
        await record_delivery_success(db, sub)
    else:
        await record_delivery_failure(db, sub)
    await db.commit()

    log.info(
        "webhook.test_sent",
        agency=str(agency_id),
        sub=str(sub_id),
        delivered=ok,
        status=status_code,
    )
    return WebhookTestOut(
        delivered=ok,
        status_code=status_code,
        error=error,
        is_active=sub.is_active,
        consecutive_failures=sub.consecutive_failures,
    )


@router.delete("/{sub_id}", status_code=204)
async def delete_subscription(
    agency_id: uuid.UUID,
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    sub = await db.get(WebhookSubscription, sub_id)
    if sub is None or sub.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription_not_found")
    await db.delete(sub)
    await db.commit()
