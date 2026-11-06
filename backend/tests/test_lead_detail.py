"""GET /api/v1/agencies/{agency_id}/leads/{lead_id} — single lead detail.

Until this endpoint existed the mobile lead card was built by downloading
the whole listing and picking the row out of it, so anything past the page
limit was simply unreachable ("Lead no encontrado" from lead 201 on).

Same harness as tests/test_panel_adversarial.py: dev auth bypass plus a fake
DB session injected through `dependency_overrides`, so no Postgres is needed.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.dashboard import router as dashboard_router
from app.core.auth import require_agency_member
from app.core.settings import settings
from app.db.models import LeadStatus, LeadTemperature
from app.db.session import get_db
from app.main import app

AGENCY_ID = uuid.uuid4()
OTHER_AGENCY_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture
def dev_auth(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")


class LeadSession:
    """Minimal AsyncSession double: only `db.get(Lead, pk)` is exercised."""

    def __init__(self, lead):
        self.lead = lead
        self.gets: list[tuple[str, uuid.UUID]] = []

    async def get(self, model, pk):
        name = getattr(model, "__name__", str(model))
        self.gets.append((name, pk))
        if name == "Lead":
            return self.lead if self.lead is not None and pk == self.lead.id else None
        raise AssertionError(f"unexpected get({name})")


def _client(session) -> TestClient:
    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_db, None)


def _lead(**overrides) -> SimpleNamespace:
    data = {
        "id": uuid.uuid4(),
        "agency_id": AGENCY_ID,
        "full_name": "Juan Pérez",
        "phone_e164": "+34600111222",
        "email": "juan@example.com",
        "status": LeadStatus.QUALIFIED,
        "temperature": LeadTemperature.HOT,
        "score": 87,
        "language": "es",
        "budget_min": 200_000,
        "budget_max": 320_000,
        "operation": "buy",
        "created_at": datetime(2026, 7, 1, 9, 30, tzinfo=UTC),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _get(client, lead_id, agency_id: uuid.UUID = AGENCY_ID):
    return client.get(f"/api/v1/agencies/{agency_id}/leads/{lead_id}")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_returns_the_lead_with_the_same_shape_as_the_listing(dev_auth):
    lead = _lead()
    session = LeadSession(lead)

    resp = _get(_client(session), lead.id)

    assert resp.status_code == 200
    body = resp.json()
    # Field for field: the app reuses the `Lead` type of the listing, so a
    # missing or renamed key here breaks the card silently.
    assert body == {
        "id": str(lead.id),
        "full_name": "Juan Pérez",
        "phone_e164": "+34600111222",
        "email": "juan@example.com",
        "status": "qualified",
        "temperature": "hot",
        "score": 87,
        "language": "es",
        "budget_min": 200_000,
        "budget_max": 320_000,
        "operation": "buy",
        "created_at": "2026-07-01T09:30:00Z",
        "next_action_at": None,
        "next_action_note": None,
    }


def test_a_lead_with_nothing_filled_in_still_answers_200(dev_auth):
    """The AI creates the row on the first message: almost everything is null
    until it has qualified the contact. That is not an error."""
    lead = _lead(
        full_name=None,
        phone_e164=None,
        email=None,
        status=LeadStatus.NEW,
        temperature=LeadTemperature.COLD,
        score=0,
        budget_min=None,
        budget_max=None,
        operation=None,
    )

    resp = _get(_client(LeadSession(lead)), lead.id)

    assert resp.status_code == 200
    body = resp.json()
    assert body["full_name"] is None
    assert body["budget_min"] is None
    assert body["operation"] is None
    assert body["status"] == "new"


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


def test_another_agencys_lead_is_404_and_leaks_nothing(dev_auth):
    """A real id belonging to someone else must not answer with that person's
    name, phone or email — and must not be distinguishable from a made-up id."""
    lead = _lead(agency_id=OTHER_AGENCY_ID)

    resp = _get(_client(LeadSession(lead)), lead.id)

    assert resp.status_code == 404
    assert resp.json()["detail"] == "lead_not_found"
    assert "Juan" not in resp.text
    assert "+34600111222" not in resp.text
    assert "juan@example.com" not in resp.text


def test_unknown_lead_is_404_with_the_same_detail(dev_auth):
    """Identical answer to the foreign-lead case: an attacker cannot tell a
    non-existent id from another tenant's id."""
    session = LeadSession(None)

    resp = _get(_client(session), uuid.uuid4())

    assert resp.status_code == 404
    assert resp.json()["detail"] == "lead_not_found"


def test_a_non_uuid_id_never_reaches_the_database(dev_auth):
    session = LeadSession(_lead())

    resp = _get(_client(session), "'; DROP TABLE leads; --")

    assert resp.status_code == 422
    assert session.gets == []


# ---------------------------------------------------------------------------
# Structural: the guard cannot be dropped by a refactor
# ---------------------------------------------------------------------------


def test_route_is_registered_and_carries_the_agency_member_guard():
    """The dependency lives on the router, so a future split of dashboard.py
    into several routers could silently publish this route unguarded.

    Asserted on the router itself, not on `app.routes`: FastAPI includes
    routers lazily and walking the app finds wrappers, not APIRoutes (same
    reason as tests/test_roles.py::_gdpr_route).
    """
    from fastapi.routing import APIRoute

    routes = [
        r
        for r in dashboard_router.routes
        if isinstance(r, APIRoute)
        and r.path.endswith("/leads/{lead_id}")
        and r.methods == {"GET"}
    ]
    assert len(routes) == 1, "GET /leads/{lead_id} is not registered"
    route = routes[0]
    assert route.methods == {"GET"}

    seen: set[int] = set()

    def walk(dep) -> bool:
        if id(dep) in seen:
            return False
        seen.add(id(dep))
        if dep.call is require_agency_member:
            return True
        return any(walk(sub) for sub in dep.dependencies)

    assert walk(route.dependant), "the lead detail route lost its tenancy guard"
