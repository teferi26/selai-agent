"""Transactional outgoing webhook dispatcher.

Domain code calls `emit("lead.created", agency_id, payload)`. We look up
all active WebhookSubscription rows for that agency and insert one durable
outbox row per delivery in the caller's transaction. A Celery sweep drains
those PostgreSQL rows later. Redis can be down at commit time without losing
the event; a worker crash after HTTP success may repeat it with the same
delivery id, which lets the receiver deduplicate.

Supported events (Sprint 5):
- lead.created
- lead.qualified
- lead.temperature_changed
- visit.scheduled
- visit.completed
- offer.created
- offer.signed

Item 194 added the pieces that make a subscription *verifiable* by the
person integrating it:

- `build_delivery` is the single definition of what we put on the wire
  (body + headers + signature). The test event and the real ones are byte
  compatible, so a CRM that validates the test validates production.
- `deliver_now` sends one immediately, for the "Enviar evento de prueba"
  button — the integrator gets an HTTP status back in a second instead of
  waiting for a real lead. It refuses targets that are not on the public
  internet: that button is a request we issue from inside our own network,
  at an address the customer typed.
- `record_delivery_success` / `record_delivery_failure` own the bookkeeping,
  including the auto-disable and — new — the email that tells the owner it
  happened. A subscription that dies in silence takes every notification
  with it and nobody finds out until a lead is lost.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import (
    Agency,
    Lead,
    LeadStatus,
    LeadTemperature,
    Offer,
    Visit,
    VisitStatus,
    WebhookOutbox,
    WebhookSubscription,
)
from app.services.secret_store import SecretDecryptionError, decrypt_secret
from app.services.url_guard import resolve_public_target

log = get_logger(__name__)


SUPPORTED_EVENTS = {
    "lead.created",
    "lead.qualified",
    "lead.temperature_changed",
    "visit.scheduled",
    "visit.completed",
    "offer.created",
    "offer.signed",
}

# Not in SUPPORTED_EVENTS: nobody subscribes to it, it is only ever sent
# on demand to the subscription being tested.
TEST_EVENT = "webhook.test"

# Same threshold the Celery deliverer has always used. Keeping it here
# makes it one number instead of a magic constant in two files.
DISABLE_AFTER_FAILURES = 20

BRAND_COLOR = "#5B61F0"
SETUP_WEBHOOKS_URL = "https://www.selai.es/setup?step=crm"
OUTBOX_BATCH_SIZE = 10
OUTBOX_MAX_BACKOFF_SECONDS = 60 * 60


async def emit(
    db: AsyncSession,
    event: str,
    agency_id: uuid.UUID,
    payload: dict[str, Any],
) -> int:
    """Stage subscribed deliveries in PostgreSQL, never in Redis.

    The insert participates in ``db``'s existing transaction. The domain
    fact and its queue row therefore commit together or roll back together.
    A deterministic key collapses concurrent/replayed enqueue attempts.
    """
    if event not in SUPPORTED_EVENTS:
        # `event` is structlog's own message key: passing it as a field raises
        # TypeError and turns an unknown event into a 500.
        log.warning("event.unknown", name=event)
        return 0

    subs = list(
        (
            await db.execute(
                select(WebhookSubscription).where(
                    WebhookSubscription.agency_id == agency_id,
                    WebhookSubscription.is_active.is_(True),
                    WebhookSubscription.events.any(event),
                )
            )
        ).scalars().all()
    )
    if not subs:
        return 0

    dedup_key = _delivery_dedup_key(event, agency_id, payload)
    for sub in subs:
        outbox_id = uuid.uuid5(sub.id, dedup_key)
        statement = (
            insert(WebhookOutbox)
            .values(
                id=outbox_id,
                subscription_id=sub.id,
                agency_id=agency_id,
                event=event,
                payload=dict(payload),
                dedup_key=dedup_key,
                attempt_count=0,
                available_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(
                constraint="uq_webhook_outbox_subscription_dedup"
            )
        )
        await db.execute(statement)
    return len(subs)


def _delivery_dedup_key(
    event: str, agency_id: uuid.UUID, payload: dict[str, Any]
) -> str:
    canonical = json.dumps(
        {
            "event": event,
            "agency_id": str(agency_id),
            "data": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# ---------------------------------------------------------------------------
# The six events the panel offered and nobody ever sent
#
# `SUPPORTED_EVENTS` has listed seven events since Sprint 5 and the panel lets
# an agency subscribe to all of them, but `lead.created` was the only `emit()`
# call in the whole backend. The other six describe facts that do happen — a
# lead qualifies, a visit is booked and later held, an offer is drafted and
# signed — they simply had nobody announcing them, so a CRM subscribed to
# "visita agendada" waited forever.
#
# Idempotency has two layers: the marker beside the domain fact avoids asking
# twice, while the unique deterministic outbox key collapses concurrent
# attempts. The marker and queue row share one transaction. HTTP delivery is
# deliberately at-least-once: the stable outbox UUID is sent as delivery_id so
# a CRM can deduplicate the only irreducible crash window (2xx before commit).
# ---------------------------------------------------------------------------

EVENTS_MARKER_KEY = "emitted_events"

# "Qualified" as the panel already counts it (see the KPI and funnel queries
# in app/api/dashboard.py): reached QUALIFIED *or moved past it*. A lead that
# jumps straight to VISIT_SCHEDULED is qualified too, and refusing to say so
# would leave the CRM waiting for an event that never arrives.
QUALIFIED_STATUSES = frozenset(
    {
        LeadStatus.QUALIFIED,
        LeadStatus.VISIT_SCHEDULED,
        LeadStatus.VISITED,
        LeadStatus.NEGOTIATING,
        LeadStatus.WON,
    }
)

# Temperature the model gives a lead before anything is known about it. Used
# as the baseline for rows that predate the marker, so a cold lead that has
# always been cold does not produce a "temperature changed" on first sight.
DEFAULT_TEMPERATURE = LeadTemperature.COLD.value


def _markers(row: Any) -> dict[str, Any]:
    extra = getattr(row, "extra", None) or {}
    markers = extra.get(EVENTS_MARKER_KEY)
    return dict(markers) if isinstance(markers, dict) else {}


def _write_markers(row: Any, markers: dict[str, Any]) -> None:
    # Reassign the whole dict: SQLAlchemy does not track in-place JSON edits.
    row.extra = {**(getattr(row, "extra", None) or {}), EVENTS_MARKER_KEY: markers}


def read_timestamp(row: Any, field: str) -> datetime | None:
    """`row.field` as a datetime, or None when it cannot be read.

    Never let reading a timestamp decide anything by accident. `created_at`
    and `updated_at` are `server_default=func.now()` / `onupdate=func.now()`,
    which means SQLAlchemy marks them **expired** on the flush that wrote
    them: the value lives in Postgres and Python does not have it. Touching an
    expired attribute emits a lazy SELECT, and on an `AsyncSession` a lazy
    SELECT from ordinary attribute access raises `MissingGreenlet` — which,
    inside a best-effort `try/except`, turns into "the CRM was never told"
    with only a log line to show for it.

    None means "unknown", and unknown must never silence an event: dropping a
    webhook is the failure we cannot detect afterwards.
    """
    try:
        value = getattr(row, field, None)
    except Exception:  # expired attribute, detached instance, greenlet-less IO
        log.warning("event.timestamp_unreadable", field=field)
        return None
    return value if isinstance(value, datetime) else None


def _too_old(occurred_at: datetime | None, max_age: timedelta | None) -> bool:
    """True when a fact is history rather than news.

    The markers are new, so the first pass over an existing database would
    otherwise replay every visit ever recorded into the customer's CRM and
    create duplicates there. Callers that sweep rows they did not just change
    pass a window; callers reacting to an action they performed themselves
    (creating an offer, closing a visit) pass none, because in their case the
    fact *is* now.

    Only usable with a timestamp that genuinely dates the *fact* — a visit's
    `created_at` dates its booking. `Lead.updated_at` does not date a
    qualification: it is the previous write's timestamp until the current
    transaction commits, so a lead that had been quiet for a week and
    qualifies right now looks a week old. Lead-level facts use
    `is_first_crm_sight` instead.
    """
    if max_age is None or occurred_at is None:
        return False
    reference = occurred_at if occurred_at.tzinfo else occurred_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - reference > max_age


async def emit_once(
    db: AsyncSession,
    event: str,
    agency_id: uuid.UUID,
    payload: dict[str, Any],
    *,
    row: Any,
    marker_key: str | None = None,
    marker_value: str | None = None,
    occurred_at: datetime | None = None,
    max_age: timedelta | None = None,
    backfill: bool = False,
) -> bool:
    """Emit `event` unless `row` already carries the same marker.

    Returns True when the delivery was handed to the dispatcher. `marker_key`
    defaults to the event name and the marker records *when* we announced it;
    `lead.temperature_changed` is the exception — it may legitimately fire
    again, but only when the value is different, so it stores the new
    temperature as the marker instead.

    A fact older than `max_age`, or one the caller already knows to be
    history (`backfill`), is marked as seen without being announced, and
    reports False.
    """
    key = marker_key or event
    markers = _markers(row)
    value = marker_value if marker_value is not None else datetime.now(UTC).isoformat()

    if marker_value is None:
        if key in markers:
            return False
    elif markers.get(key) == marker_value:
        return False

    if backfill or _too_old(occurred_at, max_age):
        markers[key] = value
        _write_markers(row, markers)
        log.info("event.backfilled_silently", name=event, agency=str(agency_id))
        return False

    # Stage the outbox first. If this raises, the marker remains absent and a
    # later pass can retry; if the caller's commit fails, both changes roll
    # back. Redis is not touched anywhere on this path.
    await emit(db, event, agency_id, payload)
    markers[key] = value
    _write_markers(row, markers)
    return True


# ---------------------------------------------------------------------------
# "Have we ever looked at this row?" — the anti-backfill anchor for lead facts
#
# A lead's qualification and its temperature are decided *inside the current
# transaction*, so no column on the row dates them. What we can answer is
# whether the announcer has ever passed over this lead before. It has not
# exactly once per lead: on the sweep that follows the deploy for rows that
# already existed, and on a lead's very first turn for rows that did not. The
# first case is the backfill we must swallow; the second is a brand-new lead,
# and `created_at` tells the two apart.
#
# The stamp is written on every pass, announcement or not — a lead whose first
# sweep had nothing to say must not still count as unseen the day it qualifies.
# ---------------------------------------------------------------------------

FIRST_SIGHT_KEY = "first_seen_at"


def is_first_crm_sight(row: Any) -> bool:
    return FIRST_SIGHT_KEY not in _markers(row)


def mark_crm_sight(row: Any) -> None:
    markers = _markers(row)
    if FIRST_SIGHT_KEY in markers:
        return
    markers[FIRST_SIGHT_KEY] = datetime.now(UTC).isoformat()
    _write_markers(row, markers)


def is_crm_backfill(row: Any, *, max_age: timedelta | None) -> bool:
    """True when this row's current facts predate us watching it.

    Unknown `created_at` (a row created in this very transaction, so the
    column is still expired) counts as new, not as history.
    """
    if max_age is None or not is_first_crm_sight(row):
        return False
    return _too_old(read_timestamp(row, "created_at"), max_age)


async def emit_lead_qualified(db: AsyncSession, lead: Lead, *, backfill: bool = False) -> bool:
    if lead.status not in QUALIFIED_STATUSES:
        return False
    return await emit_once(
        db,
        "lead.qualified",
        lead.agency_id,
        {
            "lead_id": str(lead.id),
            "name": lead.full_name,
            "phone": lead.phone_e164,
            "email": lead.email,
            "status": getattr(lead.status, "value", lead.status),
            "score": lead.score,
            "temperature": getattr(lead.temperature, "value", lead.temperature),
            "budget_min": lead.budget_min,
            "budget_max": lead.budget_max,
            "operation": lead.operation,
        },
        row=lead,
        backfill=backfill,
    )


async def emit_lead_temperature_changed(
    db: AsyncSession, lead: Lead, *, backfill: bool = False
) -> bool:
    current = getattr(lead.temperature, "value", lead.temperature)
    previous = _markers(lead).get("temperature", DEFAULT_TEMPERATURE)
    if current == previous:
        return False
    return await emit_once(
        db,
        "lead.temperature_changed",
        lead.agency_id,
        {
            "lead_id": str(lead.id),
            "name": lead.full_name,
            "from": previous,
            "to": current,
            "score": lead.score,
        },
        row=lead,
        marker_key="temperature",
        marker_value=str(current),
        backfill=backfill,
    )


async def emit_visit_scheduled(
    db: AsyncSession,
    visit: Visit,
    *,
    lead: Lead | None = None,
    max_age: timedelta | None = None,
) -> bool:
    """Announced for every visit row that exists: a row *is* a booked visit.

    Cancelled and no-show visits count as scheduled too — they were booked,
    and a CRM that never heard about them cannot make sense of the
    cancellation that follows.
    """
    return await emit_once(
        db,
        "visit.scheduled",
        visit.agency_id,
        {
            "visit_id": str(visit.id),
            "lead_id": str(visit.lead_id),
            "property_id": str(visit.property_id),
            "scheduled_at": _iso(visit.scheduled_at),
            "status": getattr(visit.status, "value", visit.status),
            "lead_name": lead.full_name if lead is not None else None,
            "lead_phone": lead.phone_e164 if lead is not None else None,
        },
        row=visit,
        # `created_at` genuinely dates a booking, so the window works here —
        # unlike on a lead. A visit created in this same transaction has it
        # expired, which reads as None and therefore as news.
        occurred_at=read_timestamp(visit, "created_at"),
        max_age=max_age,
    )


async def emit_visit_completed(
    db: AsyncSession,
    visit: Visit,
    *,
    lead: Lead | None = None,
    max_age: timedelta | None = None,
) -> bool:
    if visit.status is not VisitStatus.COMPLETED:
        return False
    return await emit_once(
        db,
        "visit.completed",
        visit.agency_id,
        {
            "visit_id": str(visit.id),
            "lead_id": str(visit.lead_id),
            "property_id": str(visit.property_id),
            "scheduled_at": _iso(visit.scheduled_at),
            "notes": visit.post_visit_notes or "",
            "lead_name": lead.full_name if lead is not None else None,
        },
        row=visit,
        # The flip to COMPLETED is a write of its own and it commits, so on a
        # later sweep `updated_at` really is when the visit was closed.
        occurred_at=read_timestamp(visit, "updated_at"),
        max_age=max_age,
    )


async def emit_offer_created(db: AsyncSession, offer: Offer) -> bool:
    return await emit_once(
        db,
        "offer.created",
        offer.agency_id,
        {
            "offer_id": str(offer.id),
            "lead_id": str(offer.lead_id),
            "property_id": str(offer.property_id),
            "amount_eur": offer.amount_eur,
            "operation": offer.operation,
            "status": getattr(offer.status, "value", offer.status),
            "pdf_url": offer.pdf_url,
        },
        row=offer,
    )


async def emit_offer_signed(db: AsyncSession, offer: Offer) -> bool:
    return await emit_once(
        db,
        "offer.signed",
        offer.agency_id,
        {
            "offer_id": str(offer.id),
            "lead_id": str(offer.lead_id),
            "property_id": str(offer.property_id),
            "amount_eur": offer.amount_eur,
            "operation": offer.operation,
            "signed_at": _iso(getattr(offer, "signed_at", None)),
        },
        row=offer,
    )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


# ---------------------------------------------------------------------------
# The wire format — one definition, used by the test button and by Celery
# ---------------------------------------------------------------------------


def build_delivery(
    *,
    event: str,
    agency_id: uuid.UUID,
    payload: dict[str, Any],
    secret: str,
    delivery_id: uuid.UUID | None = None,
) -> tuple[bytes, dict[str, str]]:
    """The exact bytes and headers of one delivery.

    The signature is HMAC-SHA256 over the *body as sent*, which is why the
    body is built here and handed back as bytes: re-serializing it on the
    way out would change a space somewhere and every signature check on the
    customer's side would start failing.
    """
    body = json.dumps(
        {
            "event": event,
            "agency_id": str(agency_id),
            "data": payload,
            "delivery_id": str(delivery_id or uuid.uuid4()),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Realtor-Event": event,
        "X-Realtor-Signature": f"sha256={signature}",
    }
    return body, headers


async def deliver_now(
    sub: WebhookSubscription,
    event: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float = 10.0,
    delivery_id: uuid.UUID | None = None,
) -> tuple[bool, int | None, str | None]:
    """POST one delivery inline. Returns `(ok, status_code, error)`.

    Never raises: the caller is an HTTP endpoint whose job is to *report*
    what happened, not to fail with it.

    The target is checked first. `target_url` is a string any signed-in
    member of the agency chose, and "Enviar evento de prueba" turns it into
    a request issued from inside our network on demand — a server-side
    request forgery button unless something refuses `http://127.0.0.1:5432/`
    and `http://169.254.169.254/`, which hands out the machine's cloud
    credentials to anything that will call it.
    """
    try:
        signing_secret = decrypt_secret(sub.secret or "")
    except SecretDecryptionError:
        log.error("webhook.secret_unreadable", sub=str(sub.id))
        return False, None, "signing_secret_unavailable"
    if not signing_secret:
        log.error("webhook.secret_missing", sub=str(sub.id))
        return False, None, "signing_secret_unavailable"

    target = await resolve_public_target(sub.target_url)
    if target is None:
        log.warning(
            "webhook.blocked_target", sub=str(sub.id), url=str(sub.target_url)[:200]
        )
        return False, None, "blocked_target_url"

    body, headers = build_delivery(
        event=event,
        agency_id=sub.agency_id,
        payload=payload,
        secret=signing_secret,
        delivery_id=delivery_id,
    )
    try:
        # No redirects: a 302 from the customer's endpoint would be a second
        # target nobody checked, and a webhook has nothing to follow anyway.
        async with httpx.AsyncClient(
            timeout=timeout_seconds, follow_redirects=False
        ) as http:
            # Connect to the exact address that passed `resolve_public_target`.
            # Sending `target.url` here would ask httpx to resolve the hostname
            # a second time and reopen DNS rebinding between the check and the
            # connection.  Keep the logical host for HTTP routing and TLS
            # certificate verification while pinning the TCP destination.
            resp = await http.post(
                target.request_url,
                content=body,
                headers={**headers, "Host": target.host_header},
                extensions={"sni_hostname": target.host},
            )
    except Exception as exc:
        return False, None, type(exc).__name__
    if resp.status_code >= 400:
        return False, resp.status_code, f"http_{resp.status_code}"
    return True, resp.status_code, None


def _retry_delay_seconds(attempt_count: int) -> int:
    """Bounded exponential retry: 10s, 20s, 40s ... at most one hour."""
    exponent = max(0, min(int(attempt_count) - 1, 12))
    return min(OUTBOX_MAX_BACKOFF_SECONDS, 10 * (2**exponent))


async def drain_webhook_outbox(
    db: AsyncSession,
    *,
    batch_size: int = OUTBOX_BATCH_SIZE,
) -> dict[str, int]:
    """Deliver due outbox rows with row-lock concurrency control.

    A row stays in PostgreSQL until the receiver accepts it. We hold
    ``FOR UPDATE SKIP LOCKED`` across the HTTP call: another worker can drain
    other rows, but never this one. A crash releases the lock and leaves the
    row pending. If it happened after the remote 2xx, the retry uses the same
    public delivery id and is therefore safely deduplicable by the receiver.
    """
    stats = {"delivered": 0, "retry": 0, "discarded": 0}
    limit = max(1, min(int(batch_size), 100))

    for _ in range(limit):
        now = datetime.now(UTC)
        statement = (
            select(WebhookOutbox)
            .where(
                WebhookOutbox.processed_at.is_(None),
                WebhookOutbox.available_at <= now,
            )
            .order_by(WebhookOutbox.available_at, WebhookOutbox.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        item = (await db.execute(statement)).scalar_one_or_none()
        if item is None:
            break

        sub = await db.get(WebhookSubscription, item.subscription_id)
        if sub is None or not sub.is_active:
            item.processed_at = now
            item.last_error = "subscription_unavailable"
            item.payload = {}
            stats["discarded"] += 1
            await db.commit()
            continue

        ok, _status_code, error = await deliver_now(
            sub,
            item.event,
            dict(item.payload or {}),
            delivery_id=item.id,
        )
        item.attempt_count = int(item.attempt_count or 0) + 1
        if ok:
            await record_delivery_success(db, sub)
            item.delivered_at = now
            item.processed_at = now
            item.last_error = None
            # Preserve the idempotency tombstone without retaining lead PII.
            item.payload = {}
            stats["delivered"] += 1
            await db.commit()
            continue

        await record_delivery_failure(db, sub)
        item.last_error = (error or "webhook_delivery_failed")[:500]
        if not sub.is_active:
            item.processed_at = now
            item.payload = {}
            stats["discarded"] += 1
        else:
            item.available_at = now + timedelta(
                seconds=_retry_delay_seconds(item.attempt_count)
            )
            stats["retry"] += 1
        await db.commit()

    return stats


# ---------------------------------------------------------------------------
# Delivery bookkeeping (+ the email nobody was sending)
# ---------------------------------------------------------------------------


async def record_delivery_success(db: AsyncSession, sub: WebhookSubscription) -> None:
    sub.last_success_at = datetime.now(UTC)
    sub.consecutive_failures = 0


async def record_delivery_failure(db: AsyncSession, sub: WebhookSubscription) -> bool:
    """Count the failure, disable the subscription if it crossed the limit.

    Returns True when this call is the one that disabled it — the moment
    the owner gets told. The transition happens exactly once (afterwards
    the deliverer skips inactive subscriptions), so the email cannot be
    sent twice for the same outage.

    Both the legacy Celery task and the transactional-outbox drainer call this
    function, so breaker state and owner notification have one definition.
    """
    sub.last_failure_at = datetime.now(UTC)
    sub.consecutive_failures = int(sub.consecutive_failures or 0) + 1
    if sub.consecutive_failures < DISABLE_AFTER_FAILURES or not sub.is_active:
        return False

    sub.is_active = False
    log.warning("webhook.disabled.too_many_failures", sub=str(sub.id))
    await _notify_subscription_disabled(db, sub)
    return True


async def _notify_subscription_disabled(
    db: AsyncSession, sub: WebhookSubscription
) -> None:
    """Best-effort email to the agency. Never breaks the delivery path."""
    try:
        from app.services.emails import send_email

        agency = await db.get(Agency, sub.agency_id)
        to = getattr(agency, "contact_email", None)
        if not to:
            log.warning("webhook.disabled.no_contact_email", sub=str(sub.id))
            return
        await send_email(
            to=to,
            subject="Hemos pausado los avisos automáticos a tu CRM",
            html=build_webhook_disabled_email_html(
                sub.target_url, int(sub.consecutive_failures or 0)
            ),
        )
    except Exception:
        log.exception("webhook.disabled.email_failed", sub=str(sub.id))


def build_webhook_disabled_email_html(target_url: str, failures: int) -> str:
    """Plain and specific: which address, how many failures, what to do.

    Inline here instead of in `app/services/emails.py` because it belongs to
    the dispatcher — it is the only thing that can know a subscription died.
    """
    safe_url = escape(target_url or "")
    return f"""\
<div style="background:#f4f5fb;padding:32px 16px;font-family:Arial,Helvetica,sans-serif;color:#1f2333;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;">
    <div style="background:{BRAND_COLOR};padding:20px 28px;">
      <span style="color:#ffffff;font-size:20px;font-weight:bold;">selai</span>
    </div>
    <div style="padding:28px;">
      <h1 style="margin:0 0 16px;font-size:20px;">Hemos pausado los avisos a tu CRM</h1>
      <p style="margin:0 0 12px;font-size:15px;line-height:1.5;">
        Llevamos {failures} intentos seguidos sin conseguir entregar avisos en
        esta dirección:
      </p>
      <p style="margin:0 0 16px;font-family:monospace;font-size:13px;word-break:break-all;color:#4a4f66;">
        {safe_url}
      </p>
      <p style="margin:0 0 20px;font-size:15px;line-height:1.5;">
        Los hemos pausado para no seguir insistiendo. Mientras tanto tu CRM no
        está recibiendo nada: ni leads nuevos ni visitas agendadas. Todo sigue
        guardado en selai, no se ha perdido nada.
      </p>
      <p style="margin:0 0 20px;font-size:15px;line-height:1.5;">
        Comprueba con quien lleve tu CRM que la dirección sigue siendo correcta
        y vuelve a activarla desde el panel. Puedes enviar un evento de prueba
        para confirmarlo antes de darlo por bueno.
      </p>
      <p style="margin:0 0 8px;">
        <a href="{SETUP_WEBHOOKS_URL}"
           style="display:inline-block;background:{BRAND_COLOR};color:#ffffff;text-decoration:none;padding:10px 18px;border-radius:8px;font-size:15px;">
          Revisar mis avisos
        </a>
      </p>
    </div>
    <div style="padding:16px 28px;border-top:1px solid #ececf4;color:#8a8fa3;font-size:12px;">
      selai — tu agente inmobiliario con IA · <a href="https://www.selai.es" style="color:{BRAND_COLOR};">selai.es</a>
    </div>
  </div>
</div>"""
