"""Adversarial review of the AI-agent track (164, 213-221, 227).

Every test here failed before the fix it documents. They are grouped by the
hole they close:

- (221) a visit asked for beyond the 14-day busy window double-booked the
  assigned agent, because the collision query never loaded that far ahead.
- (221) a 30-minute visit at 18:30 was refused by an agency open until 19:00,
  and the model was told — falsely — that 18:30 is outside 09:00-19:00.
- (guardrail) an escalation from the output guardrail left the conversation
  ACTIVE, so the AI kept answering after promising a human, the agency got a
  push per turn, and the 24h/72h sweep would nudge that lead the next day.
- (164) the follow-up copy was Spanish-only, right after 215 forced the live
  conversation into the lead's language.
- (164) the rung was marked *after* the send and never committed, so a worker
  killed mid-sweep (acks_late, 240 s soft limit) re-sent the whole batch.
- (164) a lead with a visit already booked was chased with "cuéntame qué
  buscas".
- (164) finished threads stayed in the candidate set for two weeks and, being
  the oldest, ate the whole per-sweep budget.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.agent import runner as runner_mod
from app.agent import tools as tools_mod
from app.agent.tools import (
    HANDOFF_AT_KEY,
    HANDOFF_REASON_KEY,
    VISIT_COLLISION_MINUTES,
    VISIT_SEARCH_DAYS,
    AgentContext,
    execute_tool,
    slot_is_valid,
    suggest_visit_slots,
)
from app.db.models import (
    Agency,
    ConversationChannel,
    ConversationStatus,
    Lead,
    LeadStatus,
    LeadTemperature,
    MessageRole,
    Property,
    Visit,
)
from app.services import followups as fu
from app.services.followups import (
    STEP_24H,
    STEP_72H,
    STEP_FLAG,
    build_followup,
    resolve_copy_language,
)

MADRID = ZoneInfo("Europe/Madrid")
OFFICE = {"start": "09:00", "end": "19:00", "days": [0, 1, 2, 3, 4]}


def _weekday_at(days_ahead: int, hour: int, minute: int = 0) -> datetime:
    """A weekday `days_ahead` days from now (or later), at local `hour`."""
    when = datetime.now(MADRID).replace(hour=hour, minute=minute, second=0, microsecond=0)
    when += timedelta(days=days_ahead)
    while when.weekday() > 4:
        when += timedelta(days=1)
    return when


# ---------------------------------------------------------------------------
# (221) The collision window has to cover the date actually requested
# ---------------------------------------------------------------------------


class _RangeAwareDB:
    """Serves visits, honouring the scheduled_at range of the query.

    That range is the whole point: the old code asked for `now .. now+14d`,
    so a visit three weeks out was compared against an empty agenda.
    """

    def __init__(self, *, agency, prop, lead, busy):
        self.agency = agency
        self.prop = prop
        self.lead = lead
        self.busy = busy
        self.added: list = []
        self.windows: list[tuple[datetime, datetime]] = []

    async def get(self, model, pk):
        return {Property: self.prop, Agency: self.agency, Lead: self.lead}.get(model)

    async def execute(self, stmt):
        params = stmt.compile().params
        moments = sorted(v for v in params.values() if isinstance(v, datetime))
        since, until = moments[0], moments[-1]
        self.windows.append((since, until))
        rows = [(b,) for b in self.busy if since <= b <= until]

        class _Result:
            def all(self):
                return rows

        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


@pytest.fixture
def far_future(monkeypatch):
    agency_id = uuid.uuid4()
    agency = SimpleNamespace(
        id=agency_id,
        name="Inmo Sol",
        timezone="Europe/Madrid",
        settings={"business_hours": OFFICE},
    )
    prop = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency_id,
        reference="REF-1",
        title="Piso",
        address="Calle Ancha 1",
        latitude=None,
        longitude=None,
    )
    lead = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency_id,
        status=None,
        temperature=None,
        extra={},
        interested_property_ids=[],
        qualification_notes="",
    )
    agent = SimpleNamespace(
        id=uuid.uuid4(), google_refresh_token=None, google_calendar_id=None
    )

    async def _pick(db, *, agency_id, property_):
        return agent

    monkeypatch.setattr(tools_mod, "pick_agent_for_visit", _pick)

    def _make(busy):
        db = _RangeAwareDB(agency=agency, prop=prop, lead=lead, busy=busy)
        ctx = AgentContext(
            db=db,
            agency_id=agency_id,
            lead_id=lead.id,
            conversation_id=uuid.uuid4(),
            channel="whatsapp",
        )
        return db, ctx

    return SimpleNamespace(make=_make, prop=prop)


async def _schedule(ctx, prop_id, when: datetime) -> str:
    return await execute_tool(
        "schedule_visit",
        {"property_id": str(prop_id), "scheduled_at_iso": when.isoformat()},
        ctx,
    )


async def test_a_visit_beyond_the_search_window_still_sees_the_agents_agenda(far_future):
    """Three weeks out is a perfectly normal request for a second viewing."""
    wanted = _weekday_at(21, 11)
    db, ctx = far_future.make(busy=[wanted.astimezone(UTC)])

    result = await _schedule(ctx, far_future.prop.id, wanted)

    assert result.startswith("NO AGENDADA"), result
    assert "ya hay otra visita" in result
    assert db.added == []


async def test_the_busy_window_reaches_past_the_requested_date(far_future):
    wanted = _weekday_at(21, 11)
    db, ctx = far_future.make(busy=[])
    await _schedule(ctx, far_future.prop.id, wanted)

    (since, until) = db.windows[0]
    assert since <= datetime.now(UTC)
    assert until >= wanted.astimezone(UTC) + timedelta(days=VISIT_SEARCH_DAYS - 1)


async def test_a_visit_that_just_started_still_blocks_the_next_half_hour(far_future):
    """A visit 10 minutes ago is 40 minutes from a visit in 30: a collision.
    With `since=now` that row was never loaded."""
    wanted = datetime.now(UTC) + timedelta(minutes=35)
    # Move it onto a working morning so office hours are not what rejects it.
    wanted = _weekday_at(3, 11)
    just_started = wanted - timedelta(minutes=20)
    db, ctx = far_future.make(busy=[just_started.astimezone(UTC)])

    result = await _schedule(ctx, far_future.prop.id, wanted)
    assert result.startswith("NO AGENDADA")


# ---------------------------------------------------------------------------
# (221) The last slot of the day is bookable
# ---------------------------------------------------------------------------


def test_a_visit_that_ends_exactly_at_closing_time_is_valid():
    """18:30 + 30 min = 19:00 for an agency open until 19:00. Refusing it and
    telling the client "cae fuera del horario (09:00 a 19:00)" is a lie."""
    assert slot_is_valid(
        _weekday_at(2, 18, 30), tz=MADRID, business_hours=OFFICE, duration_minutes=30
    )


def test_a_visit_that_ends_after_closing_time_is_still_refused():
    assert not slot_is_valid(
        _weekday_at(2, 18, 45), tz=MADRID, business_hours=OFFICE, duration_minutes=30
    )
    assert not slot_is_valid(
        _weekday_at(2, 18, 30), tz=MADRID, business_hours=OFFICE, duration_minutes=60
    )


def test_the_last_slot_of_the_day_is_offered_as_an_alternative():
    slots = suggest_visit_slots(
        desired=_weekday_at(2, 18, 20),
        now=datetime.now(UTC),
        tz=MADRID,
        business_hours=OFFICE,
        busy=[],
        count=1,
    )
    assert [s.astimezone(MADRID).strftime("%H:%M") for s in slots] == ["18:30"]


def test_a_long_visit_is_not_squeezed_into_the_end_of_the_day():
    """A 90-minute visit asked for at 17:40 cannot start at 18:00 (it would
    end at 19:30); the next bookable start is the following morning."""
    slots = suggest_visit_slots(
        desired=_weekday_at(2, 17, 40),
        now=datetime.now(UTC),
        tz=MADRID,
        business_hours=OFFICE,
        busy=[],
        duration_minutes=90,
        count=1,
    )
    assert slots
    local = slots[0].astimezone(MADRID)
    assert local.strftime("%H:%M") == "09:00"
    assert slot_is_valid(slots[0], tz=MADRID, business_hours=OFFICE, duration_minutes=90)


# ---------------------------------------------------------------------------
# The guardrail escalation must actually hand the conversation over
# ---------------------------------------------------------------------------


class _RunnerDB:
    def __init__(self, *, conv, lead, agency, history):
        self.conv = conv
        self.lead = lead
        self.agency = agency
        self.history = history
        self.added: list = []
        self.statements: list[str] = []

    async def get(self, model, pk):
        from app.db.models import Conversation

        if model is Conversation:
            return self.conv
        if model is Lead:
            return self.lead
        if model is Agency:
            return self.agency
        return None

    async def execute(self, stmt):
        sql = str(stmt)
        self.statements.append(sql)
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


def _runner_doubles():
    agency = SimpleNamespace(
        id=uuid.uuid4(), name="Inmo Sol", timezone="Europe/Madrid", settings={}
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
        temperature=LeadTemperature.WARM,
        extra={},
    )
    conv = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency.id,
        lead_id=lead.id,
        channel=ConversationChannel.WHATSAPP,
        status=ConversationStatus.ACTIVE,
        extra={},
    )
    history = [SimpleNamespace(role=MessageRole.LEAD, content="Quiero saber la comisión")]
    return _RunnerDB(conv=conv, lead=lead, agency=agency, history=history), conv


async def _run_with_guardrail(monkeypatch, verdict):
    async def _create(**_kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Cobramos un 3% de comisión.")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )

    monkeypatch.setattr(
        runner_mod,
        "_anthropic_client",
        lambda **_kw: SimpleNamespace(messages=SimpleNamespace(create=_create)),
    )

    async def _validate(_text, **_kwargs):
        return verdict

    monkeypatch.setattr(runner_mod, "validate_output", _validate)
    db, conv = _runner_doubles()
    reply = await runner_mod.run_agent(db=db, conversation_id=conv.id)
    return reply, conv, db


async def test_a_guardrail_escalation_closes_the_conversation_to_the_ai(monkeypatch):
    """Otherwise the AI says "te paso con un compañero" and keeps answering."""
    verdict = SimpleNamespace(
        action="escalate", text="", reason="promete una comisión concreta"
    )
    reply, conv, _ = await _run_with_guardrail(monkeypatch, verdict)

    assert reply.handed_off is True
    assert conv.status is ConversationStatus.HANDED_OFF


async def test_the_guardrail_handoff_says_why_so_the_human_is_not_blind(monkeypatch):
    """(213) The push and the email read `conversation.extra`."""
    verdict = SimpleNamespace(
        action="escalate", text="", reason="promete una comisión concreta"
    )
    _, conv, _ = await _run_with_guardrail(monkeypatch, verdict)

    reason = conv.extra[HANDOFF_REASON_KEY]
    assert "control de calidad" in reason
    assert "promete una comisión concreta" in reason
    assert datetime.fromisoformat(conv.extra[HANDOFF_AT_KEY])


async def test_a_clean_turn_leaves_the_conversation_open(monkeypatch):
    verdict = SimpleNamespace(action="pass", text="Claro, ¿qué zona te interesa?", reason="")
    reply, conv, _ = await _run_with_guardrail(monkeypatch, verdict)

    assert reply.handed_off is False
    assert conv.status is ConversationStatus.ACTIVE
    assert conv.extra == {}


async def test_the_history_query_filters_by_agency(monkeypatch):
    """Project rule: every query filters by agency_id."""
    verdict = SimpleNamespace(action="pass", text="Vale.", reason="")
    _, _, db = await _run_with_guardrail(monkeypatch, verdict)
    assert any("messages.agency_id" in s for s in db.statements)


# ---------------------------------------------------------------------------
# (164) The follow-up speaks the lead's language
# ---------------------------------------------------------------------------


def test_the_language_of_the_lead_picks_the_copy():
    assert resolve_copy_language("en") == "en"
    assert resolve_copy_language("EN-GB") == "en"
    assert resolve_copy_language("es") == "es"


def test_a_language_we_have_no_copy_for_falls_back_to_the_agency_language():
    for code in ("fr", "de", "it", "pt", "ca", "", None, "zz"):
        assert resolve_copy_language(code) == "es"


@pytest.mark.parametrize("step", [STEP_24H, STEP_72H])
@pytest.mark.parametrize("channel", [ConversationChannel.WHATSAPP, ConversationChannel.EMAIL])
def test_an_english_lead_is_not_written_to_in_spanish(step, channel):
    message = build_followup(
        step=step,
        channel=channel,
        agency_name="Inmo Sol",
        lead_name="John Smith",
        language="en",
    )
    assert message is not None
    assert message.text.startswith("Hi John")
    for spanish in ("Hola", "presupuesto", "búsqueda", "saludo"):
        assert spanish not in message.text
    if channel is ConversationChannel.EMAIL:
        assert message.subject
        assert message.subject in (
            "Shall we carry on with your search?",
            "I will leave it here, but I am still available",
        )


def test_the_english_copy_has_no_unformatted_placeholder():
    for step in (STEP_24H, STEP_72H):
        for channel in (ConversationChannel.WHATSAPP, ConversationChannel.EMAIL):
            for tone in (None, "formal"):
                message = build_followup(
                    step=step,
                    channel=channel,
                    agency_name="Inmo Sol",
                    lead_name="John",
                    ai_tone=tone,
                    language="en",
                )
                assert "{" not in message.text and "}" not in message.text
                assert "Inmo Sol" in message.text or channel is ConversationChannel.WHATSAPP


def test_the_english_whatsapp_copy_keeps_the_channel_rules():
    for step in (STEP_24H, STEP_72H):
        text = build_followup(
            step=step,
            channel=ConversationChannel.WHATSAPP,
            agency_name="Inmo Sol",
            language="en",
        ).text
        assert len(text.split("\n")) <= 3
        assert all(ord(ch) < 0x2100 for ch in text)


def test_the_agency_blacklist_still_applies_to_the_english_copy():
    assert (
        build_followup(
            step=STEP_24H,
            channel=ConversationChannel.WHATSAPP,
            agency_name="Inmo Sol",
            language="en",
            forbidden_phrases=["Hi"],
        )
        is None
    )


# ---------------------------------------------------------------------------
# (164) Claim before sending, and skip leads the copy would insult
# ---------------------------------------------------------------------------


class _JournalDB:
    def __init__(self, *, last_message, last_inbound_at, lead, agency, journal):
        self._queue = [last_message, last_inbound_at]
        self.lead = lead
        self.agency = agency
        self.journal = journal
        self.added: list = []

    async def execute(self, _stmt):
        value = self._queue.pop(0)

        class _R:
            def scalars(self):
                return self

            def first(self):
                return value

            def scalar_one_or_none(self):
                return value

        return _R()

    async def get(self, model, pk):
        if model is Lead:
            return self.lead
        if model is Agency:
            return self.agency
        return None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.journal.append("commit")

    async def rollback(self):
        self.journal.append("rollback")


@pytest.fixture
def journal(monkeypatch):
    events: list[str] = []

    class _WA:
        def __init__(self, *_a, **_k):
            pass

        async def send_text(self, to, text):
            events.append(f"send:{text[:12]}")
            return "wamid.1"

    monkeypatch.setattr("app.channels.whatsapp.client.WhatsAppClient", _WA)
    return events


def _conv(agency_id, lead_id, **overrides):
    base = {
        "id": uuid.uuid4(),
        "agency_id": agency_id,
        "lead_id": lead_id,
        "channel": ConversationChannel.WHATSAPP,
        "extra": {},
        "created_at": datetime.now(UTC) - timedelta(days=5),
    }
    return SimpleNamespace(**{**base, **overrides})


def _lead(agency_id, **overrides):
    base = {
        "id": uuid.uuid4(),
        "agency_id": agency_id,
        "full_name": "Marta López",
        "phone_e164": "+34600111222",
        "whatsapp_wa_id": "34600111222",
        "email": "marta@example.com",
        "language": "es",
        "status": LeadStatus.QUALIFIED,
    }
    return SimpleNamespace(**{**base, **overrides})


async def _process(journal_events, *, lead=None, conv=None, agency=None):
    agency = agency or SimpleNamespace(id=uuid.uuid4(), name="Inmo Sol", settings={})
    lead = lead or _lead(agency.id)
    conv = conv or _conv(agency.id, lead.id)
    db = _JournalDB(
        last_message=SimpleNamespace(role=MessageRole.AGENT_AI, content="¿Qué presupuesto?"),
        last_inbound_at=datetime.now(UTC) - timedelta(hours=30),
        lead=lead,
        agency=agency,
        journal=journal_events,
    )
    step = await fu._process_conversation(db, conv, datetime.now(UTC))
    return step, conv, db


async def test_the_rung_is_committed_before_the_message_is_sent(journal):
    """acks_late + a 240 s soft limit: a sweep that dies after the send and
    before the commit re-delivers every message on the next run."""
    step, conv, _ = await _process(journal)

    assert step == STEP_24H
    assert journal[0] == "commit"
    assert any(event.startswith("send:") for event in journal)
    assert journal.index("commit") < next(
        i for i, e in enumerate(journal) if e.startswith("send:")
    )
    assert conv.extra[STEP_FLAG[STEP_24H]]


async def test_the_logged_message_is_committed_too(journal):
    await _process(journal)
    assert journal.count("commit") == 2


@pytest.mark.parametrize(
    "status",
    [
        LeadStatus.VISIT_SCHEDULED,
        LeadStatus.VISITED,
        LeadStatus.NEGOTIATING,
        LeadStatus.WON,
        LeadStatus.LOST,
    ],
)
async def test_a_lead_who_is_past_qualification_is_not_chased(journal, status):
    """"Cuéntame qué buscas y te preparo opciones" to someone with a visit
    booked for tomorrow reads as an agency that lost their file."""
    agency = SimpleNamespace(id=uuid.uuid4(), name="Inmo Sol", settings={})
    step, conv, _ = await _process(journal, agency=agency, lead=_lead(agency.id, status=status))

    assert step is None
    assert journal == []
    assert conv.extra == {}  # not burned: a cancelled visit makes them eligible again


@pytest.mark.parametrize("status", [LeadStatus.NEW, LeadStatus.ENGAGED, LeadStatus.QUALIFIED])
async def test_a_lead_still_in_the_funnel_is_followed_up(journal, status):
    agency = SimpleNamespace(id=uuid.uuid4(), name="Inmo Sol", settings={})
    step, _, _ = await _process(journal, agency=agency, lead=_lead(agency.id, status=status))
    assert step == STEP_24H


async def test_the_english_lead_gets_the_english_message_end_to_end(journal):
    agency = SimpleNamespace(id=uuid.uuid4(), name="Inmo Sol", settings={})
    await _process(journal, agency=agency, lead=_lead(agency.id, language="en"))
    assert any(event.startswith("send:Hi Marta") for event in journal)


# ---------------------------------------------------------------------------
# (164) The sweep keeps working for an agency with many quiet threads
# ---------------------------------------------------------------------------


async def test_finished_threads_are_excluded_by_the_query_not_by_the_loop():
    """Candidates are ordered oldest-first and capped at 200: a thread that
    already got both rungs would sit at the front of the queue for two weeks
    and starve the lead who went quiet last night."""
    captured: list[str] = []

    class _Capture:
        async def execute(self, stmt):
            captured.append(
                str(stmt.compile(compile_kwargs={"literal_binds": True}))
            )

            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _R()

    await fu._candidate_conversations(_Capture(), datetime.now(UTC))
    (sql,) = captured
    assert STEP_FLAG[STEP_72H] in sql
    assert "IS NULL" in sql


async def test_the_candidate_query_does_not_aggregate_the_whole_message_table():
    """The subquery groups `messages`; unbounded it is a full scan every 30
    minutes forever."""
    captured: list[str] = []

    class _Capture:
        async def execute(self, stmt):
            captured.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))

            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _R()

    await fu._candidate_conversations(_Capture(), datetime.now(UTC))
    (sql,) = captured
    subquery = sql.split("GROUP BY")[0]
    assert "messages.created_at >=" in subquery


# ---------------------------------------------------------------------------
# (218) The expensive model is for money on the table *now*
# ---------------------------------------------------------------------------


def _smart_lead(**extra):
    return SimpleNamespace(
        temperature=LeadTemperature.WARM,
        status=LeadStatus.QUALIFIED,
        extra=extra,
    )


def test_a_fresh_preapproval_buys_the_smart_model():
    lead = _smart_lead(
        mortgage_preapproval={"requested_at": datetime.now(UTC).isoformat()}
    )
    assert runner_mod.should_use_smart_model(lead, "whatsapp") is True


def test_a_preapproval_from_last_spring_does_not_bill_opus_forever():
    """HOT expires in 48 h and NEGOTIATING is a status somebody moves on; this
    flag is written once and never removed."""
    stale = datetime.now(UTC) - timedelta(days=runner_mod.MORTGAGE_PREAPPROVAL_SMART_DAYS + 1)
    lead = _smart_lead(mortgage_preapproval={"requested_at": stale.isoformat()})
    assert runner_mod.should_use_smart_model(lead, "whatsapp") is False


def test_an_undatable_preapproval_keeps_the_benefit_of_the_doubt():
    for blob in ({"confidence": "high"}, {"requested_at": "no-es-una-fecha"}, True):
        lead = _smart_lead(mortgage_preapproval=blob)
        assert runner_mod.should_use_smart_model(lead, "whatsapp") is True, blob


def test_an_empty_preapproval_blob_is_no_preapproval_at_all():
    assert runner_mod.should_use_smart_model(_smart_lead(mortgage_preapproval={}), "email") is False
    assert runner_mod.should_use_smart_model(_smart_lead(), "email") is False


def test_a_hot_lead_does_not_need_a_preapproval():
    lead = SimpleNamespace(
        temperature=LeadTemperature.HOT, status=LeadStatus.QUALIFIED, extra={}
    )
    assert runner_mod.should_use_smart_model(lead, "whatsapp") is True
    assert runner_mod.should_use_smart_model(lead, "voice") is False


async def test_a_broken_conversation_leaves_the_session_usable(monkeypatch):
    """Without the rollback the next conversation's commit would explode on a
    session poisoned by the previous failure."""
    events: list[str] = []

    class _Session:
        async def rollback(self):
            events.append("rollback")

    async def _candidates(_db, _now):
        return [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())]

    calls = iter([RuntimeError("boom"), STEP_24H])

    async def _one(_db, _conv, _now):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(fu, "_candidate_conversations", _candidates)
    monkeypatch.setattr(fu, "_process_conversation", _one)

    assert await fu.followup_sweep(_Session()) == {"sent_24h": 1, "sent_72h": 0}
    assert events == ["rollback"]


# Keep the import used: `Visit` documents what the collision query reads.
_ = Visit
_ = VISIT_COLLISION_MINUTES
