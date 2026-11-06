"""Background tasks.

Run by Celery worker; scheduled by Celery Beat. Each task is a thin sync
wrapper around an async coroutine, because Celery 5 still doesn't natively
do async tasks reliably.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.core.settings import settings
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
    Property,
    Visit,
    VisitStatus,
)
from app.db.session import session_scope
from app.workers.celery_app import celery_app

log = get_logger(__name__)


async def send_whatsapp_as_agency(db, agency_id, to: str, text: str) -> bool:
    """Send WhatsApp with the AGENCY's own credentials (hallazgo n21).

    Every worker task used the bare `WhatsAppClient()` — the platform-global
    credentials. Two silent failure modes: an agency that connected its own
    Meta app got its reminders sent from the WRONG number, and a worker
    deployed without the global credentials "sent" a dev-stub that never left
    the building while the task marked the reminder as delivered. The client
    never received anything and no one — agency, owner, log level above
    warning — ever knew.

    Returns False when nothing actually went out, so callers can fall back to
    email and release their claim flags. The dev-stub only counts as sent
    outside production: locally and in tests it is the expected transport.
    """
    from app.services.followups import _whatsapp_client_for

    agency = await db.get(Agency, agency_id) if agency_id else None
    client = _whatsapp_client_for(agency)
    external_id = await client.send_text(to, text)
    if external_id == "dev-stub" and settings.app_env == "production":
        log.error(
            "worker.whatsapp_not_configured",
            agency=str(agency_id),
            hint="ni la agencia ni la plataforma tienen credenciales de Meta en el worker",
        )
        return False
    return bool(external_id)


def _run(coro):
    """Run a task coroutine in its own event loop, then drop the DB pool.

    `asyncio.run` builds a fresh loop per task, and asyncpg connections are
    bound to the loop that opened them. Left in the shared pool from
    `app.db.session`, the next task picks one up and gets
    "attached to a different loop" (item 223).

    Disposing inside the same loop closes those connections cleanly; the
    engine rebuilds its pool lazily on the next task. Cost is one connection
    handshake per task, which is nothing next to an LLM call — and this is
    the reason the worker does not need NullPool.
    """

    async def _with_pool_cleanup():
        try:
            return await coro
        finally:
            from app.db.session import engine

            try:
                await engine.dispose()
            except Exception:  # noqa: BLE001 — cleanup must not mask the task
                log.warning("worker.engine_dispose_failed")

    return asyncio.run(_with_pool_cleanup())


def _subject_and_html(built, *, default_subject: str) -> tuple[str, str]:
    """Normalize an email builder's return value.

    The builders in app/services/emails.py historically return the HTML
    string only, while the newer ones agreed for this wave return
    `(subject, html)`. Accept both so neither side has to land first.
    """
    if isinstance(built, tuple):
        subject, html = built
        return subject or default_subject, html
    return default_subject, built


# Every lifecycle sweep marks each agency as "already emailed" so it is not
# emailed twice. Those markers used to sit in the session until the whole
# sweep finished, which quietly undid the guarantee the moment `acks_late`
# was turned on (item 223): a worker killed mid-sweep — a deploy, an OOM,
# precisely what acks_late exists for — returns the message to the broker,
# the task restarts from the top, and nothing it had already written is on
# disk. Every agency emailed in the killed run gets a second copy: the "one
# per cycle" alert arrives daily, the winback lands on someone who has
# already read it. Hence a commit per agency, right after the send is
# accepted, so a replay costs at most one duplicate instead of hundreds.


async def _rollback_quietly(db) -> None:
    """Undo a half-applied record so the rest of the sweep can continue.

    The per-agency `except` handlers below swallow one agency's failure on
    purpose. When that failure comes from the database the session is left in
    a failed transaction and every later commit raises too, so "one agency
    broke" silently becomes "nobody was emailed, and nothing was marked".
    """
    try:
        await db.rollback()
    except Exception:  # noqa: BLE001 — cleanup must not mask the original error
        log.warning("sweep.rollback_failed")


# ---------------------------------------------------------------------------
# Property card PDF
# ---------------------------------------------------------------------------

@celery_app.task(name="properties.generate_card_pdf", autoretry_for=(Exception,), max_retries=2)
def generate_property_card_pdf(lead_id: str, property_id: str) -> None:
    _run(_generate_card(uuid.UUID(lead_id), uuid.UUID(property_id)))


async def _generate_card(lead_id: uuid.UUID, property_id: uuid.UUID) -> None:
    from app.channels.email.client import ResendClient
    from app.services.property_card import build_property_card

    async with session_scope() as db:
        lead = await db.get(Lead, lead_id)
        prop = await db.get(Property, property_id)
        if lead is None or prop is None:
            return
        try:
            url = await build_property_card(db, lead=lead, property_=prop)
        except Exception:
            log.exception("card.build_failed", lead=str(lead_id), prop=str(property_id))
            return

        message = (
            f"Te paso la ficha del piso {prop.title}: {url}\n"
            f"Cualquier duda dime y vamos viendo si encaja."
        )
        if lead.whatsapp_wa_id:
            try:
                if await send_whatsapp_as_agency(
                    db, lead.agency_id, lead.whatsapp_wa_id, message
                ):
                    return
            except Exception:
                log.exception("card.wa_failed")
        if lead.email:
            try:
                await ResendClient().send(
                    to=lead.email,
                    subject=f"Ficha de {prop.title}",
                    text=message,
                )
            except Exception:
                log.exception("card.email_failed")


# ---------------------------------------------------------------------------
# Property embeddings
# ---------------------------------------------------------------------------

@celery_app.task(name="properties.embed", autoretry_for=(Exception,), max_retries=3, retry_backoff=True)
def embed_property(property_id: str) -> None:
    _run(_embed_property(uuid.UUID(property_id)))


async def _embed_property(property_id: uuid.UUID) -> None:
    from app.services.embeddings import embed, property_text_for_embedding

    async with session_scope() as db:
        prop = await db.get(Property, property_id)
        if prop is None:
            return
        text = property_text_for_embedding(prop)
        vectors = await embed([text], input_type="document")
        if vectors:
            prop.embedding = vectors[0]
            prop.extra = {**(prop.extra or {}), "embedding_model": "voyage-3"}


@celery_app.task(name="properties.embed_missing")
def embed_missing_properties() -> None:
    _run(_embed_missing())


async def _embed_missing() -> None:
    async with session_scope() as db:
        rows = await db.execute(select(Property.id).where(Property.embedding.is_(None)).limit(50))
        ids = [str(r) for (r,) in rows]
    for pid in ids:
        embed_property.delay(pid)


# ---------------------------------------------------------------------------
# Pre-visit briefing generation
# ---------------------------------------------------------------------------

@celery_app.task(name="visits.generate_briefing")
def generate_pre_visit_briefing(visit_id: str) -> None:
    _run(_generate_briefing(uuid.UUID(visit_id)))


async def _generate_briefing(visit_id: uuid.UUID) -> None:
    from anthropic import AsyncAnthropic

    from app.core.settings import settings

    async with session_scope() as db:
        visit = await db.get(Visit, visit_id)
        if visit is None or visit.status in (VisitStatus.CANCELLED, VisitStatus.NO_SHOW):
            return
        lead = await db.get(Lead, visit.lead_id)
        prop = await db.get(Property, visit.property_id)
        if lead is None or prop is None:
            return

        msgs_stmt = (
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.lead_id == lead.id)
            .order_by(Message.created_at.asc())
        )
        history = list((await db.execute(msgs_stmt)).scalars().all())

        transcript_lines = []
        for m in history[-50:]:
            who = "Cliente" if m.role == MessageRole.LEAD else "Agente"
            transcript_lines.append(f"{who}: {m.content[:500]}")
        transcript = "\n".join(transcript_lines) or "(sin histórico)"

        prompt = f"""\
Eres un asistente que prepara a un agente inmobiliario humano antes de una visita.
Tu salida será un briefing breve (máx. 250 palabras) en español, con secciones claras.

DATOS DEL LEAD:
- Nombre: {lead.full_name or 'desconocido'}
- Idioma: {lead.language}
- Presupuesto: {lead.budget_min or '?'} - {lead.budget_max or '?'} EUR
- Operación: {lead.operation or 'no definida'}
- Imprescindibles: {', '.join(lead.must_haves or []) or 'ninguno explícito'}
- Descartes: {', '.join(lead.deal_breakers or []) or 'ninguno explícito'}
- Objeciones detectadas: {', '.join(lead.detected_objections or []) or 'ninguna'}
- Notas: {lead.qualification_notes or 'sin notas'}

PROPIEDAD A VISITAR:
- {prop.title} ({prop.reference})
- {prop.city}, {prop.neighborhood or ''}
- {prop.bedrooms or '?'} hab, {prop.area_sqm or '?'} m², {prop.price} EUR

HISTORIAL CONVERSACIÓN (extracto reciente):
{transcript}

Genera un briefing con estas secciones:
1) Resumen del lead (1-2 frases)
2) Encaje con el piso (3 puntos cortos)
3) Posibles objeciones y cómo gestionarlas (lista breve)
4) Apertura sugerida para romper el hielo
5) Próximo paso comercial recomendado
"""

        client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
        try:
            resp = await client.messages.create(
                model=settings.agent_model_smart,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            briefing = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        except Exception:
            log.exception("briefing.failed", visit=str(visit_id))
            return

        visit.pre_visit_briefing = briefing


# ---------------------------------------------------------------------------
# Visit reminders
# ---------------------------------------------------------------------------

def reminder_flag(tag: str) -> str:
    """Key under `Visit.extra` that records a reminder as sent."""
    return f"reminder_{tag}_sent"


@celery_app.task(name="visits.send_reminders")
def send_visit_reminders() -> None:
    """Find visits at ~24h and ~1h and send one reminder per window.

    Idempotency (item 225): the window is 60 min wide and Beat runs every 15,
    so the same visit used to be picked up up to four times and the lead got
    four identical WhatsApps. Now each window writes a flag into
    `Visit.extra` (`reminder_24h_sent` / `reminder_1h_sent`) **before**
    sending, commits it, and only then sends. Ordered that way a crash or a
    duplicated task (acks_late) sends nothing extra; if the send itself
    raises, the flag is rolled back so the next pass retries.

    Naming note: the flag follows the window that actually exists in the
    code — 24h and 1h — not a 2h window we do not schedule.
    """
    _run(_send_visit_reminders())


async def _send_visit_reminders() -> None:
    from app.channels.email.client import ResendClient

    now = datetime.now(UTC)
    windows = [
        ("24h", now + timedelta(hours=23, minutes=30), now + timedelta(hours=24, minutes=30)),
        ("1h", now + timedelta(minutes=45), now + timedelta(minutes=75)),
    ]

    resend = ResendClient()

    for tag, lo, hi in windows:
        flag = reminder_flag(tag)
        async with session_scope() as db:
            stmt = (
                select(Visit)
                .where(
                    Visit.scheduled_at >= lo,
                    Visit.scheduled_at < hi,
                    Visit.status.in_([VisitStatus.REQUESTED, VisitStatus.CONFIRMED]),
                )
            )
            visits = list((await db.execute(stmt)).scalars().all())

            for v in visits:
                extra = dict(v.extra or {})
                if extra.get(flag):
                    continue

                lead = await db.get(Lead, v.lead_id)
                prop = await db.get(Property, v.property_id)
                if lead is None or prop is None:
                    continue
                if not lead.whatsapp_wa_id and not lead.email:
                    continue

                local_when = v.scheduled_at.strftime("%d/%m a las %H:%M")
                msg = (
                    f"Hola{' ' + lead.full_name if lead.full_name else ''}, "
                    f"te recordamos tu visita {tag.replace('h', 'h antes')} en {prop.address} "
                    f"el {local_when}. ¿Confirmas asistencia? Responde SÍ o NO."
                )

                # Claim the send first and commit it: a second worker running
                # the same sweep now skips this visit instead of racing us.
                extra[flag] = datetime.now(UTC).isoformat()
                v.extra = extra
                await db.commit()

                sent = False
                failed = False
                if lead.whatsapp_wa_id:
                    try:
                        sent = await send_whatsapp_as_agency(
                            db, lead.agency_id, lead.whatsapp_wa_id, msg
                        )
                        failed = not sent
                    except Exception:
                        failed = True
                        log.exception("reminder.wa_failed", visit=str(v.id))
                if not sent and lead.email:
                    try:
                        await resend.send(
                            to=lead.email,
                            subject=f"Recordatorio de visita - {prop.title}",
                            text=msg,
                        )
                        sent = True
                        failed = False
                    except Exception:
                        failed = True
                        log.exception("reminder.email_failed", visit=str(v.id))

                if failed and not sent:
                    # Nothing went out: release the claim so the next pass
                    # (15 min later, still inside the window) tries again.
                    released = dict(v.extra or {})
                    released.pop(flag, None)
                    v.extra = released
                    await db.commit()


# ---------------------------------------------------------------------------
# Schedule briefings for visits coming up in ~3h (not too early, not too late)
# ---------------------------------------------------------------------------

@celery_app.task(name="visits.schedule_briefings")
def schedule_pre_visit_briefings() -> None:
    _run(_schedule_briefings())


async def _schedule_briefings() -> None:
    now = datetime.now(UTC)
    lo = now + timedelta(hours=2, minutes=45)
    hi = now + timedelta(hours=3, minutes=15)
    async with session_scope() as db:
        stmt = (
            select(Visit.id)
            .where(
                Visit.scheduled_at >= lo,
                Visit.scheduled_at < hi,
                Visit.status.in_([VisitStatus.REQUESTED, VisitStatus.CONFIRMED]),
                Visit.pre_visit_briefing == "",
            )
        )
        ids = [str(r) for (r,) in await db.execute(stmt)]
    for vid in ids:
        generate_pre_visit_briefing.delay(vid)


# ---------------------------------------------------------------------------
# Price drop alerts
# ---------------------------------------------------------------------------

@celery_app.task(name="properties.sweep_price_drops")
def sweep_price_drops() -> None:
    _run(_sweep_price_drops())


async def _sweep_price_drops() -> None:
    from app.channels.email.client import ResendClient

    resend = ResendClient()

    async with session_scope() as db:
        # Select properties flagged for alert.
        # NOTE: `.as_string()` (not `.astext`) — `Property.extra` is the
        # generic sqlalchemy JSON type, whose indexed expressions have no
        # postgres-only `.astext`; that raised AttributeError on every Beat
        # run, so no price-drop alert was ever sent. Same fix as
        # api/whatsapp.py:96 and api/sms.py.
        stmt = select(Property).where(
            Property.extra["pending_price_drop_alert"].as_string() == "true"
        )
        props = list((await db.execute(stmt)).scalars().all())

        for prop in props:
            hist = prop.price_history or []
            if len(hist) < 1:
                continue
            latest = hist[-1]
            old_price = latest.get("from")
            new_price = latest.get("to")
            if old_price is None or new_price is None or new_price >= old_price:
                continue

            drop_pct = (old_price - new_price) / float(old_price) * 100.0

            # Find candidate leads: either explicitly interested in this property,
            # or whose criteria roughly matched.
            leads_stmt = (
                select(Lead)
                .where(
                    Lead.agency_id == prop.agency_id,
                    Lead.status.notin_([LeadStatus.WON, LeadStatus.LOST]),
                )
            )
            leads = list((await db.execute(leads_stmt)).scalars().all())

            notified = 0
            for lead in leads:
                if not _lead_matches(lead, prop):
                    continue
                message = (
                    f"¡Buena noticia! El piso {prop.title} ha bajado de "
                    f"{old_price:,} € a {new_price:,} € (-{drop_pct:.1f}%). "
                    f"Si te sigue interesando, dime y organizamos visita."
                )
                try:
                    delivered = False
                    if lead.whatsapp_wa_id:
                        # Credenciales de la agencia del inmueble, por lead
                        # (n21): el cliente se resuelve dentro del bucle
                        # porque cada propiedad puede ser de una agencia
                        # distinta en el mismo barrido.
                        delivered = await send_whatsapp_as_agency(
                            db, prop.agency_id, lead.whatsapp_wa_id, message
                        )
                    if not delivered and lead.email:
                        delivered = bool(
                            await resend.send(
                                to=lead.email,
                                subject=f"Bajada de precio — {prop.title}",
                                text=message,
                            )
                        )
                    if delivered:
                        notified += 1
                except Exception:
                    log.exception("price_drop.notify_failed", lead=str(lead.id))

            prop.last_price_alert_at = datetime.now(UTC)
            extra = dict(prop.extra or {})
            extra.pop("pending_price_drop_alert", None)
            extra["last_drop_alert_notified"] = notified
            prop.extra = extra


def _lead_matches(lead: Lead, prop: Property) -> bool:
    if str(prop.id) in (lead.interested_property_ids or []):
        return True
    if lead.operation and prop.operation != lead.operation:
        return False
    if lead.budget_max and prop.price > lead.budget_max:
        return False
    if lead.budget_min and prop.price < lead.budget_min * 0.8:
        # Too cheap is rarely interesting — but allow some slack.
        return False
    if lead.min_bedrooms and (prop.bedrooms or 0) < lead.min_bedrooms:
        return False
    if lead.preferred_cities and prop.city not in lead.preferred_cities:
        return False
    return not (
        lead.preferred_neighborhoods
        and prop.neighborhood not in lead.preferred_neighborhoods
    )


# ---------------------------------------------------------------------------
# Google review request (24h after a lead is WON)
# ---------------------------------------------------------------------------

@celery_app.task(name="leads.request_google_review")
def request_google_review(lead_id: str) -> None:
    _run(_request_google_review(uuid.UUID(lead_id)))


async def _request_google_review(lead_id: uuid.UUID) -> None:
    from app.channels.email.client import ResendClient
    from app.db.models import Agency

    async with session_scope() as db:
        lead = await db.get(Lead, lead_id)
        if lead is None or lead.status != LeadStatus.WON:
            return

        agency = await db.get(Agency, lead.agency_id)
        if agency is None:
            return

        google_url = (agency.settings or {}).get("google_review_url")
        if not google_url:
            log.info("google_review.no_url_configured", agency=str(agency.id))
            return

        extra = dict(lead.extra or {})
        if extra.get("google_review_requested"):
            return

        first = (lead.full_name or "").split(" ", 1)[0]
        msg = (
            f"Hola{(' ' + first) if first else ''}, gracias por confiar en {agency.name}. "
            f"Si te ha ido bien con nosotros, nos ayudarías mucho con una reseña en Google: "
            f"{google_url}"
        )

        sent = False
        if lead.whatsapp_wa_id:
            try:
                sent = await send_whatsapp_as_agency(
                    db, lead.agency_id, lead.whatsapp_wa_id, msg
                )
            except Exception:
                log.exception("review.wa_failed", lead=str(lead_id))
        if not sent and lead.email:
            try:
                await ResendClient().send(
                    to=lead.email,
                    subject=f"Gracias de parte de {agency.name}",
                    text=msg,
                )
                sent = True
            except Exception:
                log.exception("review.email_failed", lead=str(lead_id))

        if sent:
            extra["google_review_requested"] = datetime.now(UTC).isoformat()
            lead.extra = extra
            # Committed here, not at scope exit: with acks_late a worker
            # killed between the send and the end of the task replays it, and
            # the lead is asked for a Google review twice.
            await db.commit()


# ---------------------------------------------------------------------------
# Post-visit voice note structuring
# ---------------------------------------------------------------------------

@celery_app.task(name="visits.structure_voice_note")
def structure_voice_note(visit_id: str, transcript: str) -> None:
    _run(_structure_voice_note(uuid.UUID(visit_id), transcript))


async def _structure_voice_note(visit_id: uuid.UUID, transcript: str) -> None:
    from anthropic import AsyncAnthropic

    from app.core.settings import settings
    from app.db.models import Visit

    async with session_scope() as db:
        visit = await db.get(Visit, visit_id)
        if visit is None:
            return

        prompt = f"""\
Eres un asistente que estructura notas de voz dictadas por un agente inmobiliario tras una visita.

TRANSCRIPCIÓN:
{transcript}

Devuelve un JSON con las siguientes claves (todas opcionales salvo summary):
- summary: resumen en 1-2 frases
- liked: cosas que gustaron al cliente (lista)
- objections: objeciones planteadas (lista)
- next_steps: próximos pasos propuestos (lista)
- deal_temperature: hot | warm | cold
- should_follow_up_in_hours: entero (cuándo contactar de nuevo)

Responde SOLO con el JSON, sin texto alrededor."""

        client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
        try:
            resp = await client.messages.create(
                model=settings.agent_model_fast,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            import json
            import re

            raw = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            # Strip ```json fences if the model adds them
            raw = re.sub(r"^```(?:json)?\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw)
            data = json.loads(raw)
        except Exception:
            log.exception("voice_note.structure_failed", visit=str(visit_id))
            visit.post_visit_notes = transcript
            return

        visit.post_visit_notes = (
            f"Resumen: {data.get('summary', '')}\n"
            f"Gustó: {', '.join(data.get('liked') or [])}\n"
            f"Objeciones: {', '.join(data.get('objections') or [])}\n"
            f"Próximos pasos: {', '.join(data.get('next_steps') or [])}\n"
            f"Temperatura: {data.get('deal_temperature', 'warm')}\n"
        )
        if visit.status != VisitStatus.COMPLETED:
            visit.status = VisitStatus.COMPLETED

        lead = await db.get(Lead, visit.lead_id)
        if lead is not None and (temp := data.get("deal_temperature")):
            try:
                from app.db.models import LeadTemperature
                lead.temperature = LeadTemperature(temp)
            except Exception:
                pass


@celery_app.task(name="leads.sweep_won_for_review")
def sweep_won_leads_for_review() -> None:
    """Every hour, find WON leads that were won >24h ago and still haven't
    been asked for a Google review."""
    _run(_sweep_won_leads())


async def _sweep_won_leads() -> None:
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    async with session_scope() as db:
        stmt = select(Lead).where(
            Lead.status == LeadStatus.WON,
            Lead.updated_at <= cutoff,
        )
        leads = list((await db.execute(stmt)).scalars().all())
    for lead in leads:
        if (lead.extra or {}).get("google_review_requested"):
            continue
        request_google_review.delay(str(lead.id))


# ---------------------------------------------------------------------------
# Voice Sprint 3 — missed call follow-up, proactive outbound, no-show detection
# ---------------------------------------------------------------------------

@celery_app.task(name="voice.missed_call_followup")
def missed_call_followup(agency_id: str, customer_number: str) -> None:
    _run(_missed_call_followup(uuid.UUID(agency_id), customer_number))


async def _missed_call_followup(agency_id: uuid.UUID, customer_number: str) -> None:
    from app.channels.sms.client import TwilioSMSClient
    from app.db.models import Agency

    async with session_scope() as db:
        agency = await db.get(Agency, agency_id)
        if agency is None:
            return

        msg = (
            f"Hola, te hemos visto llamar a {agency.name} pero no hemos podido cogerlo. "
            "¿En qué te podemos ayudar? Cuéntanos por aquí y te respondemos enseguida."
        )

        wa_id = customer_number.lstrip("+")
        try:
            if await send_whatsapp_as_agency(db, agency.id, wa_id, msg):
                return
            log.warning("missed_call.wa_not_delivered.fallback_sms", to=customer_number)
        except Exception:
            log.warning("missed_call.wa_failed.fallback_sms", to=customer_number)

        try:
            await TwilioSMSClient().send(customer_number, msg)
        except Exception:
            log.exception("missed_call.sms_failed", to=customer_number)


@celery_app.task(name="leads.proactive_outbound_voice")
def proactive_outbound_voice() -> None:
    """Daily sweep: leads dormant 3-14 days that are warm/hot get a proactive
    AI call from the setter."""
    _run(_proactive_outbound_voice())


async def _proactive_outbound_voice() -> None:
    from app.channels.voice.client import VapiClient
    from app.db.models import Agency, LeadTemperature

    now = datetime.now(UTC)
    lo = now - timedelta(days=14)
    hi = now - timedelta(days=3)

    async with session_scope() as db:
        leads_stmt = (
            select(Lead)
            .where(
                Lead.phone_e164.is_not(None),
                Lead.temperature.in_([LeadTemperature.HOT, LeadTemperature.WARM]),
                Lead.status.notin_([LeadStatus.WON, LeadStatus.LOST, LeadStatus.DORMANT]),
                Lead.updated_at >= lo,
                Lead.updated_at <= hi,
            )
            .limit(50)
        )
        leads = list((await db.execute(leads_stmt)).scalars().all())

        client = VapiClient()
        for lead in leads:
            agency = await db.get(Agency, lead.agency_id)
            if agency is None:
                continue
            assistant_id = (agency.settings or {}).get("vapi_assistant_id")
            if not assistant_id:
                continue
            extra = dict(lead.extra or {})
            if extra.get("proactive_call_at"):
                continue
            # Claim the call *before* placing it, and commit the claim
            # (item 223). Placing a call is not idempotent: a Vapi timeout
            # can mean "phone already ringing", so a retry — from tenacity
            # inside the client, from acks_late, or from tomorrow's sweep —
            # must not dial the same person twice. Marking afterwards left
            # exactly that hole open.
            extra["proactive_call_at"] = now.isoformat()
            lead.extra = extra
            await db.commit()
            try:
                await client.place_outbound_call(
                    assistant_id=assistant_id,
                    customer_number_e164=lead.phone_e164,
                    metadata={
                        "agency_id": str(agency.id),
                        "lead_id": str(lead.id),
                        "customer_name": lead.full_name,
                    },
                )
            except Exception:
                # Deliberately keep the claim: we cannot tell a call that
                # never happened from one that did and lost its response,
                # and calling a lead twice is worse than not calling.
                log.exception("proactive_call.failed", lead=str(lead.id))


@celery_app.task(name="visits.detect_no_shows")
def detect_no_shows() -> None:
    """Mark visits that ended >30 min ago and are still not COMPLETED as
    NO_SHOW and trigger a follow-up to the lead."""
    _run(_detect_no_shows())


async def _detect_no_shows() -> None:
    from app.channels.email.client import ResendClient

    cutoff = datetime.now(UTC) - timedelta(minutes=30)
    async with session_scope() as db:
        stmt = (
            select(Visit)
            .where(
                Visit.scheduled_at <= cutoff,
                Visit.status.in_([VisitStatus.REQUESTED, VisitStatus.CONFIRMED]),
            )
            .limit(100)
        )
        visits = list((await db.execute(stmt)).scalars().all())

        resend = ResendClient()

        for v in visits:
            # The status change is the claim, so it is committed before the
            # message goes out: the query only picks up REQUESTED/CONFIRMED
            # visits, so a replayed task (acks_late) or the next 15-minute
            # pass skips this one instead of telling the lead a second time
            # that we waited for them.
            v.status = VisitStatus.NO_SHOW
            await db.commit()

            lead = await db.get(Lead, v.lead_id)
            if lead is None:
                continue

            msg = (
                "Hola, te esperábamos para la visita de hoy y no hemos podido vernos. "
                "¿Quieres que reagendemos? Dinos qué día te viene bien."
            )
            sent = False
            if lead.whatsapp_wa_id:
                try:
                    sent = await send_whatsapp_as_agency(
                        db, lead.agency_id, lead.whatsapp_wa_id, msg
                    )
                except Exception:
                    log.exception("no_show.wa_failed", visit=str(v.id))
            if not sent and lead.email:
                try:
                    await resend.send(
                        to=lead.email,
                        subject="¿Reagendamos la visita?",
                        text=msg,
                    )
                except Exception:
                    log.exception("no_show.email_failed", visit=str(v.id))


# ---------------------------------------------------------------------------
# Sprint 4 — refresh per-agency few-shot examples (daily)
# ---------------------------------------------------------------------------

@celery_app.task(name="agencies.refresh_few_shot")
def refresh_all_agencies_few_shot() -> None:
    _run(_refresh_all_agencies_few_shot())


async def _refresh_all_agencies_few_shot() -> None:
    from app.db.models import Agency
    from app.services.agency_few_shot import refresh_agency_few_shot

    async with session_scope() as db:
        ids = [r for (r,) in (await db.execute(select(Agency.id))).all()]
    for agency_id in ids:
        async with session_scope() as db:
            try:
                await refresh_agency_few_shot(db, agency_id)
            except Exception:
                log.exception("few_shot.refresh_failed", agency=str(agency_id))


# ---------------------------------------------------------------------------
# Durable inbound channel receipts
# ---------------------------------------------------------------------------

@celery_app.task(
    name="channels.drain_inbound_outbox",
    autoretry_for=(Exception,),
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=60,
)
def drain_inbound_outbox() -> dict[str, int]:
    """Resume one PostgreSQL-backed inbound receipt.

    The request process also tries the same row immediately. ``SKIP LOCKED``
    makes that race harmless, while processing one per invocation keeps a
    slow LLM turn inside the worker time limit.
    """
    return _run(_drain_inbound_outbox())


async def _drain_inbound_outbox() -> dict[str, int]:
    from app.api.email import process_inbound_email_receipt
    from app.api.whatsapp import process_inbound_receipt
    from app.services.inbound_outbox import pending_receipts

    async with session_scope() as db:
        pending = await pending_receipts(db, limit=1)
    if not pending:
        return {"attempted": 0}

    receipt_id, channel = pending[0]
    if channel == "whatsapp":
        await process_inbound_receipt(receipt_id=receipt_id)
    elif channel == "email":
        await process_inbound_email_receipt(receipt_id=receipt_id)
    else:
        log.error(
            "inbound_outbox.unknown_channel",
            receipt=str(receipt_id),
            channel=channel,
        )
    return {"attempted": 1}


# ---------------------------------------------------------------------------
# Sprint 5 — outgoing webhook delivery
# ---------------------------------------------------------------------------

@celery_app.task(
    name="webhooks.drain_outbox",
    autoretry_for=(Exception,),
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=60,
)
def drain_webhook_outbox() -> dict[str, int]:
    """Drain the durable PostgreSQL queue; Redis is only the wake-up signal."""
    return _run(_drain_webhook_outbox())


async def _drain_webhook_outbox() -> dict[str, int]:
    from app.services.event_dispatcher import drain_webhook_outbox as drain

    async with session_scope() as db:
        return await drain(db)

@celery_app.task(
    name="webhooks.deliver",
    autoretry_for=(Exception,),
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=600,
)
def deliver_webhook(sub_id: str, event: str, payload: dict) -> None:
    """Legacy task kept so broker messages queued before migration still run."""
    _run(_deliver_webhook(uuid.UUID(sub_id), event, payload))


async def _deliver_webhook(sub_id: uuid.UUID, event: str, payload: dict) -> None:
    """(n24) Una sola implementación: la de `event_dispatcher`.

    Esta tarea tenía su propia copia — firma, POST y contabilidad a mano — con
    dos agujeros que la copia buena ya no tiene:

    - POSTeaba directamente a `sub.target_url`, una cadena que elige cualquier
      miembro de la agencia. Sin comprobar el destino, cada evento de un lead
      era una petición emitida desde dentro de nuestra red a donde quisieran:
      `http://169.254.169.254/` entrega las credenciales de la máquina en la
      nube. La guarda existía... solo en el botón de "enviar prueba".
    - Al desactivar la suscripción tras 20 fallos no avisaba a nadie: el CRM
      de la agencia dejaba de recibir leads en silencio, para siempre.

    Un destino bloqueado cuenta como fallo, no como éxito: si no cuenta, el
    breaker no salta nunca y la agencia jamás se entera de que su URL no vale.
    """
    from app.db.models import WebhookSubscription
    from app.services.event_dispatcher import (
        deliver_now,
        record_delivery_failure,
        record_delivery_success,
    )

    async with session_scope() as db:
        sub = await db.get(WebhookSubscription, sub_id)
        if sub is None or not sub.is_active:
            return

        ok, _status, error = await deliver_now(sub, event, payload)
        if ok:
            await record_delivery_success(db, sub)
            await db.commit()
            return

        await record_delivery_failure(db, sub)
        # Commit *before* re-raising. `session_scope` hace rollback al salir,
        # así que el contador que acaba de subir se descartaba siempre:
        # `consecutive_failures` no salía de 0 y el corte tras 20 fallos no
        # podía dispararse. Un endpoint muerto seguía recibiendo seis
        # reintentos por evento para siempre, y su panel decía que todo iba
        # bien.
        await db.commit()
        raise RuntimeError(error or "webhook_delivery_failed")


# ---------------------------------------------------------------------------
# Lifecycle — trial ending reminder + weekly digest
# ---------------------------------------------------------------------------

@celery_app.task(name="lifecycle.trial_ending_sweep")
def trial_ending_sweep() -> None:
    """Daily: TRIALING subscriptions ending in 3-4 days get a reminder email
    to the owner with a small activity summary and a CTA to /billing."""
    _run(_trial_ending_sweep())


async def _trial_ending_sweep() -> None:
    from app.db.models import Agency, Subscription, SubscriptionStatus, User, UserRole
    from app.services.emails import build_trial_ending_email_html, send_email

    now = datetime.now(UTC)
    lo = now + timedelta(days=3)
    hi = now + timedelta(days=4)

    async with session_scope() as db:
        subs = list(
            (
                await db.execute(
                    select(Subscription).where(
                        Subscription.status == SubscriptionStatus.TRIALING,
                        Subscription.trial_ends_at >= lo,
                        Subscription.trial_ends_at < hi,
                    )
                )
            ).scalars().all()
        )

        for sub in subs:
            # One agency failing must never take the whole sweep down.
            try:
                agency = await db.get(Agency, sub.agency_id)
                if agency is None or (agency.settings or {}).get("trial_ending_notified"):
                    continue

                owner = (
                    (
                        await db.execute(
                            select(User)
                            .where(
                                User.agency_id == agency.id,
                                User.role == UserRole.OWNER,
                            )
                            .limit(1)
                        )
                    ).scalars().first()
                )
                to = (owner.email if owner is not None else None) or agency.contact_email
                if not to:
                    continue

                days_left = max((sub.trial_ends_at - now).days, 1)
                since = sub.created_at or (now - timedelta(days=14))
                conversations = (
                    await db.execute(
                        select(func.count())
                        .select_from(Conversation)
                        .where(
                            Conversation.agency_id == agency.id,
                            Conversation.created_at >= since,
                        )
                    )
                ).scalar_one()
                leads = (
                    await db.execute(
                        select(func.count())
                        .select_from(Lead)
                        .where(Lead.agency_id == agency.id, Lead.created_at >= since)
                    )
                ).scalar_one()

                sent = await send_email(
                    to=to,
                    subject=f"Te quedan {days_left} días de prueba en selai",
                    html=build_trial_ending_email_html(
                        agency.name, days_left, conversations, leads
                    ),
                )
                if sent:
                    agency.settings = {
                        **dict(agency.settings or {}),
                        "trial_ending_notified": True,
                    }
                    await db.commit()  # see the note above `_rollback_quietly`
            except Exception:
                log.exception("trial_sweep.agency_failed", sub=str(sub.id))
                await _rollback_quietly(db)


@celery_app.task(name="lifecycle.weekly_digest")
def weekly_digest() -> None:
    """Weekly (Monday morning): activity summary email per active agency."""
    _run(_weekly_digest())


async def _weekly_digest() -> None:
    from app.db.models import Agency, Subscription, SubscriptionStatus
    from app.services.emails import build_weekly_digest_email_html, send_email

    since = datetime.now(UTC) - timedelta(days=7)

    async with session_scope() as db:
        subs = list(
            (
                await db.execute(
                    select(Subscription).where(
                        Subscription.status.in_(
                            [SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE]
                        )
                    )
                )
            ).scalars().all()
        )

        for sub in subs:
            try:
                agency = await db.get(Agency, sub.agency_id)
                if agency is None or not agency.contact_email:
                    continue

                async def _count(model, agency_id=agency.id):
                    return (
                        await db.execute(
                            select(func.count())
                            .select_from(model)
                            .where(
                                model.agency_id == agency_id,
                                model.created_at >= since,
                            )
                        )
                    ).scalar_one()

                conversations = await _count(Conversation)
                leads = await _count(Lead)
                visits = await _count(Visit)

                await send_email(
                    to=agency.contact_email,
                    subject="Tu resumen semanal de selai",
                    html=build_weekly_digest_email_html(
                        agency.name, conversations, leads, visits
                    ),
                )
            except Exception:
                log.exception("weekly_digest.agency_failed", sub=str(sub.id))


# ---------------------------------------------------------------------------
# Lifecycle — voice usage alert (66), setup rescue (84), first-week report (64)
# ---------------------------------------------------------------------------

async def _agency_owner_email(db, agency) -> str | None:
    """Owner's email, falling back to the agency contact email."""
    from app.db.models import User, UserRole

    owner = (
        (
            await db.execute(
                select(User)
                .where(User.agency_id == agency.id, User.role == UserRole.OWNER)
                .limit(1)
            )
        ).scalars().first()
    )
    return (owner.email if owner is not None else None) or agency.contact_email


@celery_app.task(name="lifecycle.voice_usage_alert")
def voice_usage_alert() -> None:
    """Daily: ACTIVE/TRIALING agencies at >=80% of their plan's voice minutes
    get one heads-up email per billing cycle (month) with a CTA to /billing."""
    _run(_voice_usage_alert())


async def _voice_usage_alert() -> None:
    from app.db.models import Agency, Subscription, SubscriptionStatus
    from app.services.emails import build_voice_usage_alert_email_html, send_email
    from app.services.plan_limits import get_usage

    cycle = datetime.now(UTC).strftime("%Y-%m")

    async with session_scope() as db:
        subs = list(
            (
                await db.execute(
                    select(Subscription).where(
                        Subscription.status.in_(
                            [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
                        )
                    )
                )
            ).scalars().all()
        )

        for sub in subs:
            # One agency failing must never take the whole sweep down.
            try:
                agency = await db.get(Agency, sub.agency_id)
                if agency is None:
                    continue
                if (agency.settings or {}).get("voice_alert_sent_cycle") == cycle:
                    continue

                voice = next(
                    (u for u in await get_usage(db, agency.id) if u.resource == "voice_minutes"),
                    None,
                )
                if voice is None or voice.limit <= 0 or voice.used < 0.8 * voice.limit:
                    continue

                to = await _agency_owner_email(db, agency)
                if not to:
                    continue

                sent = await send_email(
                    to=to,
                    subject=f"Has usado {voice.used} de {voice.limit} minutos de voz",
                    html=build_voice_usage_alert_email_html(
                        agency.name, voice.used, voice.limit
                    ),
                )
                if sent:
                    agency.settings = {
                        **dict(agency.settings or {}),
                        "voice_alert_sent_cycle": cycle,
                    }
                    await db.commit()  # see the note above `_rollback_quietly`
            except Exception:
                log.exception("voice_alert.agency_failed", sub=str(sub.id))
                await _rollback_quietly(db)


# ---------------------------------------------------------------------------
# Lifecycle — resource usage alert (187): properties/agents at 90%
# ---------------------------------------------------------------------------

RESOURCE_ALERT_THRESHOLD = 0.9
# Voice minutes have their own task (a different threshold and a different
# email); these two are the ones that only produced a dry 402 on the wall.
ALERTED_RESOURCES = ("properties", "agents")
RESOURCE_LABELS_ES = {"properties": "propiedades", "agents": "usuarios"}


@celery_app.task(name="lifecycle.resource_usage_alert")
def resource_usage_alert() -> None:
    """Daily: ACTIVE/TRIALING agencies at >=90% of their properties or agents
    limit get one heads-up email per resource per billing cycle.

    Same shape as `lifecycle.voice_usage_alert` (item 187): hitting a limit
    should arrive as "te estás quedando sin sitio, aquí tienes el siguiente
    plan", not as a 402 in the middle of an import.
    """
    _run(_resource_usage_alert())


async def _resource_usage_alert() -> None:
    from app.db.models import Agency, Subscription, SubscriptionStatus
    from app.services.emails import send_email
    from app.services.plan_limits import get_usage

    try:
        from app.services.emails import build_resource_usage_alert_email_html
    except ImportError:
        log.warning("resource_alert.builder_missing")
        return

    cycle = datetime.now(UTC).strftime("%Y-%m")

    async with session_scope() as db:
        subs = list(
            (
                await db.execute(
                    select(Subscription).where(
                        Subscription.status.in_(
                            [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
                        )
                    )
                )
            ).scalars().all()
        )

        for sub in subs:
            # One agency failing must never take the whole sweep down.
            try:
                agency = await db.get(Agency, sub.agency_id)
                if agency is None:
                    continue

                usage = {u.resource: u for u in await get_usage(db, agency.id)}
                to: str | None = None

                for resource in ALERTED_RESOURCES:
                    limit_status = usage.get(resource)
                    if limit_status is None or limit_status.limit <= 0:
                        continue
                    if limit_status.used < RESOURCE_ALERT_THRESHOLD * limit_status.limit:
                        continue

                    marker = f"{resource}_alert_sent_cycle"
                    if (agency.settings or {}).get(marker) == cycle:
                        continue

                    if to is None:
                        to = await _agency_owner_email(db, agency)
                    if not to:
                        break

                    label = RESOURCE_LABELS_ES.get(resource, resource)
                    subject, html = _subject_and_html(
                        build_resource_usage_alert_email_html(
                            agency.name, resource, limit_status.used, limit_status.limit
                        ),
                        default_subject=(
                            f"Has usado {limit_status.used} de {limit_status.limit} {label}"
                        ),
                    )
                    if await send_email(to=to, subject=subject, html=html):
                        agency.settings = {
                            **dict(agency.settings or {}),
                            marker: cycle,
                        }
                        # Per resource, not per agency: properties and agents
                        # are two independent emails and a replay must not
                        # repeat the one that already went out.
                        await db.commit()
            except Exception:
                log.exception("resource_alert.agency_failed", sub=str(sub.id))
                await _rollback_quietly(db)


# ---------------------------------------------------------------------------
# Lifecycle — winback before the contractual deletion window (184)
# ---------------------------------------------------------------------------

WINBACK_MIN_DAYS = 21
WINBACK_MAX_DAYS = 25


@celery_app.task(name="lifecycle.winback_sweep")
def winback_sweep() -> None:
    """Daily: agencies that cancelled 21-25 days ago get one "esto ha pasado
    desde que te fuiste" email, with the leads and conversations their number
    kept receiving since they left.

    The 5-day window (not a single day) exists so one failed beat run doesn't
    silently drop a cohort; `agency.settings["winback_sent"]`, committed as
    soon as the email is accepted, makes sure nobody gets it twice.
    """
    _run(_winback_sweep())


async def _winback_sweep() -> None:
    from app.db.models import Agency, Subscription, SubscriptionStatus
    from app.services.emails import send_email

    try:
        from app.services.emails import build_winback_email_html
    except ImportError:
        log.warning("winback.builder_missing")
        return

    now = datetime.now(UTC)
    oldest = now - timedelta(days=WINBACK_MAX_DAYS)
    newest = now - timedelta(days=WINBACK_MIN_DAYS)

    async with session_scope() as db:
        subs = list(
            (
                await db.execute(
                    select(Subscription).where(
                        Subscription.status == SubscriptionStatus.CANCELED
                    )
                )
            ).scalars().all()
        )

        for sub in subs:
            try:
                # `canceled_at`, set by the Stripe webhook, and nothing else.
                # This used to fall back to `updated_at` for rows cancelled
                # before that column landed, but `updated_at` moves on any
                # write to the row — a billing webhook, or the monthly usage
                # reset that used to touch every subscription — so an agency
                # that left two years ago would be told it had only just left.
                # The email states a date to the customer; if we do
                # not know it, we do not send.
                left_at = sub.canceled_at
                if left_at is None:
                    log.info("winback.no_cancel_date", sub=str(sub.id))
                    continue
                if left_at.tzinfo is None:
                    left_at = left_at.replace(tzinfo=UTC)
                if not (oldest <= left_at <= newest):
                    continue

                agency = await db.get(Agency, sub.agency_id)
                if agency is None or (agency.settings or {}).get("winback_sent"):
                    continue

                leads_since = (
                    await db.execute(
                        select(func.count())
                        .select_from(Lead)
                        .where(Lead.agency_id == agency.id, Lead.created_at >= left_at)
                    )
                ).scalar_one()
                conversations_since = (
                    await db.execute(
                        select(func.count())
                        .select_from(Conversation)
                        .where(
                            Conversation.agency_id == agency.id,
                            Conversation.created_at >= left_at,
                        )
                    )
                ).scalar_one()

                to = await _agency_owner_email(db, agency)
                if not to:
                    continue

                stats = {
                    "leads": int(leads_since),
                    "conversations": int(conversations_since),
                    "days_since_cancel": (now - left_at).days,
                }
                subject, html = _subject_and_html(
                    build_winback_email_html(agency.name, stats),
                    default_subject="Esto ha pasado desde que te fuiste",
                )
                if await send_email(to=to, subject=subject, html=html):
                    agency.settings = {
                        **dict(agency.settings or {}),
                        "winback_sent": now.isoformat(),
                    }
                    await db.commit()  # see the note above `_rollback_quietly`
            except Exception:
                log.exception("winback.agency_failed", sub=str(sub.id))
                await _rollback_quietly(db)


# ---------------------------------------------------------------------------
# Lifecycle — purge operational data before the 30-day contractual maximum
# ---------------------------------------------------------------------------

# Beat runs daily. A one-day buffer means a normally operating scheduler
# completes deletion between day 29 and day 30, rather than first becoming
# eligible at day 30 and crossing the contractual maximum before the next run.
TERMINATED_DATA_PURGE_AFTER_DAYS = 29


@celery_app.task(name="lifecycle.terminated_data_purge_sweep")
def terminated_data_purge_sweep() -> dict[str, int]:
    return _run(_terminated_data_purge_sweep())


async def _terminated_data_purge_sweep() -> dict[str, int]:
    from app.db.models import Agency, Subscription, SubscriptionStatus
    from app.services.retention import purge_terminated_agency_data

    now = datetime.now(UTC)
    cutoff = now - timedelta(days=TERMINATED_DATA_PURGE_AFTER_DAYS)
    stats = {"purged": 0, "skipped": 0, "failed": 0}

    async with session_scope() as db:
        subs = list(
            (
                await db.execute(
                    select(Subscription).where(
                        Subscription.status == SubscriptionStatus.CANCELED,
                        Subscription.canceled_at.is_not(None),
                        Subscription.canceled_at <= cutoff,
                    )
                )
            ).scalars().all()
        )
        for sub in subs:
            try:
                agency = await db.get(Agency, sub.agency_id)
                if agency is None:
                    stats["skipped"] += 1
                    continue
                changed = await purge_terminated_agency_data(
                    db,
                    agency=agency,
                    subscription=sub,
                    purged_at=now,
                )
                if not changed:
                    stats["skipped"] += 1
                    continue
                await db.commit()
                stats["purged"] += 1
                log.info("retention.agency_operational_data_purged", agency=str(agency.id))
            except Exception:
                stats["failed"] += 1
                log.exception("retention.agency_purge_failed", sub=str(sub.id))
                await _rollback_quietly(db)

    return stats


# ---------------------------------------------------------------------------
# Lifecycle — 24h/72h follow-ups to silent leads (164)
# ---------------------------------------------------------------------------

@celery_app.task(name="lifecycle.followup_sweep")
def followup_sweep() -> dict[str, int]:
    """Every 30 min: leads that stopped replying get the 24h and 72h nudge.

    The windows, the channel choice and the copy live in
    `app/services/followups.py`; this task owns only the session and the
    schedule. The import is local and tolerant so the worker still boots —
    and every other beat entry keeps running — if that module is not
    deployed yet.
    """
    return _run(_followup_sweep())


async def _followup_sweep() -> dict[str, int]:
    empty = {"sent_24h": 0, "sent_72h": 0}
    try:
        from app.services.followups import followup_sweep as run_followups
    except ImportError:
        log.warning("followup_sweep.module_missing")
        return empty

    async with session_scope() as db:
        result = await run_followups(db)

    result = dict(result or {})
    log.info(
        "followup_sweep.done",
        sent_24h=result.get("sent_24h", 0),
        sent_72h=result.get("sent_72h", 0),
    )
    return {**empty, **result}


@celery_app.task(name="lifecycle.setup_rescue")
def setup_rescue() -> None:
    """Daily: agencies created 2-3 days ago that never finished the setup
    checklist get a "¿Te ayudamos a terminar?" email. Sent once."""
    _run(_setup_rescue())


async def _setup_rescue() -> None:
    from app.db.models import Agency
    from app.services.emails import build_setup_rescue_email_html, send_email

    now = datetime.now(UTC)
    lo = now - timedelta(days=3)
    hi = now - timedelta(days=2)

    async with session_scope() as db:
        agencies = list(
            (
                await db.execute(
                    select(Agency).where(Agency.created_at >= lo, Agency.created_at < hi)
                )
            ).scalars().all()
        )

        for agency in agencies:
            try:
                blob = dict(agency.settings or {})
                if blob.get("setup_rescue_sent"):
                    continue

                checklist = blob.get("setup_checklist")
                done = (
                    sum(1 for v in checklist.values() if v is True)
                    if isinstance(checklist, dict)
                    else 0
                )
                incomplete = done < 5 or not blob.get("voice_id")
                if not incomplete:
                    continue

                to = await _agency_owner_email(db, agency)
                if not to:
                    continue

                sent = await send_email(
                    to=to,
                    subject="¿Te ayudamos a terminar? Te quedan ~10 min",
                    html=build_setup_rescue_email_html(agency.name),
                )
                if sent:
                    agency.settings = {**blob, "setup_rescue_sent": True}
                    await db.commit()  # see the note above `_rollback_quietly`
            except Exception:
                log.exception("setup_rescue.agency_failed", agency=str(agency.id))
                await _rollback_quietly(db)


@celery_app.task(name="lifecycle.first_week_report")
def first_week_report() -> None:
    """Daily: agencies whose first subscription started 7-8 days ago get a
    "Tu primera semana con selai" email with the week's activity. Sent once."""
    _run(_first_week_report())


async def _first_week_report() -> None:
    from app.db.models import Agency, Subscription
    from app.services.emails import build_first_week_email_html, send_email

    now = datetime.now(UTC)
    lo = now - timedelta(days=8)
    hi = now - timedelta(days=7)

    async with session_scope() as db:
        # Subscriptions are 1:1 per agency, so the row's created_at is the
        # agency's first subscription date.
        subs = list(
            (
                await db.execute(
                    select(Subscription).where(
                        Subscription.created_at >= lo, Subscription.created_at < hi
                    )
                )
            ).scalars().all()
        )

        for sub in subs:
            try:
                agency = await db.get(Agency, sub.agency_id)
                if agency is None or (agency.settings or {}).get("first_week_sent"):
                    continue

                since = sub.created_at

                # Same activity counters as the weekly digest, scoped to the
                # agency's first week.
                async def _count(model, agency_id=agency.id, since=since):
                    return (
                        await db.execute(
                            select(func.count())
                            .select_from(model)
                            .where(
                                model.agency_id == agency_id,
                                model.created_at >= since,
                            )
                        )
                    ).scalar_one()

                conversations = await _count(Conversation)
                leads = await _count(Lead)
                visits = await _count(Visit)

                to = await _agency_owner_email(db, agency)
                if not to:
                    continue

                sent = await send_email(
                    to=to,
                    subject="Tu primera semana con selai",
                    html=build_first_week_email_html(agency.name, conversations, leads, visits),
                )
                if sent:
                    agency.settings = {**dict(agency.settings or {}), "first_week_sent": True}
                    await db.commit()  # see the note above `_rollback_quietly`
            except Exception:
                log.exception("first_week.agency_failed", sub=str(sub.id))
                await _rollback_quietly(db)


# ---------------------------------------------------------------------------
# Billing — usage counter reset (151), trial expiry (152), dunning (153)
# ---------------------------------------------------------------------------

@celery_app.task(name="billing.reset_usage_counters")
def reset_usage_counters() -> None:
    """Monthly (day 1, 02:00 UTC): close the cycle by zeroing per-cycle
    usage counters so the plan's minutes are actually monthly."""
    _run(_reset_usage_counters())


async def _reset_usage_counters() -> None:
    from sqlalchemy import update

    from app.db.models import Subscription, SubscriptionStatus

    async with session_scope() as db:
        # Scoped to subscriptions that can still consume anything. The
        # unfiltered UPDATE this used to be rewrote `updated_at` on *every*
        # row (the column carries `onupdate=func.now()`), including
        # cancellations from years ago — which is how a monthly counter reset
        # ended up feeding the winback sweep a fresh "they left last month".
        result = await db.execute(
            update(Subscription)
            .where(Subscription.status != SubscriptionStatus.CANCELED)
            .values(voice_minutes_used=0)
        )
        log.info("billing.usage_counters_reset", rows=result.rowcount)


# How long we keep retrying the "tu prueba ha terminado" email for an account
# already paused. After this the pause stands but we stop trying to explain it.
TRIAL_NOTICE_RETRY_WINDOW = timedelta(days=7)


@celery_app.task(name="lifecycle.trial_expiry_sweep")
def trial_expiry_sweep() -> None:
    """Daily: a trial that ran out without a card gets PAUSED and the owner
    gets the "tu prueba ha terminado" email with the trial's numbers.

    Without this an agency stays TRIALING forever with full service, which
    also breaks the landing's promise ("al día 14 eliges plan o tu cuenta se
    pausa").
    """
    _run(_trial_expiry_sweep())


async def _trial_expiry_sweep() -> None:
    from sqlalchemy import and_, or_

    from app.db.models import Agency, Subscription, SubscriptionStatus
    from app.services.emails import build_trial_ended_email_html, send_email

    now = datetime.now(UTC)

    async with session_scope() as db:
        subs = list(
            (
                await db.execute(
                    select(Subscription).where(
                        or_(
                            Subscription.status == SubscriptionStatus.TRIALING,
                            # Already paused, but the "tu prueba ha terminado"
                            # email never went out (no recipient, Resend down).
                            # Pausing does not depend on the email, so without
                            # this the account would go silent and the owner
                            # would never be told. The flag below stops the
                            # ones we did warn.
                            and_(
                                Subscription.status == SubscriptionStatus.PAUSED,
                                Subscription.trial_ends_at
                                > now - TRIAL_NOTICE_RETRY_WINDOW,
                            ),
                        ),
                        Subscription.trial_ends_at.is_not(None),
                        Subscription.trial_ends_at < now,
                        Subscription.stripe_subscription_id.is_(None),
                    )
                )
            ).scalars().all()
        )

        for sub in subs:
            # One agency failing must never take the whole sweep down.
            try:
                # Pausing is the point of the sweep; the email is best-effort.
                # Committed on its own so a worker killed further down (or a
                # single agency erroring into the rollback below) still leaves
                # the account paused, which is the part that has to happen.
                sub.status = SubscriptionStatus.PAUSED
                await db.commit()

                agency = await db.get(Agency, sub.agency_id)
                if agency is None:
                    continue
                blob = dict(agency.settings or {})
                if blob.get("trial_ended_email_sent"):
                    continue

                to = await _agency_owner_email(db, agency)
                if not to:
                    continue

                since = sub.created_at or (now - timedelta(days=14))

                async def _count(model, agency_id=agency.id, since=since):
                    return (
                        await db.execute(
                            select(func.count())
                            .select_from(model)
                            .where(model.agency_id == agency_id, model.created_at >= since)
                        )
                    ).scalar_one()

                leads = await _count(Lead)
                conversations = await _count(Conversation)
                visits = await _count(Visit)

                sent = await send_email(
                    to=to,
                    subject="Tu prueba de selai ha terminado",
                    html=build_trial_ended_email_html(
                        agency.name, leads, conversations, visits
                    ),
                )
                if sent:
                    agency.settings = {**blob, "trial_ended_email_sent": True}
                    await db.commit()  # see the note above `_rollback_quietly`
            except Exception:
                log.exception("trial_expiry.agency_failed", sub=str(sub.id))
                await _rollback_quietly(db)


# Days after the first failed charge on which we insist again.
DUNNING_RETRY_DAYS = (3, 7)


@celery_app.task(name="billing.dunning_sweep")
def dunning_sweep() -> None:
    """Daily: subscriptions still PAST_DUE get a reminder 3 and 7 days after
    the first failed charge. Involuntary churn is the cheapest to recover."""
    _run(_dunning_sweep())


async def _dunning_sweep() -> None:
    from app.api.billing import send_dunning_email
    from app.db.models import Agency, Subscription, SubscriptionStatus

    now = datetime.now(UTC)

    async with session_scope() as db:
        subs = list(
            (
                await db.execute(
                    select(Subscription).where(
                        Subscription.status == SubscriptionStatus.PAST_DUE
                    )
                )
            ).scalars().all()
        )

        for sub in subs:
            try:
                agency = await db.get(Agency, sub.agency_id)
                if agency is None:
                    continue
                blob = dict(agency.settings or {})
                started_raw = blob.get("dunning_started_at")
                count = int(blob.get("dunning_count") or 0)
                if not started_raw or count < 1:
                    # No first email on record (eg. PAST_DUE arrived via
                    # customer.subscription.updated); send it now and let the
                    # 3/7-day clock start from here.
                    if await send_dunning_email(db, sub, attempt=1):
                        # `send_dunning_email` only stages the bookkeeping;
                        # its callers own the commit. See the note above
                        # `_rollback_quietly` for why it cannot wait.
                        await db.commit()
                    continue
                if count >= len(DUNNING_RETRY_DAYS) + 1:
                    continue

                try:
                    started = datetime.fromisoformat(str(started_raw))
                except ValueError:
                    continue
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)

                due_after_days = DUNNING_RETRY_DAYS[count - 1]
                if (now - started) < timedelta(days=due_after_days):
                    continue

                if await send_dunning_email(db, sub, attempt=count + 1):
                    await db.commit()
            except Exception:
                log.exception("dunning_sweep.agency_failed", sub=str(sub.id))
                await _rollback_quietly(db)


# Suppress unused warnings
_ = (ConversationStatus, ConversationChannel, MessageDirection)
