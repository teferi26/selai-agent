"""High-level orchestration of a single inbound message:

  inbound msg → resolve lead/conversation → persist inbound → run agent
             → persist outbound → deliver via channel adapter

Channel adapters call `handle_inbound()` and the service returns the text
the adapter should send back (or None if the agent stayed silent / handed off
without a goodbye).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runner import run_agent
from app.agent.tools import HANDOFF_REASON_KEY
from app.core.logging import get_logger
from app.db.models import (
    Agency,
    ConversationStatus,
    Lead,
    LeadTemperature,
    Message,
    MessageDirection,
    MessageRole,
)
from app.services.competitor_detector import classify_competitor, quick_heuristic_score
from app.services.event_dispatcher import emit
from app.services.foreign_buyer import is_foreign_buyer_signal
from app.services.language import detect_language
from app.services.lead_resolver import InboundIdentity, resolve_lead_and_conversation
from app.services.lead_scoring import recompute_lead_score

log = get_logger(__name__)


@dataclass
class InboundPayload:
    identity: InboundIdentity
    agency_id: uuid.UUID
    text: str
    external_message_id: str | None = None
    media: list[dict] | None = None
    raw: dict[str, Any] | None = None


@dataclass
class HandleResult:
    reply_text: str | None
    conversation_id: uuid.UUID
    handed_off: bool


# ---------------------------------------------------------------------------
# Business hours — "ai_mode == out_of_hours" support (52)
# ---------------------------------------------------------------------------

# Fallback when the agency configured no business_hours: Mon-Fri 9:00-19:00.
DEFAULT_BUSINESS_HOURS: dict = {"start": "09:00", "end": "19:00", "days": [0, 1, 2, 3, 4]}


def _parse_hhmm(value: Any) -> dt_time:
    hh, mm = str(value).split(":")
    return dt_time(hour=int(hh), minute=int(mm))


def is_within_business_hours(now_local: datetime, business_hours: dict | None = None) -> bool:
    """Pure check: is `now_local` (already in the agency's timezone) inside
    the agency's office hours? Malformed configs fall back to the default."""
    raw = business_hours if isinstance(business_hours, dict) else DEFAULT_BUSINESS_HOURS
    try:
        start = _parse_hhmm(raw["start"])
        end = _parse_hhmm(raw["end"])
        days = {int(d) for d in raw["days"]}
    except Exception:
        start = _parse_hhmm(DEFAULT_BUSINESS_HOURS["start"])
        end = _parse_hhmm(DEFAULT_BUSINESS_HOURS["end"])
        days = set(DEFAULT_BUSINESS_HOURS["days"])
    if now_local.weekday() not in days:
        return False
    return start <= now_local.time() < end


def should_alert_hot(
    *,
    before_turn: LeadTemperature,
    after_agent: LeadTemperature,
    after_recompute: LeadTemperature,
) -> bool:
    """(165) Fire the hot-lead alert on the transition to HOT.

    Pure. We look at the temperature right after the agent turn *and* after the
    score refresh: a tool (`update_lead_temperature`, `schedule_visit`,
    `save_seller_lead`) marking the lead hot must alert even if the
    deterministic recompute would have landed on a colder value.
    """
    if before_turn == LeadTemperature.HOT:
        return False
    return LeadTemperature.HOT in (after_agent, after_recompute)


def seller_lead_just_captured(before: Any, after: Any) -> bool:
    """(163) True the first time a capture opportunity shows up on the lead."""
    return bool(after) and not bool(before)


# ---------------------------------------------------------------------------
# (213) Handoff context — who they are, what they want, why the AI escalated.
#
# The agency used to get "Un lead necesita atención humana" and nothing else,
# so the human called back cold, sometimes to an angry client. `reason` is
# persisted by `escalate_to_human` in `conversation.extra`.
# ---------------------------------------------------------------------------

DEFAULT_HANDOFF_REASON = "la IA ha decidido que este caso necesita a una persona."

MAX_HANDOFF_PUSH_CHARS = 300
MAX_HANDOFF_SUBJECT_REASON_CHARS = 70


def describe_lead_search(lead: Lead) -> str:
    """One line with what the lead is looking for. Only what we actually know."""
    bits: list[str] = []
    if lead.operation:
        bits.append("compra" if lead.operation == "sale" else "alquiler")
    if lead.budget_max:
        bits.append(f"hasta {lead.budget_max:,} €".replace(",", "."))
    elif lead.budget_min:
        bits.append(f"desde {lead.budget_min:,} €".replace(",", "."))
    zones = (lead.preferred_neighborhoods or []) or (lead.preferred_cities or [])
    if zones:
        bits.append(", ".join(str(z) for z in zones[:3]))
    if lead.min_bedrooms:
        bits.append(f"{lead.min_bedrooms}+ hab")
    return " · ".join(bits) or "todavía no nos ha dicho qué busca"


def build_handoff_summary(lead: Lead, reason: str | None) -> list[str]:
    """The three lines a human needs before picking up the phone."""
    who = lead.full_name or "Lead sin nombre"
    contact = lead.phone_e164 or lead.email
    if contact:
        who = f"{who} · {contact}"
    clean_reason = " ".join(str(reason or "").split()) or DEFAULT_HANDOFF_REASON
    return [
        f"Quién: {who}",
        f"Qué busca: {describe_lead_search(lead)}",
        f"Por qué escala: {clean_reason}",
    ]


def handoff_subject(lead: Lead, reason: str | None) -> str:
    """Subject line that already says why, so the email is useful unopened."""
    name = lead.full_name or "Un lead"
    clean_reason = " ".join(str(reason or "").split())
    if not clean_reason:
        return f"{name} necesita atención humana"
    if len(clean_reason) > MAX_HANDOFF_SUBJECT_REASON_CHARS:
        clean_reason = clean_reason[: MAX_HANDOFF_SUBJECT_REASON_CHARS - 1].rstrip() + "…"
    return f"{name} necesita atención humana: {clean_reason}"


DEFAULT_TIMEZONE = "Europe/Madrid"


def _agency_now(agency: Agency) -> datetime:
    """Now in the agency's timezone. Never raises: a bad name falls back to
    Madrid and a machine without a timezone database falls back to UTC."""
    for candidate in ((agency.timezone or "").strip(), DEFAULT_TIMEZONE):
        if not candidate:
            continue
        try:
            return datetime.now(ZoneInfo(candidate))
        except Exception:
            log.warning("agency.bad_timezone", tz=candidate)
    return datetime.now(UTC)


async def handle_inbound(db: AsyncSession, payload: InboundPayload) -> HandleResult:
    lead, conv = await resolve_lead_and_conversation(db, payload.agency_id, payload.identity)
    is_new_lead = lead.created_at == lead.updated_at
    previous_temperature = lead.temperature
    previous_seller_lead = (lead.extra or {}).get("seller_lead")

    # Update lead language from the first inbound text we see for this lead.
    # Once we've seen a few messages we trust the existing value.
    if payload.text and (not lead.language or lead.language == "es"):
        detected = detect_language(payload.text, fallback=lead.language or "es")
        if detected != lead.language:
            lead.language = detected

    # Foreign buyer detection — flagged once, persisted forever for this lead.
    extra_now = dict(lead.extra or {})
    if not extra_now.get("foreign_buyer_mode") and is_foreign_buyer_signal(
        payload.text, lead.language
    ):
        extra_now["foreign_buyer_mode"] = True
        lead.extra = extra_now

    inbound = Message(
        agency_id=payload.agency_id,
        conversation_id=conv.id,
        direction=MessageDirection.INBOUND,
        role=MessageRole.LEAD,
        content=payload.text,
        raw=payload.raw or {},
        external_message_id=payload.external_message_id,
        media=payload.media or [],
    )
    db.add(inbound)
    await db.flush()

    # Cheap competitor reconnaissance check; only run the LLM classifier
    # if the heuristic fires.
    try:
        if quick_heuristic_score([payload.text]) > 0:
            from sqlalchemy import select
            history_stmt = (
                select(Message.content)
                .where(
                    Message.agency_id == payload.agency_id,
                    Message.conversation_id == conv.id,
                    Message.role == MessageRole.LEAD,
                )
                .order_by(Message.created_at.desc())
                .limit(12)
            )
            recent = [r for (r,) in (await db.execute(history_stmt)).all()]
            verdict = await classify_competitor(recent)
            if verdict.suspected and verdict.confidence >= 0.6:
                extra = dict(lead.extra or {})
                extra["suspected_competitor"] = {
                    "reason": verdict.reason,
                    "confidence": verdict.confidence,
                }
                lead.extra = extra
                log.warning(
                    "competitor.detected",
                    lead=str(lead.id),
                    confidence=verdict.confidence,
                )
    except Exception:
        log.exception("competitor.check_failed")

    if conv.status == ConversationStatus.HANDED_OFF:
        log.info("inbound.handed_off.skipped", conv=str(conv.id))
        return HandleResult(reply_text=None, conversation_id=conv.id, handed_off=True)

    # (52) ai_mode == "out_of_hours": within office hours the humans answer,
    # so instead of running the agent we hand the conversation off and alert
    # the agency. Because handed-off conversations short-circuit above, the
    # notification fires at most once per conversation.
    agency = await db.get(Agency, payload.agency_id)
    agency_blob = dict(agency.settings or {}) if agency else {}
    if (
        agency is not None
        and agency_blob.get("ai_mode") == "out_of_hours"
        and is_within_business_hours(_agency_now(agency), agency_blob.get("business_hours"))
    ):
        conv.status = ConversationStatus.HANDED_OFF
        await _notify_office_hours_message(db, agency, lead, conv.id)
        log.info("inbound.office_hours.handed_to_human", conv=str(conv.id))
        return HandleResult(reply_text=None, conversation_id=conv.id, handed_off=True)

    reply = await run_agent(db=db, conversation_id=conv.id)
    # Temperature as the agent left it, before the deterministic recompute.
    temperature_after_agent = lead.temperature

    if reply.text:
        outbound = Message(
            agency_id=payload.agency_id,
            conversation_id=conv.id,
            direction=MessageDirection.OUTBOUND,
            role=MessageRole.AGENT_AI,
            content=reply.text,
            raw={"run_id": str(reply.run_id) if reply.run_id else None},
        )
        db.add(outbound)
        await db.flush()

    # The agent just escalated (escalate_to_human tool or guardrail verdict):
    # notify the agency's humans on their phones. Best-effort — never breaks
    # the reply flow.
    if reply.handed_off:
        # (213) The `escalate_to_human` tool stored why it escalated; the
        # guardrail path leaves no reason and falls back to a honest default.
        handoff_reason = (conv.extra or {}).get(HANDOFF_REASON_KEY)
        summary = build_handoff_summary(lead, handoff_reason)
        subject = handoff_subject(lead, handoff_reason)

        try:
            from app.services.push import send_push

            await send_push(
                db,
                payload.agency_id,
                "Un lead necesita atención humana",
                "\n".join(summary)[:MAX_HANDOFF_PUSH_CHARS],
                data={
                    "conversation_id": str(conv.id),
                    "lead_id": str(lead.id),
                    "phone": lead.phone_e164,
                },
            )
        except Exception:
            log.exception("push.handoff_notify_failed", conv=str(conv.id))

        # Email fallback/companion to the push — some agencies live in email.
        # The builder renders the 3-line summary (who / what / why escalated).
        try:
            from app.services.emails import build_handoff_email_html, send_email

            if agency is not None and agency.contact_email:
                await send_email(
                    to=agency.contact_email,
                    subject=subject,
                    html=build_handoff_email_html(lead.full_name, conv.id, summary),
                )
        except Exception:
            log.exception("email.handoff_notify_failed", conv=str(conv.id))

    # (n33) El turno falló por nuestro lado y el lead se ha llevado una
    # disculpa genérica ("¿puedes repetirlo?"). El fallo se veía en Sentry y
    # en ningún sitio más: la agencia no se enteraba de que ese lead se estaba
    # yendo. Se avisa igual que un escalado, pero SIN marcar la conversación
    # como atendida por humano — un despliegue malo convertiría todas las
    # conversaciones vivas en "necesita a alguien" de golpe.
    elif reply.failed:
        try:
            from app.services.push import send_push

            await send_push(
                db,
                payload.agency_id,
                "Un mensaje no ha podido responderse",
                (
                    f"{lead.full_name or 'Un cliente'} escribió y hemos tenido un "
                    "problema técnico. Échale un ojo a la conversación."
                ),
                data={"conversation_id": str(conv.id), "lead_id": str(lead.id)},
            )
        except Exception:
            log.exception("push.agent_failure_notify_failed", conv=str(conv.id))

    # Refresh lead score after every turn so the dashboard stays current.
    try:
        await recompute_lead_score(db, lead.id)
    except Exception:
        log.exception("score.recompute_failed", lead=str(lead.id))

    # (163) The agent just detected an owner who wants to sell/rent out their
    # property — the agency wants that on their phone immediately. It runs
    # before the hot-lead alert because `save_seller_lead` also pins the lead
    # HOT: both alerts would otherwise fire for the same single message.
    capture_alerted = False
    try:
        seller_lead = (lead.extra or {}).get("seller_lead")
        if seller_lead_just_captured(previous_seller_lead, seller_lead):
            await _notify_seller_lead(db, payload.agency_id, lead, conv.id, seller_lead)
            capture_alerted = True
    except Exception:
        log.exception("seller_lead.notify_failed", lead=str(lead.id))

    # (72)(165) Hot-lead alert on the *transition* to hot (not on every
    # message). Best-effort — never breaks the reply flow. Skipped when the
    # capture alert already went out: it says the same thing with more detail.
    try:
        if not capture_alerted and should_alert_hot(
            before_turn=previous_temperature,
            after_agent=temperature_after_agent,
            after_recompute=lead.temperature,
        ):
            await _notify_hot_lead(db, payload.agency_id, lead, conv.id)
    except Exception:
        log.exception("hot_lead.notify_failed", lead=str(lead.id))

    # Stage outgoing webhooks *after* the score refresh so subscribers see
    # the latest snapshot. This is a PostgreSQL outbox insert in the same
    # transaction as the lead/message: do not swallow a failure and commit a
    # lead whose `lead.created` event was never made durable.
    if is_new_lead:
        await emit(
            db,
            "lead.created",
            payload.agency_id,
            {
                "lead_id": str(lead.id),
                "channel": conv.channel.value,
                "name": lead.full_name,
                "phone": lead.phone_e164,
                "email": lead.email,
                "language": lead.language,
            },
        )

    return HandleResult(
        reply_text=reply.text or None,
        conversation_id=conv.id,
        handed_off=reply.handed_off,
    )


# ---------------------------------------------------------------------------
# Notifications (best-effort: log and swallow, never break the flow)
# ---------------------------------------------------------------------------

async def _notify_office_hours_message(
    db: AsyncSession, agency: Agency, lead: Lead, conversation_id: uuid.UUID
) -> None:
    title = "Nuevo mensaje en horario de oficina"
    try:
        from app.services.push import send_push

        await send_push(
            db,
            agency.id,
            title,
            f"{lead.full_name or 'Un lead'} ha escrito. En horario de oficina responde tu equipo.",
            data={"conversation_id": str(conversation_id)},
        )
    except Exception:
        log.exception("push.office_hours_notify_failed", conv=str(conversation_id))

    try:
        from app.services.emails import build_office_hours_email_html, send_email

        if agency.contact_email:
            await send_email(
                to=agency.contact_email,
                subject=title,
                html=build_office_hours_email_html(lead.full_name, conversation_id),
            )
    except Exception:
        log.exception("email.office_hours_notify_failed", conv=str(conversation_id))


_URGENCY_ES = {"high": "quiere venderlo ya", "normal": "sin prisa concreta", "low": "solo tantea"}


def _seller_lead_details(seller_lead: dict) -> list[str]:
    """Human-readable bullets for the capture email. Only what we actually know."""
    details = ["Oportunidad de captación: quiere poner su propiedad en el mercado."]
    if zone := seller_lead.get("zone"):
        details.append(f"Zona: {zone}")
    if property_type := seller_lead.get("property_type"):
        details.append(f"Tipo de inmueble: {property_type}")
    if (price := seller_lead.get("expected_price")) is not None:
        details.append(f"Precio que espera: €{price}")
    if urgency := seller_lead.get("urgency"):
        details.append(f"Urgencia: {_URGENCY_ES.get(urgency, urgency)}")
    if notes := seller_lead.get("notes"):
        details.append(f"Notas: {notes}")
    return details


async def _notify_seller_lead(
    db: AsyncSession,
    agency_id: uuid.UUID,
    lead: Lead,
    conversation_id: uuid.UUID,
    seller_lead: dict | None,
) -> None:
    """(163) Same push+email pattern as the hot-lead alert, capture flavoured."""
    name = lead.full_name or "Sin nombre"
    title = f"Oportunidad de captación: {name}"
    seller_lead = seller_lead or {}
    zone = seller_lead.get("zone") or "zona sin concretar"
    property_type = seller_lead.get("property_type") or "inmueble"

    try:
        from app.services.push import send_push

        await send_push(
            db,
            agency_id,
            title,
            f"Quiere vender o alquilar su {property_type} en {zone}. Llámale antes que otro.",
            data={
                "conversation_id": str(conversation_id),
                "lead_id": str(lead.id),
                "phone": lead.phone_e164,
            },
        )
    except Exception:
        log.exception("push.seller_lead_notify_failed", lead=str(lead.id))

    try:
        from app.services.emails import build_hot_lead_email_html, send_email

        agency = await db.get(Agency, agency_id)
        if agency is not None and agency.contact_email:
            details = _seller_lead_details(seller_lead)
            if lead.phone_e164:
                details.append(f"Teléfono: {lead.phone_e164}")
            if lead.email:
                details.append(f"Email: {lead.email}")
            await send_email(
                to=agency.contact_email,
                subject=title,
                html=build_hot_lead_email_html(lead.full_name, conversation_id, details),
            )
    except Exception:
        log.exception("email.seller_lead_notify_failed", lead=str(lead.id))


async def _notify_hot_lead(
    db: AsyncSession, agency_id: uuid.UUID, lead: Lead, conversation_id: uuid.UUID
) -> None:
    name = lead.full_name or "Sin nombre"
    try:
        from app.services.push import send_push

        await send_push(
            db,
            agency_id,
            f"🔥 Lead caliente: {name}",
            "Ha pasado a temperatura HOT. Es el momento de llamarle.",
            data={
                "conversation_id": str(conversation_id),
                "lead_id": str(lead.id),
                "phone": lead.phone_e164,
            },
        )
    except Exception:
        log.exception("push.hot_lead_notify_failed", lead=str(lead.id))

    try:
        from app.services.emails import build_hot_lead_email_html, send_email

        agency = await db.get(Agency, agency_id)
        if agency is not None and agency.contact_email:
            details: list[str] = []
            if lead.phone_e164:
                details.append(f"Teléfono: {lead.phone_e164}")
            if lead.email:
                details.append(f"Email: {lead.email}")
            if lead.budget_min or lead.budget_max:
                details.append(
                    f"Presupuesto: €{lead.budget_min or '?'} - €{lead.budget_max or '?'}"
                )
            if lead.operation:
                details.append(f"Operación: {lead.operation}")
            details.append(f"Score: {lead.score}")
            await send_email(
                to=agency.contact_email,
                subject=f"🔥 Lead caliente: {name}",
                html=build_hot_lead_email_html(lead.full_name, conversation_id, details),
            )
    except Exception:
        log.exception("email.hot_lead_notify_failed", lead=str(lead.id))
