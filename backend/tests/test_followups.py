"""(164) 24h / 72h follow-up when a lead goes quiet on an open question.

`celery_app.py` promised these follow-ups from day one and they never
existed: the silent lead heard nothing until the day 3-14 voice call.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.db.models import (
    Agency,
    ConversationChannel,
    Lead,
    Message,
    MessageDirection,
    MessageRole,
)
from app.services import followups as fu
from app.services.followups import (
    MAX_SILENCE_HOURS,
    STEP_24H,
    STEP_72H,
    STEP_FLAG,
    WHATSAPP_FIRST_STEP_HOURS,
    build_followup,
    followup_sweep,
    has_pending_question,
    pick_step,
    step_threshold_hours,
)

WA = ConversationChannel.WHATSAPP
EMAIL = ConversationChannel.EMAIL


# ---------------------------------------------------------------------------
# Which conversations qualify
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "¿Qué presupuesto manejas?",
        "Dime zona y presupuesto, ¿te parece?",
        "Te mando opciones. Cual prefieres?",
    ],
)
def test_a_question_is_detected(text):
    assert has_pending_question(text) is True


@pytest.mark.parametrize("text", ["Perfecto, te llamamos mañana.", "", None, "Hasta luego."])
def test_a_statement_is_not_a_pending_question(text):
    assert has_pending_question(text) is False


def test_whatsapp_fires_early_enough_to_stay_inside_metas_window():
    """A free-form WhatsApp message is undeliverable more than 24h after the
    lead's last inbound, and the sweep only runs every 30 minutes."""
    assert step_threshold_hours(STEP_24H, WA) == WHATSAPP_FIRST_STEP_HOURS
    assert WHATSAPP_FIRST_STEP_HOURS < 24.0


def test_email_has_no_window_so_it_fires_at_twenty_four_hours():
    assert step_threshold_hours(STEP_24H, EMAIL) == 24.0


def test_the_second_rung_is_seventy_two_hours_on_both_channels():
    assert step_threshold_hours(STEP_72H, WA) == 72.0
    assert step_threshold_hours(STEP_72H, EMAIL) == 72.0


def test_nothing_is_sent_before_the_first_threshold():
    assert pick_step(channel=WA, silence_hours=22.0, extra={}) is None
    assert pick_step(channel=EMAIL, silence_hours=23.5, extra={}) is None


def test_the_first_rung_fires_once_the_threshold_is_crossed():
    assert pick_step(channel=WA, silence_hours=23.0, extra={}) == STEP_24H
    assert pick_step(channel=EMAIL, silence_hours=24.1, extra={}) == STEP_24H


def test_the_second_rung_fires_after_three_days():
    extra = {STEP_FLAG[STEP_24H]: "2026-07-25T10:00:00+00:00"}
    assert pick_step(channel=WA, silence_hours=72.5, extra=extra) == STEP_72H


def test_a_rung_is_never_climbed_twice():
    extra = {STEP_FLAG[STEP_24H]: "yesterday"}
    assert pick_step(channel=WA, silence_hours=30.0, extra=extra) is None


def test_a_sweep_outage_does_not_produce_two_messages_at_once():
    """72h of silence with nothing sent yet: only the 72h message goes out."""
    assert pick_step(channel=EMAIL, silence_hours=80.0, extra={}) == STEP_72H


def test_both_rungs_used_means_silence():
    extra = {STEP_FLAG[STEP_24H]: "a", STEP_FLAG[STEP_72H]: "b"}
    assert pick_step(channel=WA, silence_hours=200.0, extra=extra) is None


def test_an_abandoned_thread_is_left_alone():
    """Writing "¿seguimos?" three weeks later is spam, not a follow-up."""
    assert pick_step(channel=WA, silence_hours=MAX_SILENCE_HOURS + 1, extra={}) is None


def test_a_missing_extra_dict_is_treated_as_nothing_sent():
    assert pick_step(channel=WA, silence_hours=25.0, extra=None) == STEP_24H


# ---------------------------------------------------------------------------
# The copy
# ---------------------------------------------------------------------------


def _wa(step=STEP_24H, **kwargs):
    return build_followup(step=step, channel=WA, agency_name="Inmo Sol", **kwargs)


def _email(step=STEP_24H, **kwargs):
    return build_followup(step=step, channel=EMAIL, agency_name="Inmo Sol", **kwargs)


def test_whatsapp_copy_respects_the_three_line_limit():
    for step in (STEP_24H, STEP_72H):
        for tone in (None, "formal"):
            text = _wa(step, ai_tone=tone).text
            assert len(text.split("\n")) <= 3, text


def test_whatsapp_copy_uses_at_most_one_emoji():
    for step in (STEP_24H, STEP_72H):
        text = _wa(step).text
        assert sum(1 for ch in text if ord(ch) > 0x2100) <= 1, text


def test_whatsapp_copy_has_no_subject():
    assert _wa().subject is None


def test_email_copy_has_a_subject_a_greeting_and_a_sign_off():
    message = _email()
    assert message.subject
    assert message.text.startswith("Hola")
    assert "Un saludo" in message.text
    assert "Inmo Sol" in message.text


def test_the_lead_first_name_is_used_not_the_full_name():
    assert "Hola Marta," in _wa(lead_name="Marta López Ruiz").text


def test_a_nameless_lead_still_gets_a_natural_greeting():
    text = _wa(lead_name=None).text
    assert text.startswith("Hola,")


def test_the_formal_tone_switches_the_copy_to_usted():
    text = _wa(ai_tone="formal").text
    assert "cuentas" not in text
    assert "cuenta" in text or "prefiere" in text


def test_the_default_tone_tutea():
    assert "prefieres" in _wa().text


def test_the_seventy_two_hour_message_says_it_is_the_last_one():
    for channel_builder in (_wa, _email):
        text = channel_builder(STEP_72H).text.lower()
        assert "no quiero insistir" in text or "último mensaje" in text or "lo dejo aquí" in text


def test_a_forbidden_phrase_knocks_out_the_variant_that_contains_it():
    banned = "pudiste ver mi mensaje"
    message = _wa(forbidden_phrases=[banned])
    assert message is not None
    assert banned not in message.text.lower()


def test_when_every_variant_is_forbidden_we_say_nothing():
    """Better silent than breaking the agency's own rules."""
    assert _wa(forbidden_phrases=["hola"]) is None


def test_the_blacklist_ignores_blank_entries():
    assert _wa(forbidden_phrases=["", "   ", None]) is not None


def test_channels_we_cannot_write_to_get_no_copy():
    for channel in (ConversationChannel.VOICE, ConversationChannel.SMS, "web"):
        assert build_followup(step=STEP_24H, channel=channel, agency_name="X") is None


def test_no_copy_leaves_an_unformatted_placeholder():
    for step in (STEP_24H, STEP_72H):
        for builder in (_wa, _email):
            for tone in (None, "formal"):
                text = builder(step, ai_tone=tone, lead_name="Ana").text
                assert "{" not in text and "}" not in text


# ---------------------------------------------------------------------------
# Processing one conversation
# ---------------------------------------------------------------------------


@dataclass
class _Result:
    rows: object

    def scalars(self):
        return self

    def first(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows


class _DB:
    """`_conversation_state` runs exactly two queries, in this order:
    the last message of the thread, then when the lead last wrote."""

    def __init__(self, *, last_message, last_inbound_at, lead, agency):
        self._queue = [_Result(last_message), _Result(last_inbound_at)]
        self.lead = lead
        self.agency = agency
        self.added: list = []
        # Ordered log of "claim" / "send" / "add" so a test can assert the
        # rung is durable *before* the message leaves.
        self.journal: list[str] = []

    async def execute(self, _stmt):
        return self._queue.pop(0)

    async def commit(self):
        self.journal.append("commit")

    async def rollback(self):
        self.journal.append("rollback")

    async def get(self, model, pk):
        if model is Lead:
            return self.lead
        if model is Agency:
            return self.agency
        return None

    def add(self, obj):
        self.journal.append("add")
        self.added.append(obj)


@pytest.fixture
def channel_spies(monkeypatch):
    sent: list[dict] = []

    class _WA:
        def __init__(self, access_token=None, phone_number_id=None):
            self.access_token = access_token
            self.phone_number_id = phone_number_id

        async def send_text(self, to, text):
            sent.append(
                {
                    "channel": "whatsapp",
                    "to": to,
                    "text": text,
                    "token": self.access_token,
                }
            )
            return "wamid.1"

    class _Resend:
        async def send(self, *, to, subject, text, **_kwargs):
            sent.append({"channel": "email", "to": to, "subject": subject, "text": text})
            return "resend-1"

    monkeypatch.setattr("app.channels.whatsapp.client.WhatsAppClient", _WA)
    monkeypatch.setattr("app.channels.email.client.ResendClient", _Resend)
    return sent


def _agency(**overrides):
    base = {"id": uuid.uuid4(), "name": "Inmo Sol", "settings": {}}
    return SimpleNamespace(**{**base, **overrides})


def _lead(agency_id, **overrides):
    base = {
        "id": uuid.uuid4(),
        "agency_id": agency_id,
        "full_name": "Marta López",
        "phone_e164": "+34600111222",
        "whatsapp_wa_id": "34600111222",
        "email": "marta@example.com",
    }
    return SimpleNamespace(**{**base, **overrides})


def _conv(agency_id, lead_id, *, channel=WA, extra=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency_id,
        lead_id=lead_id,
        channel=channel,
        extra=extra if extra is not None else {},
        created_at=datetime.now(UTC) - timedelta(days=5),
    )


def _agent_msg(text="¿Qué presupuesto manejas?"):
    return SimpleNamespace(role=MessageRole.AGENT_AI, content=text)


def _lead_msg(text="Hola"):
    return SimpleNamespace(role=MessageRole.LEAD, content=text)


def _hours_ago(hours: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


async def _process(*, last_message, last_inbound_at, conv, lead=None, agency=None):
    agency = agency or _agency(id=conv.agency_id)
    lead = lead or _lead(conv.agency_id)
    db = _DB(
        last_message=last_message,
        last_inbound_at=last_inbound_at,
        lead=lead,
        agency=agency,
    )
    step = await fu._process_conversation(db, conv, datetime.now(UTC))
    return step, db


async def test_a_silent_lead_with_an_open_question_gets_the_first_follow_up(channel_spies):
    agency = _agency()
    conv = _conv(agency.id, uuid.uuid4())
    step, db = await _process(
        last_message=_agent_msg(), last_inbound_at=_hours_ago(30), conv=conv, agency=agency
    )

    assert step == STEP_24H
    assert len(channel_spies) == 1
    assert channel_spies[0]["to"] == "34600111222"
    assert conv.extra[STEP_FLAG[STEP_24H]]


async def test_a_lead_who_answered_last_is_left_alone(channel_spies):
    conv = _conv(uuid.uuid4(), uuid.uuid4())
    step, _ = await _process(
        last_message=_lead_msg(), last_inbound_at=_hours_ago(30), conv=conv
    )
    assert step is None
    assert channel_spies == []


async def test_a_thread_a_human_answered_last_is_left_alone(channel_spies):
    """A colleague stepped in without formally handing the conversation off;
    the AI must not talk over them."""
    conv = _conv(uuid.uuid4(), uuid.uuid4())
    step, _ = await _process(
        last_message=SimpleNamespace(role=MessageRole.AGENT_HUMAN, content="¿Te llamo mañana?"),
        last_inbound_at=_hours_ago(30),
        conv=conv,
    )
    assert step is None
    assert channel_spies == []


async def test_no_open_question_means_no_follow_up(channel_spies):
    conv = _conv(uuid.uuid4(), uuid.uuid4())
    step, _ = await _process(
        last_message=_agent_msg("Perfecto, te llamamos mañana."),
        last_inbound_at=_hours_ago(40),
        conv=conv,
    )
    assert step is None
    assert channel_spies == []


async def test_a_thread_that_never_went_quiet_is_left_alone(channel_spies):
    conv = _conv(uuid.uuid4(), uuid.uuid4())
    step, _ = await _process(
        last_message=_agent_msg(), last_inbound_at=_hours_ago(2), conv=conv
    )
    assert step is None
    assert channel_spies == []


async def test_the_follow_up_is_persisted_so_the_panel_shows_it(channel_spies):
    conv = _conv(uuid.uuid4(), uuid.uuid4())
    _, db = await _process(
        last_message=_agent_msg(), last_inbound_at=_hours_ago(30), conv=conv
    )
    (message,) = db.added
    assert isinstance(message, Message)
    assert message.direction is MessageDirection.OUTBOUND
    assert message.role is MessageRole.AGENT_AI
    assert message.agency_id == conv.agency_id
    assert message.raw == {"followup_step": STEP_24H}
    assert message.external_message_id == "wamid.1"


async def test_the_same_rung_is_never_sent_twice(channel_spies):
    conv = _conv(uuid.uuid4(), uuid.uuid4(), extra={STEP_FLAG[STEP_24H]: "ya"})
    step, db = await _process(
        last_message=_agent_msg(), last_inbound_at=_hours_ago(30), conv=conv
    )
    assert step is None
    assert channel_spies == []
    assert db.added == []


async def test_silence_is_measured_from_the_lead_not_from_our_own_follow_up(channel_spies):
    """The 24h follow-up is itself an agent message with a question. If the
    clock restarted on it, the 72h rung would fire three days late."""
    conv = _conv(uuid.uuid4(), uuid.uuid4(), extra={STEP_FLAG[STEP_24H]: "ayer"})
    step, _ = await _process(
        last_message=_agent_msg("¿Sigues buscando?"),
        last_inbound_at=_hours_ago(73),
        conv=conv,
    )
    assert step == STEP_72H


async def test_a_thread_with_no_inbound_at_all_falls_back_to_its_creation(channel_spies):
    conv = _conv(uuid.uuid4(), uuid.uuid4())  # created 5 days ago
    step, _ = await _process(last_message=_agent_msg(), last_inbound_at=None, conv=conv)
    assert step == STEP_72H


async def test_the_email_channel_sends_a_subject(channel_spies):
    conv = _conv(uuid.uuid4(), uuid.uuid4(), channel=EMAIL)
    step, _ = await _process(
        last_message=_agent_msg(), last_inbound_at=_hours_ago(30), conv=conv
    )
    assert step == STEP_24H
    assert channel_spies[0]["channel"] == "email"
    assert channel_spies[0]["subject"]
    assert channel_spies[0]["to"] == "marta@example.com"


async def test_the_agency_own_whatsapp_credentials_are_used(channel_spies):
    """An agency that connected its own number must send from that number."""
    agency = _agency(
        settings={"whatsapp_access_token": "tok-123", "whatsapp_phone_number_id": "555"}
    )
    conv = _conv(agency.id, uuid.uuid4())
    await _process(
        last_message=_agent_msg(), last_inbound_at=_hours_ago(30), conv=conv, agency=agency
    )
    assert channel_spies[0]["token"] == "tok-123"


async def test_the_agency_tone_reaches_the_copy(channel_spies):
    agency = _agency(settings={"ai_tone": "formal"})
    conv = _conv(agency.id, uuid.uuid4())
    await _process(
        last_message=_agent_msg(), last_inbound_at=_hours_ago(30), conv=conv, agency=agency
    )
    assert "cuentas" not in channel_spies[0]["text"]


async def test_a_forbidden_phrase_that_blocks_every_variant_burns_the_rung(channel_spies):
    """We have nothing compliant left to say; retrying every 30 minutes would
    just log the same failure forever."""
    agency = _agency(settings={"forbidden_phrases": ["Hola"]})
    conv = _conv(agency.id, uuid.uuid4())
    step, db = await _process(
        last_message=_agent_msg(), last_inbound_at=_hours_ago(30), conv=conv, agency=agency
    )
    assert step is None
    assert channel_spies == []
    assert conv.extra[STEP_FLAG[STEP_24H]]
    assert db.added == []


async def test_a_failed_send_still_burns_the_rung(monkeypatch, channel_spies):
    """Outside Meta's 24h window the send is rejected. Retrying forever would
    eventually deliver a follow-up about a three-week-old conversation."""

    class _Broken:
        def __init__(self, *_a, **_k):
            pass

        async def send_text(self, *_a, **_k):
            raise RuntimeError("(#131047) Message failed to send: outside window")

    monkeypatch.setattr("app.channels.whatsapp.client.WhatsAppClient", _Broken)
    conv = _conv(uuid.uuid4(), uuid.uuid4())
    step, db = await _process(
        last_message=_agent_msg(), last_inbound_at=_hours_ago(30), conv=conv
    )

    assert step is None
    assert conv.extra[STEP_FLAG[STEP_24H]]
    assert db.added == []  # nothing left, so nothing is shown as sent


async def test_a_lead_without_a_reachable_address_is_not_faked_as_sent(channel_spies):
    conv = _conv(uuid.uuid4(), uuid.uuid4())
    lead = _lead(conv.agency_id, phone_e164=None, whatsapp_wa_id=None)
    step, db = await _process(
        last_message=_agent_msg(), last_inbound_at=_hours_ago(30), conv=conv, lead=lead
    )
    assert step is None
    assert db.added == []


async def test_a_lead_from_another_tenant_is_never_written_to(channel_spies):
    """Multi-tenant rule: a mismatched agency_id stops everything."""
    conv = _conv(uuid.uuid4(), uuid.uuid4())
    step, _ = await _process(
        last_message=_agent_msg(),
        last_inbound_at=_hours_ago(30),
        conv=conv,
        lead=_lead(uuid.uuid4()),
    )
    assert step is None
    assert channel_spies == []


# ---------------------------------------------------------------------------
# The sweep itself — the contract with the worker track
# ---------------------------------------------------------------------------


async def test_the_sweep_returns_the_agreed_shape(monkeypatch):
    async def _none(_db, _now):
        return []

    monkeypatch.setattr(fu, "_candidate_conversations", _none)
    assert await followup_sweep(object()) == {"sent_24h": 0, "sent_72h": 0}


async def test_the_sweep_counts_each_rung_separately(monkeypatch):
    convs = [SimpleNamespace(id=uuid.uuid4()) for _ in range(4)]
    steps = iter([STEP_24H, STEP_72H, None, STEP_24H])

    async def _candidates(_db, _now):
        return convs

    async def _process_one(_db, _conv, _now):
        return next(steps)

    monkeypatch.setattr(fu, "_candidate_conversations", _candidates)
    monkeypatch.setattr(fu, "_process_conversation", _process_one)

    assert await followup_sweep(object()) == {"sent_24h": 2, "sent_72h": 1}


async def test_one_broken_conversation_does_not_abort_the_sweep(monkeypatch):
    convs = [SimpleNamespace(id=uuid.uuid4()) for _ in range(3)]
    calls = iter([RuntimeError("boom"), STEP_24H, STEP_72H])

    async def _candidates(_db, _now):
        return convs

    async def _process_one(_db, _conv, _now):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(fu, "_candidate_conversations", _candidates)
    monkeypatch.setattr(fu, "_process_conversation", _process_one)

    assert await followup_sweep(object()) == {"sent_24h": 1, "sent_72h": 1}


async def test_the_candidate_query_is_scoped_and_bounded():
    """One statement, joined on agency_id as well as conversation_id, so a
    message row from another tenant can never widen the sweep."""
    captured: list[str] = []

    class _Capture:
        async def execute(self, stmt):
            captured.append(str(stmt))

            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _R()

    await fu._candidate_conversations(_Capture(), datetime.now(UTC))
    (sql,) = captured
    assert "agency_id" in sql
    assert "LIMIT" in sql.upper()
    assert "status" in sql
