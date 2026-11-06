"""(153) Dunning: the owner learns the card failed, and we insist.

invoice.payment_failed sets PAST_DUE *and* emails the owner with a link to
manage the payment method; `billing.dunning_sweep` re-sends on day 3 and
day 7 while the subscription is still PAST_DUE, then stops.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from celery.schedules import crontab

from app.api import billing as billing_module
from app.db.models import SubscriptionStatus
from app.services.emails import build_payment_failed_email_html
from app.workers import tasks
from app.workers.celery_app import celery_app

NOW = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)
CUSTOMER = "cus_1"


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    def __init__(self, subscriptions, agencies, owner=None):
        self.subscriptions = list(subscriptions)
        self.agencies = {a.id: a for a in agencies}
        self.owner = owner
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model, pk):
        return self.agencies.get(pk)

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM users" in sql:
            return _Result([self.owner] if self.owner else [])
        if "FROM subscriptions" in sql:
            return _Result(self.subscriptions)
        raise AssertionError(f"unexpected query: {sql}")

    def add(self, obj):
        pass

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _scope(session):
    @asynccontextmanager
    async def _fake():
        yield session

    return _fake


def _agency(settings=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Inmo Sol",
        contact_email="dueno@inmosol.example.com",
        settings={} if settings is None else settings,
    )


def _subscription(agency_id, status=SubscriptionStatus.PAST_DUE):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency_id,
        status=status,
        stripe_customer_id=CUSTOMER,
        stripe_subscription_id="sub_1",
    )


@pytest.fixture
def sent_emails(monkeypatch):
    sent: list[dict] = []

    async def _fake_send(to, subject, html):
        sent.append({"to": to, "subject": subject, "html": html})
        return True

    monkeypatch.setattr(billing_module, "send_email", _fake_send)
    return sent


@pytest.fixture
def failing_send(monkeypatch):
    async def _fake_send(to, subject, html):
        return False

    monkeypatch.setattr(billing_module, "send_email", _fake_send)


# ---------------------------------------------------------------------------
# invoice.payment_failed
# ---------------------------------------------------------------------------


async def test_payment_failed_sets_past_due_and_emails_the_owner(sent_emails):
    agency = _agency()
    sub = _subscription(agency.id, status=SubscriptionStatus.ACTIVE)
    db = FakeSession([sub], [agency], owner=SimpleNamespace(email="owner@inmosol.example.com"))

    after_commit = await billing_module._on_payment_failed(db, {"customer": CUSTOMER})

    assert sub.status == SubscriptionStatus.PAST_DUE
    assert sent_emails == []
    assert after_commit is not None

    await db.commit()
    assert sent_emails == []
    await after_commit()

    assert len(sent_emails) == 1
    assert db.commits == 2
    mail = sent_emails[0]
    assert mail["to"] == "owner@inmosol.example.com"
    assert mail["subject"] == "No hemos podido cobrar tu suscripción de selai"
    assert "https://www.selai.es/billing" in mail["html"]


async def test_payment_failed_records_dunning_state(sent_emails):
    agency = _agency()
    sub = _subscription(agency.id)
    db = FakeSession([sub], [agency])

    after_commit = await billing_module._on_payment_failed(db, {"customer": CUSTOMER})

    assert agency.settings == {}
    assert after_commit is not None
    await after_commit()

    assert agency.settings["dunning_count"] == 1
    datetime.fromisoformat(agency.settings["dunning_last_sent"])
    assert agency.settings["dunning_started_at"] == agency.settings["dunning_last_sent"]


async def test_dunning_state_is_not_recorded_when_the_email_fails(failing_send):
    agency = _agency()
    sub = _subscription(agency.id)
    db = FakeSession([sub], [agency])

    after_commit = await billing_module._on_payment_failed(db, {"customer": CUSTOMER})

    assert after_commit is not None
    await after_commit()

    # PAST_DUE still applies; we just have not actually warned anyone yet, so
    # the sweep will try again tomorrow instead of counting a phantom email.
    assert sub.status == SubscriptionStatus.PAST_DUE
    assert "dunning_count" not in agency.settings


async def test_payment_failed_for_unknown_customer_is_a_noop(sent_emails):
    db = FakeSession([], [])
    after_commit = await billing_module._on_payment_failed(db, {"customer": "cus_desconocido"})
    assert after_commit is None
    assert sent_emails == []


async def test_recovering_clears_the_dunning_state(sent_emails):
    agency = _agency(
        {
            "dunning_count": 2,
            "dunning_last_sent": NOW.isoformat(),
            "dunning_started_at": NOW.isoformat(),
            "voice_id": "sonic-multilingual",
        }
    )
    sub = _subscription(agency.id)
    db = FakeSession([sub], [agency])

    await billing_module._on_subscription_updated(
        db,
        {"id": "sub_1", "customer": CUSTOMER, "status": "active", "items": {"data": []}},
    )

    assert sub.status == SubscriptionStatus.ACTIVE
    assert agency.settings == {"voice_id": "sonic-multilingual"}


# ---------------------------------------------------------------------------
# Daily sweep: day 3 and day 7
# ---------------------------------------------------------------------------


def _dunning_agency(days_ago: float, count: int):
    started = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    return _agency(
        {"dunning_count": count, "dunning_started_at": started, "dunning_last_sent": started}
    )


async def test_sweep_stays_quiet_before_day_three(monkeypatch, sent_emails):
    agency = _dunning_agency(days_ago=1, count=1)
    db = FakeSession([_subscription(agency.id)], [agency])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._dunning_sweep()

    assert sent_emails == []
    assert agency.settings["dunning_count"] == 1


async def test_sweep_resends_on_day_three(monkeypatch, sent_emails):
    agency = _dunning_agency(days_ago=3.5, count=1)
    db = FakeSession([_subscription(agency.id)], [agency])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._dunning_sweep()

    assert len(sent_emails) == 1
    assert agency.settings["dunning_count"] == 2


async def test_sweep_waits_until_day_seven_for_the_third(monkeypatch, sent_emails):
    agency = _dunning_agency(days_ago=5, count=2)
    db = FakeSession([_subscription(agency.id)], [agency])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._dunning_sweep()

    assert sent_emails == []

    agency.settings["dunning_started_at"] = (
        datetime.now(UTC) - timedelta(days=7.5)
    ).isoformat()
    await tasks._dunning_sweep()

    assert len(sent_emails) == 1
    assert agency.settings["dunning_count"] == 3


async def test_sweep_stops_after_the_third_reminder(monkeypatch, sent_emails):
    agency = _dunning_agency(days_ago=30, count=3)
    db = FakeSession([_subscription(agency.id)], [agency])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._dunning_sweep()

    assert sent_emails == []


async def test_sweep_sends_the_first_email_when_none_is_on_record(monkeypatch, sent_emails):
    """PAST_DUE can also arrive via customer.subscription.updated, which does
    not send anything — the sweep must not leave that agency in the dark."""
    agency = _agency()
    db = FakeSession([_subscription(agency.id)], [agency])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._dunning_sweep()

    assert len(sent_emails) == 1
    assert agency.settings["dunning_count"] == 1


async def test_sweep_survives_a_broken_timestamp(monkeypatch, sent_emails):
    agency = _agency({"dunning_count": 1, "dunning_started_at": "ayer por la tarde"})
    db = FakeSession([_subscription(agency.id)], [agency])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._dunning_sweep()  # must not raise

    assert sent_emails == []


def test_sweep_is_registered_daily():
    assert tasks.dunning_sweep.name == "billing.dunning_sweep"
    assert "billing.dunning_sweep" in celery_app.tasks

    entry = celery_app.conf.beat_schedule["billing-dunning-sweep"]
    assert entry["task"] == "billing.dunning_sweep"
    assert entry["schedule"] == crontab(minute=30, hour=9)


def test_retry_days_are_three_and_seven():
    assert tasks.DUNNING_RETRY_DAYS == (3, 7)


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------


def test_dunning_email_escalates_without_overpromising():
    first = build_payment_failed_email_html("Inmo Sol", attempt=1)
    third = build_payment_failed_email_html("Inmo Sol", attempt=3)

    assert "Inmo Sol" in first
    assert "Actualizar mi método de pago" in first
    assert "https://www.selai.es/billing" in first
    # Only the last reminder mentions cancelation, and attributes it to Stripe.
    assert "cancelará" not in first
    assert "cancelará" in third


def test_dunning_email_accepts_an_explicit_portal_url():
    html = build_payment_failed_email_html(
        "Inmo Sol", "https://billing.stripe.com/session/abc", attempt=2
    )
    assert "https://billing.stripe.com/session/abc" in html
