"""Voice endpoints exposed to Vapi.

Vapi calls us in three places:
  POST /voice/llm   — OpenAI-compatible chat completions; our brain.
  POST /webhooks/voice — call.start / call.end / status updates.

Vapi authenticates outgoing webhooks with a shared secret header.

Multi-tenant routing: see `_resolve_agency`. Outbound calls inject
`metadata.agency_id` per call, but inbound calls carry only whatever the
*assistant* was created with, so the resolver also matches on the assistant
id and the phone number id. A call it cannot attribute to an agency produces
nothing at all, so it is logged as an error instead of dropped in silence.

Routing to the *wrong* agency is worse than not routing: it files a lead's
call in a competitor's CRM. So every ambiguity (two agencies claiming one
handle, a payload that names an agency our own records contradict) resolves
to "no agency" plus a loud log, never to a guess.
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import (
    Agency,
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Lead,
    Message,
    MessageDirection,
    MessageRole,
    Subscription,
)
from app.db.session import get_db
from app.services.conversation_service import InboundPayload, handle_inbound
from app.services.lead_resolver import InboundIdentity, resolve_lead_and_conversation
from app.services.plan_limits import ensure_subscription_usable, voice_minutes_status

router = APIRouter(prefix="/voice", tags=["voice"])
webhook_router = APIRouter(prefix="/webhooks/voice", tags=["voice"])

log = get_logger(__name__)

# What the agent says when the agency's subscription is paused, canceled or
# unpaid. Neutral on purpose: the caller is the agency's lead, not our
# customer, and must not be told about their billing.
INACTIVE_SUBSCRIPTION_REPLY = (
    "Gracias por llamar. Ahora mismo no puedo atenderte por teléfono. "
    "Vuelve a intentarlo más tarde, por favor."
)

# What the agent says when the agency has spent every voice minute its plan
# includes. There is a person on the line: the turn that hits the ceiling is
# answered with a proper goodbye — it says the conversation is on record and
# that somebody will call back — instead of silence or a technical error.
# Like the message above it says nothing about billing; the caller is the
# agency's lead, not our customer.
VOICE_MINUTES_EXHAUSTED_REPLY = (
    "Gracias por llamar. Tengo que dejarte aquí, pero tu consulta queda "
    "registrada y un agente se pondrá en contacto contigo enseguida. "
    "Disculpa las molestias y que tengas un buen día."
)

# What the agent says when we cannot tell which agency the call belongs to.
# It used to promise "te llama un agente humano", and nobody ever called: with
# no agency there is no lead, no conversation and no notification — the
# promise was impossible by construction. It now says only what is true: it
# does not promise a callback, and it does not promise the line will be back
# "en unos minutos" either, because a misrouted assistant stays broken until
# somebody fixes it.
UNROUTED_CALL_REPLY = (
    "Lo siento, ahora mismo no puedo atenderte: hay un problema técnico con la línea. "
    "Vuelve a intentarlo más tarde, por favor."
)


@dataclass(frozen=True)
class CallRouting:
    """Every handle in a Vapi payload that can point at one agency.

    `_resolve_agency` used to receive only `metadata`, which is the one thing
    an inbound call to an already-provisioned assistant does *not* carry
    reliably. It takes this object instead — still a single positional
    argument, so what routing depends on is readable in one place.
    """

    agency_id: str | None = None
    slug: str | None = None
    assistant_id: str | None = None
    phone_number_id: str | None = None
    call_id: str | None = None


def _as_text(value: Any) -> str | None:
    """Non-empty scalar as a stripped string, otherwise None."""
    if value is None or isinstance(value, dict | list | bool):
        return None
    text = str(value).strip()
    return text or None


def _dig(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def call_routing(payload: dict[str, Any]) -> CallRouting:
    """Read the routing handles out of a Vapi payload.

    `payload` is a webhook's `message` object or the /voice/llm body; both
    carry `call`. Vapi does not put the assistant metadata in the same place
    on every event (`message.metadata`, `call.metadata`,
    `call.assistantOverrides.metadata`, `call.assistant.metadata`...), so all
    the known spots are merged with the outermost winning, instead of trusting
    the single `message.metadata` the code used to read.
    """
    call = payload.get("call") or {}
    sources = [
        payload.get("metadata"),
        call.get("metadata"),
        _dig(call, "assistantOverrides", "metadata"),
        _dig(call, "assistant", "metadata"),
        _dig(payload, "assistant", "metadata"),
    ]
    metadata: dict[str, Any] = {}
    for source in reversed(sources):  # reversed so the earliest source wins
        if isinstance(source, dict):
            metadata.update(source)

    return CallRouting(
        agency_id=_as_text(metadata.get("agency_id")),
        slug=_as_text(metadata.get("slug")),
        assistant_id=_as_text(
            call.get("assistantId")
            or _dig(call, "assistant", "id")
            or _dig(payload, "assistant", "id")
        ),
        phone_number_id=_as_text(
            call.get("phoneNumberId")
            or _dig(call, "phoneNumber", "id")
            or _dig(payload, "phoneNumber", "id")
        ),
        call_id=_as_text(call.get("id") or metadata.get("call_id")),
    )


def _log_unrouted(vapi_event: str, routing: CallRouting) -> None:
    """An unroutable call yields nothing: no lead, no transcript, no minutes.

    Three of the four webhooks used to `return` in silence, so this failure
    was invisible in the logs and surfaced only as an agency insisting the
    phone agent "does not work". Error level, with every handle we saw, so it
    can be traced back to an assistant in the Vapi dashboard.
    """
    # NOTE: `vapi_event`, not `event`: structlog's first positional argument is
    # already named `event`, so passing one here raises TypeError — inside the
    # error path that exists precisely so this stops being invisible.
    log.error(
        "voice.unrouted_call",
        vapi_event=vapi_event,
        call_id=routing.call_id,
        assistant_id=routing.assistant_id,
        phone_number_id=routing.phone_number_id,
        metadata_agency_id=routing.agency_id,
        metadata_slug=routing.slug,
    )


def call_is_already_under_way(messages: list[dict[str, Any]]) -> bool:
    """Has this call already been answered for real, before this turn?

    The minute counter only moves when a call *ends*, but an agency can have
    two lines busy at once: call A ending is what pushes the counter past the
    ceiling, and call B — mid-sentence — would then be hung up on its very
    next turn. A conversation that we were already holding when the ceiling
    was reached is allowed to finish; the cap still stops the next call, which
    is where the block belongs.

    "Answered for real" means an assistant turn of ours *after* the caller
    first spoke, and not the farewell:

    - the assistant greeting Vapi plays before the caller says anything is not
      an answer, so a call that starts over the cap is stopped on turn one;
    - a caller who keeps talking after the farewell must keep getting the
      farewell, not a free conversation;
    - if the request carries no assistant history at all, the answer is False
      and the cap applies — an unreadable transcript must not buy voice
      minutes selai pays for.
    """
    seen_caller = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "user":
            seen_caller = True
        elif role == "assistant" and seen_caller:
            content = str(message.get("content") or "")
            if content and VOICE_MINUTES_EXHAUSTED_REPLY not in content:
                return True
    return False


# ---------------------------------------------------------------------------
# OpenAI-compatible /voice/llm — Vapi's "custom LLM" endpoint
# ---------------------------------------------------------------------------

@router.post("/llm")
async def custom_llm(
    request: Request,
    x_vapi_secret: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _verify_vapi(x_vapi_secret)
    body = await request.json()

    messages: list[dict[str, Any]] = body.get("messages") or []
    metadata = body.get("metadata") or {}
    routing = call_routing(body)
    call_id: str | None = routing.call_id

    # Extract the latest user utterance.
    last_user = next(
        (m for m in reversed(messages) if m.get("role") == "user"),
        None,
    )
    user_text = (last_user or {}).get("content", "") if last_user else ""
    if not user_text:
        return _openai_response("¿Sigues ahí?")

    agency_id = await _resolve_agency(db, routing)
    if agency_id is None:
        _log_unrouted("llm", routing)
        return _openai_response(UNROUTED_CALL_REPLY)

    # An expired trial, a canceled plan or an unpaid invoice has to stop the
    # service somewhere, and this is the endpoint that actually spends money
    # (Claude tokens + Vapi minutes) on every turn of every inbound call.
    # Gating only the outbound endpoint left the pause purely decorative.
    try:
        await ensure_subscription_usable(db, agency_id)
    except HTTPException:
        log.info("voice.blocked_inactive_subscription", agency_id=str(agency_id))
        return _openai_response(INACTIVE_SUBSCRIPTION_REPLY)

    # Voice minutes were metered on the way out (voice_outbound.py) and not on
    # the way in, so an inbound line kept answering for free past the plan's
    # cap — every minute of it billed to selai by Vapi.
    #
    # `voice_minutes_status` is used instead of `ensure_can_use_voice` because
    # this is a live call: a 402 would surface to the caller as a broken line.
    # It also fails open, so a billing read that misbehaves never hangs up on
    # a real person. And a call we are already holding is left to finish
    # (`call_is_already_under_way`): the ceiling is crossed by the call that
    # *ends*, which with two lines busy is not the one being spoken into.
    minutes = await voice_minutes_status(db, agency_id)
    if minutes is not None and minutes.exceeded and not call_is_already_under_way(messages):
        log.warning(
            "voice.blocked_voice_minutes_exhausted",
            agency_id=str(agency_id),
            call_id=call_id,
            used=minutes.used,
            limit=minutes.limit,
        )
        return _openai_response(VOICE_MINUTES_EXHAUSTED_REPLY)

    caller_phone = metadata.get("customer_number") or _extract_phone(body)
    identity = InboundIdentity(
        channel=ConversationChannel.VOICE,
        external_thread_id=call_id,
        phone_e164=caller_phone,
        whatsapp_wa_id=None,
        display_name=metadata.get("customer_name"),
    )

    payload = InboundPayload(
        identity=identity,
        agency_id=agency_id,
        text=user_text,
        external_message_id=f"vapi-{call_id}-{int(time.time() * 1000)}",
        media=[],
        raw={"call_id": call_id, "vapi_metadata": metadata},
    )

    try:
        result = await handle_inbound(db, payload)
        await db.commit()
    except Exception:
        await db.rollback()
        log.exception("voice.handle_inbound.failed")
        return _openai_response("Disculpa, he tenido un fallo técnico. Repite, por favor.")

    reply = result.reply_text or "Un momento, por favor."
    return _openai_response(reply)


def _openai_response(text: str) -> dict[str, Any]:
    """Return an OpenAI-compatible chat-completion response. Vapi only
    reads the first choice's message content."""
    return {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "realtor-ai-voice",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ---------------------------------------------------------------------------
# /webhooks/voice — call lifecycle events from Vapi
# ---------------------------------------------------------------------------

@webhook_router.post("")
async def vapi_webhook(
    request: Request,
    x_vapi_secret: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    _verify_vapi(x_vapi_secret)
    body = await request.json()

    event_type = body.get("message", {}).get("type") or body.get("type")
    handlers = {
        "end-of-call-report": _on_call_end,
        "status-update": _on_status_update,
        "call-started": _on_call_start,
    }
    handler = handlers.get(event_type)
    if handler is None:
        log.info("voice.webhook.unhandled_event", event_type=event_type)
        return {"status": "ignored"}

    try:
        result = await handler(db, body)
        await db.commit()
    except Exception:
        await db.rollback()
        log.exception("voice.webhook.failed", event_type=event_type)
        raise

    # A dropped call answers "unrouted", not "ok": Vapi shows our response body
    # next to each delivery, and that is where whoever is asking "why is this
    # call not in the CRM?" looks first. 200 on purpose — retrying an
    # unroutable payload would produce the same nothing.
    return {"status": result or "ok"}


async def _on_call_start(db: AsyncSession, body: dict) -> str | None:
    """Create a placeholder conversation as soon as the call connects so
    other parts of the system can react in real time (UI updates, etc.)."""
    msg = body.get("message", {}) or body
    call = msg.get("call") or {}

    routing = call_routing(msg)
    agency_id = await _resolve_agency(db, routing)
    if agency_id is None:
        _log_unrouted("call-started", routing)
        return "unrouted"

    call_id = call.get("id")
    if await _conversation_for_call(db, agency_id, call_id) is not None:
        return None  # Vapi retried `call-started`; the placeholder already exists.

    customer_number = (call.get("customer") or {}).get("number")
    identity = InboundIdentity(
        channel=ConversationChannel.VOICE,
        external_thread_id=call_id,
        phone_e164=customer_number,
    )
    await resolve_lead_and_conversation(db, agency_id, identity)


async def _on_status_update(db: AsyncSession, body: dict) -> str | None:
    """Detect missed / not-answered calls and trigger a WhatsApp follow-up."""
    msg = body.get("message", {}) or body
    status_value = msg.get("status")
    ended_reason = msg.get("endedReason")
    call = msg.get("call") or {}

    is_missed = (
        status_value == "ended"
        and ended_reason in {"customer-did-not-answer", "no-answer", "busy", "voicemail"}
    )
    if not is_missed:
        return None

    routing = call_routing(msg)
    agency_id = await _resolve_agency(db, routing)
    if agency_id is None:
        _log_unrouted("status-update", routing)
        return "unrouted"

    customer_number = (call.get("customer") or {}).get("number")
    if not customer_number:
        return None

    # Defer the follow-up to a Celery task so the webhook stays snappy.
    try:
        from app.workers.tasks import missed_call_followup
        missed_call_followup.delay(str(agency_id), customer_number)
    except Exception:
        log.exception("voice.missed.dispatch_failed")


async def _on_call_end(db: AsyncSession, body: dict) -> str | None:
    """Persist the full transcript + summary at end of call.

    Vapi sends a structured `messages` array with role/content/timestamp.
    We replay it as Message rows so the dashboard sees the full convo.
    """
    msg = body.get("message", {}) or body
    call = msg.get("call") or {}
    transcript_messages: list[dict] = msg.get("messages") or []

    routing = call_routing(msg)
    agency_id = await _resolve_agency(db, routing)
    if agency_id is None:
        # Nothing to persist against: the transcript, the recording and the
        # billable minutes of a real, answered call are all lost here.
        _log_unrouted("end-of-call-report", routing)
        return "unrouted"

    call_id = call.get("id")
    customer_number = (call.get("customer") or {}).get("number")
    identity = InboundIdentity(
        channel=ConversationChannel.VOICE,
        external_thread_id=call_id,
        phone_e164=customer_number,
    )
    # Look the call up by its own id before falling back to identity
    # matching. `resolve_lead_and_conversation` finds leads by phone / email
    # / wa_id, so a caller who withheld their number matches nothing and gets
    # a brand-new conversation on every delivery — and the per-call dedup
    # below lives in `conversation.extra`, so a retried report would have
    # billed the same minutes twice and duplicated the call in the dashboard.
    conv = await _conversation_for_call(db, agency_id, call_id)
    lead = await db.get(Lead, conv.lead_id) if conv is not None else None
    if conv is None or lead is None:
        lead, conv = await resolve_lead_and_conversation(db, agency_id, identity)

    # Persist the per-turn transcript (skip system messages).
    existing = (
        await db.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conv.id)
        )
    ).scalar_one()
    if existing == 0:  # avoid duplicating if Vapi retries
        for tm in transcript_messages:
            role = tm.get("role")
            if role == "system":
                continue
            text = (tm.get("message") or tm.get("content") or "").strip()
            if not text:
                continue
            db.add(
                Message(
                    agency_id=agency_id,
                    conversation_id=conv.id,
                    direction=(
                        MessageDirection.INBOUND
                        if role == "user"
                        else MessageDirection.OUTBOUND
                    ),
                    role=(
                        MessageRole.LEAD
                        if role == "user"
                        else MessageRole.AGENT_AI
                    ),
                    content=text,
                    raw={"vapi_role": role, "time": tm.get("time")},
                )
            )

    # Mark conversation closed if the call ended cleanly and no human took over.
    if conv.status == ConversationStatus.ACTIVE:
        conv.status = ConversationStatus.CLOSED

    summary = msg.get("summary") or msg.get("analysis", {}).get("summary")
    recording_url = msg.get("recordingUrl")
    duration = msg.get("durationSeconds") or msg.get("duration")
    ended_at = datetime.now(UTC).isoformat()

    # One record per finished call on the conversation, deduped by call id
    # (Vapi retries webhooks). GET /calls in app/api/dashboard.py reads this.
    conv_extra = dict(conv.extra or {})
    records = [r for r in (conv_extra.get("calls") or []) if isinstance(r, dict)]
    is_new_call = not (call_id and any(r.get("call_id") == call_id for r in records))
    if is_new_call:
        records.append(
            {
                "call_id": call_id,
                "ended_at": ended_at,
                "duration_seconds": duration,
                "recording_url": recording_url,
                "summary": summary,
            }
        )
    conv_extra["calls"] = records
    conv.extra = conv_extra

    # Bill the call against the plan's voice minutes. Only on the first
    # delivery of this call id — Vapi retries and we must not double-charge.
    if is_new_call:
        await _add_voice_minutes(db, agency_id, duration)

    # Keep the legacy last-call snapshot on the lead (pre-existing consumers).
    extra = dict(lead.extra or {})
    extra["last_call_summary"] = summary
    extra["last_call_recording_url"] = recording_url
    extra["last_call_duration_sec"] = duration
    extra["last_call_at"] = ended_at
    lead.extra = extra


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _conversation_for_call(
    db: AsyncSession, agency_id: uuid.UUID, call_id: str | None
) -> Conversation | None:
    """The conversation already opened for this exact call, if any.

    Keying on the call id (rather than on who the caller is) is what makes
    every Vapi delivery for one call land on one conversation, which is what
    the per-call billing dedup depends on.
    """
    if not call_id:
        return None
    return (
        await db.execute(
            select(Conversation)
            .where(
                Conversation.agency_id == agency_id,
                Conversation.channel == ConversationChannel.VOICE,
                Conversation.external_thread_id == call_id,
            )
            .order_by(Conversation.created_at)
            .limit(1)
        )
    ).scalars().first()


def seconds_to_billable_minutes(duration_seconds: Any) -> int:
    """Round a call's duration up to whole minutes (Vapi bills the same way).

    Anything unusable (None, non-numeric, <= 0) bills 0 minutes: we would
    rather under-count than invent usage the agency never had.
    """
    try:
        seconds = float(duration_seconds)
    except (TypeError, ValueError):
        return 0
    if seconds <= 0:
        return 0
    return math.ceil(seconds / 60)


async def _add_voice_minutes(db: AsyncSession, agency_id: uuid.UUID, duration: Any) -> None:
    """Accumulate the call into the agency's `voice_minutes_used`.

    This is what makes the /billing usage bar, the plan limit and the 80%
    alert real; the counter is zeroed monthly by `billing.reset_usage_counters`.
    Agencies without a subscription row (dev/seeds) are simply not metered.
    """
    minutes = seconds_to_billable_minutes(duration)
    if minutes <= 0:
        return
    sub = (
        await db.execute(select(Subscription).where(Subscription.agency_id == agency_id))
    ).scalar_one_or_none()
    if sub is None:
        return
    sub.voice_minutes_used = (sub.voice_minutes_used or 0) + minutes


def _verify_vapi(received: str | None) -> None:
    """Compare against `SECRET_KEY` set in Vapi's webhook secret field.

    We re-use the global SECRET_KEY for now; Vapi only allows one secret
    per server URL so a per-agency secret is not feasible without their
    multi-server feature.
    """
    if not settings.is_dev:
        expected = settings.secret_key.get_secret_value()
        if received != expected:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_vapi_secret")


async def _unique_agency_for(
    db: AsyncSession, condition: Any, *, handle: str, value: str
) -> uuid.UUID | None:
    """The one agency matching `condition`, or None if zero — or more than one.

    `.limit(2)`: one row is an answer, two rows are an ambiguity. Two agencies
    pointing at the same Vapi assistant (a botched restore, a copy-pasted
    settings blob) must NOT resolve to whichever row the database returned
    first — that is a coin flip between filing the call under its own agency
    and filing it under a competitor's. It resolves to nobody, loudly.
    """
    ids = list((await db.execute(select(Agency.id).where(condition).limit(2))).scalars().all())
    if len(ids) == 1:
        return ids[0]
    if len(ids) > 1:
        log.error("voice.routing.ambiguous_handle", handle=handle, value=value)
    return None


async def _agency_exists(db: AsyncSession, agency_id: uuid.UUID) -> bool:
    return (
        await db.execute(select(Agency.id).where(Agency.id == agency_id).limit(1))
    ).scalars().first() is not None


async def _resolve_agency(db: AsyncSession, routing: CallRouting) -> uuid.UUID | None:
    """Which agency does this call belong to? Trust our own records first.

    1. `call.assistantId` / `call.phoneNumberId` vs the ids we store per
       agency (`settings["vapi_assistant_id"]`, `settings["vapi_phone_number_id"]`).
       These are the *only* handles whose agency mapping lives in our database,
       written by our own provisioning code.
    2. `metadata.agency_id` — what outbound calls inject per call
       (workers/tasks.py) and what we now write on every assistant we create.
       Used when (1) matched nothing, and only after checking the agency
       actually exists here.
    3. `metadata.slug` — assistants provisioned before (2) existed carry
       `{"slug": ...}`; this rescues them without touching Vapi.
    4. Single-agency fallback, same rule as WhatsApp: harmless while there is
       one tenant, and never reached before the checks above.

    Why the payload's metadata does NOT win over (1): `metadata` lives inside
    Vapi, where a human duplicates an assistant to set up the next agency and
    the copy keeps the original's `agency_id` and `slug` verbatim. That call
    then arrives with agency A's metadata on agency B's assistant, and
    honouring the metadata files B's leads into A's CRM. The assistant id, by
    contrast, is issued by Vapi and mapped to an agency by us. When both are
    present and they disagree, our mapping wins and the disagreement is
    logged as the data bug it is.

    (1) costs one or two extra queries per turn versus reading the metadata
    and returning; on a table with one row per tenant that is the cheapest
    cross-tenant check available.
    """
    # NOTE: `.as_string()` (not `.astext`) — `Agency.settings` is the generic
    # sqlalchemy JSON type, whose indexed expressions have no postgres-only
    # `.astext` (same as app/api/whatsapp.py).
    owner: uuid.UUID | None = None
    if routing.assistant_id:
        owner = await _unique_agency_for(
            db,
            Agency.settings["vapi_assistant_id"].as_string() == routing.assistant_id,
            handle="vapi_assistant_id",
            value=routing.assistant_id,
        )

    if routing.phone_number_id:
        by_phone = await _unique_agency_for(
            db,
            Agency.settings["vapi_phone_number_id"].as_string() == routing.phone_number_id,
            handle="vapi_phone_number_id",
            value=routing.phone_number_id,
        )
        if owner is None:
            owner = by_phone
        elif by_phone is not None and by_phone != owner:
            # Our own two records contradict each other: the number says one
            # agency and the assistant another. There is no safe guess here.
            log.error(
                "voice.routing.conflict",
                reason="assistant_and_phone_number_disagree",
                assistant_id=routing.assistant_id,
                phone_number_id=routing.phone_number_id,
                assistant_agency_id=str(owner),
                phone_number_agency_id=str(by_phone),
            )
            return None

    claimed: uuid.UUID | None = None
    if routing.agency_id:
        try:
            candidate = uuid.UUID(routing.agency_id)
        except (TypeError, ValueError):
            # A corrupt id must not shadow the other handles, and it is worth
            # knowing about.
            log.warning("voice.routing.bad_agency_id", agency_id=routing.agency_id)
        else:
            # Existence check: a well-formed id for an agency that is not in
            # this database (metadata copied from staging, tenant deleted)
            # used to be returned as-is. Every write downstream then failed on
            # the foreign key, the caller heard a technical error and nothing
            # logged the call as unrouted — the failure was invisible.
            if await _agency_exists(db, candidate):
                claimed = candidate
            else:
                log.warning("voice.routing.unknown_agency_id", agency_id=str(candidate))

    if owner is not None:
        if claimed is not None and claimed != owner:
            log.error(
                "voice.routing.conflict",
                reason="metadata_contradicts_our_records",
                assistant_id=routing.assistant_id,
                phone_number_id=routing.phone_number_id,
                metadata_agency_id=str(claimed),
                metadata_slug=routing.slug,
                resolved_agency_id=str(owner),
            )
        return owner

    if claimed is not None:
        return claimed

    if routing.slug:
        # Last of the payload-supplied handles on purpose: the slug in Vapi is
        # a copy of a value our database owns, it travels with duplicated
        # assistants, and for a while onboarding wrote the wrong one
        # ("privacy_policy") into every assistant it created.
        by_slug = await _unique_agency_for(
            db, Agency.slug == routing.slug, handle="slug", value=routing.slug
        )
        if by_slug is not None:
            return by_slug

    count = (await db.execute(select(func.count()).select_from(Agency))).scalar_one()
    if count == 1:
        only = (await db.execute(select(Agency).limit(1))).scalar_one()
        # Attributed by luck, not by any handle: the day a second agency signs
        # up this call stops being routable and starts producing nothing. The
        # log is what turns that into a fixable ticket instead of a mystery.
        log.info(
            "voice.routing.single_agency_fallback",
            agency_id=str(only.id),
            call_id=routing.call_id,
            assistant_id=routing.assistant_id,
            phone_number_id=routing.phone_number_id,
        )
        return only.id
    return None


def _extract_phone(body: dict) -> str | None:
    call = body.get("call") or {}
    customer = call.get("customer") or {}
    return customer.get("number")
