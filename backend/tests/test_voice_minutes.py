"""(151) Voice minutes actually get metered.

`_on_call_end` must add the call's duration (rounded up to whole minutes)
to the agency's `Subscription.voice_minutes_used`, exactly once per call id,
and `billing.reset_usage_counters` must zero it at cycle rollover.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from celery.schedules import crontab

from app.api import voice as voice_module
from app.db.models import ConversationStatus
from app.workers import tasks
from app.workers.celery_app import celery_app

AGENCY_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Pure rounding core
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "minutes"),
    [
        (0, 0),
        (1, 1),
        (59, 1),
        (60, 1),
        (61, 2),
        (599, 10),
        (600, 10),
        (601, 11),
        (90.5, 2),
        ("120", 2),
        (None, 0),
        ("no-soy-un-numero", 0),
        (-30, 0),
    ],
)
def test_seconds_round_up_to_whole_minutes(seconds, minutes):
    assert voice_module.seconds_to_billable_minutes(seconds) == minutes


# ---------------------------------------------------------------------------
# _on_call_end accumulates onto the subscription
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """Answers only the queries `_on_call_end` issues."""

    def __init__(self, subscription, message_count: int = 0):
        self.subscription = subscription
        self.message_count = message_count
        self.added: list = []

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM conversations" in sql:
            # No conversation is on file for this call id, so `_on_call_end`
            # falls back to identity matching (patched by the `wired` fixture).
            return _Result(None)
        if "subscriptions" in sql:
            return _Result(self.subscription)
        if "count" in sql.lower():
            return _Result(self.message_count)
        raise AssertionError(f"unexpected query: {sql}")

    async def get(self, model, pk):
        return None

    def add(self, obj):
        self.added.append(obj)


def _subscription(used: int = 0):
    return SimpleNamespace(agency_id=AGENCY_ID, voice_minutes_used=used)


def _conversation(extra=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        extra=extra or {},
        status=ConversationStatus.CLOSED,
    )


def _lead():
    return SimpleNamespace(id=uuid.uuid4(), extra={})


@pytest.fixture
def wired(monkeypatch):
    """Patch agency resolution + lead/conversation lookup out of the way."""
    lead, conv = _lead(), _conversation()

    async def _resolve_agency(db, metadata):
        return AGENCY_ID

    async def _resolve_lead(db, agency_id, identity):
        return lead, conv

    monkeypatch.setattr(voice_module, "_resolve_agency", _resolve_agency)
    monkeypatch.setattr(voice_module, "resolve_lead_and_conversation", _resolve_lead)
    return SimpleNamespace(lead=lead, conversation=conv)


def _body(call_id: str = "call_1", duration=125):
    return {
        "message": {
            "type": "end-of-call-report",
            "call": {"id": call_id, "customer": {"number": "+34600111222"}},
            "messages": [],
            "durationSeconds": duration,
            "summary": "Quiere ver el ático el sábado.",
        }
    }


async def test_call_end_adds_rounded_up_minutes(wired):
    sub = _subscription(used=10)
    db = FakeSession(sub)

    await voice_module._on_call_end(db, _body(duration=125))

    # 125 s -> 3 min (2 min 5 s rounds up).
    assert sub.voice_minutes_used == 13


async def test_call_end_accumulates_across_calls(wired):
    sub = _subscription(used=0)
    db = FakeSession(sub)

    await voice_module._on_call_end(db, _body(call_id="call_a", duration=30))
    await voice_module._on_call_end(db, _body(call_id="call_b", duration=200))

    assert sub.voice_minutes_used == 1 + 4


async def test_retried_webhook_does_not_double_charge(wired):
    sub = _subscription(used=0)
    db = FakeSession(sub)

    await voice_module._on_call_end(db, _body(call_id="call_dup", duration=125))
    await voice_module._on_call_end(db, _body(call_id="call_dup", duration=125))

    assert sub.voice_minutes_used == 3
    assert len(wired.conversation.extra["calls"]) == 1


async def test_zero_duration_bills_nothing(wired):
    sub = _subscription(used=7)
    db = FakeSession(sub)

    await voice_module._on_call_end(db, _body(duration=0))

    assert sub.voice_minutes_used == 7


async def test_agency_without_subscription_is_not_metered(wired):
    db = FakeSession(subscription=None)

    await voice_module._on_call_end(db, _body(duration=125))  # must not raise

    assert wired.conversation.extra["calls"][0]["duration_seconds"] == 125


# ---------------------------------------------------------------------------
# Monthly reset
# ---------------------------------------------------------------------------


def test_reset_task_is_registered_and_monthly():
    assert tasks.reset_usage_counters.name == "billing.reset_usage_counters"
    assert "billing.reset_usage_counters" in celery_app.tasks

    entry = celery_app.conf.beat_schedule["billing-reset-usage-counters"]
    assert entry["task"] == "billing.reset_usage_counters"
    assert entry["schedule"] == crontab(minute=0, hour=2, day_of_month=1)


async def test_reset_zeroes_voice_minutes_for_everyone(monkeypatch):
    executed: list[str] = []

    class _ResetSession:
        async def execute(self, stmt):
            executed.append(str(stmt))
            return SimpleNamespace(rowcount=3)

    monkeypatch.setattr(tasks, "session_scope", _scope(_ResetSession()))

    await tasks._reset_usage_counters()

    assert len(executed) == 1
    sql = executed[0]
    assert "UPDATE subscriptions" in sql
    assert "voice_minutes_used" in sql


def _scope(session):
    """Minimal stand-in for `app.db.session.session_scope`."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake():
        yield session

    return _fake
