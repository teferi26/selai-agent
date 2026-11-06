"""WhatsApp Cloud API webhook endpoints.

GET  /webhooks/whatsapp  → verification challenge
POST /webhooks/whatsapp  → inbound messages (signed by Meta)

The challenge accepts the per-agency token the panel hands out *and* the
global one (`app/channels/whatsapp/security.py`): the raw global token is a
secret shared by every tenant and had no business being printed in anybody's
dashboard, but apps configured with it already have it saved in Meta.

Maps `phone_number_id` in the payload to the owning agency via
`agencies.settings.whatsapp_phone_number_id`. For the pilot we only have
one agency, so we fall back to the only agency if the mapping is absent.

Replies are sent with the *agency's own* Cloud API credentials when the
agency connected its number from the dashboard (settings keys
`whatsapp_access_token` + `whatsapp_phone_number_id`); otherwise we fall
back to the global env-configured client.

Signature: an agency connects its own Meta app (that is what the dashboard
guide walks it through) and stores that app's secret (`whatsapp_app_secret`,
now mandatory in POST /channels/whatsapp). Meta signs that agency's webhooks
with it, never with ours. The owning agency is resolved *first* (from the
still-untrusted payload, read-only) because **which** secret is the right
one depends on it:

- webhook for a number an agency connected → that agency's own secret and
  nothing else. The global secret is shared by every tenant on our Meta app,
  so accepting it as a fallback would let anyone holding it forge messages
  into the one tenant that isolated itself. No fallback, in either
  direction.
- webhook for *our own* number (`settings.whatsapp_phone_number_id`) or for
  a number no agency claims → the global secret. That number is served by
  our Meta app, so our app secret is the sender of record.

An agency that connected a number and stored no secret is a **rejection**,
not a fallback: our guide has it running its own Meta app, so its webhooks
are signed with a secret we do not have and the global check could only ever
say "no". Answering 401 with a diagnostic log beats accepting a signature
that, by construction, cannot be the sender's — and the connect endpoint no
longer lets that state be created (only rows predating item 217 can be in
it, and they need a human to fix them, not a silent guess). The one
exception is the escape hatch `verify_signature` already has (development
with no global secret configured → any well-formed header passes): where no
signature is checked at all, none of this can add a rule. See
`_signature_checks_are_off`.

The validating secret also fixes the *scope* of the request: a payload may
batch changes for several numbers, so every message is re-checked against
the identity that signed it before it is processed. Nothing with side
effects — no agent run, no reply, no media download, no write — happens
before that.

Voice notes: audio messages carry a media id, which is downloaded from
Graph and transcribed (Deepgram) so the agent reads words instead of
"[audio]". Every step is best-effort; a failure degrades, never breaks.

Timing/durability (173/216): before the POST answers Meta, every authenticated
message is inserted idempotently in PostgreSQL. Everything after that —
transcription, the debounce window, the agent, the reply — can run in a
BackgroundTask for low latency or in the Celery recovery sweep. Both lock the
same durable receipt, so a process restart or a Redis outage delays work but
cannot silently acknowledge and lose it.

Both halves of the debounce take a `SELECT ... FOR UPDATE` on the
conversation row (`_lock_conversation`): appending and draining are
read-modify-writes on the same JSON column from different sessions, and
without the lock two messages landing together get the same `seq`, both
runs believe they are the last, and the lead gets the two answers the whole
feature exists to prevent.

The 12 s timer is still an `asyncio.sleep`, but both halves of its state are
durable: the message buffer lives on the conversation and the recovery timer
is represented by the pending inbound-outbox row. A restart reruns the wait
and rejoins the already-buffered message instead of waiting for a new one.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.background import task_session
from app.channels.dedup import inbound_already_processed
from app.channels.whatsapp.client import WhatsAppClient
from app.channels.whatsapp.debounce import (
    DEFAULT_DEBOUNCE_SECONDS,
    append_to_buffer,
    buffer_is_current,
    merge_texts,
    take_buffer,
)
from app.channels.whatsapp.parser import parse_whatsapp_webhook
from app.channels.whatsapp.security import (
    is_well_formed_signature,
    verify_signature,
    verify_token_matches,
)
from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import Agency, Conversation
from app.db.session import get_db
from app.services.conversation_service import InboundPayload, handle_inbound
from app.services.inbound_outbox import (
    complete_receipt,
    defer_receipt,
    deserialize_inbound,
    enqueue_inbound,
    lease_pending_reply,
    lock_receipt,
    pending_reply,
    stage_reply,
)
from app.services.lead_resolver import resolve_lead_and_conversation
from app.services.plan_limits import is_subscription_blocked
from app.services.secret_store import SecretDecryptionError, secret_from_settings
from app.services.transcription import transcribe_audio

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])
log = get_logger(__name__)

# A voice note blocks the reply, so both hops are kept short: better a
# degraded "[audio]" than a lead waiting 60s for an answer.
_MEDIA_DOWNLOAD_TIMEOUT = 10.0
_TRANSCRIPTION_TIMEOUT = 20.0
VOICE_NOTE_PREFIX = "[Nota de voz] "

# (216) How long a message waits for its follow-ups. Module-level so tests
# can turn the window off; 0 skips the buffer entirely.
DEBOUNCE_SECONDS = DEFAULT_DEBOUNCE_SECONDS


@router.get("")
async def verify(
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
) -> PlainTextResponse:
    # `verify_token_matches` accepts the per-agency token the panel hands out
    # and the global one (apps configured before per-agency tokens existed,
    # plus our own). It also refuses everything when none is configured, and
    # compares as bytes — `hmac.compare_digest` on strs raises TypeError for
    # non-ASCII input, so this endpoint used to answer 500 to
    # `?hub.verify_token=ñ` instead of 403.
    if mode == "subscribe" and verify_token_matches(token):
        return PlainTextResponse(challenge)
    log.warning("whatsapp.webhook.verification_failed", mode=mode)
    raise HTTPException(status.HTTP_403_FORBIDDEN, "verification failed")


@router.post("")
async def receive(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    raw = await request.body()
    if not is_well_formed_signature(x_hub_signature_256):
        # Missing or not even shaped like "sha256=<64 hex>": reject before
        # parsing the body or touching the DB. The endpoint is exempt from the
        # rate limiter, so garbage must cost as little as possible.
        log.warning("whatsapp.webhook.missing_signature")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")

    # Parse *without trusting anything*: the only value taken from the body
    # before the signature is checked is the phone_number_id, and only to run
    # a read-only lookup of the agency that owns it. Parsing never raises
    # here — an unauthenticated caller must get the same 401 whether its
    # payload was malformed or merely unsigned.
    body = _parse_body(raw)
    signing_pnid = _first_phone_number_id(body) if body is not None else None
    # A body that didn't parse identifies no tenant: don't spend a query on
    # it, only the global secret can possibly vouch for it.
    signing_agency = (
        await _agency_for_phone_number(db, signing_pnid) if body is not None else None
    )
    try:
        agency_secret = (
            _agency_signing_secret(signing_agency, signing_pnid)
            if signing_agency is not None
            else ""
        )
    except SecretDecryptionError as exc:
        log.error(
            "whatsapp.webhook.credentials_unreadable",
            agency=str(signing_agency.id) if signing_agency is not None else None,
        )
        # A 5xx makes Meta retry after the owner reconnects the channel. A 2xx
        # would acknowledge and permanently lose a message we cannot process.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "channel_credentials_unavailable"
        ) from exc
    if agency_secret is None:
        if _signature_checks_are_off():
            agency_secret = ""
        else:
            # The agency connected this number but stored no app secret. It is
            # running its own Meta app (that is what the guide sets up), so the
            # global secret cannot be the signer and checking against it would
            # only produce a 401 with no explanation. Say why instead: this is
            # a configuration hole a human has to close by reconnecting the
            # number with the secret.
            log.warning(
                "whatsapp.webhook.agency_without_app_secret",
                phone_number_id=_short(signing_pnid),
                agency=str(signing_agency.id) if signing_agency is not None else None,
            )
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")

    # The agency's own secret is exclusive: no falling back to the global one.
    if agency_secret:
        valid = verify_signature(raw, x_hub_signature_256, agency_secret)
        signing_agency_id = signing_agency.id if signing_agency is not None else None
    else:
        valid = verify_signature(raw, x_hub_signature_256)
        signing_agency_id = None
    if not valid:
        # (217) A bad signature is either an attack or — far more often — an
        # agency that pasted the secret of a different Meta app. The log has
        # to tell the two apart without a debugger: which number, whether we
        # know it, whose it is, and which secret we checked against.
        log.warning(
            "whatsapp.webhook.bad_signature",
            phone_number_id=_short(signing_pnid),
            known_number=signing_agency is not None,
            agency=str(signing_agency.id) if signing_agency is not None else None,
            agency_owns_number=(
                signing_agency is not None and _owns_number(signing_agency, signing_pnid)
            ),
            agency_app_secret_set=_has_own_secret(signing_agency),
            secret_checked="agency" if agency_secret else "global",
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")
    if body is None:
        # Authentic sender, unusable body: worth surfacing as a 400.
        log.warning("whatsapp.webhook.bad_json")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid payload")

    # ── Signature verified: side effects allowed from here on. ──
    inbounds = parse_whatsapp_webhook(body)
    if not inbounds:
        return {"status": "ignored"}

    # The lookup above already resolved this phone_number_id: reuse it for the
    # first matching message (the common one-message webhook) instead of
    # querying twice, then drop it — after a commit/rollback the instance can
    # be expired and must be re-queried.
    prefetched: dict[str, Agency] = {}
    if signing_pnid and signing_agency is not None:
        prefetched[signing_pnid] = signing_agency

    queued = 0
    duplicates = 0
    pending_tasks: list[
        tuple[uuid.UUID, uuid.UUID, dict[str, Any], str | None, InboundPayload]
    ] = []
    for payload in inbounds:
        phone_number_id = (payload.raw or {}).get("phone_number_id")
        agency = prefetched.pop(str(phone_number_id), None) if phone_number_id else None
        if agency is None:
            agency = await _agency_for_phone_number(db, phone_number_id)
        if agency is None:
            log.warning("whatsapp.no_agency_match", phone_number_id=phone_number_id)
            continue
        if not _in_signature_scope(agency, phone_number_id, signing_agency_id):
            # One webhook can batch changes for several numbers. Whoever
            # signed it only speaks for their own: a payload signed with
            # agency A's secret cannot inject a message into agency B, and a
            # globally signed one cannot inject into an agency that has its
            # own secret (that agency's Meta app is the only valid sender).
            log.warning(
                "whatsapp.webhook.out_of_scope_message",
                phone_number_id=phone_number_id,
                agency=str(agency.id),
            )
            continue
        payload.agency_id = agency.id

        # Paused/canceled/unpaid accounts are not served (the trial-expiry
        # promise); drop silently — a 4xx here would make Meta retry forever.
        if await is_subscription_blocked(db, agency.id):
            log.info("whatsapp.skipped_blocked_subscription", agency=str(agency.id))
            continue

        # (173) Meta retried a delivery we already handled. Everything with
        # side effects is downstream of here, so a retry costs one indexed
        # query: no agent run, no second reply, no duplicated lead.
        if await inbound_already_processed(db, agency.id, payload.external_message_id):
            log.info(
                "whatsapp.inbound.duplicate",
                agency=str(agency.id),
                message_id=payload.external_message_id,
            )
            duplicates += 1
            continue

        if not _outbound_credentials_available(agency, phone_number_id):
            log.error(
                "whatsapp.webhook.credentials_unavailable",
                agency=str(agency.id),
                phone_number_id=_short(phone_number_id),
            )
            # Do not acknowledge an inbound that the background task cannot
            # persist and answer. Meta will retry after the channel is fixed.
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "channel_credentials_unavailable"
            )

        # The receipt is staged in the request transaction. We commit every
        # accepted message before the 2xx below; an unavailable PostgreSQL
        # therefore produces a 5xx and Meta retries instead of losing a lead.
        receipt_id = await enqueue_inbound(
            db,
            channel="whatsapp",
            payload=payload,
            context={
                "phone_number_id": str(phone_number_id) if phone_number_id else None
            },
        )
        pending_tasks.append(
            (
                receipt_id,
                agency.id,
                dict(agency.settings or {}),
                str(phone_number_id) if phone_number_id else None,
                payload,
            )
        )
        queued += 1

    if queued:
        await db.commit()
        for receipt_id, agency_id, agency_settings, phone_number_id, payload in pending_tasks:
            # Fast path. The database row remains the source of truth and the
            # periodic worker will resume it if this in-process task vanishes.
            background_tasks.add_task(
                process_inbound_receipt,
                receipt_id=receipt_id,
                fallback_agency_id=agency_id,
                fallback_agency_settings=agency_settings,
                fallback_phone_number_id=phone_number_id,
                fallback_payload=payload,
            )

    if queued == 0 and duplicates:
        return {"status": "duplicate"}
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Background processing — everything with side effects lives here
# ---------------------------------------------------------------------------


async def process_inbound(
    *,
    agency_id: uuid.UUID,
    agency_settings: dict[str, Any],
    phone_number_id: str | None,
    payload: InboundPayload,
) -> None:
    """Transcribe, debounce, run the agent, answer the lead.

    Runs after the response went out, so nothing here may raise: an
    exception in a BackgroundTask has nobody left to tell.
    """
    try:
        await _process_inbound(
            agency_id=agency_id,
            agency_settings=agency_settings,
            phone_number_id=phone_number_id,
            payload=payload,
        )
    except Exception:
        log.exception("whatsapp.background.failed", agency=str(agency_id))


async def _settle_inbound_receipt(
    receipt_id: uuid.UUID,
    *,
    error: BaseException | None = None,
) -> None:
    """Complete or defer a staged delivery in a fresh transaction."""
    async with task_session() as db:
        receipt = await lock_receipt(
            db,
            receipt_id,
            channel="whatsapp",
            due_only=False,
        )
        if receipt is None:
            return
        if error is None:
            complete_receipt(receipt)
            return
        defer_receipt(receipt, error)
        log.error(
            "whatsapp.inbound_outbox.deferred",
            receipt=str(receipt.id),
            attempt=receipt.attempt_count,
            error=type(error).__name__,
        )


async def _deliver_pending_reply(
    agency_settings: dict[str, Any],
    phone_number_id: str | None,
    reply: dict[str, Any],
) -> None:
    to = str(reply.get("to") or "").strip()
    text = str(reply.get("text") or "")
    if not to or not text:
        raise ValueError("staged WhatsApp reply is incomplete")
    client = _client_for_settings(agency_settings, phone_number_id)
    await client.send_text(to, text)


async def process_inbound_receipt(
    *,
    receipt_id: uuid.UUID,
    fallback_agency_id: uuid.UUID | None = None,
    fallback_agency_settings: dict[str, Any] | None = None,
    fallback_phone_number_id: str | None = None,
    fallback_payload: InboundPayload | None = None,
) -> None:
    """Process one durable receipt and durably stage its external reply.

    Fallback values keep direct/unit callers compatible with the former
    BackgroundTask contract; real database sessions always read the durable
    copy. The inbound turn and its pending reply are committed together before
    Meta is called. Failures leave that reply on the row for the recovery sweep
    without running the inbound message or agent twice.
    """
    reply: dict[str, Any] | None = None
    agency_settings: dict[str, Any] = {}
    phone_number_id: str | None = None
    try:
        async with task_session() as db:
            receipt = await lock_receipt(db, receipt_id, channel="whatsapp")
            if receipt is None:
                # Test doubles used by the channel contract tests predate the
                # outbox. Never take this branch for a real AsyncSession: None
                # can also mean another worker owns the SKIP LOCKED row.
                if not isinstance(db, AsyncSession) and fallback_payload is not None:
                    await process_inbound(
                        agency_id=fallback_agency_id or fallback_payload.agency_id,
                        agency_settings=fallback_agency_settings or {},
                        phone_number_id=fallback_phone_number_id,
                        payload=fallback_payload,
                    )
                return

            payload = deserialize_inbound(receipt.payload)
            agency = await db.get(Agency, receipt.agency_id)
            if agency is None:
                complete_receipt(receipt)
                return
            agency_settings = dict(agency.settings or {})
            phone_number_id = (receipt.context or {}).get("phone_number_id")

            reply = pending_reply(receipt)
            if reply is not None:
                lease_pending_reply(receipt)
            else:
                if await inbound_already_processed(
                    db, receipt.agency_id, payload.external_message_id
                ):
                    complete_receipt(receipt)
                    return
                reply = await _process_inbound(
                    agency_id=receipt.agency_id,
                    agency_settings=agency_settings,
                    phone_number_id=phone_number_id,
                    payload=payload,
                    resume_buffered=True,
                    db=db,
                    deliver_reply=False,
                )
                if reply is None:
                    complete_receipt(receipt)
                    return
                stage_reply(receipt, reply)
    except Exception as exc:
        await _settle_inbound_receipt(receipt_id, error=exc)
        return

    try:
        await _deliver_pending_reply(agency_settings, phone_number_id, reply)
    except Exception as exc:
        await _settle_inbound_receipt(receipt_id, error=exc)
        return
    await _settle_inbound_receipt(receipt_id)


async def _process_inbound(
    *,
    agency_id: uuid.UUID,
    agency_settings: dict[str, Any],
    phone_number_id: str | None,
    payload: InboundPayload,
    resume_buffered: bool = False,
    db: AsyncSession | None = None,
    deliver_reply: bool = True,
) -> dict[str, Any] | None:
    client = _client_for_settings(agency_settings, phone_number_id)

    await _transcribe_voice_note(client, payload)

    # Straight after the download, and before the debounce window: the two
    # ticks are the only sign the lead gets that someone is there.
    if payload.external_message_id:
        try:
            await client.mark_as_read(payload.external_message_id)
        except Exception:
            log.warning("whatsapp.mark_as_read.failed")

    seq: int | None = None
    conversation_id: uuid.UUID | None = None
    if DEBOUNCE_SECONDS > 0:
        seq, conversation_id = await _buffer_inbound(
            agency_id,
            payload,
            resume_existing=resume_buffered,
        )
        if seq is None:
            # Already buffered or already processed: a retry that slipped
            # past the check in the route (its Message row did not exist
            # yet). Drop it here, still before the agent runs.
            log.info("whatsapp.debounce.duplicate_dropped", agency=str(agency_id))
            return
        await asyncio.sleep(DEBOUNCE_SECONDS)

    async def _handle(processing_db: AsyncSession):
        try:
            if seq is not None and conversation_id is not None:
                merged = await _claim_buffer(processing_db, conversation_id, seq)
                if merged is None:
                    log.info(
                        "whatsapp.debounce.superseded", conv=str(conversation_id)
                    )
                    return None
                if merged:
                    payload.text = merged
            return await handle_inbound(processing_db, payload)
        except Exception:
            log.exception("whatsapp.handle_inbound.failed", agency=str(agency_id))
            raise

    if db is None:
        async with task_session() as processing_db:
            result = await _handle(processing_db)
    else:
        result = await _handle(db)

    if result is None:
        return None
    if result.reply_text and payload.identity.whatsapp_wa_id:
        reply = {
            "to": payload.identity.whatsapp_wa_id,
            "text": result.reply_text,
        }
        if deliver_reply:
            await client.send_text(reply["to"], reply["text"])
            return None
        return reply
    return None


async def _lock_conversation(
    db: AsyncSession, conversation_id: uuid.UUID
) -> Conversation | None:
    """The conversation row, locked until this transaction ends.

    Both users of the buffer do a read-modify-write on `conversation.extra`
    from *different* sessions, so without a lock they race:

    - two webhooks landing together both read the same empty buffer, both
      write back their own single-item copy, and one message is gone;
    - worse, both then hold `seq == 1`, both believe they are the last one
      to arrive, and the lead gets two answers to one burst — exactly the
      thing item 216 exists to prevent;
    - and a run draining the buffer can overwrite an append that happened
      while it was reading, which drops that message's `seq` on the floor:
      its own run then finds itself superseded and nobody ever answers it.

    `populate_existing` because the row must be re-read *after* the lock is
    granted; without it SQLAlchemy keeps whatever this session loaded a
    moment earlier and the lock protects a stale copy.

    On SQLite (tests, local scripts) `FOR UPDATE` compiles to nothing; the
    guarantee is a PostgreSQL one, which is where it matters.
    """
    stmt = (
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _buffer_inbound(
    agency_id: uuid.UUID,
    payload: InboundPayload,
    *,
    resume_existing: bool = False,
) -> tuple[int | None, uuid.UUID | None]:
    """(216) Append this message to its conversation's pending buffer.

    Resolving the lead/conversation here is what makes the buffer
    addressable; `handle_inbound` resolves the same pair again later and
    finds what this call created.
    """
    async with task_session() as db:
        _lead, conv = await resolve_lead_and_conversation(
            db, agency_id, payload.identity
        )
        # Serialize the append against every other message on this
        # conversation. The lock is released a few milliseconds later, when
        # this session commits — long before the debounce window opens.
        conv = await _lock_conversation(db, conv.id) or conv
        extra, seq = append_to_buffer(
            conv.extra,
            external_message_id=payload.external_message_id,
            text=payload.text,
        )
        if seq is None:
            if resume_existing and payload.external_message_id:
                # A process may have died after committing the buffer but
                # before completing its outbox row. That is not a provider
                # duplicate: the recovery worker owns the same receipt and
                # must restart the timer for the durable buffered item.
                buffer = (conv.extra or {}).get("pending_buffer")
                items = buffer.get("items") if isinstance(buffer, dict) else []
                if any(
                    isinstance(item, dict)
                    and item.get("id") == payload.external_message_id
                    for item in (items or [])
                ):
                    return int(buffer.get("seq") or 0), conv.id
            return None, conv.id
        conv.extra = extra
        return seq, conv.id


async def _claim_buffer(
    db: AsyncSession, conversation_id: uuid.UUID, seq: int
) -> str | None:
    """Take the whole buffer if this run is still the last one to arrive.

    `None` means a newer message landed while we slept and its run will
    process everything, ours included. `""` means there was nothing usable
    to merge, so the caller keeps the text it already had.

    Draining in the same session that runs the agent is deliberate: if the
    run fails, the rollback puts the buffered messages back instead of
    silently eating them. The row stays locked for the length of that run,
    which `handle_inbound` would have taken anyway the moment it touched the
    conversation.
    """
    conv = await _lock_conversation(db, conversation_id)
    if conv is None:
        return None
    if not buffer_is_current(conv.extra, seq):
        return None
    extra, texts = take_buffer(conv.extra)
    conv.extra = extra
    return merge_texts(texts)


def _parse_body(raw: bytes) -> dict[str, Any] | None:
    """The JSON object, or None when the bytes aren't one.

    Never raises: at call time the request is still unauthenticated, and the
    caller must not answer differently depending on what an unsigned body
    contained.
    """
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _short(value: Any, limit: int = 64) -> str | None:
    """A log-safe rendering of an unverified value: never None-crashes, never
    lets a hostile body write a megabyte into our logs."""
    if value in (None, ""):
        return None
    return str(value)[:limit]


def _owns_number(agency: Agency, phone_number_id: Any) -> bool:
    stored = (agency.settings or {}).get("whatsapp_phone_number_id")
    return bool(stored) and bool(phone_number_id) and str(stored) == str(phone_number_id)


def _has_own_secret(agency: Agency | None) -> bool:
    return bool(agency is not None and (agency.settings or {}).get("whatsapp_app_secret"))


def _signature_checks_are_off() -> bool:
    """True when `verify_signature` waves every well-formed header through.

    That is the documented dev escape hatch (development + no global secret
    configured). The stricter rules below are about production trust, so they
    must not make a local environment reject webhooks that the verification
    itself would have accepted — nothing was checked there in the first place.
    """
    return settings.is_dev and not settings.whatsapp_app_secret.get_secret_value()


def _is_our_own_number(phone_number_id: Any) -> bool:
    """The number *we* own (the env-configured one), served by our Meta app.

    Kept apart from `_owns_number` because the two answer different
    questions: whose tenant a message belongs to, versus whose Meta app is
    the sender of record for it.
    """
    ours = settings.whatsapp_phone_number_id
    return bool(ours) and bool(phone_number_id) and str(ours) == str(phone_number_id)


def _agency_signing_secret(agency: Agency, phone_number_id: str | None) -> str | None:
    """Which secret Meta must have signed this webhook with.

    - `""` → the global one. The number is ours (our Meta app is the sender
      of record even when the conversation lands in an agency that runs its
      own app elsewhere), or no agency claims it.
    - `"<secret>"` → the agency's own app secret, the only signature accepted
      for the number that agency connected.
    - `None` → the agency connected this number and stored no secret. Not a
      fallback: see the module docstring. The caller rejects.
    """
    if _is_our_own_number(phone_number_id):
        return ""
    if not _owns_number(agency, phone_number_id):
        return ""
    return secret_from_settings(agency.settings, "whatsapp_app_secret") or None


def _in_signature_scope(
    agency: Agency, phone_number_id: Any, signing_agency_id: uuid.UUID | None
) -> bool:
    """True when this message may be processed under the signature we checked.

    `signing_agency_id` is set when the payload was validated with an
    agency's own secret: that vouches for that agency's own number and
    nothing else. When it is None the global secret validated the payload,
    which vouches for our own number and for numbers no agency connected —
    every number an agency took over with its own Meta app is out, whether or
    not it stored a secret, because there only that app may speak.
    """
    if signing_agency_id is not None:
        return agency.id == signing_agency_id and _owns_number(agency, phone_number_id)
    if _signature_checks_are_off():
        # Nothing was actually verified (dev, no secret configured), so there
        # is no signer whose reach could be limited.
        return True
    return _is_our_own_number(phone_number_id) or not _owns_number(
        agency, phone_number_id
    )


def _first_phone_number_id(body: dict[str, Any]) -> str | None:
    """`phone_number_id` of the first change carrying one. Defensive: the
    body is still unverified at this point, so anything may be missing or of
    the wrong type."""
    entries = body.get("entry")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata")
            if not isinstance(metadata, dict):
                continue
            pnid = metadata.get("phone_number_id")
            if pnid:
                return str(pnid)
    return None


async def _transcribe_voice_note(
    client: WhatsAppClient, payload: InboundPayload
) -> None:
    """Replace `[audio]` with the transcription of the voice note, in place.

    Best-effort at every step (no media id, download failure, no Deepgram
    key, empty transcript): on any of them the payload keeps the current
    `[audio]` text and the webhook carries on.
    """
    raw = payload.raw or {}
    media_id = raw.get("audio_media_id")
    if not media_id:
        return

    try:
        downloaded = await client.download_media(
            str(media_id), timeout_seconds=_MEDIA_DOWNLOAD_TIMEOUT
        )
    except Exception:
        log.warning("whatsapp.voice_note.download_failed")
        return
    if not downloaded:
        return

    audio, mime = downloaded
    content_type = str(mime or raw.get("audio_mime_type") or "audio/ogg")
    try:
        transcript = await transcribe_audio(
            audio, content_type=content_type, timeout_seconds=_TRANSCRIPTION_TIMEOUT
        )
    except Exception:
        log.warning("whatsapp.voice_note.transcription_failed")
        return

    transcript = (transcript or "").strip()
    if not transcript:
        log.info("whatsapp.voice_note.empty_transcript")
        return

    payload.text = f"{VOICE_NOTE_PREFIX}{transcript}"
    log.info("whatsapp.voice_note.transcribed", chars=len(transcript))


async def _agency_for_phone_number(db: AsyncSession, phone_number_id: str | None) -> Agency | None:
    if phone_number_id:
        # NOTE: `.as_string()` (not `.astext`) — the column is the generic
        # sqlalchemy JSON type, whose indexed expressions don't have the
        # postgres-only `.astext`; that raised AttributeError on every
        # webhook that carried a phone_number_id.
        stmt = select(Agency).where(
            Agency.settings["whatsapp_phone_number_id"].as_string() == phone_number_id
        )
        # Nothing stops two agencies from storing the same number: connect
        # only asks Meta whether the caller's token can read it, and two
        # tenants of the same Meta Business both can. `scalar_one_or_none()`
        # then raises → 500 on *every* webhook for that number, which Meta
        # retries and eventually disables: the channel dies for both of them
        # while the panel still says "conectado". Refusing is no better for
        # them, but it is bounded and it says so in the log instead of
        # arbitrarily handing one tenant the other's messages.
        try:
            agency = (await db.execute(stmt)).scalar_one_or_none()
        except MultipleResultsFound:
            log.error(
                "whatsapp.duplicate_phone_number_id",
                phone_number_id=_short(phone_number_id),
            )
            return None
        if agency is not None:
            return agency

    # Fallback: if exactly one agency exists, use it. Avoids blocking the pilot
    # until the settings mapping is filled in. In prod with multiple tenants,
    # this is removed.
    count_result = await db.execute(select(func.count()).select_from(Agency))
    if count_result.scalar_one() == 1:
        agency = (await db.execute(select(Agency).limit(1))).scalar_one()
        stored = (agency.settings or {}).get("whatsapp_phone_number_id")
        if (
            phone_number_id
            and stored
            and str(stored) != str(phone_number_id)
            and phone_number_id != settings.whatsapp_phone_number_id
        ):
            # The agency connected its *own* number and this webhook is for a
            # different, unknown one. Falling back would attach the message to
            # this agency and reply from its number — a number the sender
            # never texted. Drop it instead.
            log.warning(
                "whatsapp.fallback_rejected",
                phone_number_id=phone_number_id,
                agency_phone_number_id=str(stored),
            )
            return None
        return agency

    return None


def _client_for_settings(
    cfg: dict[str, Any], phone_number_id: str | None = None
) -> WhatsAppClient:
    """The agency's own Cloud API client when it connected its number from
    the dashboard; otherwise the global env-configured client.

    Takes the settings blob rather than the Agency row because the caller is
    a background task: the ORM instance belongs to a session that closed
    when the response went out.

    `phone_number_id` is the number the inbound webhook targeted: the
    agency's stored credentials are only valid for *their* number, so when
    the inbound number is a different one (single-agency fallback on the
    global env number) the reply must go through the global client — using
    the agency's credentials there would answer from the wrong number.
    """
    access_token = secret_from_settings(cfg, "whatsapp_access_token")
    stored_phone_number_id = cfg.get("whatsapp_phone_number_id")
    if (
        access_token
        and stored_phone_number_id
        and (phone_number_id is None or str(stored_phone_number_id) == str(phone_number_id))
    ):
        return WhatsAppClient(
            access_token=str(access_token), phone_number_id=str(stored_phone_number_id)
        )
    return WhatsAppClient()


def _outbound_credentials_available(agency: Agency, phone_number_id: Any) -> bool:
    """Whether processing this inbound can build the correct outbound client.

    Our global number uses the global client. A number connected by the agency
    must have a readable tenant token; silently falling back would reply from a
    number the lead never contacted.
    """
    if _is_our_own_number(phone_number_id) or not _owns_number(agency, phone_number_id):
        return True
    try:
        return bool(secret_from_settings(agency.settings, "whatsapp_access_token"))
    except SecretDecryptionError:
        return False
