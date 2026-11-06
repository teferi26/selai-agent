"""Tools the agent can call.

Each tool has:
- A JSON schema for Claude (`schema`).
- An async `execute()` that runs the side effect and returns a string Claude will see.

Tools are channel-agnostic. They receive an `AgentContext` with the active
agency_id, lead_id, conversation_id and DB session.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import MONTHS_ES, WEEKDAYS_ES
from app.core.logging import get_logger
from app.db.models import (
    Agency,
    Conversation,
    ConversationStatus,
    Lead,
    LeadStatus,
    LeadTemperature,
    Property,
    PropertyStatus,
    UserRole,
    Visit,
    VisitStatus,
)
from app.services.agent_routing import pick_agent_for_visit
from app.services.calculators import (
    ITP_RATES_SECOND_HAND,
    mortgage_quote,
    total_purchase_cost,
)
from app.services.embeddings import EMBEDDING_DIM, embed_query
from app.services.google_calendar import create_visit_event
from app.services.lead_scoring import clear_hot_mark, mark_temperature_hot
from app.services.match_reasoning import reasons_for_match
from app.services.mortgage_preapproval import request_preapproval

log = get_logger(__name__)


@dataclass
class AgentContext:
    db: AsyncSession
    agency_id: uuid.UUID
    lead_id: uuid.UUID
    conversation_id: uuid.UUID
    channel: str


# ---------------------------------------------------------------------------
# Tool schemas (Anthropic tool-use format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_properties",
        "description": (
            "Buscar propiedades del catálogo de la inmobiliaria que encajen con los criterios "
            "del cliente. Devuelve hasta 5 propiedades ordenadas por afinidad. Úsala antes de "
            "mencionar cualquier piso concreto."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["sale", "rent"],
                    "description": "Compra o alquiler.",
                },
                "city": {"type": "string"},
                "neighborhoods": {"type": "array", "items": {"type": "string"}},
                "min_price": {"type": "integer", "minimum": 0},
                "max_price": {"type": "integer", "minimum": 0},
                "min_bedrooms": {"type": "integer", "minimum": 0},
                "min_bathrooms": {"type": "integer", "minimum": 0},
                "min_area_sqm": {"type": "integer", "minimum": 0},
                "must_haves": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "elevator", "parking", "terrace", "pool",
                            "ac", "heating", "furnished", "pet_friendly",
                        ],
                    },
                },
                "free_text": {
                    "type": "string",
                    "description": "Descripción libre para búsqueda semántica adicional.",
                },
            },
        },
    },
    {
        "name": "save_lead_info",
        "description": (
            "Guardar información que has aprendido del cliente (nombre, presupuesto, zonas, "
            "necesidades, objeciones, etc.). Llámala SIEMPRE que descubras algo nuevo."
        ),
        "input_schema": {
            "type": "object",
            # Refuerzo del allowlist de _save_lead_info: el modelo no debe ni
            # proponer campos fuera del contrato.
            "additionalProperties": False,
            "properties": {
                "full_name": {"type": "string"},
                "email": {"type": "string"},
                "budget_min": {"type": "integer"},
                "budget_max": {"type": "integer"},
                "operation": {"type": "string", "enum": ["sale", "rent"]},
                "preferred_cities": {"type": "array", "items": {"type": "string"}},
                "preferred_neighborhoods": {"type": "array", "items": {"type": "string"}},
                "min_bedrooms": {"type": "integer"},
                "min_bathrooms": {"type": "integer"},
                "min_area_sqm": {"type": "integer"},
                "must_haves": {"type": "array", "items": {"type": "string"}},
                "deal_breakers": {"type": "array", "items": {"type": "string"}},
                "detected_objections": {"type": "array", "items": {"type": "string"}},
                "qualification_notes_append": {
                    "type": "string",
                    "description": "Texto a añadir a las notas (no reemplaza, concatena).",
                },
                "language": {"type": "string", "description": "ISO 639-1: es, en, fr..."},
            },
        },
    },
    {
        "name": "save_seller_lead",
        "description": (
            "Registrar una OPORTUNIDAD DE CAPTACIÓN: el interlocutor quiere vender o alquilar "
            "SU propiedad. Llámala en cuanto sepas al menos la zona y el tipo de inmueble; "
            "avisa al instante al equipo de la agencia. Vuelve a llamarla si luego consigues "
            "el precio esperado o la urgencia."
        ),
        "input_schema": {
            "type": "object",
            "required": ["zone", "property_type"],
            "properties": {
                "zone": {
                    "type": "string",
                    "description": "Zona/barrio/municipio donde está el inmueble.",
                },
                "property_type": {
                    "type": "string",
                    "description": "Tipo de inmueble: piso, ático, chalet, local, terreno...",
                },
                "expected_price": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Precio en euros que espera el propietario, si lo ha dicho.",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "description": "high = quiere venderlo ya; low = solo está tanteando.",
                },
                "notes": {
                    "type": "string",
                    "description": "Detalles útiles para el agente: metros, estado, motivo...",
                },
            },
        },
    },
    {
        "name": "schedule_visit",
        "description": (
            "Proponer/agendar una visita a un piso concreto. Crea la visita en estado REQUESTED. "
            "Confirma con el cliente fecha y hora antes de llamarla."
        ),
        "input_schema": {
            "type": "object",
            "required": ["property_id", "scheduled_at_iso"],
            "properties": {
                "property_id": {
                    "type": "string",
                    "description": "ID interno del piso devuelto por search_properties.",
                },
                "scheduled_at_iso": {
                    "type": "string",
                    "description": "Fecha/hora en ISO 8601 con zona horaria, ej: 2026-04-25T17:30:00+02:00",
                },
                "duration_minutes": {"type": "integer", "default": 30},
                "notes": {"type": "string", "description": "Notas para el agente humano."},
            },
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Pasar la conversación a un agente humano. Úsala cuando: el cliente lo pida, esté "
            "molesto, haya queja formal, o cuando detectes que el caso requiere negociación o "
            "información que no puedes resolver."
        ),
        "input_schema": {
            "type": "object",
            "required": ["reason"],
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Por qué escalas. Lo verá el agente humano.",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "default": "normal",
                },
            },
        },
    },
    {
        "name": "update_lead_temperature",
        "description": (
            "Marcar el lead como hot/warm/cold según señales de la conversación. Hot: comprador "
            "serio con presupuesto y plazo claros. Warm: interesado pero no urgente. Cold: "
            "explorando o no muestra intención real."
        ),
        "input_schema": {
            "type": "object",
            "required": ["temperature"],
            "properties": {
                "temperature": {"type": "string", "enum": ["hot", "warm", "cold"]},
                "reasoning": {"type": "string"},
            },
        },
    },
    {
        "name": "send_property_card",
        "description": (
            "Generar y devolver al cliente la ficha personalizada de un piso (PDF + texto con "
            "3 razones por las que le encaja). Úsala cuando el cliente muestra interés concreto "
            "en una propiedad y quiere materiales para revisarla."
        ),
        "input_schema": {
            "type": "object",
            "required": ["property_id"],
            "properties": {
                "property_id": {"type": "string"},
            },
        },
    },
    {
        "name": "mortgage_quote",
        "description": (
            "Calcular cuota mensual estimada de una hipoteca. Estándar Francia (cuota fija). "
            "Devuelve cuota, total a pagar, intereses totales. Aclara que es informativo."
        ),
        "input_schema": {
            "type": "object",
            "required": ["price_eur"],
            "properties": {
                "price_eur": {"type": "integer", "minimum": 1000},
                "down_payment_pct": {"type": "number", "default": 20.0, "minimum": 1, "maximum": 99},
                "annual_rate_pct": {"type": "number", "default": 3.5, "minimum": 0.1, "maximum": 15},
                "years": {"type": "integer", "default": 25, "minimum": 1, "maximum": 40},
            },
        },
    },
    {
        "name": "mortgage_preapproval",
        "description": (
            "Estimación INDICATIVA de pre-aprobación hipotecaria. Úsala SOLO cuando el cliente "
            "ya muestra interés concreto en comprar y comparte sus ingresos/ahorros. Aclara "
            "siempre que es informativa y que la oferta real depende del banco. Devuelve "
            "también un enlace a un broker partner para tramitarla en serio."
        ),
        "input_schema": {
            "type": "object",
            "required": ["monthly_income_eur", "savings_eur", "age"],
            "properties": {
                "monthly_income_eur": {"type": "integer", "minimum": 100},
                "other_monthly_debts_eur": {"type": "integer", "default": 0, "minimum": 0},
                "savings_eur": {"type": "integer", "minimum": 0},
                "has_indefinite_contract": {"type": "boolean", "default": True},
                "age": {"type": "integer", "minimum": 18, "maximum": 90},
                "target_property_price_eur": {"type": "integer"},
            },
        },
    },
    {
        "name": "purchase_costs",
        "description": (
            "Calcular gastos totales de compra de un piso en España (ITP segunda mano por CCAA, "
            "o IVA+AJD obra nueva, + notario, registro, gestoría, tasación). Aclara que es "
            "informativo."
        ),
        "input_schema": {
            "type": "object",
            "required": ["price_eur", "region"],
            "properties": {
                "price_eur": {"type": "integer", "minimum": 1000},
                "region": {
                    "type": "string",
                    "description": "Comunidad autónoma en español, sin tildes ni mayúsculas, p.ej. 'madrid', 'cataluna', 'pais-vasco'.",
                    "enum": list(ITP_RATES_SECOND_HAND.keys()),
                },
                "new_build": {"type": "boolean", "default": False},
                "needs_mortgage": {"type": "boolean", "default": True},
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------

async def execute_tool(name: str, arguments: dict, ctx: AgentContext) -> str:
    """Dispatch tool call to its implementation. Returns string for Claude."""
    handlers = {
        "search_properties": _search_properties,
        "save_lead_info": _save_lead_info,
        "save_seller_lead": _save_seller_lead,
        "schedule_visit": _schedule_visit,
        "escalate_to_human": _escalate_to_human,
        "update_lead_temperature": _update_lead_temperature,
        "send_property_card": _send_property_card,
        "mortgage_quote": _mortgage_quote,
        "mortgage_preapproval": _mortgage_preapproval,
        "purchase_costs": _purchase_costs,
    }
    handler = handlers.get(name)
    if handler is None:
        return f"ERROR: tool desconocida '{name}'"
    try:
        return await handler(arguments, ctx)
    except Exception as exc:  # pragma: no cover — defensive
        log.exception("tool.failed", tool=name, args=arguments)
        return f"ERROR ejecutando {name}: {exc}"


async def _search_properties(args: dict, ctx: AgentContext) -> str:
    stmt = select(Property).where(
        Property.agency_id == ctx.agency_id,
        Property.status == PropertyStatus.AVAILABLE,
    )

    if op := args.get("operation"):
        stmt = stmt.where(Property.operation == op)
    if city := args.get("city"):
        stmt = stmt.where(Property.city.ilike(f"%{city}%"))
    if neighborhoods := args.get("neighborhoods"):
        stmt = stmt.where(Property.neighborhood.in_(neighborhoods))
    if mn := args.get("min_price"):
        stmt = stmt.where(Property.price >= mn)
    if mx := args.get("max_price"):
        stmt = stmt.where(Property.price <= mx)
    if v := args.get("min_bedrooms"):
        stmt = stmt.where(Property.bedrooms >= v)
    if v := args.get("min_bathrooms"):
        stmt = stmt.where(Property.bathrooms >= v)
    if v := args.get("min_area_sqm"):
        stmt = stmt.where(Property.area_sqm >= v)

    feature_map = {
        "elevator": Property.has_elevator,
        "parking": Property.has_parking,
        "terrace": Property.has_terrace,
        "pool": Property.has_pool,
        "ac": Property.has_ac,
        "heating": Property.has_heating,
        "furnished": Property.furnished,
        "pet_friendly": Property.pet_friendly,
    }
    for feat in args.get("must_haves") or []:
        col = feature_map.get(feat)
        if col is not None:
            stmt = stmt.where(col.is_(True))

    free_text = (args.get("free_text") or "").strip()
    if free_text:
        # Hybrid: keep the SQL filters but rank by vector similarity to the query.
        try:
            query_vec = await embed_query(free_text)
        except Exception as exc:
            log.warning("search.embed_failed", error=str(exc))
            query_vec = None
        if query_vec is not None:
            stmt = stmt.where(Property.embedding.is_not(None))
            stmt = stmt.order_by(Property.embedding.cosine_distance(query_vec))
        else:
            stmt = stmt.order_by(Property.created_at.desc())
    else:
        stmt = stmt.order_by(Property.created_at.desc())

    stmt = stmt.limit(5)
    result = await ctx.db.execute(stmt)
    properties = list(result.scalars().all())

    if not properties:
        return "No hay propiedades en el catálogo que cumplan esos criterios."

    lines = ["Propiedades encontradas:"]
    for p in properties:
        lines.append(
            f"- id={p.id} | ref={p.reference} | {p.title} | {p.bedrooms or '?'} hab "
            f"| {p.area_sqm or '?'} m² | {p.neighborhood or p.city} | €{p.price:,} "
            f"| {p.operation}"
        )
    return "\n".join(lines)


# Exactly the fields the tool's input_schema declares (minus the append-only
# notes, handled apart). The model DECIDES the arguments, and the model reads
# whatever the lead writes — so a prompt-injected lead could otherwise talk
# the agent into emitting extra keys. The old code did `setattr` on any Lead
# column that matched: `status`, `score`, `consent_marketing`, `extra`, even
# `agency_id`. The allowlist makes the blast radius exactly what the schema
# promised, no matter what the model emits.
_SAVE_LEAD_FIELDS: frozenset[str] = frozenset({
    "full_name",
    "email",
    "budget_min",
    "budget_max",
    "operation",
    "preferred_cities",
    "preferred_neighborhoods",
    "min_bedrooms",
    "min_bathrooms",
    "min_area_sqm",
    "must_haves",
    "deal_breakers",
    "detected_objections",
    "language",
})

# Integer columns come back from the model as strings often enough ("320.000",
# "unos 250000€"); writing those raw blows up the flush and the lead loses the
# whole turn. Coerced like `save_seller_lead` already does.
_SAVE_LEAD_INT_FIELDS: frozenset[str] = frozenset({
    "budget_min", "budget_max", "min_bedrooms", "min_bathrooms", "min_area_sqm",
})


async def _save_lead_info(args: dict, ctx: AgentContext) -> str:
    lead = await ctx.db.get(Lead, ctx.lead_id)
    if lead is None:
        return "ERROR: lead no encontrado."

    notes_append = args.pop("qualification_notes_append", None)
    for field, value in args.items():
        if value is None or field not in _SAVE_LEAD_FIELDS:
            continue
        if field in _SAVE_LEAD_INT_FIELDS:
            value = _as_optional_int(value)
            if value is None:
                continue
        elif field == "operation":
            # The one enum-ish column: only the two values the schema allows.
            if str(value).lower() not in ("sale", "rent"):
                continue
            value = str(value).lower()
        setattr(lead, field, value)

    if notes_append:
        lead.qualification_notes = (lead.qualification_notes + "\n" + notes_append).strip()

    if lead.status == LeadStatus.NEW:
        lead.status = LeadStatus.ENGAGED

    if (
        lead.status == LeadStatus.ENGAGED
        and lead.budget_max is not None
        and (lead.preferred_cities or lead.preferred_neighborhoods)
    ):
        lead.status = LeadStatus.QUALIFIED

    await ctx.db.flush()
    return "OK: información del lead actualizada."


def _as_optional_int(value: Any) -> int | None:
    """Claude sometimes sends "320000" or "320.000 €" for an integer field."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else None


async def _save_seller_lead(args: dict, ctx: AgentContext) -> str:
    """(163) The interlocutor owns a property and wants to sell/rent it out."""
    lead = await ctx.db.get(Lead, ctx.lead_id)
    if lead is None:
        return "ERROR: lead no encontrado."

    zone = str(args.get("zone") or "").strip()
    property_type = str(args.get("property_type") or "").strip()
    if not zone or not property_type:
        return "ERROR: faltan la zona o el tipo de inmueble. Pregúntaselos al propietario."

    urgency = args.get("urgency")
    if urgency not in ("low", "normal", "high"):
        urgency = None
    notes = str(args.get("notes") or "").strip()

    expected_price = _as_optional_int(args.get("expected_price"))
    if expected_price is not None and expected_price < 0:
        expected_price = None

    previous = (lead.extra or {}).get("seller_lead")
    previous = previous if isinstance(previous, dict) else {}
    captured_at = datetime.now(UTC).isoformat()
    # The tool is meant to be called again as the conversation reveals more
    # (price first, urgency later). A follow-up call only carries the new fact,
    # so we merge: a field the agent omits keeps what we already knew.
    seller_lead = {
        "zone": zone,
        "property_type": property_type,
        "expected_price": (
            expected_price if expected_price is not None else previous.get("expected_price")
        ),
        "urgency": urgency or previous.get("urgency"),
        "notes": notes or previous.get("notes"),
        "channel": ctx.channel,
        "captured_at": captured_at,
        "first_captured_at": previous.get("first_captured_at") or captured_at,
    }

    lead.extra = {**(lead.extra or {}), "seller_lead": seller_lead}
    # A property to capture is the most valuable lead an agency can get: pin it
    # hot so the deterministic score (built around buyers) can't bury it.
    mark_temperature_hot(lead, source="save_seller_lead")

    if lead.status == LeadStatus.NEW:
        lead.status = LeadStatus.ENGAGED

    await ctx.db.flush()
    return (
        f"OK: captación registrada ({property_type} en {zone}). El equipo de la agencia ya "
        f"ha sido avisado. Dile que un agente le llamará para valorar el inmueble sin "
        f"compromiso; tú no des ningún precio de mercado."
    )


# ---------------------------------------------------------------------------
# (221) Visit scheduling rules — office hours + collisions
#
# The only check used to be "at least 30 minutes in the future", so the model
# could book a Sunday at 03:00 or double-book the same agent. Every impossible
# visit is a manual apology that erodes the agency's trust in the product.
# ---------------------------------------------------------------------------

VISIT_MIN_LEAD_MINUTES = 30
# Two visits closer than this either overlap or leave no time to drive between
# flats. Applies to the lead's own agenda and to the assigned agent's.
VISIT_COLLISION_MINUTES = 45
VISIT_SLOT_STEP_MINUTES = 30
VISIT_SEARCH_DAYS = 14
VISIT_ALTERNATIVES = 3
# Distance between two alternatives we offer: at least a collision window,
# rounded up to a whole number of steps so the times stay on :00 / :30.
VISIT_SLOT_GAP_MINUTES = (
    -(-VISIT_COLLISION_MINUTES // VISIT_SLOT_STEP_MINUTES) * VISIT_SLOT_STEP_MINUTES
)

# Statuses that still occupy a slot in someone's calendar.
BOOKED_VISIT_STATUSES = (
    VisitStatus.REQUESTED,
    VisitStatus.CONFIRMED,
    VisitStatus.RESCHEDULED,
)


def _agency_timezone(agency: Agency | None) -> tuple[tzinfo, str]:
    """The agency's timezone, never raising.

    Imported lazily: `agent/runner.py` imports this module at load time, so a
    module-level import would close the cycle.
    """
    from app.agent.runner import resolve_timezone

    return resolve_timezone(getattr(agency, "timezone", None))


def format_slot_es(when_local: datetime) -> str:
    """"martes 28 de julio a las 10:00" — same spelling as the prompt clock."""
    return (
        f"{WEEKDAYS_ES[when_local.weekday()]} {when_local.day} de "
        f"{MONTHS_ES[when_local.month - 1]} a las {when_local:%H:%M}"
    )


def describe_business_hours(business_hours: dict | None) -> str:
    """"de lunes a viernes, de 09:00 a 19:00" — for the message to the model."""
    from app.services.conversation_service import DEFAULT_BUSINESS_HOURS

    raw = business_hours if isinstance(business_hours, dict) else DEFAULT_BUSINESS_HOURS
    try:
        start = str(raw["start"])
        end = str(raw["end"])
        days = sorted({int(d) for d in raw["days"]})
    except Exception:
        raw = DEFAULT_BUSINESS_HOURS
        start = str(raw["start"])
        end = str(raw["end"])
        days = sorted(int(d) for d in raw["days"])

    if not days:
        return "sin horario de atención configurado"
    names = [WEEKDAYS_ES[d] for d in days if 0 <= d <= 6]
    if not names:
        return "sin horario de atención configurado"
    if len(names) > 1 and days[-1] - days[0] == len(days) - 1:
        when = f"de {names[0]} a {names[-1]}"
    elif len(names) == 1:
        when = f"solo los {names[0]}"
    else:
        when = ", ".join(names[:-1]) + f" y {names[-1]}"
    return f"{when}, de {start} a {end}"


def collides(candidate: datetime, busy: list[datetime], minutes: int) -> bool:
    """True when `candidate` falls within `minutes` of any busy slot."""
    window = minutes * 60
    for other in busy:
        if other.tzinfo is None:
            other = other.replace(tzinfo=UTC)
        if abs((candidate - other).total_seconds()) < window:
            return True
    return False


def slot_is_valid(
    candidate: datetime,
    *,
    tz: tzinfo,
    business_hours: dict | None,
    duration_minutes: int,
) -> bool:
    """Start and end both inside the agency's office hours.

    The end instant is checked one minute early on purpose. `is_within_business
    _hours` is a half-open check (`start <= t < end`) because it answers "is
    someone in the office *right now*"; applied literally to the end of a
    visit it would reject 18:30-19:00 for an agency that closes at 19:00 and
    then tell the client that 18:30 "cae fuera del horario de la agencia
    (09:00 a 19:00)" — a visibly false statement, and one lost slot every
    single day. A visit that *ends* exactly at closing time is fine; one that
    ends at 19:15 is not.
    """
    from app.services.conversation_service import is_within_business_hours

    local_start = candidate.astimezone(tz)
    local_end = (
        candidate + timedelta(minutes=max(duration_minutes, 1)) - timedelta(minutes=1)
    ).astimezone(tz)
    return is_within_business_hours(
        local_start, business_hours
    ) and is_within_business_hours(local_end, business_hours)


def suggest_visit_slots(
    *,
    desired: datetime,
    now: datetime,
    tz: tzinfo,
    business_hours: dict | None,
    busy: list[datetime],
    duration_minutes: int = 30,
    count: int = VISIT_ALTERNATIVES,
) -> list[datetime]:
    """The next `count` bookable slots at or after `desired`.

    Pure (given a timezone): walks forward in 30-minute steps, skipping
    anything outside office hours or too close to an existing visit. Returns
    fewer than `count` — possibly none — when the next two weeks are full.
    """
    earliest = max(desired, now + timedelta(minutes=VISIT_MIN_LEAD_MINUTES))
    step = timedelta(minutes=VISIT_SLOT_STEP_MINUTES)

    # Round up to the next half hour in the agency's own timezone, so slots
    # land on :00 / :30 local instead of on a UTC offset artefact.
    local = earliest.astimezone(tz)
    floor = local.replace(second=0, microsecond=0) - timedelta(
        minutes=local.minute % VISIT_SLOT_STEP_MINUTES
    )
    candidate = floor if floor >= earliest else floor + step

    out: list[datetime] = []
    steps = int(VISIT_SEARCH_DAYS * 24 * 60 / VISIT_SLOT_STEP_MINUTES)
    for _ in range(steps):
        if slot_is_valid(
            candidate, tz=tz, business_hours=business_hours, duration_minutes=duration_minutes
        ) and not collides(candidate, busy, VISIT_COLLISION_MINUTES):
            out.append(candidate)
            if len(out) >= count:
                break
            # Don't offer two adjacent slots: they'd read as the same time.
            # A whole number of steps, so the offers stay on :00 / :30.
            candidate += timedelta(minutes=VISIT_SLOT_GAP_MINUTES)
            continue
        candidate += step
    return out


def render_slot_alternatives(reason: str, slots: list[datetime], tz: tzinfo) -> str:
    """The tool_result the model gets back when the requested time is invalid.

    Never an exception: the model has to be able to keep the conversation
    going, and the only way it can do that is with concrete alternatives.
    """
    if not slots:
        return (
            f"NO AGENDADA: {reason} No he encontrado ningún hueco válido en los próximos "
            f"{VISIT_SEARCH_DAYS} días. No inventes una hora: dile al cliente qué horario tiene "
            "la agencia, pregúntale qué día le vendría bien y avísale de que lo confirma un "
            "agente del equipo."
        )
    lines = [
        f"- {format_slot_es(s.astimezone(tz))} → {s.astimezone(tz).isoformat(timespec='minutes')}"
        for s in slots
    ]
    return (
        f"NO AGENDADA: {reason} No inventes otro horario. Ofrécele al cliente estas opciones "
        "y, cuando elija una, vuelve a llamar a `schedule_visit` con esa fecha exacta:\n"
        + "\n".join(lines)
    )


async def _busy_slots(
    ctx: AgentContext,
    *,
    agent_id: uuid.UUID | None,
    since: datetime,
    until: datetime,
) -> list[datetime]:
    """Visits already occupying the lead's or the assigned agent's calendar."""
    who = [Visit.lead_id == ctx.lead_id]
    if agent_id is not None:
        who.append(Visit.assigned_user_id == agent_id)
    stmt = select(Visit.scheduled_at).where(
        Visit.agency_id == ctx.agency_id,
        Visit.status.in_(BOOKED_VISIT_STATUSES),
        Visit.scheduled_at >= since,
        Visit.scheduled_at <= until,
        or_(*who),
    )
    return [row for (row,) in (await ctx.db.execute(stmt)).all()]


async def _schedule_visit(args: dict, ctx: AgentContext) -> str:
    try:
        property_id = uuid.UUID(args["property_id"])
    except (ValueError, KeyError):
        return "ERROR: property_id inválido o ausente."

    try:
        scheduled_at = datetime.fromisoformat(args["scheduled_at_iso"])
    except (ValueError, KeyError):
        return "ERROR: scheduled_at_iso inválido. Usa formato ISO 8601."

    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=UTC)

    now = datetime.now(UTC)
    if scheduled_at < now + timedelta(minutes=VISIT_MIN_LEAD_MINUTES):
        return (
            f"ERROR: la visita debe ser al menos {VISIT_MIN_LEAD_MINUTES} minutos en el futuro."
        )

    prop = await ctx.db.get(Property, property_id)
    if prop is None or prop.agency_id != ctx.agency_id:
        return "ERROR: la propiedad no existe en esta agencia."

    # Clamp: a model that sends 0 or -30 would otherwise store a nonsense
    # duration and make every office-hours check meaningless.
    duration = min(max(int(args.get("duration_minutes", 30) or 30), 15), 240)
    notes = args.get("notes", "")

    # Pick the closest active agent (Sprint 4: geographic routing). Done
    # before validating: the agent's own agenda is part of what we validate.
    agent = await pick_agent_for_visit(ctx.db, agency_id=ctx.agency_id, property_=prop)

    # (221) Office hours + collisions. Both checks answer with alternatives
    # instead of an error the model can only apologise for.
    agency = await ctx.db.get(Agency, ctx.agency_id)
    business_hours = (getattr(agency, "settings", None) or {}).get("business_hours")
    tz, _tz_name = _agency_timezone(agency)
    # The window must cover the requested time *and* the fortnight we search
    # for alternatives in. Loading only `now .. now+14d` meant a visit asked
    # for three weeks out was compared against an empty agenda and happily
    # double-booked the same agent — the exact failure this item exists to
    # prevent. The lower bound reaches back one collision window: a visit that
    # started 10 minutes ago still blocks a visit 30 minutes from now.
    busy = await _busy_slots(
        ctx,
        agent_id=getattr(agent, "id", None),
        since=now - timedelta(minutes=VISIT_COLLISION_MINUTES),
        until=max(now, scheduled_at) + timedelta(days=VISIT_SEARCH_DAYS),
    )

    invalid_reason: str | None = None
    if not slot_is_valid(
        scheduled_at, tz=tz, business_hours=business_hours, duration_minutes=duration
    ):
        invalid_reason = (
            f"esa hora cae fuera del horario de la agencia ({describe_business_hours(business_hours)})."
        )
    elif collides(scheduled_at, busy, VISIT_COLLISION_MINUTES):
        invalid_reason = (
            "a esa hora ya hay otra visita del cliente o del agente que la atendería "
            f"(hacen falta {VISIT_COLLISION_MINUTES} minutos entre visitas)."
        )

    if invalid_reason is not None:
        slots = suggest_visit_slots(
            desired=scheduled_at,
            now=now,
            tz=tz,
            business_hours=business_hours,
            busy=busy,
            duration_minutes=duration,
        )
        log.info(
            "schedule_visit.rejected",
            lead=str(ctx.lead_id),
            requested=scheduled_at.isoformat(),
            alternatives=len(slots),
        )
        return render_slot_alternatives(invalid_reason, slots, tz)

    calendar_event_id: str | None = None
    if agent is not None and agent.google_refresh_token and agent.google_calendar_id:
        try:
            event = create_visit_event(
                refresh_token=agent.google_refresh_token,
                calendar_id=agent.google_calendar_id,
                summary=f"Visita {prop.reference} — {prop.title}",
                description=(
                    f"Lead ID: {ctx.lead_id}\n"
                    f"Propiedad: {prop.title} ({prop.reference})\n"
                    f"Notas: {notes}"
                ),
                start=scheduled_at,
                duration_minutes=duration,
                location=prop.address,
            )
            calendar_event_id = event.get("id")
        except Exception:
            log.exception("schedule_visit.calendar_failed")

    visit = Visit(
        agency_id=ctx.agency_id,
        lead_id=ctx.lead_id,
        property_id=property_id,
        assigned_user_id=agent.id if agent else None,
        scheduled_at=scheduled_at,
        duration_minutes=duration,
        status=VisitStatus.REQUESTED,
        post_visit_notes="",
        pre_visit_briefing=notes,
        calendar_event_id=calendar_event_id,
    )
    ctx.db.add(visit)

    lead = await ctx.db.get(Lead, ctx.lead_id)
    if lead is not None:
        lead.status = LeadStatus.VISIT_SCHEDULED
        mark_temperature_hot(lead, source="schedule_visit")
        existing = lead.interested_property_ids or []
        if str(property_id) not in existing:
            lead.interested_property_ids = [*existing, str(property_id)]

    await ctx.db.flush()
    return (
        f"OK: visita propuesta para {scheduled_at.isoformat()} en {prop.address}. "
        f"Confirma con el cliente y avísale de que recibirá confirmación por escrito."
    )


# (213) Where the handoff context lives. The tool schema promises the human
# will read `reason`; until now it was thrown away and the agency got a bare
# "un lead necesita atención".
HANDOFF_REASON_KEY = "handoff_reason"
HANDOFF_URGENCY_KEY = "handoff_urgency"
HANDOFF_AT_KEY = "handoff_at"
MAX_HANDOFF_REASON_CHARS = 400


async def _escalate_to_human(args: dict, ctx: AgentContext) -> str:
    conv = await ctx.db.get(Conversation, ctx.conversation_id)
    if conv is None:
        return "ERROR: conversación no encontrada."
    conv.status = ConversationStatus.HANDED_OFF

    urgency = args.get("urgency")
    if urgency not in ("low", "normal", "high"):
        urgency = "normal"
    reason = " ".join(str(args.get("reason") or "").split())[:MAX_HANDOFF_REASON_CHARS]

    # Reassign the whole dict: SQLAlchemy does not track in-place JSON edits.
    conv.extra = {
        **(conv.extra or {}),
        HANDOFF_REASON_KEY: reason,
        HANDOFF_URGENCY_KEY: urgency,
        HANDOFF_AT_KEY: datetime.now(UTC).isoformat(),
    }

    lead = await ctx.db.get(Lead, ctx.lead_id)
    if lead is not None and urgency == "high":
        mark_temperature_hot(lead, source="escalate_to_human")

    await ctx.db.flush()
    return (
        "OK: la conversación se ha marcado como derivada a humano. Despídete con calidez "
        "indicando que un agente contactará en breve. No sigas respondiendo después de esto."
    )


async def _update_lead_temperature(args: dict, ctx: AgentContext) -> str:
    lead = await ctx.db.get(Lead, ctx.lead_id)
    if lead is None:
        return "ERROR: lead no encontrado."
    try:
        temp = LeadTemperature(args["temperature"])
    except (KeyError, ValueError):
        return "ERROR: temperatura inválida."

    # (165) HOT set from the conversation is sticky for 48h; an explicit
    # downgrade by the agent clears that mark straight away.
    if temp == LeadTemperature.HOT:
        mark_temperature_hot(lead, source="update_lead_temperature")
    else:
        lead.temperature = temp
        clear_hot_mark(lead)

    if reasoning := args.get("reasoning"):
        lead.qualification_notes = (
            lead.qualification_notes + f"\n[temp={temp.value}] {reasoning}"
        ).strip()

    await ctx.db.flush()
    return f"OK: temperatura del lead actualizada a {temp.value}."


async def _send_property_card(args: dict, ctx: AgentContext) -> str:
    """Defer the heavy PDF render to a Celery task; return a stable URL."""
    try:
        property_id = uuid.UUID(args["property_id"])
    except (KeyError, ValueError):
        return "ERROR: property_id inválido."

    prop = await ctx.db.get(Property, property_id)
    if prop is None or prop.agency_id != ctx.agency_id:
        return "ERROR: propiedad no encontrada en esta agencia."

    lead = await ctx.db.get(Lead, ctx.lead_id)
    if lead is None:
        return "ERROR: lead no encontrado."

    bullets = await reasons_for_match(ctx.db, lead=lead, property_=prop)

    # Trigger background PDF generation (async dispatch — don't block the agent
    # turn). We import here to avoid a circular import at module load.
    try:
        from app.workers.tasks import generate_property_card_pdf
        generate_property_card_pdf.delay(str(lead.id), str(prop.id))
    except Exception:
        log.exception("send_property_card.dispatch_failed")

    bullet_text = "\n".join(f"- {b}" for b in bullets) if bullets else "(sin razones generadas)"
    return (
        f"OK: ficha en preparación para {prop.title} ({prop.reference}). "
        f"En cuanto termine la generación recibirá el PDF. "
        f"Razones de encaje (úsalas en el mensaje al cliente):\n{bullet_text}"
    )


async def _mortgage_quote(args: dict, ctx: AgentContext) -> str:
    try:
        q = mortgage_quote(
            price_eur=int(args["price_eur"]),
            down_payment_pct=float(args.get("down_payment_pct", 20.0)),
            annual_rate_pct=float(args.get("annual_rate_pct", 3.5)),
            years=int(args.get("years", 25)),
        )
    except (KeyError, ValueError) as exc:
        return f"ERROR: {exc}"
    return (
        f"Hipoteca estimada (informativa): cuota ~{q.monthly_payment_eur:,.0f} €/mes, "
        f"a {q.years} años, importe financiado {q.loan_amount_eur:,.0f} €, "
        f"intereses totales {q.total_interest_eur:,.0f} €. Aclara al cliente que es informativo "
        f"y depende del banco."
    )


# (218) A lead who has shared income and savings to size a mortgage is in the
# most valuable conversation the agency will have this month. We record it on
# the lead so the runner can route those turns to the smart model.
MORTGAGE_PREAPPROVAL_EXTRA_KEY = "mortgage_preapproval"


async def _mortgage_preapproval(args: dict, ctx: AgentContext) -> str:
    try:
        result = await request_preapproval(
            monthly_income_eur=int(args["monthly_income_eur"]),
            other_monthly_debts_eur=int(args.get("other_monthly_debts_eur", 0)),
            savings_eur=int(args["savings_eur"]),
            has_indefinite_contract=bool(args.get("has_indefinite_contract", True)),
            age=int(args["age"]),
            target_property_price_eur=args.get("target_property_price_eur"),
        )
    except (KeyError, ValueError) as exc:
        return f"ERROR: {exc}"

    # Only a viable pre-approval counts as "in play": a 0 € ceiling means the
    # banks said no, and paying for the expensive model there buys nothing.
    if result.indicative_max_loan_eur > 0:
        lead = await ctx.db.get(Lead, ctx.lead_id)
        if lead is not None:
            lead.extra = {
                **(lead.extra or {}),
                MORTGAGE_PREAPPROVAL_EXTRA_KEY: {
                    "requested_at": datetime.now(UTC).isoformat(),
                    "confidence": result.confidence,
                    "indicative_max_loan_eur": result.indicative_max_loan_eur,
                },
            }
            await ctx.db.flush()

    notes = " ".join(result.notes)
    return (
        f"Pre-aprobación INDICATIVA: importe máximo ~{result.indicative_max_loan_eur:,.0f} €, "
        f"cuota máx asumible ~{result.indicative_monthly_payment_eur:,.0f} €/mes, "
        f"confianza {result.confidence}. {notes} "
        f"Para tramitar de verdad: {result.partner_deep_link}"
    )


async def _purchase_costs(args: dict, ctx: AgentContext) -> str:
    try:
        c = total_purchase_cost(
            price_eur=int(args["price_eur"]),
            region=str(args["region"]),
            new_build=bool(args.get("new_build", False)),
            needs_mortgage=bool(args.get("needs_mortgage", True)),
        )
    except (KeyError, ValueError) as exc:
        return f"ERROR: {exc}"
    notes = " ".join(c.notes)
    return (
        f"Gastos compra estimados ({c.region}, {c.operation_type}): "
        f"impuesto {c.tax_eur:,.0f} €, notaría {c.notary_eur:,.0f} €, "
        f"registro {c.registry_eur:,.0f} €, gestoría {c.gestoria_eur:,.0f} €, "
        f"tasación {c.appraisal_eur:,.0f} €. "
        f"TOTAL desembolso: {c.total_eur:,.0f} € (precio + ~{c.total_extras_eur + c.tax_eur:,.0f} € extras). "
        f"{notes}"
    )


# Suppress unused-import warnings for re-exports
_ = and_
_ = EMBEDDING_DIM
_ = UserRole
