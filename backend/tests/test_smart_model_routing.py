"""(218) smart-model routing and (227) explicit Anthropic timeouts.

218: `use_smart_model` existed since Sprint 1 and nobody ever passed True, so
the conversation that decides a 300.000 € sale ran on the fast model.
227: the SDK defaults to a 600 s timeout while this runner executes inline
inside the WhatsApp webhook and `/voice/llm`.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace

import pytest

from app.agent import runner as runner_mod
from app.agent.runner import (
    ANTHROPIC_MAX_RETRIES,
    CHANNELS_WITHOUT_SMART_MODEL,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_TURN_BUDGET_SECONDS,
    request_timeout_for,
    should_use_smart_model,
    timeout_fallback_for,
    turn_budget_for,
)
from app.agent.tools import MORTGAGE_PREAPPROVAL_EXTRA_KEY, AgentContext, execute_tool
from app.db.models import (
    Agency,
    Conversation,
    ConversationChannel,
    Lead,
    LeadStatus,
    LeadTemperature,
    MessageRole,
)


def _lead(**overrides) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "status": LeadStatus.ENGAGED,
        "temperature": LeadTemperature.WARM,
        "extra": {},
        "qualification_notes": "",
    }
    return SimpleNamespace(**{**base, **overrides})


# ---------------------------------------------------------------------------
# (218) When is a conversation worth the expensive model?
# ---------------------------------------------------------------------------


def test_an_ordinary_warm_lead_stays_on_the_fast_model():
    assert should_use_smart_model(_lead(), "whatsapp") is False


def test_a_hot_lead_gets_the_smart_model():
    assert should_use_smart_model(_lead(temperature=LeadTemperature.HOT), "whatsapp") is True


def test_a_negotiating_lead_gets_the_smart_model():
    assert should_use_smart_model(_lead(status=LeadStatus.NEGOTIATING), "email") is True


def test_a_mortgage_preapproval_in_play_gets_the_smart_model():
    lead = _lead(extra={MORTGAGE_PREAPPROVAL_EXTRA_KEY: {"confidence": "medium"}})
    assert should_use_smart_model(lead, "whatsapp") is True


def test_an_empty_preapproval_marker_does_not_count():
    assert should_use_smart_model(_lead(extra={MORTGAGE_PREAPPROVAL_EXTRA_KEY: {}}), "web") is False


def test_a_lead_with_no_extra_dict_does_not_explode():
    assert should_use_smart_model(_lead(extra=None), "whatsapp") is False


@pytest.mark.parametrize("channel", ["voice", "VOICE", " voice "])
def test_voice_never_pays_the_latency_of_the_smart_model(channel):
    """A voice turn has ~20 s end to end; Opus would leave the lead in
    silence on the phone."""
    lead = _lead(temperature=LeadTemperature.HOT, status=LeadStatus.NEGOTIATING)
    assert should_use_smart_model(lead, channel) is False


def test_voice_is_the_only_excluded_channel():
    assert set(CHANNELS_WITHOUT_SMART_MODEL) == {"voice"}


# ---------------------------------------------------------------------------
# (218) The flag the routing reads is actually written by the tool
# ---------------------------------------------------------------------------


class _ToolDB:
    def __init__(self, lead):
        self.lead = lead

    async def get(self, model, pk):
        return self.lead if model is Lead else None

    async def flush(self):
        return None


def _ctx(db, lead) -> AgentContext:
    return AgentContext(
        db=db,
        agency_id=uuid.uuid4(),
        lead_id=lead.id,
        conversation_id=uuid.uuid4(),
        channel="whatsapp",
    )


async def test_a_viable_preapproval_marks_the_lead():
    lead = _lead()
    await execute_tool(
        "mortgage_preapproval",
        {"monthly_income_eur": 3500, "savings_eur": 60000, "age": 35},
        _ctx(_ToolDB(lead), lead),
    )
    marker = lead.extra[MORTGAGE_PREAPPROVAL_EXTRA_KEY]
    assert marker["indicative_max_loan_eur"] > 0
    assert marker["requested_at"]
    assert should_use_smart_model(lead, "whatsapp") is True


async def test_a_rejected_preapproval_does_not_buy_the_expensive_model():
    """Income below what any Spanish bank lends against: nothing to reason
    about, so nothing to pay Opus for."""
    lead = _lead()
    await execute_tool(
        "mortgage_preapproval",
        {"monthly_income_eur": 400, "savings_eur": 1000, "age": 30},
        _ctx(_ToolDB(lead), lead),
    )
    assert MORTGAGE_PREAPPROVAL_EXTRA_KEY not in (lead.extra or {})


async def test_the_marker_does_not_wipe_the_rest_of_extra():
    lead = _lead(extra={"foreign_buyer_mode": True})
    await execute_tool(
        "mortgage_preapproval",
        {"monthly_income_eur": 3500, "savings_eur": 60000, "age": 35},
        _ctx(_ToolDB(lead), lead),
    )
    assert lead.extra["foreign_buyer_mode"] is True


# ---------------------------------------------------------------------------
# (227) Timeouts, retries and a kind fallback
# ---------------------------------------------------------------------------


def test_voice_gets_a_tighter_request_timeout_than_text():
    assert request_timeout_for("voice") == 10.0
    assert request_timeout_for("whatsapp") == DEFAULT_REQUEST_TIMEOUT_SECONDS == 30.0


def test_unknown_channels_get_the_text_timeout():
    for channel in ("sms", "web", "", None):
        assert request_timeout_for(channel) == DEFAULT_REQUEST_TIMEOUT_SECONDS


def test_the_whole_turn_has_a_budget_not_just_each_request():
    """Up to 6 tool iterations per turn: a per-request timeout alone would let
    a voice call hang for a minute."""
    assert turn_budget_for("voice") == 20.0
    assert turn_budget_for("email") == DEFAULT_TURN_BUDGET_SECONDS


def test_every_channel_budget_covers_at_least_two_requests():
    for channel in ("voice", "whatsapp", "email"):
        assert turn_budget_for(channel) >= 2 * request_timeout_for(channel)


def test_the_client_is_built_with_explicit_timeout_and_retries():
    client = runner_mod._anthropic_client(timeout_seconds=12.5)
    assert client.timeout == 12.5
    assert client.max_retries == ANTHROPIC_MAX_RETRIES == 2


def test_clients_are_cached_per_timeout_not_rebuilt_every_turn():
    """Rebuilding would throw away the httpx keep-alive pool on every message."""
    first = runner_mod._anthropic_client(timeout_seconds=17.0)
    second = runner_mod._anthropic_client(timeout_seconds=17.0)
    third = runner_mod._anthropic_client(timeout_seconds=18.0)
    assert first is second
    assert first is not third


def test_the_voice_fallback_obeys_the_voice_style_rules():
    """It is read out loud: no emoji, no lists, short (214)."""
    text = timeout_fallback_for("voice")
    assert text
    assert "\n" not in text
    assert "-" not in text
    assert len(text) < 120
    assert all(ord(ch) < 0x2190 for ch in text)


def test_the_text_fallback_offers_a_human():
    text = timeout_fallback_for("whatsapp")
    assert "compañero" in text or "agente" in text


def test_fallbacks_never_blame_the_client():
    for channel in ("voice", "whatsapp", "email", "sms"):
        assert "error" not in timeout_fallback_for(channel).lower()


# ---------------------------------------------------------------------------
# A whole turn: the routing decision and the timeout actually take effect
# ---------------------------------------------------------------------------


class _RunnerDB:
    """The four queries a turn makes, dispatched by the table they hit."""

    def __init__(self, *, conv, lead, agency, history):
        self.conv = conv
        self.lead = lead
        self.agency = agency
        self.history = history
        self.added: list = []

    async def get(self, model, pk):
        if model is Conversation:
            return self.conv
        if model is Lead:
            return self.lead
        if model is Agency:
            return self.agency
        return None

    async def execute(self, stmt):
        sql = str(stmt)
        rows = self.history if "FROM messages" in sql else []

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return rows

        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _turn_doubles(*, temperature=LeadTemperature.WARM):
    agency = SimpleNamespace(
        id=uuid.uuid4(),
        name="Inmo Sol",
        timezone="Europe/Madrid",
        settings={},
    )
    lead = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency.id,
        full_name="Marta López",
        budget_min=None,
        budget_max=None,
        operation=None,
        preferred_cities=None,
        preferred_neighborhoods=None,
        min_bedrooms=None,
        must_haves=None,
        deal_breakers=None,
        qualification_notes="",
        detected_objections=None,
        language="es",
        status=LeadStatus.ENGAGED,
        temperature=temperature,
        extra={},
    )
    conv = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency.id,
        lead_id=lead.id,
        channel=ConversationChannel.WHATSAPP,
        extra={},
    )
    history = [
        SimpleNamespace(role=MessageRole.LEAD, content="Hola, busco piso en Chamberí"),
    ]
    return _RunnerDB(conv=conv, lead=lead, agency=agency, history=history), conv


def _fake_client(create):
    return SimpleNamespace(messages=SimpleNamespace(create=create))


async def test_a_hot_lead_actually_runs_on_the_smart_model(monkeypatch):
    used: list[str] = []

    async def _create(**kwargs):
        used.append(kwargs["model"])
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Claro, ¿qué presupuesto manejas?")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )

    monkeypatch.setattr(
        runner_mod, "_anthropic_client", lambda **_kw: _fake_client(_create)
    )
    db, conv = _turn_doubles(temperature=LeadTemperature.HOT)
    reply = await runner_mod.run_agent(db=db, conversation_id=conv.id)

    assert used == [runner_mod.settings.agent_model_smart]
    assert reply.text.startswith("Claro")


async def test_a_warm_lead_stays_on_the_fast_model(monkeypatch):
    used: list[str] = []

    async def _create(**kwargs):
        used.append(kwargs["model"])
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Te cuento.")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )

    monkeypatch.setattr(
        runner_mod, "_anthropic_client", lambda **_kw: _fake_client(_create)
    )
    db, conv = _turn_doubles()
    await runner_mod.run_agent(db=db, conversation_id=conv.id)
    assert used == [runner_mod.settings.agent_model_fast]


async def test_a_hanging_api_answers_the_lead_instead_of_holding_the_webhook(monkeypatch):
    """(227) Without a budget the SDK would wait 600 s while Meta retries the
    webhook and the lead sees nothing."""

    async def _create(**_kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(
        runner_mod, "_anthropic_client", lambda **_kw: _fake_client(_create)
    )
    monkeypatch.setattr(runner_mod, "DEFAULT_TURN_BUDGET_SECONDS", 0.05)

    db, conv = _turn_doubles()
    started = time.perf_counter()
    reply = await runner_mod.run_agent(db=db, conversation_id=conv.id)
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0
    assert reply.text == timeout_fallback_for("whatsapp")
    assert reply.handed_off is False
    # The failed turn is still recorded for observability.
    assert db.added and db.added[0].error == "timeout"
