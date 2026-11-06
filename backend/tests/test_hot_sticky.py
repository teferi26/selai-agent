"""(165) A HOT temperature set by the agent survives the deterministic recompute."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.agent.tools import AgentContext, execute_tool
from app.db.models import Lead, LeadStatus, LeadTemperature
from app.services.conversation_service import should_alert_hot
from app.services.lead_scoring import (
    HOT_STICKY_HOURS,
    STICKY_HOT_LABEL,
    clear_hot_mark,
    hot_mark_is_active,
    mark_temperature_hot,
    recompute_lead_score,
)


def _lead(**overrides) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "agency_id": uuid.uuid4(),
        "full_name": None,
        "email": None,
        "phone_e164": None,
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
        "status": LeadStatus.NEW,
        "temperature": LeadTemperature.COLD,
        "score": 0,
        "extra": {},
        "qualification_notes": "",
    }
    return SimpleNamespace(**{**base, **overrides})


def _hours_ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


# ---------------------------------------------------------------------------
# The mark itself
# ---------------------------------------------------------------------------


def test_mark_temperature_hot_stamps_who_and_when():
    lead = _lead()
    mark_temperature_hot(lead, source="update_lead_temperature")
    assert lead.temperature == LeadTemperature.HOT
    assert lead.extra["hot_marked_by"] == "update_lead_temperature"
    datetime.fromisoformat(lead.extra["hot_marked_at"])
    assert hot_mark_is_active(lead) is True


def test_mark_temperature_hot_keeps_the_rest_of_extra():
    lead = _lead(extra={"foreign_buyer_mode": True})
    mark_temperature_hot(lead, source="schedule_visit")
    assert lead.extra["foreign_buyer_mode"] is True


def test_hot_mark_expires_after_the_sticky_window():
    assert HOT_STICKY_HOURS == 48
    assert hot_mark_is_active(_lead(extra={"hot_marked_at": _hours_ago(47)})) is True
    assert hot_mark_is_active(_lead(extra={"hot_marked_at": _hours_ago(49)})) is False


def test_hot_mark_ignores_missing_or_broken_timestamps():
    assert hot_mark_is_active(_lead()) is False
    assert hot_mark_is_active(_lead(extra={"hot_marked_at": "ayer por la tarde"})) is False
    assert hot_mark_is_active(_lead(extra={"hot_marked_at": None})) is False


def test_hot_mark_accepts_a_naive_timestamp_as_utc():
    naive = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    assert hot_mark_is_active(_lead(extra={"hot_marked_at": naive})) is True


def test_clear_hot_mark_removes_both_keys():
    lead = _lead()
    mark_temperature_hot(lead, source="tool")
    clear_hot_mark(lead)
    assert "hot_marked_at" not in lead.extra
    assert "hot_marked_by" not in lead.extra
    assert hot_mark_is_active(lead) is False


# ---------------------------------------------------------------------------
# recompute_lead_score
# ---------------------------------------------------------------------------


class _FakeResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _FakeDB:
    """`recompute_lead_score` only needs get() + two empty result sets."""

    def __init__(self, lead):
        self.lead = lead

    async def get(self, model, pk):
        return self.lead

    async def execute(self, stmt):
        return _FakeResult()


async def test_recompute_downgrades_an_unmarked_cold_lead():
    lead = _lead(temperature=LeadTemperature.HOT)
    breakdown = await recompute_lead_score(_FakeDB(lead), lead.id)
    assert breakdown.temperature == "cold"
    assert lead.temperature == LeadTemperature.COLD


async def test_recompute_respects_a_fresh_hot_mark():
    lead = _lead(temperature=LeadTemperature.HOT, extra={"hot_marked_at": _hours_ago(2)})
    breakdown = await recompute_lead_score(_FakeDB(lead), lead.id)
    assert lead.temperature == LeadTemperature.HOT
    assert breakdown.temperature == "hot"
    # The score is not inflated — only the temperature is pinned.
    assert breakdown.total == 0
    sticky = [c for c in breakdown.components if c.label == STICKY_HOT_LABEL]
    assert len(sticky) == 1 and sticky[0].points == 0


async def test_recompute_recalculates_normally_once_the_mark_expired():
    lead = _lead(temperature=LeadTemperature.HOT, extra={"hot_marked_at": _hours_ago(49)})
    breakdown = await recompute_lead_score(_FakeDB(lead), lead.id)
    assert breakdown.temperature == "cold"
    assert lead.temperature == LeadTemperature.COLD
    assert all(c.label != STICKY_HOT_LABEL for c in breakdown.components)


async def test_recompute_keeps_the_mark_alongside_the_breakdown():
    marked = _hours_ago(1)
    lead = _lead(extra={"hot_marked_at": marked})
    await recompute_lead_score(_FakeDB(lead), lead.id)
    assert lead.extra["hot_marked_at"] == marked
    assert lead.extra["score_breakdown"]["temperature"] == "hot"


async def test_a_high_score_is_still_hot_without_any_mark():
    lead = _lead(
        budget_max=400000,
        preferred_cities=["Madrid"],
        min_bedrooms=3,
        operation="sale",
        full_name="Juan",
        email="j@e.com",
        phone_e164="+34600111222",
        must_haves=["parking"],
        status=LeadStatus.NEGOTIATING,
    )
    breakdown = await recompute_lead_score(_FakeDB(lead), lead.id)
    assert breakdown.total >= 70
    assert breakdown.temperature == "hot"
    assert all(c.label != STICKY_HOT_LABEL for c in breakdown.components)


# ---------------------------------------------------------------------------
# The tool that marks / unmarks
# ---------------------------------------------------------------------------


class _ToolDB:
    def __init__(self, lead):
        self.lead = lead

    async def get(self, model, pk):
        return self.lead if model is Lead else None

    async def flush(self):
        return None


def _ctx(lead) -> AgentContext:
    return AgentContext(
        db=_ToolDB(lead),
        agency_id=uuid.uuid4(),
        lead_id=lead.id,
        conversation_id=uuid.uuid4(),
        channel="whatsapp",
    )


async def test_update_lead_temperature_hot_leaves_a_sticky_mark():
    lead = _lead()
    await execute_tool(
        "update_lead_temperature",
        {"temperature": "hot", "reasoning": "paga al contado y tiene prisa"},
        _ctx(lead),
    )
    assert lead.temperature == LeadTemperature.HOT
    assert hot_mark_is_active(lead) is True

    # ...and the recompute no longer buries it.
    breakdown = await recompute_lead_score(_FakeDB(lead), lead.id)
    assert breakdown.temperature == "hot"


async def test_an_explicit_downgrade_clears_the_mark():
    lead = _lead()
    await execute_tool("update_lead_temperature", {"temperature": "hot"}, _ctx(lead))
    await execute_tool("update_lead_temperature", {"temperature": "cold"}, _ctx(lead))
    assert lead.temperature == LeadTemperature.COLD
    assert hot_mark_is_active(lead) is False


# ---------------------------------------------------------------------------
# The alert fires when the tool marks hot, not only when the recompute does
# ---------------------------------------------------------------------------


def test_alert_fires_when_the_recompute_turns_the_lead_hot():
    assert (
        should_alert_hot(
            before_turn=LeadTemperature.WARM,
            after_agent=LeadTemperature.WARM,
            after_recompute=LeadTemperature.HOT,
        )
        is True
    )


def test_alert_fires_when_a_tool_marked_hot_during_the_turn():
    assert (
        should_alert_hot(
            before_turn=LeadTemperature.COLD,
            after_agent=LeadTemperature.HOT,
            after_recompute=LeadTemperature.WARM,
        )
        is True
    )


def test_alert_does_not_repeat_for_an_already_hot_lead():
    assert (
        should_alert_hot(
            before_turn=LeadTemperature.HOT,
            after_agent=LeadTemperature.HOT,
            after_recompute=LeadTemperature.HOT,
        )
        is False
    )


def test_no_alert_when_the_lead_never_gets_hot():
    assert (
        should_alert_hot(
            before_turn=LeadTemperature.COLD,
            after_agent=LeadTemperature.WARM,
            after_recompute=LeadTemperature.WARM,
        )
        is False
    )
