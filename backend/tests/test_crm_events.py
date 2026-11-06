"""The six webhook events the panel offered and nobody ever sent.

`SUPPORTED_EVENTS` has listed seven events since Sprint 5, the panel lets an
agency subscribe to all of them, and `lead.created` was the only `emit()` call
in the backend. These tests pin the other six: they fire when the fact
actually happens, they carry the ids a CRM needs to match its own records,
and — the part that matters for an integration — each one fires *once*, no
matter how many conversation turns, retries or sweeps pass over the same row.

`emit` itself is replaced by a recorder: what is under test is which facts get
announced, not the HMAC on the wire (that lives in test_event_dispatcher.py
and in the delivery tests).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import offers as offers_module
from app.api import visits as visits_module
from app.core.settings import settings
from app.db.models import (
    LeadStatus,
    LeadTemperature,
    OfferStatus,
    VisitStatus,
)
from app.db.session import get_db
from app.main import app
from app.services import event_dispatcher, lead_scoring
from app.services.event_dispatcher import (
    SUPPORTED_EVENTS,
    emit_lead_qualified,
    emit_lead_temperature_changed,
    emit_visit_completed,
    emit_visit_scheduled,
)

AGENCY_ID = uuid.uuid4()
OTHER_AGENCY_ID = uuid.uuid4()
NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch):
    """`upload_voice_note` defers the transcript rewrite to Celery. With no
    Redis in this suite `.delay()` blocks retrying the connection, so the
    enqueue is stubbed out for every test in the module."""
    from app.workers import tasks as tasks_module

    monkeypatch.setattr(tasks_module.structure_voice_note, "delay", lambda *a, **k: None)


@pytest.fixture
def sent(monkeypatch) -> list[tuple[str, uuid.UUID, dict]]:
    """Everything handed to the dispatcher during one test."""
    captured: list[tuple[str, uuid.UUID, dict]] = []

    async def _emit(db, event, agency_id, payload):
        captured.append((event, agency_id, payload))

    monkeypatch.setattr(event_dispatcher, "emit", _emit)
    return captured


def _events(sent) -> list[str]:
    return [event for event, _agency, _payload in sent]


def _payload(sent, event: str) -> dict:
    return next(payload for name, _agency, payload in sent if name == event)


class _Result:
    def __init__(self, payload):
        self._payload = payload

    def all(self):
        return list(self._payload)

    def scalars(self):
        return self


class FakeDB:
    """Enough session for the scorer and the endpoints: canned result sets
    plus a tiny identity map for `get()`."""

    def __init__(self, *results, rows: dict | None = None):
        self.results = list(results)
        self.rows = rows or {}
        self.added: list = []
        self.commits = 0

    async def execute(self, stmt):
        payload = self.results.pop(0) if self.results else []
        return _Result(payload)

    async def get(self, model, pk):
        return self.rows.get(pk)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    async def flush(self):
        # The ORM fills the primary key and `created_at` on flush; without
        # that the endpoint's response model rejects its own new row.
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if getattr(obj, "created_at", None) is None:
                obj.created_at = NOW
            self.rows[obj.id] = obj

    def add(self, obj):
        self.added.append(obj)


def _lead(
    *,
    status: LeadStatus = LeadStatus.NEW,
    temperature: LeadTemperature = LeadTemperature.COLD,
    extra: dict | None = None,
    updated_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        full_name="Juan Pérez",
        phone_e164="+34600111222",
        email="juan@example.com",
        status=status,
        temperature=temperature,
        score=55,
        language="es",
        budget_min=200_000,
        budget_max=300_000,
        operation="sale",
        preferred_cities=["Madrid"],
        preferred_neighborhoods=None,
        min_bedrooms=2,
        must_haves=None,
        interested_property_ids=None,
        qualification_notes="",
        extra=dict(extra or {}),
        updated_at=updated_at or NOW,
    )


def _visit(
    *,
    lead_id: uuid.UUID | None = None,
    status: VisitStatus = VisitStatus.CONFIRMED,
    extra: dict | None = None,
    created_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        lead_id=lead_id or uuid.uuid4(),
        property_id=uuid.uuid4(),
        scheduled_at=NOW + timedelta(days=1),
        status=status,
        post_visit_notes="",
        pre_visit_briefing="",
        extra=dict(extra or {}),
        created_at=created_at or NOW,
        updated_at=created_at or NOW,
    )


def _offer(*, status: OfferStatus = OfferStatus.DRAFT, extra: dict | None = None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        lead_id=uuid.uuid4(),
        property_id=uuid.uuid4(),
        amount_eur=310_000,
        operation="sale",
        conditions="",
        status=status,
        pdf_url="https://cdn.selai.es/offer.pdf",
        sign_url=None,
        created_at=NOW,
        sent_at=None,
        opened_at=None,
        signed_at=None,
        extra=dict(extra or {}),
    )


# ---------------------------------------------------------------------------
# Every event the panel offers has a producer
# ---------------------------------------------------------------------------


def test_every_supported_event_is_actually_emitted_somewhere(sent):
    """The selector in the panel promises seven events. Six of them are these;
    `lead.created` is emitted by conversation_service. If someone adds an
    eighth option to the panel without a producer, this fails."""
    emitted = {
        "lead.qualified",
        "lead.temperature_changed",
        "visit.scheduled",
        "visit.completed",
        "offer.created",
        "offer.signed",
        "lead.created",
    }
    assert emitted == SUPPORTED_EVENTS


# ---------------------------------------------------------------------------
# lead.qualified
# ---------------------------------------------------------------------------


async def test_lead_qualified_fires_when_the_lead_qualifies(sent):
    lead = _lead(status=LeadStatus.QUALIFIED)

    assert await emit_lead_qualified(FakeDB(), lead) is True
    assert _events(sent) == ["lead.qualified"]
    payload = _payload(sent, "lead.qualified")
    assert payload["lead_id"] == str(lead.id)
    assert payload["budget_max"] == 300_000


async def test_lead_qualified_never_fires_twice(sent):
    lead = _lead(status=LeadStatus.QUALIFIED)
    db = FakeDB()

    await emit_lead_qualified(db, lead)
    lead.status = LeadStatus.NEGOTIATING
    await emit_lead_qualified(db, lead)
    await emit_lead_qualified(db, lead)

    assert _events(sent) == ["lead.qualified"]


async def test_a_lead_that_has_not_qualified_says_nothing(sent):
    for status in (LeadStatus.NEW, LeadStatus.ENGAGED):
        assert await emit_lead_qualified(FakeDB(), _lead(status=status)) is False
    assert sent == []


async def test_a_lead_that_skipped_straight_to_a_visit_still_counts_as_qualified(sent):
    """The panel's own KPI counts VISIT_SCHEDULED as qualified. A CRM waiting
    for `lead.qualified` must not be left hanging because the lead booked a
    visit before we ever wrote down its budget."""
    await emit_lead_qualified(FakeDB(), _lead(status=LeadStatus.VISIT_SCHEDULED))

    assert _events(sent) == ["lead.qualified"]


# ---------------------------------------------------------------------------
# lead.temperature_changed
# ---------------------------------------------------------------------------


async def test_temperature_change_reports_both_ends(sent):
    lead = _lead(temperature=LeadTemperature.HOT)

    await emit_lead_temperature_changed(FakeDB(), lead)

    payload = _payload(sent, "lead.temperature_changed")
    assert payload["from"] == "cold"
    assert payload["to"] == "hot"


async def test_a_cold_lead_that_stays_cold_is_not_news(sent):
    assert await emit_lead_temperature_changed(FakeDB(), _lead()) is False
    assert sent == []


async def test_temperature_fires_once_per_value_not_once_per_turn(sent):
    lead = _lead(temperature=LeadTemperature.HOT)
    db = FakeDB()

    await emit_lead_temperature_changed(db, lead)
    await emit_lead_temperature_changed(db, lead)
    await emit_lead_temperature_changed(db, lead)
    lead.temperature = LeadTemperature.WARM
    await emit_lead_temperature_changed(db, lead)

    assert _events(sent) == ["lead.temperature_changed", "lead.temperature_changed"]
    assert _payload(sent, "lead.temperature_changed")["to"] == "hot"
    assert sent[1][2] == {
        "lead_id": str(lead.id),
        "name": "Juan Pérez",
        "from": "hot",
        "to": "warm",
        "score": 55,
    }


# ---------------------------------------------------------------------------
# visit.scheduled / visit.completed
# ---------------------------------------------------------------------------


async def test_visit_scheduled_fires_once_for_a_booking(sent):
    visit = _visit()
    db = FakeDB()

    await emit_visit_scheduled(db, visit)
    await emit_visit_scheduled(db, visit)

    assert _events(sent) == ["visit.scheduled"]
    payload = _payload(sent, "visit.scheduled")
    assert payload["visit_id"] == str(visit.id)
    assert payload["property_id"] == str(visit.property_id)


async def test_visit_completed_only_when_it_is_completed(sent):
    visit = _visit(status=VisitStatus.CONFIRMED)

    assert await emit_visit_completed(FakeDB(), visit) is False

    visit.status = VisitStatus.COMPLETED
    visit.post_visit_notes = "Le gustó la terraza"
    assert await emit_visit_completed(FakeDB(), visit) is True
    assert _payload(sent, "visit.completed")["notes"] == "Le gustó la terraza"


async def test_visit_completed_fires_once_across_repeated_sweeps(sent):
    visit = _visit(status=VisitStatus.COMPLETED)
    db = FakeDB()

    for _ in range(5):
        await emit_visit_completed(db, visit)

    assert _events(sent) == ["visit.completed"]


async def test_a_cancelled_visit_was_still_scheduled(sent):
    await emit_visit_scheduled(FakeDB(), _visit(status=VisitStatus.CANCELLED))

    assert _events(sent) == ["visit.scheduled"]


# ---------------------------------------------------------------------------
# Backfill guard
# ---------------------------------------------------------------------------


async def test_old_facts_are_marked_as_seen_but_not_replayed(sent):
    """The markers are new. Without this the first turn after the deploy would
    push every visit an agency ever booked into its CRM as if it had just
    been booked."""
    visit = _visit(created_at=NOW - timedelta(days=30))
    db = FakeDB()

    assert await emit_visit_scheduled(db, visit, max_age=timedelta(hours=24)) is False
    assert sent == []

    # ...and it stays quiet afterwards instead of firing on the next turn.
    assert await emit_visit_scheduled(db, visit) is False
    assert sent == []


async def test_a_fresh_fact_is_not_suppressed_by_the_guard(sent):
    visit = _visit(created_at=NOW - timedelta(minutes=5))

    assert await emit_visit_scheduled(FakeDB(), visit, max_age=timedelta(hours=24)) is True


# ---------------------------------------------------------------------------
# The recompute is where lead + visit events are announced
# ---------------------------------------------------------------------------


async def test_recompute_announces_qualification_temperature_and_visits(sent):
    lead = _lead(status=LeadStatus.QUALIFIED)
    visit = _visit(lead_id=lead.id, status=VisitStatus.CONFIRMED)
    # _load_visits, then _score_engagement.
    db = FakeDB([visit], [], rows={lead.id: lead})

    await lead_scoring.recompute_lead_score(db, lead.id)

    assert set(_events(sent)) == {
        "lead.qualified",
        "lead.temperature_changed",
        "visit.scheduled",
    }
    # A scheduled visit is worth 20 points and pushes the lead to hot/warm,
    # which is exactly the transition the CRM is told about.
    assert _payload(sent, "lead.temperature_changed")["from"] == "cold"


async def test_recompute_is_quiet_on_the_turns_that_change_nothing(sent):
    lead = _lead(status=LeadStatus.QUALIFIED)
    visit = _visit(lead_id=lead.id)
    rows = {lead.id: lead}

    await lead_scoring.recompute_lead_score(FakeDB([visit], [], rows=rows), lead.id)
    first = len(sent)
    await lead_scoring.recompute_lead_score(FakeDB([visit], [], rows=rows), lead.id)

    assert len(sent) == first


async def test_a_webhook_failure_never_breaks_the_score(monkeypatch):
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(event_dispatcher, "emit", _boom)
    lead = _lead(status=LeadStatus.QUALIFIED)
    db = FakeDB([], [], rows={lead.id: lead})

    breakdown = await lead_scoring.recompute_lead_score(db, lead.id)

    assert breakdown.total > 0
    assert lead.score == breakdown.total


# ---------------------------------------------------------------------------
# offers.py — offer.created / offer.signed through the API
# ---------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")

    def _client(db) -> TestClient:
        async def _override():
            yield db

        app.dependency_overrides[get_db] = _override
        return TestClient(app)

    yield _client
    app.dependency_overrides.pop(get_db, None)


async def _noop(*_args, **_kwargs):
    return None


def test_creating_an_offer_announces_it(api, sent, monkeypatch):
    lead = _lead()
    prop = SimpleNamespace(id=uuid.uuid4(), agency_id=AGENCY_ID, title="Piso en Chamberí")
    db = FakeDB(rows={lead.id: lead, prop.id: prop})
    monkeypatch.setattr(offers_module, "ensure_feature", _noop)

    async def _pdf(*_args, **_kwargs):
        return "https://cdn.selai.es/offer.pdf"

    monkeypatch.setattr(offers_module, "build_offer_pdf", _pdf)

    resp = api(db).post(
        f"/api/v1/agencies/{AGENCY_ID}/offers",
        json={
            "lead_id": str(lead.id),
            "property_id": str(prop.id),
            "amount_eur": 310_000,
            "operation": "sale",
        },
    )

    assert resp.status_code == 201
    assert _events(sent) == ["offer.created"]
    payload = _payload(sent, "offer.created")
    assert payload["amount_eur"] == 310_000
    assert payload["lead_id"] == str(lead.id)


def test_signing_an_offer_announces_it_once(api, sent):
    offer = _offer(status=OfferStatus.SENT)
    db = FakeDB(rows={offer.id: offer})
    client = api(db)

    url = f"/api/v1/agencies/{AGENCY_ID}/offers/{offer.id}/status"
    assert client.patch(url, json={"status": "signed"}).status_code == 200
    # The signing provider retries; the CRM must not get a second contract.
    assert client.patch(url, json={"status": "signed"}).status_code == 200

    assert _events(sent) == ["offer.signed"]
    assert _payload(sent, "offer.signed")["offer_id"] == str(offer.id)


def test_moving_an_offer_to_sent_says_nothing(api, sent):
    offer = _offer()
    db = FakeDB(rows={offer.id: offer})

    api(db).patch(
        f"/api/v1/agencies/{AGENCY_ID}/offers/{offer.id}/status", json={"status": "sent"}
    )

    assert sent == []


# ---------------------------------------------------------------------------
# visits.py — a post-visit voice note closes the visit and announces it
# ---------------------------------------------------------------------------


def _upload(client, visit_id: uuid.UUID):
    return client.post(
        f"/api/v1/agencies/{AGENCY_ID}/visits/{visit_id}/voice-note",
        files={"file": ("nota.ogg", b"audio-bytes", "audio/ogg")},
    )


def test_a_voice_note_completes_the_visit_and_announces_it(api, sent, monkeypatch):
    visit = _visit(status=VisitStatus.CONFIRMED)
    lead = _lead()
    visit.lead_id = lead.id
    db = FakeDB(rows={visit.id: visit, lead.id: lead})

    async def _transcribe(*_args, **_kwargs):
        return "El cliente quiere hacer una oferta"

    monkeypatch.setattr(visits_module, "transcribe_audio", _transcribe)

    resp = _upload(api(db), visit.id)

    assert resp.status_code == 202
    assert visit.status is VisitStatus.COMPLETED
    # Scheduled first: a CRM that never heard about the booking cannot place
    # its completion.
    assert _events(sent) == ["visit.scheduled", "visit.completed"]
    assert _payload(sent, "visit.completed")["notes"] == "El cliente quiere hacer una oferta"


def test_a_visit_closes_even_when_the_transcription_fails(api, sent, monkeypatch):
    """The agent was there — that is what the upload proves. Leaving the visit
    CONFIRMED forever because Deepgram had a bad minute is the old bug."""
    visit = _visit(status=VisitStatus.CONFIRMED)
    lead = _lead()
    visit.lead_id = lead.id
    db = FakeDB(rows={visit.id: visit, lead.id: lead})

    async def _transcribe(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(visits_module, "transcribe_audio", _transcribe)

    resp = _upload(api(db), visit.id)

    assert resp.json()["status"] == "transcription_unavailable"
    assert visit.status is VisitStatus.COMPLETED
    assert "visit.completed" in _events(sent)


def test_a_second_voice_note_does_not_re_announce_the_visit(api, sent, monkeypatch):
    visit = _visit(status=VisitStatus.CONFIRMED)
    lead = _lead()
    visit.lead_id = lead.id
    db = FakeDB(rows={visit.id: visit, lead.id: lead})

    async def _transcribe(*_args, **_kwargs):
        return "otra nota"

    monkeypatch.setattr(visits_module, "transcribe_audio", _transcribe)
    client = api(db)

    _upload(client, visit.id)
    _upload(client, visit.id)

    assert _events(sent) == ["visit.scheduled", "visit.completed"]


def test_a_visit_of_another_agency_is_not_touched(api, sent, monkeypatch):
    visit = _visit(status=VisitStatus.CONFIRMED)
    visit.agency_id = OTHER_AGENCY_ID
    db = FakeDB(rows={visit.id: visit})

    async def _transcribe(*_args, **_kwargs):
        return "nota"

    monkeypatch.setattr(visits_module, "transcribe_audio", _transcribe)

    resp = _upload(api(db), visit.id)

    assert resp.status_code == 404
    assert visit.status is VisitStatus.CONFIRMED
    assert sent == []


def test_a_cross_tenant_lead_never_reaches_the_payload(api, sent, monkeypatch):
    """A visit pointing at another tenant's lead is corruption; it must not
    put that person's name and phone into someone else's CRM."""
    visit = _visit(status=VisitStatus.CONFIRMED)
    stranger = _lead()
    stranger.agency_id = OTHER_AGENCY_ID
    visit.lead_id = stranger.id
    db = FakeDB(rows={visit.id: visit, stranger.id: stranger})

    async def _transcribe(*_args, **_kwargs):
        return "nota"

    monkeypatch.setattr(visits_module, "transcribe_audio", _transcribe)

    _upload(api(db), visit.id)

    assert _payload(sent, "visit.scheduled")["lead_name"] is None
    assert _payload(sent, "visit.scheduled")["lead_phone"] is None
