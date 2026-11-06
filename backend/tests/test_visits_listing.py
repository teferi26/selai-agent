"""(167) GET /api/v1/agencies/{id}/visits — the agenda listing.

Same shape as test_channels_api.py: dev auth bypass (no Clerk JWKS) plus a
fake DB session injected through dependency_overrides, so no Postgres is
needed. The fake session records the statement it was handed, which is how
we assert the tenancy filter and the date range actually reach the query.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.settings import settings
from app.db.models import VisitStatus
from app.db.session import get_db
from app.main import app

AGENCY_ID = uuid.uuid4()


def _visit(
    scheduled_at: datetime,
    *,
    briefing: str = "",
    status: VisitStatus = VisitStatus.CONFIRMED,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        scheduled_at=scheduled_at,
        status=status,
        property_id=uuid.uuid4(),
        pre_visit_briefing=briefing,
    )


def _lead(name: str | None = "Juan Pérez", phone: str | None = "+34600111222"):
    return SimpleNamespace(id=uuid.uuid4(), full_name=name, phone_e164=phone)


def _property(title: str = "Piso en Chamberí"):
    return SimpleNamespace(id=uuid.uuid4(), title=title)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    """Returns the canned rows and keeps the statement for inspection."""

    def __init__(self, rows, agency=None):
        self.rows = rows
        # The listing reads the agency to know which timezone the agenda is
        # rendered in (see test_panel_adversarial.py).
        self.agency = agency or SimpleNamespace(
            id=AGENCY_ID, name="Inmo Sol", timezone="Europe/Madrid"
        )
        self.statements: list = []

    async def get(self, model, pk):
        name = getattr(model, "__name__", str(model))
        if name == "Agency":
            return self.agency if pk == self.agency.id else None
        raise AssertionError(f"unexpected get({name})")

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self.rows)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    @property
    def last_sql(self) -> str:
        return str(self.statements[-1])

    @property
    def last_params(self) -> dict:
        return self.statements[-1].compile().params


@pytest.fixture
def rows():
    return []


@pytest.fixture
def db(rows):
    session = FakeSession(rows)

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield session
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    return TestClient(app)


def _get(client, params: dict | None = None) -> object:
    return client.get(f"/api/v1/agencies/{AGENCY_ID}/visits", params=params or {})


# ---------------------------------------------------------------------------
# Tenancy + range filtering
# ---------------------------------------------------------------------------


def test_listing_filters_by_agency(client, db):
    resp = _get(client)

    assert resp.status_code == 200
    assert "visits.agency_id = " in db.last_sql
    assert AGENCY_ID in db.last_params.values()


def test_listing_filters_by_date_range(client, db):
    since = datetime(2026, 7, 26, 0, 0, tzinfo=UTC)
    until = datetime(2026, 7, 31, 23, 59, tzinfo=UTC)

    resp = _get(client, {"from": since.isoformat(), "to": until.isoformat()})

    assert resp.status_code == 200
    sql = db.last_sql
    assert "visits.scheduled_at >= " in sql
    assert "visits.scheduled_at <= " in sql
    values = list(db.last_params.values())
    assert since in values
    assert until in values


def test_range_bounds_are_optional(client, db):
    resp = _get(client)

    assert resp.status_code == 200
    sql = db.last_sql
    assert "scheduled_at >= " not in sql
    assert "scheduled_at <= " not in sql


def test_listing_is_ordered_chronologically(client, db):
    _get(client)
    assert "ORDER BY visits.scheduled_at ASC" in db.last_sql


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


def test_returns_agenda_payload(client, db):
    visit = _visit(
        datetime(2026, 7, 27, 17, 30, tzinfo=UTC),
        briefing="Busca 3 hab, presupuesto 250k, le preocupa el ruido.",
    )
    lead = _lead()
    prop = _property()
    db.rows = [(visit, lead, prop)]

    payload = _get(client).json()

    assert len(payload) == 1
    item = payload[0]
    assert item["id"] == str(visit.id)
    assert item["scheduled_at"].startswith("2026-07-27T17:30")
    assert item["status"] == "confirmed"
    assert item["lead_name"] == "Juan Pérez"
    assert item["lead_phone"] == "+34600111222"
    assert item["property_title"] == "Piso en Chamberí"
    assert item["property_id"] == str(visit.property_id)
    assert item["briefing"].startswith("Busca 3 hab")
    assert item["timezone"] == "Europe/Madrid"


def test_missing_property_and_empty_briefing_are_tolerated(client, db):
    """A listing deleted after booking must not hide the visit, and a visit
    whose briefing has not been generated yet returns an empty string."""
    visit = _visit(datetime(2026, 7, 27, 9, 0, tzinfo=UTC), briefing="")
    db.rows = [(visit, _lead(name=None, phone=None), None)]

    item = _get(client).json()[0]

    assert item["property_title"] is None
    assert item["lead_name"] is None
    assert item["lead_phone"] is None
    assert item["briefing"] == ""


def test_no_visits_returns_empty_list(client, db):
    assert _get(client).json() == []
