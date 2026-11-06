"""(152) An expired trial without a card pauses the account.

`lifecycle.trial_expiry_sweep` flips TRIALING -> PAUSED and sends the "tu
prueba ha terminado" email once; `plan_limits` then treats PAUSED as a
blocking status, so paid resources answer 402.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from celery.schedules import crontab
from fastapi import HTTPException

from app.db.models import SubscriptionStatus
from app.services import plan_limits
from app.services.emails import build_trial_ended_email_html
from app.workers import tasks
from app.workers.celery_app import celery_app

NOW = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows, scalar=None):
        self._rows = rows
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar


class FakeSession:
    """Routes queries by the table name that shows up in the SQL text."""

    def __init__(self, subscriptions, agencies, owner=None, counts=None):
        self.subscriptions = subscriptions
        self.agencies = {a.id: a for a in agencies}
        self.owner = owner
        self.counts = counts or {}
        self.commits = 0

    async def commit(self):
        # The sweep commits the pause, and then the "already emailed" flag,
        # per agency instead of once at the end: with `task_acks_late` a
        # worker killed mid-sweep replays the task from the top, and anything
        # still pending in the session was never on disk.
        self.commits += 1

    async def rollback(self):
        pass

    async def get(self, model, pk):
        return self.agencies.get(pk)

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM users" in sql:
            return _Result([self.owner] if self.owner else [])
        if "count" in sql.lower():
            for table, value in self.counts.items():
                if f"FROM {table}" in sql:
                    return _Result([], scalar=value)
            return _Result([], scalar=0)
        if "FROM subscriptions" in sql or "subscriptions." in sql:
            return _Result(self.subscriptions)
        raise AssertionError(f"unexpected query: {sql}")


def _scope(session):
    @asynccontextmanager
    async def _fake():
        yield session

    return _fake


def _agency(settings=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Inmo Demo",
        contact_email="dueno@inmodemo.example.com",
        settings=settings if settings is not None else {},
    )


def _subscription(agency_id, **kw):
    base = dict(
        id=uuid.uuid4(),
        agency_id=agency_id,
        status=SubscriptionStatus.TRIALING,
        trial_ends_at=NOW - timedelta(days=1),
        stripe_subscription_id=None,
        created_at=NOW - timedelta(days=15),
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def sent_emails(monkeypatch):
    sent: list[dict] = []

    async def _fake_send(to, subject, html):
        sent.append({"to": to, "subject": subject, "html": html})
        return True

    monkeypatch.setattr("app.services.emails.send_email", _fake_send)
    return sent


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


async def test_expired_trial_without_card_is_paused_and_emailed(monkeypatch, sent_emails):
    agency = _agency()
    sub = _subscription(agency.id)
    db = FakeSession(
        [sub],
        [agency],
        counts={"leads": 42, "conversations": 118, "visits": 9},
    )
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._trial_expiry_sweep()

    assert sub.status == SubscriptionStatus.PAUSED
    assert agency.settings["trial_ended_email_sent"] is True

    assert len(sent_emails) == 1
    mail = sent_emails[0]
    assert mail["to"] == "dueno@inmodemo.example.com"
    assert mail["subject"] == "Tu prueba de selai ha terminado"
    # The trial's figures are in the body — that is the whole point.
    assert "42" in mail["html"]
    assert "118" in mail["html"]
    assert "9" in mail["html"]
    assert "/billing" in mail["html"]


async def test_email_is_not_sent_twice(monkeypatch, sent_emails):
    agency = _agency({"trial_ended_email_sent": True})
    sub = _subscription(agency.id)
    db = FakeSession([sub], [agency])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._trial_expiry_sweep()

    # Still paused (that is not conditional on the email)...
    assert sub.status == SubscriptionStatus.PAUSED
    # ...but no second email.
    assert sent_emails == []


async def test_owner_email_wins_over_agency_contact(monkeypatch, sent_emails):
    agency = _agency()
    sub = _subscription(agency.id)
    owner = SimpleNamespace(email="owner@inmodemo.example.com")
    db = FakeSession([sub], [agency], owner=owner)
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._trial_expiry_sweep()

    assert sent_emails[0]["to"] == "owner@inmodemo.example.com"


async def test_sweep_survives_one_broken_agency(monkeypatch, sent_emails):
    good = _agency()
    orphan_sub = _subscription(uuid.uuid4())  # agency row missing
    good_sub = _subscription(good.id)
    db = FakeSession([orphan_sub, good_sub], [good])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._trial_expiry_sweep()

    assert orphan_sub.status == SubscriptionStatus.PAUSED
    assert good_sub.status == SubscriptionStatus.PAUSED
    assert len(sent_emails) == 1


async def test_sweep_without_recipient_still_pauses(monkeypatch, sent_emails):
    agency = _agency()
    agency.contact_email = None
    sub = _subscription(agency.id)
    db = FakeSession([sub], [agency])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._trial_expiry_sweep()

    assert sub.status == SubscriptionStatus.PAUSED
    assert sent_emails == []
    assert "trial_ended_email_sent" not in agency.settings


def test_sweep_query_only_targets_cardless_expired_trials(monkeypatch):
    """The filter is the safety net: a paying agency must never be paused."""
    captured: list[str] = []

    class _CapturingSession(FakeSession):
        async def execute(self, stmt):
            captured.append(str(stmt))
            return await super().execute(stmt)

    db = _CapturingSession([], [])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    import asyncio

    asyncio.run(tasks._trial_expiry_sweep())

    sql = captured[0]
    assert "subscriptions.status" in sql
    assert "trial_ends_at IS NOT NULL" in sql
    assert "trial_ends_at <" in sql
    assert "stripe_subscription_id IS NULL" in sql


# ---------------------------------------------------------------------------
# Registration + schedule
# ---------------------------------------------------------------------------


def test_task_is_registered_daily():
    assert tasks.trial_expiry_sweep.name == "lifecycle.trial_expiry_sweep"
    assert "lifecycle.trial_expiry_sweep" in celery_app.tasks

    entry = celery_app.conf.beat_schedule["lifecycle-trial-expiry-sweep"]
    assert entry["task"] == "lifecycle.trial_expiry_sweep"
    assert entry["schedule"] == crontab(minute=0, hour=5)


# ---------------------------------------------------------------------------
# PAUSED blocks paid resources
# ---------------------------------------------------------------------------


class _PlanStateSession:
    def __init__(self, sub, plan=None):
        self._sub = sub
        self._plan = plan

    async def execute(self, stmt):
        return _Result([], scalar=self._sub)

    async def get(self, model, pk):
        return self._plan


async def test_paused_subscription_is_blocked():
    assert SubscriptionStatus.PAUSED in plan_limits.BLOCKING_STATUSES

    sub = SimpleNamespace(
        status=SubscriptionStatus.PAUSED, plan_id=uuid.uuid4(), voice_minutes_used=0
    )
    db = _PlanStateSession(sub)

    with pytest.raises(HTTPException) as exc:
        await plan_limits.ensure_subscription_usable(db, uuid.uuid4())
    assert exc.value.status_code == 402
    assert exc.value.detail == {"code": "subscription_inactive", "status": "paused"}


async def test_active_subscription_is_not_blocked():
    sub = SimpleNamespace(
        status=SubscriptionStatus.ACTIVE, plan_id=uuid.uuid4(), voice_minutes_used=0
    )
    db = _PlanStateSession(sub)
    await plan_limits.ensure_subscription_usable(db, uuid.uuid4())  # must not raise


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------


def test_trial_ended_email_states_what_stopped():
    html = build_trial_ended_email_html("Inmo Demo", leads=5, conversations=12, visits=2)
    assert "Tu prueba ha terminado" in html
    assert "Inmo Demo" in html
    assert "pausado" in html
    assert "https://www.selai.es/billing" in html
    for value in ("5", "12", "2"):
        assert f">{value}<" in html
