"""(164) 24h / 72h follow-up when a lead goes quiet on an open question.

`celery_app.py` has promised 24h follow-ups since day one and they never
existed: a silent lead got nothing until the proactive voice call on day 3-14,
by which time they had already visited a flat with someone else.

The sweep only writes to a lead who is genuinely mid-conversation:

- the last message in the thread is the AI's and it contains a question mark
  the lead never answered (see `has_pending_question`: it is a "contains",
  not an "ends with"),
- the conversation is still ACTIVE (never handed off or closed),
- the lead is still in the qualification funnel — a lead with a visit already
  booked, or won, or lost, is not someone to ask "cuéntame qué buscas",
- the channel is WhatsApp or email (a voice call cannot be "followed up" with
  text on the same thread, and SMS/web are not connected for outbound),
- that rung of the ladder has not been used yet on this conversation.

**The rung is claimed and committed before the message is sent**, the same
rule item 223 applied to the proactive call in `workers/tasks.py`. Celery runs
with `task_acks_late=True` and a 240 s soft limit: a worker killed by a deploy,
an OOM or that limit halfway through a sweep would roll back every mark while
the WhatsApps and emails were already delivered, and the redelivered task
would send the whole batch again.

**Meta's 24-hour window.** A free-form WhatsApp message is only deliverable
within 24h of the lead's last inbound. Firing the "24h" rung exactly at 24h of
silence would land outside that window every single time — the sweep runs
every 30 minutes, so the message would go out at ~24h05 and be rejected. On
WhatsApp the first rung therefore fires at `WHATSAPP_FIRST_STEP_HOURS` (23h),
which is the last half-hour where the message can actually be delivered.
Email has no such window and fires at 24h.

When a send fails anyway (expired window, revoked token, wrong number) we
still mark the rung as used: retrying every 30 minutes forever would burn
tokens and, the day the window reopened, deliver a follow-up about a
conversation from three weeks ago.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import (
    Agency,
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Lead,
    LeadStatus,
    Message,
    MessageDirection,
    MessageRole,
)
from app.services.secret_store import secret_from_settings

log = get_logger(__name__)

STEP_24H = "24h"
STEP_72H = "72h"

STEP_FLAG: dict[str, str] = {
    STEP_24H: "followup_24h_sent",
    STEP_72H: "followup_72h_sent",
}
STEP_HOURS: dict[str, float] = {STEP_24H: 24.0, STEP_72H: 72.0}

# See the module docstring: 23h is the last moment a free-form WhatsApp
# message is still inside Meta's customer-service window.
WHATSAPP_FIRST_STEP_HOURS = 23.0

FOLLOWUP_CHANNELS = (ConversationChannel.WHATSAPP, ConversationChannel.EMAIL)

# Past this much silence the thread is dead; writing "¿seguimos?" to someone
# who stopped answering three weeks ago is spam, not a follow-up.
MAX_SILENCE_HOURS = 14 * 24

# Bound the sweep: it runs every 30 minutes and shares the worker with
# everything else.
MAX_CONVERSATIONS_PER_SWEEP = 200

# Statuses where the follow-up copy would be false or unwelcome. Someone with
# a visit booked for tomorrow must not be asked "cuéntame qué buscas y te
# preparo opciones", and someone who bought or said no must not be chased at
# all. Same spirit as the proactive call, which excludes WON/LOST/DORMANT.
SKIP_LEAD_STATUSES = frozenset(
    {
        LeadStatus.VISIT_SCHEDULED,
        LeadStatus.VISITED,
        LeadStatus.NEGOTIATING,
        LeadStatus.WON,
        LeadStatus.LOST,
    }
)

# Languages we actually have copy for. `lead.language` is detected from the
# lead's own words (services/language.py can return es/en/fr/ca/pt/de/it) and
# item 215 makes the live conversation follow it, so a lead who has been
# talking in English cannot get a Spanish follow-up out of nowhere. The other
# codes fall back to Spanish — the agency's own language — until someone
# writes that copy; adding a locale is adding one entry to each dict below.
COPY_LANGUAGES = ("es", "en")
DEFAULT_COPY_LANGUAGE = "es"


def resolve_copy_language(language: str | None) -> str:
    """Which copy set to use for `lead.language`. Pure."""
    code = (language or "").strip().lower().replace("_", "-").split("-")[0]
    return code if code in COPY_LANGUAGES else DEFAULT_COPY_LANGUAGE


@dataclass
class FollowupMessage:
    """What we are about to send. `subject` is None on WhatsApp."""

    subject: str | None
    text: str


# ---------------------------------------------------------------------------
# Pure decision helpers
# ---------------------------------------------------------------------------

def has_pending_question(text: str | None) -> bool:
    """Does the agent's last message contain something to answer?

    Deliberately a "contains", not an "ends with": a WhatsApp message often
    closes with an emoji or a "gracias", and being strict here would silence
    the follow-up for exactly the leads it exists to recover. The cost is the
    occasional nudge after a rhetorical question, which the status guard in
    `_process_conversation` keeps away from anyone with a visit booked.
    """
    return "?" in (text or "") or "¿" in (text or "")


def step_threshold_hours(step: str, channel: ConversationChannel | str) -> float:
    """Hours of silence needed for `step` on `channel`."""
    channel_value = channel.value if isinstance(channel, ConversationChannel) else str(channel)
    if step == STEP_24H and channel_value == ConversationChannel.WHATSAPP.value:
        return WHATSAPP_FIRST_STEP_HOURS
    return STEP_HOURS[step]


def pick_step(
    *,
    channel: ConversationChannel | str,
    silence_hours: float,
    extra: dict | None,
) -> str | None:
    """Which rung of the ladder applies right now, if any.

    Pure. A conversation that crossed 72h while the sweep was down gets the
    72h message only: sending both at once would read as desperation.
    """
    if silence_hours > MAX_SILENCE_HOURS:
        return None
    sent = extra or {}
    for step in (STEP_72H, STEP_24H):
        if sent.get(STEP_FLAG[step]):
            continue
        if silence_hours >= step_threshold_hours(step, channel):
            return step
    return None


def _first_name(full_name: str | None) -> str:
    name = (full_name or "").strip()
    return name.split(" ", 1)[0] if name else ""


def _allowed(text: str, forbidden_phrases: list[str] | None) -> bool:
    """(53)(77) The agency's blacklist applies to our own copy too."""
    lowered = text.lower()
    for phrase in forbidden_phrases or []:
        clean = str(phrase).strip().lower()
        if clean and clean in lowered:
            return False
    return True


# Two variants per (step, tone) so one blacklisted phrase does not silence the
# whole follow-up. WhatsApp: max 3 lines, at most 1 emoji (214).
_WHATSAPP_ES: dict[tuple[str, str], list[str]] = {
    (STEP_24H, "tu"): [
        "Hola{name}, ¿pudiste ver mi mensaje? Si me cuentas un poco más de lo que buscas, te "
        "preparo un par de opciones. Y si prefieres que lo deje, dímelo sin problema.",
        "Hola{name}, sigo por aquí por si te viene bien retomarlo. ¿Te preparo un par de "
        "opciones o lo dejamos para más adelante?",
    ],
    (STEP_24H, "usted"): [
        "Hola{name}, ¿pudo ver mi mensaje? Si me cuenta un poco más de lo que busca, le preparo "
        "un par de opciones. Y si prefiere que lo deje, dígamelo sin problema.",
        "Hola{name}, sigo por aquí por si le viene bien retomarlo. ¿Le preparo un par de "
        "opciones o lo dejamos para más adelante?",
    ],
    (STEP_72H, "tu"): [
        "Hola{name}, no quiero insistir más de la cuenta. Si sigues buscando, dime zona y "
        "presupuesto y te mando opciones; si ahora no es el momento, lo dejo aquí y quedo a tu "
        "disposición.",
        "Hola{name}, cierro esta conversación por no molestarte más. Si retomas la búsqueda, "
        "escríbeme cuando quieras y seguimos donde lo dejamos.",
    ],
    (STEP_72H, "usted"): [
        "Hola{name}, no quiero insistir más de la cuenta. Si sigue buscando, dígame zona y "
        "presupuesto y le mando opciones; si ahora no es el momento, lo dejo aquí y quedo a su "
        "disposición.",
        "Hola{name}, cierro esta conversación por no molestarle más. Si retoma la búsqueda, "
        "escríbame cuando quiera y seguimos donde lo dejamos.",
    ],
}

# English has no tú/usted split, so both tones share the same copy.
_WHATSAPP_EN_BY_STEP: dict[str, list[str]] = {
    STEP_24H: [
        "Hi{name}, just checking you saw my message. Tell me a bit more about what you are "
        "looking for and I will put a couple of options together. And if you would rather I "
        "left it, just say so.",
        "Hi{name}, I am still here whenever you want to pick this up. Shall I send you a "
        "couple of options, or leave it for another time?",
    ],
    STEP_72H: [
        "Hi{name}, I do not want to keep chasing you. If you are still looking, tell me the "
        "area and your budget and I will send options; if now is not the moment, I will leave "
        "it here and stay available.",
        "Hi{name}, I am closing this one so I do not bother you again. If you pick the search "
        "back up, message me any time and we carry on where we left off.",
    ],
}

_WHATSAPP_EN: dict[tuple[str, str], list[str]] = {
    (step, tone): variants
    for step, variants in _WHATSAPP_EN_BY_STEP.items()
    for tone in ("tu", "usted")
}

_WHATSAPP_TEXTS: dict[str, dict[tuple[str, str], list[str]]] = {
    "es": _WHATSAPP_ES,
    "en": _WHATSAPP_EN,
}

# Email: full paragraphs, greeting and sign-off (214).
_EMAIL_SUBJECTS: dict[str, dict[tuple[str, str], str]] = {
    "es": {
        (STEP_24H, "tu"): "¿Seguimos con tu búsqueda?",
        (STEP_24H, "usted"): "¿Seguimos con su búsqueda?",
        (STEP_72H, "tu"): "Lo dejo aquí, pero sigo disponible",
        (STEP_72H, "usted"): "Lo dejo aquí, pero sigo disponible",
    },
    "en": {
        (STEP_24H, "tu"): "Shall we carry on with your search?",
        (STEP_24H, "usted"): "Shall we carry on with your search?",
        (STEP_72H, "tu"): "I will leave it here, but I am still available",
        (STEP_72H, "usted"): "I will leave it here, but I am still available",
    },
}

_EMAIL_ES: dict[tuple[str, str], list[str]] = {
    (STEP_24H, "tu"): [
        "Hola{name}:\n\nTe escribí ayer y no he vuelto a saber de ti, así que te dejo este "
        "recordatorio por si se te traspapeló.\n\nSi me cuentas un poco más de lo que buscas "
        "(zona, presupuesto y para cuándo lo necesitas), te preparo una selección con lo que "
        "mejor encaje. Y si prefieres que lo dejemos, dímelo y no te escribo más.\n\n"
        "Un saludo,\nEl asistente virtual de {agency}",
        "Hola{name}:\n\nRetomo nuestra conversación por si te viene bien seguir.\n\n"
        "Dime zona y presupuesto aproximado y te mando un par de opciones concretas. Si ahora "
        "no es el momento, no pasa nada: avísame y lo dejamos.\n\nUn saludo,\n"
        "El asistente virtual de {agency}",
    ],
    (STEP_24H, "usted"): [
        "Hola{name}:\n\nLe escribí ayer y no he vuelto a saber de usted, así que le dejo este "
        "recordatorio por si se le traspapeló.\n\nSi me cuenta un poco más de lo que busca "
        "(zona, presupuesto y para cuándo lo necesita), le preparo una selección con lo que "
        "mejor encaje. Y si prefiere que lo dejemos, dígamelo y no le escribo más.\n\n"
        "Un saludo,\nEl asistente virtual de {agency}",
        "Hola{name}:\n\nRetomo nuestra conversación por si le viene bien seguir.\n\n"
        "Dígame zona y presupuesto aproximado y le mando un par de opciones concretas. Si ahora "
        "no es el momento, no pasa nada: avíseme y lo dejamos.\n\nUn saludo,\n"
        "El asistente virtual de {agency}",
    ],
    (STEP_72H, "tu"): [
        "Hola{name}:\n\nComo no he vuelto a saber de ti, lo dejo aquí para no llenarte el "
        "buzón.\n\nSi retomas la búsqueda, respóndeme a este correo cuando quieras y seguimos "
        "justo donde lo dejamos: tengo anotado lo que me contaste.\n\nUn saludo,\n"
        "El asistente virtual de {agency}",
        "Hola{name}:\n\nEste es mi último mensaje para no insistir más de la cuenta.\n\n"
        "Si sigues buscando, dime zona y presupuesto y te preparo opciones el mismo día. Si no, "
        "quedo a tu disposición para cuando lo necesites.\n\nUn saludo,\n"
        "El asistente virtual de {agency}",
    ],
    (STEP_72H, "usted"): [
        "Hola{name}:\n\nComo no he vuelto a saber de usted, lo dejo aquí para no llenarle el "
        "buzón.\n\nSi retoma la búsqueda, respóndame a este correo cuando quiera y seguimos "
        "justo donde lo dejamos: tengo anotado lo que me contó.\n\nUn saludo,\n"
        "El asistente virtual de {agency}",
        "Hola{name}:\n\nEste es mi último mensaje para no insistir más de la cuenta.\n\n"
        "Si sigue buscando, dígame zona y presupuesto y le preparo opciones el mismo día. Si no, "
        "quedo a su disposición para cuando lo necesite.\n\nUn saludo,\n"
        "El asistente virtual de {agency}",
    ],
}

_EMAIL_EN_BY_STEP: dict[str, list[str]] = {
    STEP_24H: [
        "Hi{name}:\n\nI wrote to you yesterday and have not heard back, so here is a quick "
        "reminder in case it got buried.\n\nIf you tell me a bit more about what you are "
        "looking for (area, budget and when you need it), I will put together a shortlist that "
        "fits. And if you would rather we left it, say so and I will not write again.\n\n"
        "Best regards,\nThe virtual assistant at {agency}",
        "Hi{name}:\n\nPicking our conversation back up in case now is a better moment.\n\n"
        "Tell me the area and roughly your budget and I will send you a couple of concrete "
        "options. If now is not the time, no problem at all: let me know and we leave it.\n\n"
        "Best regards,\nThe virtual assistant at {agency}",
    ],
    STEP_72H: [
        "Hi{name}:\n\nAs I have not heard back, I will leave it here so I do not clutter your "
        "inbox.\n\nIf you pick the search back up, reply to this email whenever you like and we "
        "carry on exactly where we left off: I have everything you told me.\n\n"
        "Best regards,\nThe virtual assistant at {agency}",
        "Hi{name}:\n\nThis is my last message, so I do not keep chasing you.\n\nIf you are "
        "still looking, tell me the area and your budget and I will prepare options the same "
        "day. Otherwise I am here whenever you need me.\n\n"
        "Best regards,\nThe virtual assistant at {agency}",
    ],
}

_EMAIL_EN: dict[tuple[str, str], list[str]] = {
    (step, tone): variants
    for step, variants in _EMAIL_EN_BY_STEP.items()
    for tone in ("tu", "usted")
}

_EMAIL_TEXTS: dict[str, dict[tuple[str, str], list[str]]] = {
    "es": _EMAIL_ES,
    "en": _EMAIL_EN,
}


def build_followup(
    *,
    step: str,
    channel: ConversationChannel | str,
    agency_name: str,
    lead_name: str | None = None,
    ai_tone: str | None = None,
    forbidden_phrases: list[str] | None = None,
    language: str | None = None,
) -> FollowupMessage | None:
    """The message to send, or None when the agency's blacklist rules them out.

    Pure. `ai_tone == "formal"` switches the copy to usted, matching
    `prompts.render_style_directives`; `language` picks the copy set the same
    way the live conversation does (215). English has no tú/usted split, so
    `ai_tone` does not change it.
    """
    channel_value = channel.value if isinstance(channel, ConversationChannel) else str(channel)
    tone = "usted" if ai_tone == "formal" else "tu"
    lang = resolve_copy_language(language)
    first = _first_name(lead_name)
    name = f" {first}" if first else ""

    if channel_value == ConversationChannel.WHATSAPP.value:
        variants = _WHATSAPP_TEXTS[lang].get((step, tone), [])
        subject = None
    elif channel_value == ConversationChannel.EMAIL.value:
        variants = _EMAIL_TEXTS[lang].get((step, tone), [])
        subject = _EMAIL_SUBJECTS[lang].get((step, tone))
    else:
        return None

    for template in variants:
        text = template.format(name=name, agency=agency_name)
        if _allowed(text, forbidden_phrases) and _allowed(subject or "", forbidden_phrases):
            return FollowupMessage(subject=subject, text=text)

    log.warning("followup.blocked_by_forbidden_phrases", step=step, channel=channel_value)
    return None


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

@dataclass
class ConversationState:
    last_role: MessageRole | None
    last_text: str
    last_inbound_at: datetime | None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _candidate_conversations(
    db: AsyncSession, now: datetime
) -> list[Conversation]:
    """Active WhatsApp/email threads whose last message is old enough.

    One query, tenant-safe by construction: the aggregate is joined on both
    `conversation_id` and `agency_id`, so a stray message row from another
    agency can never pull a conversation into another tenant's sweep.

    Threads that already used the last rung are excluded in SQL. Without that
    the batch limit quietly kills the feature for the agencies that need it:
    candidates are ordered oldest-first and a finished thread stays inside the
    14-day window for a fortnight, so an agency with more than
    `MAX_CONVERSATIONS_PER_SWEEP` quiet threads would spend every sweep
    re-reading conversations it had already followed up and never reach the
    lead who went quiet last night.
    """
    oldest_threshold = now - timedelta(hours=min(WHATSAPP_FIRST_STEP_HOURS, STEP_HOURS[STEP_24H]))
    newest_threshold = now - timedelta(hours=MAX_SILENCE_HOURS)
    # The `created_at` predicate lives inside the aggregate on purpose: without
    # it this groups the *entire* messages table every 30 minutes. It changes
    # nothing — a thread whose last message is older than the window has no
    # rows left and drops out, which is exactly what the outer `last_at >=`
    # would have done — but it cuts the aggregate down to a fortnight of
    # messages. There is no index on `messages.created_at` yet; adding one
    # (with `conversation_id`) is the next step if this ever shows up slow.
    last_msg = (
        select(
            Message.conversation_id.label("cid"),
            Message.agency_id.label("aid"),
            func.max(Message.created_at).label("last_at"),
        )
        .where(Message.created_at >= newest_threshold)
        .group_by(Message.conversation_id, Message.agency_id)
        .subquery()
    )
    stmt = (
        select(Conversation)
        .join(
            last_msg,
            (last_msg.c.cid == Conversation.id) & (last_msg.c.aid == Conversation.agency_id),
        )
        .where(
            Conversation.status == ConversationStatus.ACTIVE,
            Conversation.channel.in_(FOLLOWUP_CHANNELS),
            Conversation.extra[STEP_FLAG[STEP_72H]].is_(None),
            last_msg.c.last_at <= oldest_threshold,
            last_msg.c.last_at >= newest_threshold,
        )
        .order_by(last_msg.c.last_at.asc())
        .limit(MAX_CONVERSATIONS_PER_SWEEP)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _conversation_state(db: AsyncSession, conv: Conversation) -> ConversationState:
    """Last message of the thread + when the lead last wrote."""
    last_stmt = (
        select(Message)
        .where(
            Message.agency_id == conv.agency_id,
            Message.conversation_id == conv.id,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    last = (await db.execute(last_stmt)).scalars().first()

    inbound_stmt = select(func.max(Message.created_at)).where(
        Message.agency_id == conv.agency_id,
        Message.conversation_id == conv.id,
        Message.direction == MessageDirection.INBOUND,
    )
    last_inbound = (await db.execute(inbound_stmt)).scalar_one_or_none()

    return ConversationState(
        last_role=getattr(last, "role", None),
        last_text=getattr(last, "content", "") or "",
        last_inbound_at=_aware(last_inbound),
    )


def _mark_step(conv: Conversation, step: str, when: datetime) -> None:
    """Burn this rung. Whole-dict reassignment so SQLAlchemy sees the change."""
    conv.extra = {**(conv.extra or {}), STEP_FLAG[step]: when.isoformat()}


async def _claim(db: AsyncSession, conv: Conversation, step: str, when: datetime) -> None:
    """Burn the rung *and make it durable* before we send anything.

    See the module docstring: sending is not idempotent, and with
    `task_acks_late` a sweep that dies halfway would otherwise replay every
    message it had already delivered.
    """
    _mark_step(conv, step, when)
    await db.commit()


def _whatsapp_client_for(agency: Agency | None):
    """The agency's own Cloud API credentials, or the global env client.

    Same rule as `api/dashboard.py::manual_reply`: an agency that connected
    its own number must send from that number.
    """
    from app.channels.whatsapp.client import WhatsAppClient

    cfg = dict((agency.settings if agency is not None else None) or {})
    token = secret_from_settings(cfg, "whatsapp_access_token")
    phone_number_id = cfg.get("whatsapp_phone_number_id")
    if token and phone_number_id:
        return WhatsAppClient(access_token=str(token), phone_number_id=str(phone_number_id))
    return WhatsAppClient()


async def _send(
    conv: Conversation, lead: Lead, agency: Agency | None, message: FollowupMessage
) -> str | None:
    """Deliver on the conversation's channel. Returns the provider message id."""
    if conv.channel is ConversationChannel.WHATSAPP:
        destination = lead.whatsapp_wa_id or lead.phone_e164
        if not destination:
            return None
        return await _whatsapp_client_for(agency).send_text(destination, message.text)

    from app.channels.email.client import ResendClient

    if not lead.email:
        return None
    return await ResendClient().send(
        to=lead.email,
        subject=(message.subject or "Seguimos con tu búsqueda")[:255],
        text=message.text,
    )


async def _process_conversation(
    db: AsyncSession, conv: Conversation, now: datetime
) -> str | None:
    """Follow up on one conversation. Returns the step actually delivered."""
    state = await _conversation_state(db, conv)
    if state.last_role != MessageRole.AGENT_AI or not has_pending_question(state.last_text):
        return None

    # Silence is measured from the lead's last inbound, never from our own
    # messages: otherwise the 24h follow-up would reset the clock and the 72h
    # rung would fire three days after the follow-up instead of after the lead.
    silence_since = state.last_inbound_at or _aware(conv.created_at) or now
    silence_hours = (now - silence_since).total_seconds() / 3600.0

    step = pick_step(
        channel=conv.channel, silence_hours=silence_hours, extra=conv.extra
    )
    if step is None:
        return None

    lead = await db.get(Lead, conv.lead_id)
    if lead is None or lead.agency_id != conv.agency_id:
        return None

    # A lead with a visit booked for tomorrow, or one that already bought or
    # said no, is not "quiet on an open question" — chasing them with the
    # re-qualification copy is worse than saying nothing. Not marked: if the
    # visit is cancelled the lead becomes eligible again.
    if getattr(lead, "status", None) in SKIP_LEAD_STATUSES:
        return None

    agency = await db.get(Agency, conv.agency_id)
    settings_blob = dict((agency.settings if agency is not None else None) or {})
    message = build_followup(
        step=step,
        channel=conv.channel,
        agency_name=(agency.name if agency is not None else "la inmobiliaria"),
        lead_name=lead.full_name,
        ai_tone=settings_blob.get("ai_tone"),
        forbidden_phrases=settings_blob.get("forbidden_phrases"),
        language=getattr(lead, "language", None),
    )
    if message is None:
        # Blacklisted copy: mark the rung anyway, we have nothing else to say.
        await _claim(db, conv, step, now)
        return None

    # Claim first, send second (item 223's rule for the proactive call).
    await _claim(db, conv, step, now)

    try:
        external_id = await _send(conv, lead, agency, message)
    except Exception:
        # Expired WhatsApp window, revoked token, bad address... The claim
        # stays: retrying every 30 minutes would burn tokens and, the day the
        # window reopened, deliver a follow-up about a three-week-old thread.
        log.warning(
            "followup.send_failed",
            conv=str(conv.id),
            channel=conv.channel.value,
            step=step,
            exc_info=True,
        )
        return None

    if external_id is None:
        log.info("followup.no_destination", conv=str(conv.id), channel=conv.channel.value)
        return None

    db.add(
        Message(
            agency_id=conv.agency_id,
            conversation_id=conv.id,
            direction=MessageDirection.OUTBOUND,
            role=MessageRole.AGENT_AI,
            content=message.text,
            raw={"followup_step": step},
            external_message_id=external_id or None,
        )
    )
    # The message is already delivered; make the panel's copy of it durable
    # too, instead of leaving it to a commit 199 conversations later.
    await db.commit()
    log.info("followup.sent", conv=str(conv.id), channel=conv.channel.value, step=step)
    return step


async def _rollback(db: AsyncSession) -> None:
    """Leave the session usable for the next conversation of the sweep."""
    try:
        await db.rollback()
    except Exception:  # pragma: no cover — a test double without rollback
        log.debug("followup.rollback_unavailable")


async def followup_sweep(db: AsyncSession) -> dict:
    """Send every due 24h/72h follow-up. Returns how many of each went out.

    Contract with the worker track: `{"sent_24h": int, "sent_72h": int}`. The
    caller owns the session; the sweep commits each conversation on its own
    (the rung is claimed before the send, see the module docstring), so a
    caller wrapping this in `session_scope` gets a no-op commit at the end.
    """
    now = datetime.now(UTC)
    sent = {"sent_24h": 0, "sent_72h": 0}

    conversations = await _candidate_conversations(db, now)
    for conv in conversations:
        try:
            step = await _process_conversation(db, conv, now)
        except Exception:
            log.exception("followup.conversation_failed", conv=str(getattr(conv, "id", "?")))
            await _rollback(db)
            continue
        if step is not None:
            sent[f"sent_{step}"] += 1

    log.info("followup.sweep.done", candidates=len(conversations), **sent)
    return sent


# Re-export for callers that only want the id type hint.
__all__ = [
    "COPY_LANGUAGES",
    "FollowupMessage",
    "SKIP_LEAD_STATUSES",
    "STEP_24H",
    "STEP_72H",
    "STEP_FLAG",
    "build_followup",
    "followup_sweep",
    "has_pending_question",
    "pick_step",
    "resolve_copy_language",
]
