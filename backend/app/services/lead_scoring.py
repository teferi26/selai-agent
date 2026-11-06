"""Lead scoring 0-100 with explainable breakdown.

Hybrid: deterministic rules (cheap, predictable) + LLM-derived boost based
on conversation signals (intent strength, urgency, financing readiness).

Output is stored as `lead.score` plus a structured `extra["score_breakdown"]`
so the dashboard can show *why* this lead is hot.

Updates `lead.temperature` to one of hot/warm/cold based on score:
  - 70+ → hot
  - 40-69 → warm
  - 0-39 → cold

(165) Exception — sticky hot: when the agent (or one of its tools) explicitly
marks a lead as HOT we store `extra["hot_marked_at"]` and refuse to downgrade
the temperature for `HOT_STICKY_HOURS`. The *score* is never faked: it keeps
its computed value and the breakdown shows why the lead stays hot. Once the
window expires the temperature follows the score again.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import (
    Conversation,
    Lead,
    LeadStatus,
    LeadTemperature,
    Message,
    MessageRole,
    Visit,
    VisitStatus,
)

log = get_logger(__name__)


@dataclass
class ScoreComponent:
    label: str
    points: int
    detail: str = ""


@dataclass
class ScoreBreakdown:
    total: int
    temperature: str
    components: list[ScoreComponent]

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "temperature": self.temperature,
            "components": [asdict(c) for c in self.components],
        }


# ---------------------------------------------------------------------------
# (165) Sticky hot — an explicit HOT mark survives the deterministic recompute
# ---------------------------------------------------------------------------

HOT_STICKY_HOURS = 48

HOT_MARK_KEY = "hot_marked_at"
HOT_MARK_SOURCE_KEY = "hot_marked_by"

STICKY_HOT_LABEL = "Marcado como caliente por el agente"


def mark_temperature_hot(lead: Lead, *, source: str, now: datetime | None = None) -> None:
    """Pin a lead to HOT and remember when/who did it.

    `recompute_lead_score` honours that mark for `HOT_STICKY_HOURS` so the
    deterministic score can't undo what the agent just detected in the
    conversation ("pago al contado y tengo prisa").
    """
    stamped = (now or datetime.now(UTC)).isoformat()
    lead.temperature = LeadTemperature.HOT
    lead.extra = {
        **(lead.extra or {}),
        HOT_MARK_KEY: stamped,
        HOT_MARK_SOURCE_KEY: source,
    }


def clear_hot_mark(lead: Lead) -> None:
    """Drop the sticky mark — used when the agent downgrades on purpose."""
    extra = dict(lead.extra or {})
    extra.pop(HOT_MARK_KEY, None)
    extra.pop(HOT_MARK_SOURCE_KEY, None)
    lead.extra = extra


def hot_mark_is_active(lead: Lead, *, now: datetime | None = None) -> bool:
    """True while an explicit HOT mark is still within its sticky window."""
    raw = (lead.extra or {}).get(HOT_MARK_KEY)
    if not raw:
        return False
    try:
        marked_at = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return False
    if marked_at.tzinfo is None:
        marked_at = marked_at.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return reference - marked_at < timedelta(hours=HOT_STICKY_HOURS)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

async def recompute_lead_score(db: AsyncSession, lead_id: uuid.UUID) -> ScoreBreakdown:
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise ValueError(f"lead {lead_id} not found")

    components: list[ScoreComponent] = []
    visits = await _load_visits(db, lead)

    components.extend(_score_qualification(lead))
    components.extend(await _score_engagement(db, lead))
    components.extend(_score_intent(lead, visits))
    components.extend(_score_status(lead))

    raw = sum(c.points for c in components)
    total = max(0, min(100, raw))
    temperature = _temperature_for(total)

    # (165) An explicit HOT mark wins over the computed temperature for 48h.
    # It adds 0 points on purpose: the score stays honest, only the priority
    # the humans see is pinned.
    if temperature != "hot" and hot_mark_is_active(lead):
        temperature = "hot"
        components.append(
            ScoreComponent(
                STICKY_HOT_LABEL,
                0,
                f"se respeta {HOT_STICKY_HOURS}h desde la marca",
            )
        )

    breakdown = ScoreBreakdown(total=total, temperature=temperature, components=components)

    lead.score = total
    lead.temperature = LeadTemperature(temperature)
    lead.extra = {**(lead.extra or {}), "score_breakdown": breakdown.as_dict()}

    await _announce_crm_events(db, lead, visits)
    return breakdown


# ---------------------------------------------------------------------------
# Outgoing webhooks for the lead lifecycle
#
# This runs once per conversation turn, right after the agent's tools have
# had their say (see conversation_service.handle_inbound), which makes it the
# first moment the lead's real state is known: `save_lead_info` may have just
# promoted it to QUALIFIED, `schedule_visit` may have just created a Visit,
# and the temperature above is decided here. Announcing from anywhere else
# would either be too early (before the recompute settles the temperature) or
# spread across four files.
#
# Everything is best-effort and idempotent: the marker lives on the row, so
# the same fact is never announced twice no matter how many turns follow.
# ---------------------------------------------------------------------------

# How far back a fact has to be for the *first* sweep over a row to treat it
# as history instead of news. Without it, the first turn after deploying this
# would replay every visit an agency ever booked into its CRM and create
# duplicate records there. It applies only to rows we have never swept before;
# once a row carries our stamp every later change is announced, whatever the
# clock says.
CRM_EVENT_MAX_AGE = timedelta(hours=24)


async def _announce_crm_events(db: AsyncSession, lead: Lead, visits: list[Visit]) -> None:
    try:
        from app.services.event_dispatcher import (
            emit_lead_qualified,
            emit_lead_temperature_changed,
            emit_visit_completed,
            emit_visit_scheduled,
            is_crm_backfill,
            mark_crm_sight,
        )

        # Decided *before* the stamp is written, and only from the lead's own
        # `created_at`: a lead we have never swept before that predates the
        # sweep is history, anything else is news. `updated_at` cannot be used
        # for this — it is the previous write's timestamp until this
        # transaction commits, so a lead that had been quiet for a week and
        # qualifies right now would look a week old and be silenced forever.
        backfill = is_crm_backfill(lead, max_age=CRM_EVENT_MAX_AGE)
        mark_crm_sight(lead)

        await emit_lead_qualified(db, lead, backfill=backfill)
        await emit_lead_temperature_changed(db, lead, backfill=backfill)
        for visit in visits:
            # Scheduled first even when the visit is already done: a CRM that
            # never heard the visit was booked cannot place its completion.
            await emit_visit_scheduled(db, visit, lead=lead, max_age=CRM_EVENT_MAX_AGE)
            await emit_visit_completed(db, visit, lead=lead, max_age=CRM_EVENT_MAX_AGE)
    except Exception:
        log.exception("crm_events.announce_failed", lead=str(lead.id))


def _temperature_for(score: int) -> str:
    if score >= 70:
        return "hot"
    if score >= 40:
        return "warm"
    return "cold"


# ---------------------------------------------------------------------------
# Component scorers
# ---------------------------------------------------------------------------

def _score_qualification(lead: Lead) -> list[ScoreComponent]:
    out: list[ScoreComponent] = []

    if lead.budget_max:
        out.append(ScoreComponent("Presupuesto definido", 15, f"hasta {lead.budget_max} EUR"))
    if lead.preferred_cities or lead.preferred_neighborhoods:
        out.append(ScoreComponent("Zona definida", 10))
    if lead.min_bedrooms is not None:
        out.append(ScoreComponent("Habitaciones definidas", 5))
    if lead.operation:
        out.append(ScoreComponent("Tipo operación clara", 5, lead.operation))
    if lead.full_name:
        out.append(ScoreComponent("Nombre conocido", 3))
    if lead.email and lead.phone_e164:
        out.append(ScoreComponent("Contacto multi-canal", 4))
    if lead.must_haves:
        out.append(ScoreComponent("Imprescindibles claros", 3))

    return out


async def _score_engagement(db: AsyncSession, lead: Lead) -> list[ScoreComponent]:
    out: list[ScoreComponent] = []

    msg_count = (
        await db.execute(
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.lead_id == lead.id,
                Message.role == MessageRole.LEAD,
            )
        )
    ).scalars().all()
    n = len(msg_count)
    if n >= 10:
        out.append(ScoreComponent("Muy participativo", 12, f"{n} mensajes del lead"))
    elif n >= 5:
        out.append(ScoreComponent("Conversación activa", 8, f"{n} mensajes del lead"))
    elif n >= 2:
        out.append(ScoreComponent("Ha respondido", 4, f"{n} mensajes del lead"))

    return out


async def _load_visits(db: AsyncSession, lead: Lead) -> list[Visit]:
    """The lead's visits, scoped to its agency.

    Loaded once by the caller and passed down: the scorer needs them and so
    does the webhook announcement, and reading them twice per turn to keep
    two functions self-contained is not worth the round trip.
    """
    return list(
        (
            await db.execute(
                select(Visit).where(
                    Visit.lead_id == lead.id, Visit.agency_id == lead.agency_id
                )
            )
        ).scalars().all()
    )


def _score_intent(lead: Lead, visits: list[Visit]) -> list[ScoreComponent]:
    out: list[ScoreComponent] = []

    interested = lead.interested_property_ids or []
    if interested:
        out.append(ScoreComponent("Pidió detalles de pisos concretos", 8, f"{len(interested)}"))

    visited = sum(1 for v in visits if v.status == VisitStatus.COMPLETED)
    scheduled = sum(1 for v in visits if v.status in (VisitStatus.REQUESTED, VisitStatus.CONFIRMED))
    no_shows = sum(1 for v in visits if v.status == VisitStatus.NO_SHOW)

    if scheduled:
        out.append(ScoreComponent("Visita agendada", 20, f"{scheduled} pendientes"))
    if visited:
        out.append(ScoreComponent("Ya ha visitado", 25, f"{visited} visitas"))
    if no_shows:
        out.append(ScoreComponent("No-show histórico", -15, f"{no_shows}"))

    return out


def _score_status(lead: Lead) -> list[ScoreComponent]:
    bonuses = {
        LeadStatus.QUALIFIED: ScoreComponent("Estado: cualificado", 5),
        LeadStatus.NEGOTIATING: ScoreComponent("Estado: negociando", 25),
        LeadStatus.WON: ScoreComponent("Estado: ganado", 40),
        LeadStatus.LOST: ScoreComponent("Estado: perdido", -50),
        LeadStatus.DORMANT: ScoreComponent("Estado: dormido", -20),
    }
    bonus = bonuses.get(lead.status)
    return [bonus] if bonus else []
