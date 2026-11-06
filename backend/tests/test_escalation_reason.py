"""(213) The handoff reason survives the tool call and reaches the human.

The tool schema promises "lo verá el agente humano" but `args["reason"]` was
dropped on the floor: the agency got "Un lead necesita atención" and had to
call back cold, sometimes to an angry client.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.agent.tools import (
    HANDOFF_AT_KEY,
    HANDOFF_REASON_KEY,
    HANDOFF_URGENCY_KEY,
    MAX_HANDOFF_REASON_CHARS,
    AgentContext,
    execute_tool,
)
from app.db.models import (
    Agency,
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Lead,
    LeadStatus,
    LeadTemperature,
)
from app.services import conversation_service as cs
from app.services.conversation_service import (
    DEFAULT_HANDOFF_REASON,
    build_handoff_summary,
    describe_lead_search,
    handoff_subject,
)


def _lead(**overrides) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "full_name": "Marta López",
        "phone_e164": "+34600111222",
        "email": None,
        "budget_min": None,
        "budget_max": 300000,
        "operation": "sale",
        "preferred_cities": ["Madrid"],
        "preferred_neighborhoods": ["Chamberí"],
        "min_bedrooms": 2,
        "min_bathrooms": None,
        "min_area_sqm": None,
        "must_haves": None,
        "deal_breakers": None,
        "detected_objections": None,
        "interested_property_ids": None,
        "language": "es",
        "score": 0,
        "status": LeadStatus.ENGAGED,
        "temperature": LeadTemperature.WARM,
        "extra": {},
        "qualification_notes": "",
    }
    return SimpleNamespace(**{**base, **overrides})


class _ToolDB:
    """`_escalate_to_human` looks up the conversation and then the lead."""

    def __init__(self, conv, lead):
        self.conv = conv
        self.lead = lead

    async def get(self, model, pk):
        if model is Conversation:
            return self.conv
        if model is Lead:
            return self.lead
        return None

    async def flush(self):
        return None


def _conv(**overrides) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "status": ConversationStatus.ACTIVE,
        "channel": ConversationChannel.WHATSAPP,
        "extra": {},
    }
    return SimpleNamespace(**{**base, **overrides})


def _ctx(db, lead, conv) -> AgentContext:
    return AgentContext(
        db=db,
        agency_id=uuid.uuid4(),
        lead_id=lead.id,
        conversation_id=conv.id,
        channel="whatsapp",
    )


# ---------------------------------------------------------------------------
# The tool persists what the human needs
# ---------------------------------------------------------------------------


async def test_the_reason_is_persisted_on_the_conversation():
    lead, conv = _lead(), _conv()
    db = _ToolDB(conv, lead)
    await execute_tool(
        "escalate_to_human",
        {"reason": "Pide hablar con una persona sobre la comisión", "urgency": "high"},
        _ctx(db, lead, conv),
    )
    assert conv.extra[HANDOFF_REASON_KEY] == "Pide hablar con una persona sobre la comisión"
    assert conv.extra[HANDOFF_URGENCY_KEY] == "high"
    assert conv.status is ConversationStatus.HANDED_OFF


async def test_the_handoff_is_timestamped():
    lead, conv = _lead(), _conv()
    await execute_tool(
        "escalate_to_human", {"reason": "Está molesto"}, _ctx(_ToolDB(conv, lead), lead, conv)
    )
    when = datetime.fromisoformat(conv.extra[HANDOFF_AT_KEY])
    assert abs((datetime.now(UTC) - when).total_seconds()) < 60


async def test_previous_conversation_extra_survives():
    """Whole-dict reassignment must not wipe the voice call records."""
    lead, conv = _lead(), _conv(extra={"calls": [{"id": "abc"}]})
    await execute_tool(
        "escalate_to_human", {"reason": "Quiere negociar"}, _ctx(_ToolDB(conv, lead), lead, conv)
    )
    assert conv.extra["calls"] == [{"id": "abc"}]
    assert conv.extra[HANDOFF_REASON_KEY] == "Quiere negociar"


async def test_a_novel_length_reason_is_truncated():
    lead, conv = _lead(), _conv()
    await execute_tool(
        "escalate_to_human", {"reason": "x" * 5000}, _ctx(_ToolDB(conv, lead), lead, conv)
    )
    assert len(conv.extra[HANDOFF_REASON_KEY]) == MAX_HANDOFF_REASON_CHARS


async def test_reason_whitespace_is_normalised():
    """A multi-line reason would break the push notification body."""
    lead, conv = _lead(), _conv()
    await execute_tool(
        "escalate_to_human",
        {"reason": "  Está   muy\nmolesto\ncon la tasación  "},
        _ctx(_ToolDB(conv, lead), lead, conv),
    )
    assert conv.extra[HANDOFF_REASON_KEY] == "Está muy molesto con la tasación"


@pytest.mark.parametrize("urgency", [None, "", "urgentísimo", 7])
async def test_an_invalid_urgency_falls_back_to_normal(urgency):
    lead, conv = _lead(), _conv()
    await execute_tool(
        "escalate_to_human",
        {"reason": "Pide un humano", "urgency": urgency},
        _ctx(_ToolDB(conv, lead), lead, conv),
    )
    assert conv.extra[HANDOFF_URGENCY_KEY] == "normal"
    assert lead.temperature is LeadTemperature.WARM  # only "high" pins it hot


async def test_high_urgency_still_pins_the_lead_hot():
    lead, conv = _lead(), _conv()
    await execute_tool(
        "escalate_to_human",
        {"reason": "Amenaza con reclamación", "urgency": "high"},
        _ctx(_ToolDB(conv, lead), lead, conv),
    )
    assert lead.temperature is LeadTemperature.HOT


async def test_a_missing_reason_does_not_break_the_escalation():
    """The model can call the tool badly; handing off still has to work."""
    lead, conv = _lead(), _conv()
    result = await execute_tool("escalate_to_human", {}, _ctx(_ToolDB(conv, lead), lead, conv))
    assert not result.startswith("ERROR")
    assert conv.status is ConversationStatus.HANDED_OFF
    assert conv.extra[HANDOFF_REASON_KEY] == ""


# ---------------------------------------------------------------------------
# The three lines a human needs before picking up the phone
# ---------------------------------------------------------------------------


def test_the_summary_answers_who_what_and_why():
    lines = build_handoff_summary(_lead(), "Quiere negociar el precio")
    assert len(lines) == 3
    assert lines[0].startswith("Quién:")
    assert "Marta López" in lines[0]
    assert "+34600111222" in lines[0]
    assert lines[1].startswith("Qué busca:")
    assert lines[2] == "Por qué escala: Quiere negociar el precio"


def test_the_summary_admits_it_when_there_is_no_reason():
    """The guardrail escalation path has no reason; we say so instead of
    inventing one."""
    lines = build_handoff_summary(_lead(), None)
    assert lines[2] == f"Por qué escala: {DEFAULT_HANDOFF_REASON}"


def test_the_summary_survives_a_lead_we_know_nothing_about():
    lead = _lead(
        full_name=None,
        phone_e164=None,
        email=None,
        budget_max=None,
        operation=None,
        preferred_cities=None,
        preferred_neighborhoods=None,
        min_bedrooms=None,
    )
    lines = build_handoff_summary(lead, "Pide un humano")
    assert lines[0] == "Quién: Lead sin nombre"
    assert lines[1] == "Qué busca: todavía no nos ha dicho qué busca"


def test_the_search_line_uses_the_data_we_have():
    line = describe_lead_search(_lead())
    assert "compra" in line
    assert "300.000 €" in line
    assert "Chamberí" in line
    assert "2+ hab" in line


def test_neighbourhoods_win_over_cities_because_they_are_more_precise():
    line = describe_lead_search(_lead(preferred_neighborhoods=["Salamanca"]))
    assert "Salamanca" in line
    assert "Madrid" not in line


def test_the_subject_says_why_so_the_email_is_useful_unopened():
    subject = handoff_subject(_lead(), "Pide hablar con una persona")
    assert subject == "Marta López necesita atención humana: Pide hablar con una persona"


def test_a_long_reason_is_trimmed_in_the_subject():
    subject = handoff_subject(_lead(), "porque " * 40)
    assert len(subject) < 120
    assert subject.endswith("…")


def test_the_subject_degrades_gracefully_without_a_reason():
    assert handoff_subject(_lead(), None) == "Marta López necesita atención humana"


# ---------------------------------------------------------------------------
# End to end: the alert that lands on the agency's phone carries the context
# ---------------------------------------------------------------------------


@pytest.fixture
def inbound(monkeypatch):
    pushes: list[dict] = []
    emails: list[dict] = []

    async def _fake_push(db, agency_id, title, body, data=None):
        pushes.append({"title": title, "body": body, "data": data})
        return 1

    async def _fake_email(to, subject, html):
        emails.append({"to": to, "subject": subject, "html": html})
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

    def _run(*, lead, conv_extra):
        created = datetime.now(UTC)
        lead.created_at = created
        lead.updated_at = created + timedelta(minutes=5)
        conv = SimpleNamespace(
            id=uuid.uuid4(),
            channel=ConversationChannel.WHATSAPP,
            status=ConversationStatus.ACTIVE,
            lead_id=lead.id,
            agency_id=agency.id,
            extra=conv_extra,
        )

        async def _resolve(db, agency_id, identity):
            return lead, conv

        async def _run_agent(*, db, conversation_id, use_smart_model=False):
            return SimpleNamespace(
                text="Te paso con un compañero.",
                tool_calls=[],
                handed_off=True,
                run_id=uuid.uuid4(),
            )

        async def _recompute(db, lead_id):
            return None

        monkeypatch.setattr(cs, "resolve_lead_and_conversation", _resolve)
        monkeypatch.setattr(cs, "run_agent", _run_agent)
        monkeypatch.setattr(cs, "recompute_lead_score", _recompute)

        payload = cs.InboundPayload(
            identity=SimpleNamespace(),
            agency_id=agency.id,
            text="Quiero hablar con una persona",
        )
        return payload, _Session()

    return SimpleNamespace(pushes=pushes, emails=emails, run=_run)


async def test_the_push_carries_who_what_and_why(inbound):
    payload, session = inbound.run(
        lead=_lead(),
        conv_extra={HANDOFF_REASON_KEY: "Está molesto con la tasación"},
    )
    await cs.handle_inbound(session, payload)

    assert len(inbound.pushes) == 1
    body = inbound.pushes[0]["body"]
    assert "Marta López" in body
    assert "Chamberí" in body
    assert "Está molesto con la tasación" in body


async def test_the_push_links_to_the_lead_as_well_as_the_conversation(inbound):
    payload, session = inbound.run(lead=_lead(), conv_extra={HANDOFF_REASON_KEY: "x"})
    await cs.handle_inbound(session, payload)
    assert "lead_id" in inbound.pushes[0]["data"]


async def test_the_email_subject_carries_the_reason(inbound):
    payload, session = inbound.run(
        lead=_lead(), conv_extra={HANDOFF_REASON_KEY: "Pide hablar con una persona"}
    )
    await cs.handle_inbound(session, payload)
    assert inbound.emails[0]["subject"] == (
        "Marta López necesita atención humana: Pide hablar con una persona"
    )


async def test_a_guardrail_escalation_without_a_reason_still_alerts(inbound):
    """The guardrail hands off without calling the tool: no reason stored."""
    payload, session = inbound.run(lead=_lead(), conv_extra={})
    await cs.handle_inbound(session, payload)
    assert len(inbound.pushes) == 1
    assert DEFAULT_HANDOFF_REASON in inbound.pushes[0]["body"]
