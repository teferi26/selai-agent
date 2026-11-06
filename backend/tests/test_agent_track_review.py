"""Adversarial review of the AI-agent track (161, 163, 165, 174).

Complements `test_prompt_now.py`, `test_seller_lead.py` and `test_hot_sticky.py`
by attacking the edges those files leave open: a machine with no timezone
database, a re-capture that would erase what we already knew, and the two
alerts (hot + capture) racing to notify the same agency twice for one message.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.agent import runner as runner_mod
from app.agent.prompts import (
    FIRST_MESSAGE_DISCLOSURE_HEADER,
    NOW_HEADER,
    PROMPT_VERSION,
    build_now_line,
    render_setter_prompt,
)
from app.agent.runner import (
    CHANNELS_WITH_SCRIPTED_INTRO,
    _agency_now_line,
    _count_ai_messages,
)
from app.agent.tools import AgentContext, execute_tool
from app.db.models import (
    Agency,
    ConversationChannel,
    ConversationStatus,
    Lead,
    LeadStatus,
    LeadTemperature,
)
from app.services import conversation_service as cs
from app.services.lead_scoring import (
    STICKY_HOT_LABEL,
    hot_mark_is_active,
    recompute_lead_score,
)

# ---------------------------------------------------------------------------
# Shared doubles
# ---------------------------------------------------------------------------


def _lead(**overrides) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "agency_id": uuid.uuid4(),
        "full_name": "Marta López",
        "phone_e164": "+34600111222",
        "email": None,
        "budget_min": None,
        "budget_max": None,
        "preferred_cities": None,
        "preferred_neighborhoods": None,
        "min_bedrooms": None,
        "min_bathrooms": None,
        "min_area_sqm": None,
        "operation": None,
        "must_haves": None,
        "interested_property_ids": None,
        "language": "es",
        "score": 0,
        "status": LeadStatus.NEW,
        "temperature": LeadTemperature.COLD,
        "extra": {},
        "qualification_notes": "",
    }
    return SimpleNamespace(**{**base, **overrides})


class _ToolDB:
    def __init__(self, lead):
        self.lead = lead
        self.flushes = 0

    async def get(self, model, pk):
        return self.lead if model is Lead else None

    async def flush(self):
        self.flushes += 1


def _ctx(db, lead, channel="whatsapp") -> AgentContext:
    return AgentContext(
        db=db,
        agency_id=uuid.uuid4(),
        lead_id=lead.id,
        conversation_id=uuid.uuid4(),
        channel=channel,
    )


def _hours_ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


class _ScoreDB:
    """`recompute_lead_score` needs get() plus two empty result sets."""

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    def __init__(self, lead):
        self.lead = lead

    async def get(self, model, pk):
        return self.lead

    async def execute(self, stmt):
        return self._Result()


# ---------------------------------------------------------------------------
# (161) A broken or absent timezone database must never take the agent down
# ---------------------------------------------------------------------------


def _explode(*_args, **_kwargs):
    raise KeyError("No time zone found with key")


def test_now_line_survives_a_machine_without_a_timezone_database(monkeypatch):
    """`python:3.12-slim` ships no tzdata and it is not a declared dependency:
    if the fallback timezone also fails to load we must degrade to UTC, not 500.
    """
    monkeypatch.setattr(runner_mod, "ZoneInfo", _explode)
    line = _agency_now_line(SimpleNamespace(timezone="Europe/Madrid"))
    assert line.startswith("Ahora mismo es ")
    assert "(UTC)" in line


def test_agency_now_survives_a_machine_without_a_timezone_database(monkeypatch):
    monkeypatch.setattr(cs, "ZoneInfo", _explode)
    now = cs._agency_now(SimpleNamespace(timezone="Europe/Madrid"))
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_now_line_always_carries_an_explicit_utc_offset():
    """`schedule_visit` reads a naive ISO string as UTC, so an offset-less
    reference would silently shift every visit by the agency's offset."""
    for tz_name in ("Europe/Madrid", "America/New_York", "Atlantic/Canary"):
        line = _agency_now_line(SimpleNamespace(timezone=tz_name))
        iso = line.rsplit("Referencia ISO 8601: ", 1)[1].rstrip(".")
        assert re.search(r"[+-]\d{2}:\d{2}$|Z$", iso), iso
        assert datetime.fromisoformat(iso).tzinfo is not None


def test_now_line_falls_back_when_the_agency_timezone_is_whitespace():
    assert "(Europe/Madrid)" in _agency_now_line(SimpleNamespace(timezone="   "))


# ---------------------------------------------------------------------------
# Prompt integrity — no placeholder may reach the model unformatted
# ---------------------------------------------------------------------------


_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _render(**overrides) -> str:
    base = {
        "agency_name": "Inmo Sol",
        "channel": "whatsapp",
        "lead_summary": "Nombre: Juan",
        "agency_context": "Pisos en Madrid",
    }
    return render_setter_prompt(**{**base, **overrides})


def test_no_combination_leaves_an_unformatted_placeholder():
    now_line = build_now_line(datetime.now(UTC), "UTC")
    for first in (True, False):
        for line in (now_line, None):
            for tone in ("formal", "cercano", None):
                prompt = _render(
                    is_first_agent_message=first, now_line=line, ai_tone=tone
                )
                assert not _PLACEHOLDER.search(prompt), prompt
                assert "{agency_name}" not in prompt
                assert "{now_line}" not in prompt


def test_a_forbidden_phrase_with_braces_does_not_break_rendering():
    prompt = _render(forbidden_phrases=["te lo dejo en {precio} sin comisión"])
    assert "te lo dejo en {precio} sin comisión" in prompt


def test_prompt_version_moved_past_v4():
    assert re.fullmatch(r"v\d+", PROMPT_VERSION)
    assert int(PROMPT_VERSION[1:]) >= 5


# ---------------------------------------------------------------------------
# (174) Identify once — not never, not every message
# ---------------------------------------------------------------------------


def test_the_disclosure_directive_appears_exactly_once():
    prompt = _render(is_first_agent_message=True)
    assert prompt.count(FIRST_MESSAGE_DISCLOSURE_HEADER) == 1


def test_the_standing_rule_survives_without_the_first_message_directive():
    """Turn 2+ drops the "introduce yourself" directive but must keep the
    "never deny you are an AI" rule (AI Act art. 50 applies all conversation)."""
    prompt = _render(is_first_agent_message=False)
    assert FIRST_MESSAGE_DISCLOSURE_HEADER not in prompt
    assert "Nunca ocultes ni niegues que eres una IA" in prompt


def test_voice_is_the_only_channel_with_a_scripted_intro():
    assert set(CHANNELS_WITH_SCRIPTED_INTRO) == {ConversationChannel.VOICE.value}
    for channel in (ConversationChannel.WHATSAPP, ConversationChannel.EMAIL):
        assert channel.value not in CHANNELS_WITH_SCRIPTED_INTRO


async def test_the_truncated_history_recount_is_tenant_scoped():
    """Multi-tenant rule: every query filters by agency_id."""
    captured = {}

    class _CountDB:
        async def execute(self, stmt):
            captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": False}))
            return SimpleNamespace(scalar_one=lambda: 3)

    agency_id = uuid.uuid4()
    n = await _count_ai_messages(_CountDB(), uuid.uuid4(), agency_id)
    assert n == 3
    assert "agency_id" in captured["sql"]


# ---------------------------------------------------------------------------
# (165) Sticky hot pins the floor, never the ceiling, and always expires
# ---------------------------------------------------------------------------


async def test_the_mark_never_blocks_a_genuine_rise_to_hot():
    lead = _lead(
        extra={"hot_marked_at": _hours_ago(1)},
        budget_max=400000,
        preferred_cities=["Madrid"],
        min_bedrooms=3,
        operation="sale",
        email="m@e.com",
        must_haves=["parking"],
        status=LeadStatus.NEGOTIATING,
    )
    breakdown = await recompute_lead_score(_ScoreDB(lead), lead.id)
    assert breakdown.temperature == "hot"
    assert breakdown.total >= 70
    # No 0-point filler when the score already earned "hot" on its own.
    assert all(c.label != STICKY_HOT_LABEL for c in breakdown.components)


async def test_the_mark_stops_pinning_once_it_expires_even_after_many_recomputes():
    lead = _lead(temperature=LeadTemperature.HOT, extra={"hot_marked_at": _hours_ago(49)})
    for _ in range(3):
        breakdown = await recompute_lead_score(_ScoreDB(lead), lead.id)
        assert breakdown.temperature == "cold"
        assert lead.temperature == LeadTemperature.COLD


async def test_a_warm_score_under_an_active_mark_is_pinned_hot_without_faking_points():
    lead = _lead(
        extra={"hot_marked_at": _hours_ago(1)},
        budget_max=300000,
        preferred_cities=["Madrid"],
        min_bedrooms=2,
        operation="sale",
    )
    breakdown = await recompute_lead_score(_ScoreDB(lead), lead.id)
    assert breakdown.temperature == "hot"
    assert breakdown.total < 70
    sticky = [c for c in breakdown.components if c.label == STICKY_HOT_LABEL]
    assert len(sticky) == 1 and sticky[0].points == 0


# ---------------------------------------------------------------------------
# (163) save_seller_lead must never destroy what we already knew
# ---------------------------------------------------------------------------


async def test_save_seller_lead_without_property_type_saves_nothing():
    lead = _lead()
    db = _ToolDB(lead)
    result = await execute_tool(
        "save_seller_lead", {"zone": "Chamberí", "property_type": "  "}, _ctx(db, lead)
    )
    assert result.startswith("ERROR")
    assert "seller_lead" not in lead.extra
    assert lead.temperature == LeadTemperature.COLD
    assert hot_mark_is_active(lead) is False
    assert db.flushes == 0


async def test_a_second_capture_does_not_erase_the_price_we_already_had():
    """The tool description invites a second call once urgency is known; that
    call carries no price, and the first one must not be forgotten."""
    lead = _lead()
    ctx = _ctx(_ToolDB(lead), lead)
    await execute_tool(
        "save_seller_lead",
        {
            "zone": "Chamberí",
            "property_type": "piso",
            "expected_price": 420000,
            "notes": "90 m2, reformado",
        },
        ctx,
    )
    await execute_tool(
        "save_seller_lead",
        {"zone": "Chamberí", "property_type": "piso", "urgency": "high"},
        ctx,
    )
    seller = lead.extra["seller_lead"]
    assert seller["expected_price"] == 420000
    assert seller["notes"] == "90 m2, reformado"
    assert seller["urgency"] == "high"


async def test_a_second_capture_can_still_correct_a_previous_value():
    lead = _lead()
    ctx = _ctx(_ToolDB(lead), lead)
    await execute_tool(
        "save_seller_lead",
        {"zone": "Chamberí", "property_type": "piso", "expected_price": 420000},
        ctx,
    )
    await execute_tool(
        "save_seller_lead",
        {"zone": "Chamberí", "property_type": "ático", "expected_price": 390000},
        ctx,
    )
    seller = lead.extra["seller_lead"]
    assert seller["property_type"] == "ático"
    assert seller["expected_price"] == 390000


async def test_a_nonsense_negative_price_is_dropped_not_shown_to_the_agency():
    lead = _lead()
    await execute_tool(
        "save_seller_lead",
        {"zone": "Chamberí", "property_type": "piso", "expected_price": -1},
        _ctx(_ToolDB(lead), lead),
    )
    assert lead.extra["seller_lead"]["expected_price"] is None


async def test_capturing_an_already_qualified_lead_does_not_walk_its_status_back():
    lead = _lead(status=LeadStatus.QUALIFIED, temperature=LeadTemperature.HOT)
    await execute_tool(
        "save_seller_lead",
        {"zone": "Chamberí", "property_type": "piso"},
        _ctx(_ToolDB(lead), lead),
    )
    assert lead.status == LeadStatus.QUALIFIED
    assert lead.temperature == LeadTemperature.HOT


# ---------------------------------------------------------------------------
# handle_inbound — one message must not produce two alerts
# ---------------------------------------------------------------------------


@pytest.fixture
def inbound(monkeypatch):
    """Drives `handle_inbound` with everything external stubbed out."""
    pushes: list[dict] = []
    emails: list[dict] = []

    async def _fake_push(db, agency_id, title, body, data=None):
        pushes.append({"title": title, "body": body})
        return 1

    async def _fake_email(to, subject, html):
        emails.append({"to": to, "subject": subject})
        return True

    monkeypatch.setattr("app.services.push.send_push", _fake_push)
    monkeypatch.setattr("app.services.emails.send_email", _fake_email)
    monkeypatch.setattr(cs, "quick_heuristic_score", lambda _texts: 0)
    monkeypatch.setattr(cs, "is_foreign_buyer_signal", lambda *_a, **_k: False)
    monkeypatch.setattr(cs, "detect_language", lambda *_a, **_k: "es")

    async def _no_emit(*_a, **_k):
        return None

    monkeypatch.setattr(cs, "emit", _no_emit)

    agency = SimpleNamespace(
        id=uuid.uuid4(),
        name="Inmo Sol",
        contact_email="dueno@inmosol.example.com",
        timezone="Europe/Madrid",
        settings={},
    )

    class _Session:
        def add(self, _obj):
            return None

        async def flush(self):
            return None

        async def get(self, model, pk):
            return agency if model is Agency else None

    def _run(*, lead, agent_turn, recomputed_temperature):
        created = datetime.now(UTC)
        conv = SimpleNamespace(
            id=uuid.uuid4(),
            channel=ConversationChannel.WHATSAPP,
            status=ConversationStatus.ACTIVE,
            lead_id=lead.id,
            agency_id=agency.id,
        )
        lead.created_at = created
        lead.updated_at = created + timedelta(minutes=5)  # not a brand new lead

        async def _resolve(db, agency_id, identity):
            return lead, conv

        async def _run_agent(*, db, conversation_id, use_smart_model=False):
            agent_turn(lead)
            # El dataclass real, no un SimpleNamespace: así este doble no
            # puede quedarse atrás cuando AgentReply gane un campo (pasó con
            # `failed`, el aviso de turno fallido).
            from app.agent.runner import AgentReply

            return AgentReply(
                text="Vale, te llamamos.", tool_calls=[], handed_off=False, run_id=uuid.uuid4()
            )

        async def _recompute(db, lead_id):
            lead.temperature = recomputed_temperature
            return None

        monkeypatch.setattr(cs, "resolve_lead_and_conversation", _resolve)
        monkeypatch.setattr(cs, "run_agent", _run_agent)
        monkeypatch.setattr(cs, "recompute_lead_score", _recompute)

        payload = cs.InboundPayload(
            identity=SimpleNamespace(),
            agency_id=agency.id,
            text="Hola, quiero vender mi piso en Chamberí",
        )
        return payload, _Session()

    return SimpleNamespace(pushes=pushes, emails=emails, run=_run, agency=agency)


async def test_a_captured_seller_alerts_the_agency_once_not_twice(inbound):
    """`save_seller_lead` pins the lead HOT, so without care the agency gets
    both the capture alert and the generic hot-lead alert for one message."""
    lead = _lead()

    def _turn(lead_):
        lead_.extra = {
            **lead_.extra,
            "seller_lead": {"zone": "Chamberí", "property_type": "piso"},
        }
        lead_.temperature = LeadTemperature.HOT

    payload, session = inbound.run(
        lead=lead, agent_turn=_turn, recomputed_temperature=LeadTemperature.HOT
    )
    await cs.handle_inbound(session, payload)

    assert len(inbound.pushes) == 1, inbound.pushes
    assert inbound.pushes[0]["title"].startswith("Oportunidad de captación")
    assert len(inbound.emails) == 1, inbound.emails
    assert inbound.emails[0]["subject"].startswith("Oportunidad de captación")


async def test_a_second_message_from_the_same_seller_alerts_nobody(inbound):
    lead = _lead(
        temperature=LeadTemperature.HOT,
        extra={"seller_lead": {"zone": "Chamberí", "property_type": "piso"}},
    )

    def _turn(lead_):
        lead_.extra = {
            **lead_.extra,
            "seller_lead": {"zone": "Chamberí", "property_type": "piso", "urgency": "high"},
        }

    payload, session = inbound.run(
        lead=lead, agent_turn=_turn, recomputed_temperature=LeadTemperature.HOT
    )
    await cs.handle_inbound(session, payload)

    assert inbound.pushes == []
    assert inbound.emails == []


async def test_a_buyer_turning_hot_still_gets_exactly_one_alert(inbound):
    lead = _lead(temperature=LeadTemperature.WARM)

    def _turn(lead_):
        lead_.temperature = LeadTemperature.HOT

    payload, session = inbound.run(
        lead=lead, agent_turn=_turn, recomputed_temperature=LeadTemperature.HOT
    )
    await cs.handle_inbound(session, payload)

    assert len(inbound.pushes) == 1
    assert "Lead caliente" in inbound.pushes[0]["title"]
    assert len(inbound.emails) == 1


async def test_an_already_hot_lead_is_not_re_announced_every_message(inbound):
    lead = _lead(temperature=LeadTemperature.HOT)

    payload, session = inbound.run(
        lead=lead,
        agent_turn=lambda _l: None,
        recomputed_temperature=LeadTemperature.HOT,
    )
    await cs.handle_inbound(session, payload)

    assert inbound.pushes == []
    assert inbound.emails == []


def test_the_prompt_time_block_precedes_the_lead_data():
    prompt = _render(now_line="Ahora mismo es lunes", is_first_agent_message=True)
    assert prompt.index(FIRST_MESSAGE_DISCLOSURE_HEADER) < prompt.index(NOW_HEADER)
    assert prompt.index(NOW_HEADER) < prompt.index("DATOS DEL LEAD")


# ---------------------------------------------------------------------------
# (n33) Un turno fallido avisa a la agencia sin secuestrar la conversación
# ---------------------------------------------------------------------------


def test_a_failed_turn_is_marked_as_failed_but_not_as_handed_off():
    """El 400 de Anthropic dejaba al lead con una disculpa genérica y a la
    agencia sin enterarse: el fallo solo se veía en Sentry.

    `failed` NO es `handed_off` a propósito: un despliegue malo convertiría
    todas las conversaciones vivas en "necesita a alguien" de golpe.
    """
    from app.agent.runner import AgentReply

    fallido = AgentReply(text="Disculpa, no he podido procesar ese mensaje.", failed=True)
    assert fallido.failed is True
    assert fallido.handed_off is False

    normal = AgentReply(text="¡Hola! ¿En qué te ayudo?")
    assert normal.failed is False
