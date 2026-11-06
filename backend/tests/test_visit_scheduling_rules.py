"""(221) office hours + collision rules for `schedule_visit`, and
(220) the lead's visits inside the agent's lead summary.

Before 221 the only check was "at least 30 minutes in the future", so the
model could book a Sunday at 03:00 or double-book the same agent, and every
impossible visit is a manual apology.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.agent import tools as tools_mod
from app.agent.runner import VISITS_HEADER, _summarize_visits, format_visit_datetime_es
from app.agent.tools import (
    VISIT_COLLISION_MINUTES,
    VISIT_SEARCH_DAYS,
    Agency,
    AgentContext,
    collides,
    describe_business_hours,
    execute_tool,
    render_slot_alternatives,
    slot_is_valid,
    suggest_visit_slots,
)
from app.db.models import Lead, Property, Visit, VisitStatus

MADRID = ZoneInfo("Europe/Madrid")
OFFICE = {"start": "09:00", "end": "19:00", "days": [0, 1, 2, 3, 4]}


def _future_local(weekday: int, hour: int, minute: int = 0) -> datetime:
    """The next `weekday` at `hour`:`minute` Madrid time, always > 2 days away."""
    base = datetime.now(MADRID).replace(hour=hour, minute=minute, second=0, microsecond=0)
    base += timedelta(days=2)
    while base.weekday() != weekday:
        base += timedelta(days=1)
    return base


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


def test_business_hours_are_described_as_a_range_when_contiguous():
    assert describe_business_hours(OFFICE) == "de lunes a viernes, de 09:00 a 19:00"


def test_scattered_business_days_are_listed():
    text = describe_business_hours({"start": "10:00", "end": "14:00", "days": [0, 2, 4]})
    assert text == "lunes, miércoles y viernes, de 10:00 a 14:00"


def test_a_single_business_day_reads_naturally():
    assert describe_business_hours({"start": "10:00", "end": "14:00", "days": [5]}).startswith(
        "solo los sábados"[:9]
    )


def test_a_missing_or_broken_config_falls_back_to_the_documented_default():
    for broken in (None, {}, {"start": "nope"}, {"days": "x"}, 7):
        assert describe_business_hours(broken) == "de lunes a viernes, de 09:00 a 19:00"


def test_a_sunday_at_three_in_the_morning_is_not_a_valid_slot():
    assert not slot_is_valid(
        _future_local(6, 3), tz=MADRID, business_hours=OFFICE, duration_minutes=30
    )


def test_a_tuesday_at_ten_is_a_valid_slot():
    assert slot_is_valid(
        _future_local(1, 10), tz=MADRID, business_hours=OFFICE, duration_minutes=30
    )


def test_a_visit_that_would_end_after_closing_time_is_rejected():
    """18:45 + 30 min spills past 19:00 and nobody is there to close the door."""
    assert not slot_is_valid(
        _future_local(1, 18, 45), tz=MADRID, business_hours=OFFICE, duration_minutes=30
    )


def test_collisions_use_a_forty_five_minute_window_both_ways():
    when = _future_local(1, 12)
    assert collides(when, [when + timedelta(minutes=30)], VISIT_COLLISION_MINUTES)
    assert collides(when, [when - timedelta(minutes=44)], VISIT_COLLISION_MINUTES)
    assert not collides(when, [when + timedelta(minutes=45)], VISIT_COLLISION_MINUTES)


def test_naive_datetimes_from_the_database_do_not_break_the_collision_check():
    when = datetime.now(UTC) + timedelta(days=3)
    naive_same_instant = when.replace(tzinfo=None)
    assert collides(when, [naive_same_instant], VISIT_COLLISION_MINUTES)


# ---------------------------------------------------------------------------
# Alternative slots
# ---------------------------------------------------------------------------


def _slots(desired, busy=None, **overrides):
    kwargs = {
        "desired": desired,
        "now": datetime.now(UTC),
        "tz": MADRID,
        "business_hours": OFFICE,
        "busy": busy or [],
    }
    return suggest_visit_slots(**{**kwargs, **overrides})


def test_alternatives_are_all_inside_office_hours():
    slots = _slots(_future_local(6, 3))
    assert len(slots) == 3
    for s in slots:
        assert slot_is_valid(s, tz=MADRID, business_hours=OFFICE, duration_minutes=30)


def test_alternatives_land_on_clean_half_hours_in_local_time():
    for slot in _slots(_future_local(1, 10, 7)):
        local = slot.astimezone(MADRID)
        assert local.minute in (0, 30)
        assert local.second == 0


def test_alternatives_are_never_offered_two_at_a_time():
    """Two slots 30 minutes apart read as the same appointment."""
    slots = _slots(_future_local(6, 3))
    for earlier, later in zip(slots, slots[1:], strict=False):
        assert (later - earlier).total_seconds() >= VISIT_COLLISION_MINUTES * 60


def test_alternatives_avoid_the_agenda_that_is_already_busy():
    tuesday = _future_local(1, 10)
    busy = [tuesday + timedelta(minutes=m) for m in range(0, 240, 30)]
    for slot in _slots(tuesday, busy=busy):
        assert not collides(slot, busy, VISIT_COLLISION_MINUTES)


def test_a_fully_booked_fortnight_returns_nothing_instead_of_a_bad_slot():
    start = datetime.now(UTC)
    busy = [start + timedelta(minutes=30 * i) for i in range(VISIT_SEARCH_DAYS * 48 + 4)]
    assert _slots(start + timedelta(hours=2), busy=busy) == []


def test_a_slot_in_the_past_is_pulled_forward_to_the_future():
    past = datetime.now(UTC) - timedelta(days=5)
    for slot in _slots(past):
        assert slot > datetime.now(UTC)


def test_the_message_to_the_model_carries_iso_dates_it_can_reuse():
    slots = _slots(_future_local(6, 3))
    text = render_slot_alternatives("fuera de horario.", slots, MADRID)
    assert "NO AGENDADA" in text
    assert "schedule_visit" in text
    for slot in slots:
        assert slot.astimezone(MADRID).isoformat(timespec="minutes") in text


def test_with_no_alternatives_the_model_is_told_not_to_invent_one():
    text = render_slot_alternatives("fuera de horario.", [], MADRID)
    assert "No inventes" in text
    assert "un agente" in text


# ---------------------------------------------------------------------------
# The tool itself
# ---------------------------------------------------------------------------


class _ScheduleDB:
    """Serves the property, the agency, the lead and the busy-slot query."""

    def __init__(self, *, agency, prop, lead, busy):
        self.agency = agency
        self.prop = prop
        self.lead = lead
        self.busy = busy
        self.added: list = []
        self.statements: list[str] = []

    async def get(self, model, pk):
        if model is Property:
            return self.prop
        if model is Agency:
            return self.agency
        if model is Lead:
            return self.lead
        return None

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        rows = [(b,) for b in self.busy]

        class _Result:
            def all(self):
                return rows

        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


@pytest.fixture
def scheduling(monkeypatch):
    agency_id = uuid.uuid4()
    agency = SimpleNamespace(
        id=agency_id, name="Inmo Sol", timezone="Europe/Madrid", settings={"business_hours": OFFICE}
    )
    prop = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency_id,
        reference="REF-1",
        title="Piso en Chamberí",
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

    async def _no_agent(db, *, agency_id, property_):
        return None

    monkeypatch.setattr(tools_mod, "pick_agent_for_visit", _no_agent)

    def _make(busy=None):
        db = _ScheduleDB(agency=agency, prop=prop, lead=lead, busy=busy or [])
        ctx = AgentContext(
            db=db,
            agency_id=agency_id,
            lead_id=lead.id,
            conversation_id=uuid.uuid4(),
            channel="whatsapp",
        )
        return db, ctx

    return SimpleNamespace(make=_make, prop=prop, lead=lead, agency=agency)


async def _schedule(ctx, when: datetime, **extra) -> str:
    return await execute_tool(
        "schedule_visit",
        {
            "property_id": str(ctx_property_id(ctx)),
            "scheduled_at_iso": when.isoformat(),
            **extra,
        },
        ctx,
    )


def ctx_property_id(ctx) -> uuid.UUID:
    return ctx.db.prop.id


async def test_a_sunday_at_dawn_is_refused_with_alternatives(scheduling):
    db, ctx = scheduling.make()
    result = await _schedule(ctx, _future_local(6, 3))
    assert result.startswith("NO AGENDADA")
    assert "horario de la agencia" in result
    assert db.added == []


async def test_a_valid_tuesday_morning_creates_the_visit(scheduling):
    db, ctx = scheduling.make()
    result = await _schedule(ctx, _future_local(1, 10))
    assert result.startswith("OK")
    assert len(db.added) == 1
    assert isinstance(db.added[0], Visit)


async def test_a_double_booking_is_refused_with_alternatives(scheduling):
    wanted = _future_local(1, 10)
    db, ctx = scheduling.make(busy=[wanted + timedelta(minutes=20)])
    result = await _schedule(ctx, wanted)
    assert result.startswith("NO AGENDADA")
    assert "ya hay otra visita" in result
    assert db.added == []


async def test_the_alternatives_offered_are_themselves_bookable(scheduling):
    wanted = _future_local(1, 10)
    busy = [wanted + timedelta(minutes=m) for m in (0, 30, 60)]
    db, ctx = scheduling.make(busy=busy)
    result = await _schedule(ctx, wanted)

    offered = [line for line in result.split("\n") if line.startswith("- ")]
    assert 1 <= len(offered) <= 3
    for line in offered:
        iso = line.split("→ ", 1)[1].strip()
        slot = datetime.fromisoformat(iso)
        assert slot_is_valid(slot, tz=MADRID, business_hours=OFFICE, duration_minutes=30)
        assert not collides(slot, busy, VISIT_COLLISION_MINUTES)


async def test_the_busy_query_is_tenant_scoped(scheduling):
    """Multi-tenant rule: every query filters by agency_id."""
    db, ctx = scheduling.make()
    await _schedule(ctx, _future_local(1, 10))
    assert any("visits.agency_id" in s for s in db.statements)


async def test_the_agency_can_widen_its_own_hours(scheduling):
    """A Saturday agency must be able to book on Saturday."""
    db, ctx = scheduling.make()
    scheduling.agency.settings = {
        "business_hours": {"start": "10:00", "end": "14:00", "days": [5]}
    }
    result = await _schedule(ctx, _future_local(5, 11))
    assert result.startswith("OK")


async def test_an_agency_without_business_hours_uses_the_documented_default(scheduling):
    db, ctx = scheduling.make()
    scheduling.agency.settings = {}
    assert (await _schedule(ctx, _future_local(6, 12))).startswith("NO AGENDADA")
    assert (await _schedule(ctx, _future_local(2, 12))).startswith("OK")


async def test_a_visit_in_the_past_still_fails_fast(scheduling):
    db, ctx = scheduling.make()
    result = await _schedule(ctx, datetime.now(UTC) - timedelta(days=1))
    assert result.startswith("ERROR")


# ---------------------------------------------------------------------------
# (220) Visits in the lead summary
# ---------------------------------------------------------------------------


class _SummaryDB:
    """First execute() serves visits, second serves properties."""

    def __init__(self, visits, properties):
        self._queue = [visits, properties]
        self.statements: list[str] = []

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        rows = self._queue.pop(0) if self._queue else []

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return rows

        return _Result()


def _visit(when, status=VisitStatus.CONFIRMED, property_id=None):
    return SimpleNamespace(
        scheduled_at=when, status=status, property_id=property_id or uuid.uuid4()
    )


def _prop(pid, reference="REF-9", title="Ático en Salamanca"):
    return SimpleNamespace(id=pid, reference=reference, title=title)


async def test_no_visits_means_no_section():
    db = _SummaryDB([], [])
    text = await _summarize_visits(
        db, agency_id=uuid.uuid4(), lead_id=uuid.uuid4(), tz=MADRID
    )
    assert text == ""


async def test_an_upcoming_visit_is_listed_as_upcoming():
    pid = uuid.uuid4()
    when = datetime.now(UTC) + timedelta(days=1, hours=2)
    db = _SummaryDB([_visit(when, property_id=pid)], [_prop(pid)])
    text = await _summarize_visits(db, agency_id=uuid.uuid4(), lead_id=uuid.uuid4(), tz=MADRID)

    assert VISITS_HEADER in text
    assert "Próximas:" in text
    assert "Anteriores:" not in text
    assert "REF-9" in text
    assert format_visit_datetime_es(when.astimezone(MADRID)) in text
    assert "confirmada" in text


async def test_a_no_show_is_reported_so_the_agent_can_be_tactful():
    pid = uuid.uuid4()
    when = datetime.now(UTC) - timedelta(days=1)
    db = _SummaryDB([_visit(when, status=VisitStatus.NO_SHOW, property_id=pid)], [_prop(pid)])
    text = await _summarize_visits(db, agency_id=uuid.uuid4(), lead_id=uuid.uuid4(), tz=MADRID)

    assert "Anteriores:" in text
    assert "no se presentó" in text
    assert "no-show" in text  # the directive tells the agent what to do with it


async def test_a_cancelled_future_visit_is_not_sold_as_upcoming():
    pid = uuid.uuid4()
    when = datetime.now(UTC) + timedelta(days=2)
    db = _SummaryDB([_visit(when, status=VisitStatus.CANCELLED, property_id=pid)], [_prop(pid)])
    text = await _summarize_visits(db, agency_id=uuid.uuid4(), lead_id=uuid.uuid4(), tz=MADRID)
    assert "Próximas:" not in text
    assert "cancelada" in text


async def test_the_summary_tells_the_agent_not_to_double_book():
    pid = uuid.uuid4()
    db = _SummaryDB(
        [_visit(datetime.now(UTC) + timedelta(days=1), property_id=pid)], [_prop(pid)]
    )
    text = await _summarize_visits(db, agency_id=uuid.uuid4(), lead_id=uuid.uuid4(), tz=MADRID)
    assert "No propongas una visita que ya está agendada" in text


async def test_a_naive_timestamp_from_the_database_is_handled():
    pid = uuid.uuid4()
    naive = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None)
    db = _SummaryDB([_visit(naive, property_id=pid)], [_prop(pid)])
    text = await _summarize_visits(db, agency_id=uuid.uuid4(), lead_id=uuid.uuid4(), tz=MADRID)
    assert "Próximas:" in text


async def test_the_visit_queries_are_tenant_scoped():
    pid = uuid.uuid4()
    db = _SummaryDB(
        [_visit(datetime.now(UTC) + timedelta(days=1), property_id=pid)], [_prop(pid)]
    )
    await _summarize_visits(db, agency_id=uuid.uuid4(), lead_id=uuid.uuid4(), tz=MADRID)
    assert any("visits.agency_id" in s for s in db.statements)
    assert any("properties.agency_id" in s for s in db.statements)


async def test_a_visit_whose_property_vanished_still_renders():
    db = _SummaryDB([_visit(datetime.now(UTC) + timedelta(days=1))], [])
    text = await _summarize_visits(db, agency_id=uuid.uuid4(), lead_id=uuid.uuid4(), tz=MADRID)
    assert "propiedad" in text
