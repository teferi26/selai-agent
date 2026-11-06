"""Item 225 — one visit reminder per window, not four.

The 24h window is 60 minutes wide and Beat runs `visits.send_reminders`
every 15, so the same visit was picked up up to four times and the lead got
four identical WhatsApps from the agency. Spam like that burns the
inmobiliaria's image with its own client.

The fix is a flag in `Visit.extra`, written and committed *before* sending,
and released again only if nothing went out.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.workers import tasks


class FakeVisit:
    def __init__(self, extra: dict | None = None):
        self.id = uuid.uuid4()
        self.lead_id = uuid.uuid4()
        self.property_id = uuid.uuid4()
        self.scheduled_at = datetime.now(UTC) + timedelta(hours=24)
        self.extra = extra or {}


class FakeLead:
    def __init__(self, wa: str | None = "34600111222", email: str | None = None):
        self.whatsapp_wa_id = wa
        self.email = email
        self.full_name = "Marta"
        self.agency_id = uuid.uuid4()


class FakeProperty:
    address = "Calle Mayor 3"
    title = "Ático con terraza"


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    """Serves the visits on the first window only, then nothing.

    `_send_visit_reminders` scans two windows (24h and 1h); a visit belongs
    to exactly one of them, so returning rows once mirrors reality and keeps
    the assertions about a single flag readable.
    """

    def __init__(self, visits, lead, prop, events):
        self.visits = visits
        self.lead = lead
        self.prop = prop
        self.events = events
        self.queries = 0

    async def execute(self, stmt):
        self.queries += 1
        return _Scalars(self.visits if self.queries == 1 else [])

    async def get(self, model, pk):
        if model.__name__ == "Lead":
            return self.lead
        if model.__name__ == "Property":
            return self.prop
        return None

    async def commit(self):
        self.events.append(("commit", [dict(v.extra) for v in self.visits]))

    async def rollback(self):
        pass


@pytest.fixture
def harness(monkeypatch):
    events: list = []
    visit = FakeVisit()
    lead = FakeLead()
    session = FakeSession([visit], lead, FakeProperty(), events)

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(tasks, "session_scope", _scope)

    class FakeWhatsApp:
        fail = False

        async def send_text(self, wa_id, text):
            events.append(("wa", dict(visit.extra)))
            if FakeWhatsApp.fail:
                raise RuntimeError("meta is down")
            # Como el cliente real: el id del mensaje es la prueba de entrega.
            return "wamid.test"

    class FakeResend:
        fail = False

        async def send(self, *, to, subject, text):
            events.append(("email", dict(visit.extra)))
            if FakeResend.fail:
                raise RuntimeError("resend is down")

    monkeypatch.setattr("app.channels.whatsapp.client.WhatsAppClient", FakeWhatsApp)
    monkeypatch.setattr("app.channels.email.client.ResendClient", FakeResend)

    return {
        "events": events,
        "visit": visit,
        "lead": lead,
        "session": session,
        "wa": FakeWhatsApp,
        "resend": FakeResend,
    }


def _kinds(events) -> list[str]:
    return [kind for kind, _ in events]


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def test_flag_names_follow_the_windows_that_exist():
    """The code schedules a 24h and a 1h reminder; the flags say so."""
    assert tasks.reminder_flag("24h") == "reminder_24h_sent"
    assert tasks.reminder_flag("1h") == "reminder_1h_sent"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_a_reminder_is_sent_once_and_marked(harness):
    await tasks._send_visit_reminders()
    assert _kinds(harness["events"]).count("wa") == 1
    assert harness["visit"].extra["reminder_24h_sent"]


async def test_the_flag_is_committed_before_the_message_goes_out(harness):
    """Ordering is the whole guarantee: a crash between the two must lose the
    message, never duplicate it."""
    await tasks._send_visit_reminders()
    events = harness["events"]
    wa_index = _kinds(events).index("wa")
    assert wa_index > 0
    assert events[wa_index - 1][0] == "commit"
    # ...and the flag the worker committed is the one it then acted on.
    assert events[wa_index][1]["reminder_24h_sent"]


async def test_a_second_sweep_sends_nothing(harness):
    """Beat runs every 15 min over a 60 min window: this is the bug."""
    await tasks._send_visit_reminders()
    harness["session"].queries = 0  # the visit is still inside the window
    harness["events"].clear()

    await tasks._send_visit_reminders()
    assert "wa" not in _kinds(harness["events"])


async def test_four_sweeps_send_one_message(harness):
    for _ in range(4):
        harness["session"].queries = 0
        await tasks._send_visit_reminders()
    assert _kinds(harness["events"]).count("wa") == 1


# ---------------------------------------------------------------------------
# Failure path: the claim is released so the next pass retries
# ---------------------------------------------------------------------------


async def test_a_failed_send_releases_the_flag(harness):
    harness["wa"].fail = True
    try:
        await tasks._send_visit_reminders()
    finally:
        harness["wa"].fail = False
    assert "reminder_24h_sent" not in harness["visit"].extra


async def test_the_next_sweep_retries_after_a_failure(harness):
    harness["wa"].fail = True
    await tasks._send_visit_reminders()
    harness["wa"].fail = False
    harness["session"].queries = 0
    harness["events"].clear()

    await tasks._send_visit_reminders()
    assert _kinds(harness["events"]).count("wa") == 1
    assert harness["visit"].extra["reminder_24h_sent"]


async def test_the_email_fallback_keeps_the_flag(harness):
    """WhatsApp failed but the email went out: the lead was reminded, so the
    claim stands and no second message follows."""
    harness["wa"].fail = True
    harness["lead"].email = "marta@example.com"
    try:
        await tasks._send_visit_reminders()
    finally:
        harness["wa"].fail = False

    assert _kinds(harness["events"]).count("email") == 1
    assert harness["visit"].extra["reminder_24h_sent"]


async def test_both_channels_failing_releases_the_flag(harness):
    harness["wa"].fail = True
    harness["resend"].fail = True
    harness["lead"].email = "marta@example.com"
    try:
        await tasks._send_visit_reminders()
    finally:
        harness["wa"].fail = False
        harness["resend"].fail = False

    assert "reminder_24h_sent" not in harness["visit"].extra


# ---------------------------------------------------------------------------
# Nothing to send to
# ---------------------------------------------------------------------------


async def test_a_lead_with_no_channel_is_not_marked(harness):
    """Marking an unreachable lead as reminded would silently swallow the
    reminder if a phone number arrives later."""
    harness["lead"].whatsapp_wa_id = None
    harness["lead"].email = None

    await tasks._send_visit_reminders()
    assert harness["visit"].extra == {}
    assert _kinds(harness["events"]).count("wa") == 0


async def test_a_visit_already_flagged_is_skipped_without_a_commit(harness):
    harness["visit"].extra = {"reminder_24h_sent": "2026-07-25T10:00:00+00:00"}
    await tasks._send_visit_reminders()
    assert _kinds(harness["events"]) == []
