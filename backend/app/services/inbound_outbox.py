"""Persistence and serialization for authenticated inbound webhooks.

PostgreSQL is the source of truth. FastAPI background tasks and Celery are
only two interchangeable ways to wake processing; losing either cannot lose
the receipt after :func:`enqueue_inbound` has committed.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agency, ConversationChannel, InboundOutbox, Subscription
from app.services.conversation_service import InboundPayload
from app.services.gdpr_erasure import PURGED_AT_KEY
from app.services.lead_resolver import InboundIdentity
from app.services.plan_limits import BLOCKING_STATUSES

PENDING_REPLY_KEY = "pending_reply"
DELIVERY_LEASE = timedelta(minutes=5)


def serialize_inbound(payload: InboundPayload) -> dict[str, Any]:
    identity = payload.identity
    return {
        "agency_id": str(payload.agency_id),
        "identity": {
            "channel": identity.channel.value,
            "external_thread_id": identity.external_thread_id,
            "phone_e164": identity.phone_e164,
            "email": identity.email,
            "whatsapp_wa_id": identity.whatsapp_wa_id,
            "display_name": identity.display_name,
            "language": identity.language,
            "source_campaign": identity.source_campaign,
        },
        "text": payload.text,
        "external_message_id": payload.external_message_id,
        "media": payload.media,
        "raw": payload.raw,
    }


def deserialize_inbound(value: dict[str, Any]) -> InboundPayload:
    identity = dict(value["identity"])
    identity["channel"] = ConversationChannel(identity["channel"])
    return InboundPayload(
        agency_id=uuid.UUID(str(value["agency_id"])),
        identity=InboundIdentity(**identity),
        text=str(value.get("text") or ""),
        external_message_id=value.get("external_message_id"),
        media=value.get("media"),
        raw=value.get("raw"),
    )


def idempotency_key(
    payload: InboundPayload,
    *,
    provider_receipt_id: str | None = None,
) -> str:
    """A tenant-scoped fixed-length key for provider retries.

    Provider identifiers win. The canonical payload hash is the safe fallback
    for malformed/legacy senders that omit one; those senders give us no
    stronger way to distinguish a retry from an identical new delivery.
    """
    source = (payload.external_message_id or provider_receipt_id or "").strip()
    if source:
        material = f"provider:{source}"
    else:
        material = "payload:" + json.dumps(
            serialize_inbound(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def enqueue_inbound(
    db: AsyncSession,
    *,
    channel: str,
    payload: InboundPayload,
    context: dict[str, Any] | None = None,
    provider_receipt_id: str | None = None,
) -> uuid.UUID:
    """Stage a receipt idempotently in the caller's database transaction."""
    key = idempotency_key(payload, provider_receipt_id=provider_receipt_id)
    receipt_id = uuid.uuid5(payload.agency_id, f"{channel}:{key}")
    # Canonical strings make the bind values portable through lightweight DB
    # proxies as well as asyncpg; PostgreSQL performs the JSONB validation.
    serialized_payload = json.dumps(
        serialize_inbound(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    serialized_context = json.dumps(
        context or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    stmt = (
        insert(InboundOutbox)
        .values(
            id=receipt_id,
            agency_id=payload.agency_id,
            channel=channel,
            idempotency_key=key,
            payload=cast(serialized_payload, JSONB),
            context=cast(serialized_context, JSONB),
            attempt_count=0,
            available_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(
            constraint="uq_inbound_outbox_agency_channel_key"
        )
    )
    await db.execute(stmt)
    return receipt_id


def retry_at(attempt_count: int) -> datetime:
    delay = min(3600, 2 ** min(max(attempt_count, 1), 12))
    return datetime.now(UTC) + timedelta(seconds=delay)


def complete_receipt(receipt: InboundOutbox) -> None:
    receipt.processed_at = datetime.now(UTC)
    receipt.last_error = None
    receipt.payload = {}
    receipt.context = {}


def pending_reply(receipt: InboundOutbox) -> dict[str, Any] | None:
    """Return a reply staged for external delivery, if one exists."""
    value = (receipt.context or {}).get(PENDING_REPLY_KEY)
    return dict(value) if isinstance(value, dict) else None


def stage_reply(receipt: InboundOutbox, reply: dict[str, Any]) -> None:
    """Persist delivery intent and keep other channel context intact.

    The lease prevents the periodic worker from racing the process that just
    staged the reply.  If that process dies, the same receipt becomes due and
    the worker delivers the stored reply without running the inbound turn a
    second time.
    """
    context = dict(receipt.context or {})
    context[PENDING_REPLY_KEY] = dict(reply)
    receipt.context = context
    receipt.available_at = datetime.now(UTC) + DELIVERY_LEASE


def lease_pending_reply(receipt: InboundOutbox) -> None:
    """Reserve an already-staged delivery while this process sends it."""
    receipt.available_at = datetime.now(UTC) + DELIVERY_LEASE


def defer_receipt(receipt: InboundOutbox, exc: BaseException) -> None:
    receipt.attempt_count += 1
    receipt.available_at = retry_at(receipt.attempt_count)
    receipt.last_error = f"{type(exc).__name__}: {exc}"[:500]


async def lock_receipt(
    db: AsyncSession,
    receipt_id: uuid.UUID,
    *,
    channel: str,
    due_only: bool = True,
) -> InboundOutbox | None:
    conditions = [
        InboundOutbox.id == receipt_id,
        InboundOutbox.channel == channel,
        InboundOutbox.processed_at.is_(None),
    ]
    if due_only:
        conditions.append(InboundOutbox.available_at <= datetime.now(UTC))
    stmt = (
        select(InboundOutbox)
        .where(*conditions)
        .with_for_update(skip_locked=True)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not isinstance(row, InboundOutbox):
        return None

    # The receipt may have been accepted immediately before cancellation or
    # while the termination purge was racing the channel worker. Check again
    # under its row lock and clear the PII-bearing body instead of creating a
    # new Lead/Conversation for an account whose operational data is gone.
    if not await _agency_accepts_inbound_processing(db, row.agency_id):
        complete_receipt(row)
        return None
    return row


async def _agency_accepts_inbound_processing(
    db: AsyncSession,
    agency_id: uuid.UUID,
) -> bool:
    agency = await db.get(Agency, agency_id)
    if agency is None or (agency.settings or {}).get(PURGED_AT_KEY):
        return False
    subscription_status = (
        await db.execute(
            select(Subscription.status).where(Subscription.agency_id == agency_id)
        )
    ).scalar_one_or_none()
    return subscription_status not in BLOCKING_STATUSES


async def pending_receipts(
    db: AsyncSession,
    *,
    limit: int = 10,
) -> list[tuple[uuid.UUID, str]]:
    """Snapshot due work; channel processors take the actual row lock."""
    stmt = (
        select(InboundOutbox.id, InboundOutbox.channel)
        .where(
            InboundOutbox.processed_at.is_(None),
            InboundOutbox.available_at <= datetime.now(UTC),
        )
        .order_by(InboundOutbox.available_at, InboundOutbox.created_at)
        .limit(limit)
    )
    return [(receipt_id, channel) for receipt_id, channel in (await db.execute(stmt)).all()]
