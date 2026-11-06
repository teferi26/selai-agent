"""Items 223, 224, 184, 187 + beat wiring for the follow-up sweep.

The worker was the least defended process in the product: no time limits (a
hung LLM call held a slot forever), early acks (a redeploy dropped work), a
shared asyncpg pool across event loops, no Sentry (every background failure
died in a Railway log), and a blind retry that could dial a lead twice.

This file also covers the three beat entries added this wave: the 24h/72h
follow-up sweep, the 30-day winback and the properties/agents usage alert.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from celery.schedules import crontab

from app.services.plan_limits import LimitStatus
from app.workers import celery_app as celery_module
from app.workers import tasks
from app.workers.celery_app import celery_app

CYCLE = datetime.now(UTC).strftime("%Y-%m")


# ---------------------------------------------------------------------------
# 223 — Celery configuration
# ---------------------------------------------------------------------------


def test_tasks_have_a_time_limit():
    """A hung Anthropic call used to hold a worker slot forever; with
    concurrency=2, two of them stopped the whole queue."""
    conf = celery_app.conf
    assert conf.task_soft_time_limit == 240
    assert conf.task_time_limit == 300
    assert conf.task_soft_time_limit < conf.task_time_limit


def test_work_is_acked_late_and_not_prefetched():
    """acks_late returns the message to the broker if the worker dies
    mid-task; prefetch=1 stops one slow task from parking a queue of work on
    a busy worker while another sits idle."""
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1


# ---------------------------------------------------------------------------
# 223 — the DB pool does not survive the event loop that made it
# ---------------------------------------------------------------------------


class FakeEngine:
    def __init__(self):
        self.disposed = 0

    async def dispose(self):
        self.disposed += 1


@pytest.fixture
def fake_engine(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr("app.db.session.engine", engine)
    return engine


def test_run_disposes_the_pool_after_each_task(fake_engine):
    """asyncpg connections belong to the loop that opened them; left in the
    pool, the next task gets "attached to a different loop"."""

    async def _work():
        return "done"

    assert tasks._run(_work()) == "done"
    assert fake_engine.disposed == 1


def test_run_disposes_the_pool_even_when_the_task_raises(fake_engine):
    async def _boom():
        raise RuntimeError("task failed")

    with pytest.raises(RuntimeError):
        tasks._run(_boom())
    assert fake_engine.disposed == 1


def test_a_failing_dispose_does_not_mask_the_task_result(monkeypatch):
    class Broken:
        async def dispose(self):
            raise OSError("connection already gone")

    monkeypatch.setattr("app.db.session.engine", Broken())

    async def _work():
        return 42

    assert tasks._run(_work()) == 42


# ---------------------------------------------------------------------------
# 224 — Sentry in the worker
# ---------------------------------------------------------------------------


def test_sentry_is_initialized_in_the_worker_when_a_dsn_is_set(monkeypatch):
    captured: dict = {}

    def _fake_init(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(celery_module.settings, "sentry_dsn", "https://key@sentry.test/1")
    monkeypatch.setattr("sentry_sdk.init", _fake_init)

    assert celery_module.init_sentry() is True
    assert captured["dsn"] == "https://key@sentry.test/1"
    # Lead PII must never reach Sentry: same rule as the web process.
    assert captured["send_default_pii"] is False
    assert any(
        type(i).__name__ == "CeleryIntegration" for i in captured["integrations"]
    )


def test_no_dsn_means_no_sentry(monkeypatch):
    monkeypatch.setattr(celery_module.settings, "sentry_dsn", "")

    def _explode(**kwargs):
        raise AssertionError("must not initialize Sentry without a DSN")

    monkeypatch.setattr("sentry_sdk.init", _explode)
    assert celery_module.init_sentry() is False


# ---------------------------------------------------------------------------
# 223 — placing a call is not idempotent
# ---------------------------------------------------------------------------


class _Rows:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class OutboundSession:
    def __init__(self, leads, agency, events):
        self.leads = leads
        self.agency = agency
        self.events = events

    async def execute(self, stmt):
        return _Rows(rows=self.leads)

    async def get(self, model, pk):
        return self.agency if model.__name__ == "Agency" else None

    async def commit(self):
        self.events.append(("commit", [dict(lead.extra or {}) for lead in self.leads]))

    async def rollback(self):
        pass


@pytest.fixture
def outbound(monkeypatch):
    events: list = []
    lead = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=uuid.uuid4(),
        phone_e164="+34600111222",
        full_name="Marta",
        extra={},
    )
    agency = SimpleNamespace(id=lead.agency_id, settings={"vapi_assistant_id": "asst_1"})
    session = OutboundSession([lead], agency, events)

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(tasks, "session_scope", _scope)

    class FakeVapi:
        fail = False

        async def place_outbound_call(self, **kwargs):
            events.append(("call", dict(lead.extra or {})))
            if FakeVapi.fail:
                raise RuntimeError("vapi timed out")
            return {"id": "call_1"}

    monkeypatch.setattr("app.channels.voice.client.VapiClient", FakeVapi)
    return SimpleNamespace(events=events, lead=lead, session=session, vapi=FakeVapi)


async def test_the_call_is_claimed_before_the_phone_rings(outbound):
    await tasks._proactive_outbound_voice()
    kinds = [kind for kind, _ in outbound.events]
    assert kinds[: kinds.index("call")] == ["commit"]
    # The claim the worker committed is already visible when it dials.
    assert outbound.events[kinds.index("call")][1]["proactive_call_at"]


async def test_a_failed_call_keeps_the_claim(outbound):
    """A Vapi timeout can mean "the phone is already ringing". Calling the
    same person twice is worse than not calling."""
    outbound.vapi.fail = True
    try:
        await tasks._proactive_outbound_voice()
    finally:
        outbound.vapi.fail = False
    assert outbound.lead.extra["proactive_call_at"]


async def test_an_already_called_lead_is_skipped(outbound):
    outbound.lead.extra = {"proactive_call_at": "2026-07-01T17:00:00+00:00"}
    await tasks._proactive_outbound_voice()
    assert [kind for kind, _ in outbound.events] == []


def test_the_vapi_client_only_retries_failures_that_never_reached_vapi():
    """POST /call rings a real phone: retrying a read timeout or a 5xx can
    dial a lead three times."""
    import httpx

    from app.channels.voice.client import VapiClient

    policy = VapiClient.place_outbound_call.retry
    assert policy.retry(_attempt(httpx.ConnectError("refused"))) is True
    assert policy.retry(_attempt(httpx.ReadTimeout("slow"))) is False
    assert policy.retry(_attempt(httpx.HTTPStatusError("502", request=None, response=None))) is False


def _attempt(exc: Exception):
    """Minimal tenacity RetryCallState carrying a failed outcome."""
    from tenacity import Future, RetryCallState

    state = RetryCallState(retry_object=None, fn=None, args=(), kwargs={})
    state.outcome = Future(1)
    state.outcome.set_exception(exc)
    return state


# ---------------------------------------------------------------------------
# Contract 1 — lifecycle.followup_sweep
# ---------------------------------------------------------------------------


def test_the_followup_sweep_runs_every_thirty_minutes():
    entry = celery_app.conf.beat_schedule["lifecycle-followup-sweep"]
    assert entry["task"] == "lifecycle.followup_sweep"
    assert entry["schedule"] == crontab(minute="*/30")
    assert "lifecycle.followup_sweep" in celery_app.tasks


async def test_the_followup_sweep_delegates_to_the_service(monkeypatch):
    """This task owns the schedule and the session; the windows and the copy
    live in app/services/followups.py."""
    seen: list = []

    @asynccontextmanager
    async def _scope():
        yield "db-session"

    async def _sweep(db):
        seen.append(db)
        return {"sent_24h": 3, "sent_72h": 1}

    monkeypatch.setattr(tasks, "session_scope", _scope)
    monkeypatch.setitem(
        __import__("sys").modules,
        "app.services.followups",
        SimpleNamespace(followup_sweep=_sweep),
    )

    assert await tasks._followup_sweep() == {"sent_24h": 3, "sent_72h": 1}
    assert seen == ["db-session"]


async def test_the_followup_sweep_survives_a_missing_service(monkeypatch):
    """Beat must keep running every other entry if that module is not
    deployed yet."""
    import sys

    monkeypatch.setitem(sys.modules, "app.services.followups", None)
    assert await tasks._followup_sweep() == {"sent_24h": 0, "sent_72h": 0}


# ---------------------------------------------------------------------------
# Contract 2 — builders may return html, or (subject, html)
# ---------------------------------------------------------------------------


def test_email_builders_are_accepted_in_both_shapes():
    assert tasks._subject_and_html("<p>hola</p>", default_subject="Asunto") == (
        "Asunto",
        "<p>hola</p>",
    )
    assert tasks._subject_and_html(("Vuelve", "<p>hola</p>"), default_subject="Asunto") == (
        "Vuelve",
        "<p>hola</p>",
    )
    assert tasks._subject_and_html(("", "<p>x</p>"), default_subject="Asunto")[0] == "Asunto"


# ---------------------------------------------------------------------------
# 184 — winback before post-termination data deletion
# ---------------------------------------------------------------------------


class SweepSession:
    """Routes the three queries the sweeps make, by their SQL text.

    Records commits and rollbacks: the sweeps have to persist each agency's
    dedup marker before moving on, and a snapshot of what was in flight at
    the time is what proves it.
    """

    def __init__(self, subs, agency, counts):
        self.subs = subs
        self.agency = agency
        self.counts = counts
        self.commits: list[dict] = []
        self.rollbacks = 0

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM leads" in sql:
            return _Rows(scalar=self.counts.get("leads", 0))
        if "FROM conversations" in sql:
            return _Rows(scalar=self.counts.get("conversations", 0))
        return _Rows(rows=self.subs)

    async def get(self, model, pk):
        return self.agency if model.__name__ == "Agency" else None

    async def commit(self):
        self.commits.append(dict(self.agency.settings or {}))

    async def rollback(self):
        self.rollbacks += 1


def _canceled_sub(days_ago: float) -> SimpleNamespace:
    from app.db.models import SubscriptionStatus

    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=uuid.uuid4(),
        status=SubscriptionStatus.CANCELED,
        canceled_at=datetime.now(UTC) - timedelta(days=days_ago),
        updated_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


@pytest.fixture
def winback(monkeypatch):
    sent: list = []
    agency = SimpleNamespace(id=uuid.uuid4(), name="Inmo Sol", settings={})

    async def _send_email(*, to, subject, html):
        sent.append({"to": to, "subject": subject, "html": html})
        return True

    def _builder(agency_name, stats):
        return (f"{agency_name}: {stats['leads']} leads", f"<p>{stats}</p>")

    monkeypatch.setattr("app.services.emails.send_email", _send_email, raising=False)
    monkeypatch.setattr(
        "app.services.emails.build_winback_email_html", _builder, raising=False
    )

    async def _owner(db, ag):
        return "owner@inmosol.test"

    monkeypatch.setattr(tasks, "_agency_owner_email", _owner)
    return SimpleNamespace(sent=sent, agency=agency, monkeypatch=monkeypatch)


def _install_session(monkeypatch, session):
    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(tasks, "session_scope", _scope)


async def test_winback_emails_before_the_termination_purge_window(winback, monkeypatch):
    session = SweepSession(
        [_canceled_sub(23)], winback.agency, {"leads": 14, "conversations": 9}
    )
    _install_session(monkeypatch, session)

    await tasks._winback_sweep()

    assert len(winback.sent) == 1
    assert winback.sent[0]["to"] == "owner@inmosol.test"
    # The email carries what they kept missing since they left.
    assert "14 leads" in winback.sent[0]["subject"]
    assert winback.agency.settings["winback_sent"]


async def test_winback_is_sent_once(winback, monkeypatch):
    session = SweepSession([_canceled_sub(22)], winback.agency, {"leads": 3})
    _install_session(monkeypatch, session)

    await tasks._winback_sweep()
    await tasks._winback_sweep()
    assert len(winback.sent) == 1


@pytest.mark.parametrize("days", [10, 20.5, 26, 40])
async def test_winback_ignores_cancellations_outside_the_window(
    winback, monkeypatch, days
):
    session = SweepSession([_canceled_sub(days)], winback.agency, {"leads": 3})
    _install_session(monkeypatch, session)

    await tasks._winback_sweep()
    assert winback.sent == []


async def test_winback_never_guesses_the_cancellation_date(winback, monkeypatch):
    """`updated_at` is not a cancellation date and must not stand in for one.

    It moves on any write to the row — a billing webhook, or (until this
    wave) the monthly usage reset, which ran an unfiltered UPDATE over every
    subscription including cancellations from years ago. Weeks after
    each of those resets, an agency that left long ago would be told "esto ha
    pasado desde que te fuiste". The email asserts a date to
    the customer; without `canceled_at` we do not have one.
    """
    sub = _canceled_sub(23)
    sub.canceled_at = None
    session = SweepSession([sub], winback.agency, {"leads": 2})
    _install_session(monkeypatch, session)

    await tasks._winback_sweep()
    assert winback.sent == []


def test_the_monthly_counter_reset_leaves_cancelled_rows_alone():
    """The other half of the same bug, at the source.

    `updated_at` carries `onupdate=func.now()`, so the unfiltered
    `UPDATE subscriptions SET voice_minutes_used = 0` this used to be
    rewrote the timestamp on every row in the table, every month.
    """
    import inspect

    from sqlalchemy import update
    from sqlalchemy.dialects import postgresql

    from app.db.models import Subscription, SubscriptionStatus

    # The compiled statement still bumps updated_at — that is the column's
    # contract — so the protection has to be the WHERE clause.
    compiled = str(
        update(Subscription)
        .where(Subscription.status != SubscriptionStatus.CANCELED)
        .values(voice_minutes_used=0)
        .compile(dialect=postgresql.dialect())
    )
    assert "updated_at=now()" in compiled
    assert "WHERE" in compiled

    source = inspect.getsource(tasks._reset_usage_counters)
    assert ".where(" in source, "the reset must not touch every subscription row"


async def test_the_winback_marker_is_committed_before_the_next_agency(
    winback, monkeypatch
):
    """acks_late replays the whole task after a kill. A marker still sitting
    in the session was never on disk, so every agency already emailed in the
    killed run gets the winback twice."""
    session = SweepSession([_canceled_sub(23)], winback.agency, {"leads": 4})
    _install_session(monkeypatch, session)

    await tasks._winback_sweep()

    assert len(winback.sent) == 1
    # The commit happened *inside* the loop, carrying the flag with it.
    assert session.commits and session.commits[-1].get("winback_sent")


async def test_a_broken_agency_rolls_back_instead_of_poisoning_the_sweep(
    winback, monkeypatch
):
    """A failure from the database leaves the session in an aborted
    transaction; without a rollback every later commit in the same sweep
    raises too and nobody gets marked."""

    async def _boom(db, ag):
        raise RuntimeError("owner lookup exploded")

    monkeypatch.setattr(tasks, "_agency_owner_email", _boom)
    session = SweepSession([_canceled_sub(23)], winback.agency, {"leads": 2})
    _install_session(monkeypatch, session)

    await tasks._winback_sweep()
    assert session.rollbacks == 1


async def test_winback_is_not_marked_when_the_email_fails(winback, monkeypatch):
    async def _fails(*, to, subject, html):
        return False

    monkeypatch.setattr("app.services.emails.send_email", _fails, raising=False)
    session = SweepSession([_canceled_sub(23)], winback.agency, {"leads": 2})
    _install_session(monkeypatch, session)

    await tasks._winback_sweep()
    assert "winback_sent" not in winback.agency.settings


async def test_one_broken_agency_does_not_stop_the_sweep(winback, monkeypatch):
    async def _boom(db, ag):
        raise RuntimeError("owner lookup exploded")

    monkeypatch.setattr(tasks, "_agency_owner_email", _boom)
    session = SweepSession([_canceled_sub(23)], winback.agency, {"leads": 2})
    _install_session(monkeypatch, session)

    await tasks._winback_sweep()  # must not raise
    assert winback.sent == []


def test_winback_runs_daily():
    entry = celery_app.conf.beat_schedule["lifecycle-winback-sweep"]
    assert entry["task"] == "lifecycle.winback_sweep"
    assert entry["schedule"] == crontab(minute=0, hour=10)


# ---------------------------------------------------------------------------
# 187 — properties / agents usage alert at 90%
# ---------------------------------------------------------------------------


@pytest.fixture
def usage(monkeypatch):
    from app.db.models import SubscriptionStatus

    sent: list = []
    agency = SimpleNamespace(id=uuid.uuid4(), name="Inmo Sol", settings={})
    sub = SimpleNamespace(
        id=uuid.uuid4(), agency_id=agency.id, status=SubscriptionStatus.ACTIVE
    )

    async def _send_email(*, to, subject, html):
        sent.append({"to": to, "subject": subject, "html": html})
        return True

    def _builder(agency_name, resource, used, limit):
        return (f"{resource}: {used}/{limit}", f"<p>{agency_name}</p>")

    monkeypatch.setattr("app.services.emails.send_email", _send_email, raising=False)
    monkeypatch.setattr(
        "app.services.emails.build_resource_usage_alert_email_html",
        _builder,
        raising=False,
    )

    async def _owner(db, ag):
        return "owner@inmosol.test"

    monkeypatch.setattr(tasks, "_agency_owner_email", _owner)
    session = SweepSession([sub], agency, {})
    _install_session(monkeypatch, session)

    def _set_usage(*statuses):
        async def _get_usage(db, agency_id):
            return list(statuses)

        monkeypatch.setattr("app.services.plan_limits.get_usage", _get_usage)

    return SimpleNamespace(
        sent=sent, agency=agency, set_usage=_set_usage, session=session
    )


async def test_properties_at_ninety_percent_triggers_one_email(usage):
    usage.set_usage(
        LimitStatus("properties", 180, 200),
        LimitStatus("agents", 2, 10),
        LimitStatus("voice_minutes", 10, 300),
    )
    await tasks._resource_usage_alert()

    assert len(usage.sent) == 1
    assert usage.sent[0]["subject"] == "properties: 180/200"
    assert usage.agency.settings["properties_alert_sent_cycle"] == CYCLE


async def test_below_the_threshold_nothing_is_sent(usage):
    usage.set_usage(LimitStatus("properties", 170, 200))
    await tasks._resource_usage_alert()
    assert usage.sent == []


async def test_agents_and_properties_are_alerted_independently(usage):
    usage.set_usage(
        LimitStatus("properties", 200, 200), LimitStatus("agents", 9, 10)
    )
    await tasks._resource_usage_alert()

    assert {s["subject"] for s in usage.sent} == {"properties: 200/200", "agents: 9/10"}
    assert usage.agency.settings["agents_alert_sent_cycle"] == CYCLE
    assert usage.agency.settings["properties_alert_sent_cycle"] == CYCLE


async def test_the_alert_is_once_per_cycle(usage):
    usage.set_usage(LimitStatus("properties", 190, 200))
    await tasks._resource_usage_alert()
    await tasks._resource_usage_alert()
    assert len(usage.sent) == 1


async def test_each_resource_marker_is_committed_as_it_is_sent(usage):
    """Otherwise "one email per resource per cycle" is only true for runs
    that reach the end: a worker killed mid-sweep replays the task and the
    90% warning arrives again the same day, and the next, and the next."""
    usage.set_usage(LimitStatus("properties", 200, 200), LimitStatus("agents", 9, 10))
    await tasks._resource_usage_alert()

    commits = usage.session.commits
    assert len(commits) == 2
    # First commit carries only the first resource: it was flushed before the
    # second email was even composed.
    assert "properties_alert_sent_cycle" in commits[0]
    assert "agents_alert_sent_cycle" not in commits[0]
    assert "agents_alert_sent_cycle" in commits[1]


async def test_a_new_cycle_alerts_again(usage):
    usage.set_usage(LimitStatus("properties", 190, 200))
    usage.agency.settings = {"properties_alert_sent_cycle": "2020-01"}
    await tasks._resource_usage_alert()
    assert len(usage.sent) == 1


async def test_unlimited_plans_are_never_alerted(usage):
    """`max_* = -1` is the "no limit" convention; 90% of it is meaningless."""
    usage.set_usage(LimitStatus("properties", 5000, -1))
    await tasks._resource_usage_alert()
    assert usage.sent == []


async def test_voice_minutes_stay_with_their_own_task(usage):
    """lifecycle.voice_usage_alert owns voice at 80% with its own email; two
    emails for the same thing would be noise."""
    usage.set_usage(LimitStatus("voice_minutes", 299, 300))
    await tasks._resource_usage_alert()
    assert usage.sent == []


def test_the_usage_alert_runs_daily_apart_from_the_voice_one():
    entry = celery_app.conf.beat_schedule["lifecycle-resource-usage-alert"]
    assert entry["task"] == "lifecycle.resource_usage_alert"
    assert entry["schedule"] == crontab(minute=15, hour=9)
    voice = celery_app.conf.beat_schedule["lifecycle-voice-usage-alert"]
    assert entry["schedule"] != voice["schedule"]


# ---------------------------------------------------------------------------
# 223 — acks_late means every task must survive being run twice
# ---------------------------------------------------------------------------


class NoShowSession:
    """Serves the visits the sweep's own filter would still return."""

    def __init__(self, visits, lead, events):
        self.visits = visits
        self.lead = lead
        self.events = events

    async def execute(self, stmt):
        from app.db.models import VisitStatus

        open_states = {VisitStatus.REQUESTED, VisitStatus.CONFIRMED}
        return _Rows(rows=[v for v in self.visits if v.status in open_states])

    async def get(self, model, pk):
        return self.lead if model.__name__ == "Lead" else None

    async def commit(self):
        self.events.append(("commit", [v.status for v in self.visits]))

    async def rollback(self):
        pass


@pytest.fixture
def no_shows(monkeypatch):
    from app.db.models import VisitStatus

    events: list = []
    visit = SimpleNamespace(
        id=uuid.uuid4(), lead_id=uuid.uuid4(), status=VisitStatus.CONFIRMED
    )
    lead = SimpleNamespace(whatsapp_wa_id="34600111222", email=None, agency_id=uuid.uuid4())
    session = NoShowSession([visit], lead, events)
    _install_session(monkeypatch, session)

    class FakeWhatsApp:
        async def send_text(self, wa_id, text):
            events.append(("wa", None))
            return "wamid.test"

    class FakeResend:
        async def send(self, **kwargs):
            events.append(("email", None))

    monkeypatch.setattr("app.channels.whatsapp.client.WhatsAppClient", FakeWhatsApp)
    monkeypatch.setattr("app.channels.email.client.ResendClient", FakeResend)
    return SimpleNamespace(events=events, visit=visit)


async def test_the_no_show_status_is_committed_before_the_message(no_shows):
    """The status change *is* the claim: the query only picks up
    REQUESTED/CONFIRMED visits, so committing it first is what stops a replay
    telling the lead a second time that we waited for them."""
    await tasks._detect_no_shows()
    kinds = [kind for kind, _ in no_shows.events]
    assert kinds.index("commit") < kinds.index("wa")


async def test_a_replayed_no_show_sweep_sends_nothing(no_shows):
    await tasks._detect_no_shows()
    no_shows.events.clear()
    await tasks._detect_no_shows()
    assert [k for k, _ in no_shows.events] == []


class ReviewSession:
    def __init__(self, lead, agency, events):
        self.lead = lead
        self.agency = agency
        self.events = events

    async def get(self, model, pk):
        return self.lead if model.__name__ == "Lead" else self.agency

    async def commit(self):
        self.events.append(("commit", dict(self.lead.extra or {})))

    async def rollback(self):
        pass


async def test_the_google_review_request_is_marked_on_disk(monkeypatch):
    """One lead, but the same rule: a worker killed between the send and the
    end of the task replays it and the customer is asked twice."""
    from app.db.models import LeadStatus

    events: list = []
    lead = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=uuid.uuid4(),
        status=LeadStatus.WON,
        full_name="Marta Ruiz",
        whatsapp_wa_id="34600111222",
        email=None,
        extra={},
    )
    agency = SimpleNamespace(
        id=lead.agency_id,
        name="Inmo Sol",
        settings={"google_review_url": "https://g.page/inmosol"},
    )
    _install_session(monkeypatch, ReviewSession(lead, agency, events))

    class FakeWhatsApp:
        async def send_text(self, wa_id, text):
            events.append(("wa", None))
            return "wamid.test"

    monkeypatch.setattr("app.channels.whatsapp.client.WhatsAppClient", FakeWhatsApp)

    await tasks._request_google_review(lead.id)

    assert events[-1][0] == "commit"
    assert events[-1][1]["google_review_requested"]


class WebhookSession:
    def __init__(self, sub):
        self.sub = sub
        self.commits: list[int] = []

    async def get(self, model, pk):
        return self.sub

    async def commit(self):
        self.commits.append(self.sub.consecutive_failures)

    async def rollback(self):
        pass


async def test_a_failed_delivery_persists_its_own_failure_count(monkeypatch):
    """`session_scope` rolls back on the way out, so the increment this
    branch makes was discarded every single time: `consecutive_failures`
    never left 0 and the "disable after 20" breaker could not fire. A dead
    customer endpoint was retried for ever while their dashboard claimed the
    integration was healthy."""
    import httpx

    sub = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=uuid.uuid4(),
        is_active=True,
        secret="s3cret",
        target_url="https://customer.test/hook",
        consecutive_failures=0,
        last_success_at=None,
        last_failure_at=None,
    )
    session = WebhookSession(sub)
    _install_session(monkeypatch, session)

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content=None, headers=None):
            return SimpleNamespace(status_code=502)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    with pytest.raises(RuntimeError):
        await tasks._deliver_webhook(sub.id, "lead.created", {"id": "1"})

    assert sub.consecutive_failures == 1
    assert session.commits == [1], "the counter must be committed before re-raising"


async def test_twenty_failures_disable_the_subscription(monkeypatch):
    import httpx

    sub = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=uuid.uuid4(),
        is_active=True,
        secret="s3cret",
        target_url="https://customer.test/hook",
        consecutive_failures=19,
        last_success_at=None,
        last_failure_at=None,
    )
    _install_session(monkeypatch, WebhookSession(sub))

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content=None, headers=None):
            raise ConnectionError("host unreachable")

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    # (n24) La tarea ya no POSTea a mano: delega en `deliver_now`, que nunca
    # lanza y devuelve el motivo. Lo que sube hasta Celery es un RuntimeError
    # con ese motivo — sigue habiendo excepcion (los reintentos dependen de
    # ella), pero el destino se comprueba antes de salir.
    with pytest.raises(RuntimeError):
        await tasks._deliver_webhook(sub.id, "lead.created", {"id": "1"})

    assert sub.consecutive_failures == 20
    assert sub.is_active is False


def test_the_new_tasks_are_registered():
    for name in (
        "lifecycle.followup_sweep",
        "lifecycle.winback_sweep",
        "lifecycle.resource_usage_alert",
    ):
        assert name in celery_app.tasks


# ---------------------------------------------------------------------------
# (n21) El stub sin credenciales no cuenta como enviado en producción
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_dev_stub_is_not_a_delivery_in_production(monkeypatch):
    """El peor modo de fallo del worker: sin credenciales de Meta, el cliente
    devolvía "dev-stub" sin lanzar, la tarea marcaba el recordatorio como
    entregado y el cliente no recibía nada. Nadie se enteraba."""

    class _Db:
        async def get(self, model, pk):
            return None  # sin agencia -> credenciales globales (ausentes)

    monkeypatch.setattr(tasks.settings, "app_env", "production")

    class StubWhatsApp:
        def __init__(self, **kwargs):
            pass

        async def send_text(self, to, text):
            return "dev-stub"

    monkeypatch.setattr("app.channels.whatsapp.client.WhatsAppClient", StubWhatsApp)

    sent = await tasks.send_whatsapp_as_agency(_Db(), uuid.uuid4(), "34600", "hola")
    assert sent is False

    # Y fuera de producción el stub sigue valiendo (tests y desarrollo local).
    monkeypatch.setattr(tasks.settings, "app_env", "development")
    sent = await tasks.send_whatsapp_as_agency(_Db(), uuid.uuid4(), "34600", "hola")
    assert sent is True


@pytest.mark.anyio
async def test_the_agencys_own_credentials_win(monkeypatch):
    """Una agencia con su propia app de Meta envía desde SU número."""
    seen: dict = {}

    class SpyWhatsApp:
        def __init__(self, access_token=None, phone_number_id=None):
            seen["token"] = access_token
            seen["phone"] = phone_number_id

        async def send_text(self, to, text):
            return "wamid.real"

    monkeypatch.setattr("app.channels.whatsapp.client.WhatsAppClient", SpyWhatsApp)

    agency = SimpleNamespace(
        settings={
            "whatsapp_access_token": "token-de-la-agencia",
            "whatsapp_phone_number_id": "555",
        }
    )

    class _Db:
        async def get(self, model, pk):
            return agency

    sent = await tasks.send_whatsapp_as_agency(_Db(), uuid.uuid4(), "34600", "hola")
    assert sent is True
    assert seen == {"token": "token-de-la-agencia", "phone": "555"}


# ---------------------------------------------------------------------------
# (n24) El webhook del worker ya no es una primitiva de SSRF
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_an_internal_target_is_blocked_and_counted_as_a_failure(monkeypatch):
    """`target_url` la elige cualquier miembro de la agencia.

    La tarea POSTeaba directamente ahí, así que cada evento de un lead era
    una petición emitida desde dentro de nuestra red al destino que quisieran:
    `http://169.254.169.254/` devuelve las credenciales de la máquina en la
    nube. La guarda existía solo en el botón de "enviar prueba".

    Y cuenta como fallo, no se descarta: si no contara, el corte tras 20
    fallos no saltaría nunca y la agencia jamás sabría que su URL no vale.
    """
    sub = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=uuid.uuid4(),
        is_active=True,
        secret="s3cret",
        target_url="http://169.254.169.254/latest/meta-data/",
        consecutive_failures=0,
        last_success_at=None,
        last_failure_at=None,
    )
    _install_session(monkeypatch, WebhookSession(sub))

    posted: list = []

    class ExplodingClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content=None, headers=None):
            posted.append(url)
            raise AssertionError("no debería salir NINGUNA petición a un destino interno")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", ExplodingClient)

    with pytest.raises(RuntimeError):
        await tasks._deliver_webhook(sub.id, "lead.created", {"id": "1"})

    assert posted == []
    assert sub.consecutive_failures == 1
