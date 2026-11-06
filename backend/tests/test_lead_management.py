"""PATCH /leads/{lead_id}: an agency member can operate the CRM safely."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.dashboard import LEAD_NEXT_ACTION_EXTRA_KEY
from app.api.dashboard import router as dashboard_router
from app.core.auth import require_agency_member
from app.core.settings import settings
from app.db.models import LeadStatus, LeadTemperature, UserRole
from app.db.session import get_db
from app.main import app

AGENCY_ID = uuid.uuid4()
OTHER_AGENCY_ID = uuid.uuid4()
FUTURE_AWARE = (datetime.now(UTC) + timedelta(days=2)).isoformat()
FUTURE_NAIVE = (datetime.now() + timedelta(days=2)).replace(tzinfo=None).isoformat()
PAST_AWARE = (datetime.now(UTC) - timedelta(days=2)).isoformat()


@pytest.fixture
def dev_auth(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")


class LeadSession:
    def __init__(self, lead, *, auth_user=None):
        self.lead = lead
        self.auth_user = auth_user
        self.gets: list[tuple[str, uuid.UUID]] = []
        self.commits = 0

    async def execute(self, _statement):
        user = self.auth_user

        class Result:
            def scalar_one_or_none(self):
                return user

        return Result()

    async def get(self, model, pk):
        name = getattr(model, "__name__", str(model))
        self.gets.append((name, pk))
        if name == "Lead":
            return self.lead if self.lead is not None and pk == self.lead.id else None
        raise AssertionError(f"unexpected get({name})")

    async def commit(self):
        self.commits += 1


def _lead(**overrides) -> SimpleNamespace:
    data = {
        "id": uuid.uuid4(),
        "agency_id": AGENCY_ID,
        "full_name": "Ana Perez",
        "phone_e164": "+34600111222",
        "email": "ana@example.com",
        "status": LeadStatus.NEW,
        "temperature": LeadTemperature.WARM,
        "score": 42,
        "language": "es",
        "budget_min": 200_000,
        "budget_max": 300_000,
        "operation": "sale",
        "created_at": datetime(2026, 8, 1, 10, tzinfo=UTC),
        "extra": {"provider_id": "keep-me"},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _client(session) -> TestClient:
    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_db, None)


def _patch(client, lead_id, body, agency_id: uuid.UUID = AGENCY_ID):
    return client.patch(f"/api/v1/agencies/{agency_id}/leads/{lead_id}", json=body)


def test_member_can_move_a_lead_to_any_real_pipeline_stage(dev_auth):
    lead = _lead()
    session = LeadSession(lead)

    response = _patch(_client(session), lead.id, {"status": "negotiating"})

    assert response.status_code == 200
    assert response.json()["status"] == "negotiating"
    assert lead.status is LeadStatus.NEGOTIATING
    assert session.commits == 1


def test_active_agent_role_can_update_its_own_agencys_lead(dev_auth):
    lead = _lead()
    user = SimpleNamespace(
        agency_id=AGENCY_ID,
        is_active=True,
        role=UserRole.AGENT,
    )
    session = LeadSession(lead, auth_user=user)

    response = _client(session).patch(
        f"/api/v1/agencies/{AGENCY_ID}/leads/{lead.id}",
        json={"status": "qualified"},
        headers={"X-Dev-Email": "agent@agency.test"},
    )

    assert response.status_code == 200
    assert lead.status is LeadStatus.QUALIFIED
    assert session.commits == 1


def test_member_of_another_agency_is_stopped_before_the_lead_lookup(dev_auth):
    lead = _lead()
    outsider = SimpleNamespace(
        agency_id=OTHER_AGENCY_ID,
        is_active=True,
        role=UserRole.OWNER,
    )
    session = LeadSession(lead, auth_user=outsider)

    response = _client(session).patch(
        f"/api/v1/agencies/{AGENCY_ID}/leads/{lead.id}",
        json={"status": "won"},
        headers={"X-Dev-Email": "owner@other-agency.test"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "agency_access_denied"}
    assert session.gets == []
    assert lead.status is LeadStatus.NEW
    assert session.commits == 0


def test_member_can_schedule_a_complete_next_action_without_losing_other_extra(dev_auth):
    lead = _lead()
    session = LeadSession(lead)
    due = datetime.now(UTC) + timedelta(days=2)

    response = _patch(
        _client(session),
        lead.id,
        {
            "next_action_at": due.isoformat(),
            "next_action_note": "  Llamar   para confirmar financiacion  ",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert datetime.fromisoformat(body["next_action_at"]) == due
    assert body["next_action_note"] == "Llamar para confirmar financiacion"
    assert lead.extra["provider_id"] == "keep-me"
    assert lead.extra[LEAD_NEXT_ACTION_EXTRA_KEY]["note"] == body["next_action_note"]
    assert "updated_at" in lead.extra[LEAD_NEXT_ACTION_EXTRA_KEY]
    assert session.commits == 1


def test_member_can_clear_the_next_action(dev_auth):
    lead = _lead(
        extra={
            "provider_id": "keep-me",
            LEAD_NEXT_ACTION_EXTRA_KEY: {
                "at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "note": "Llamar",
            },
        }
    )
    session = LeadSession(lead)

    response = _patch(
        _client(session), lead.id, {"next_action_at": None, "next_action_note": None}
    )

    assert response.status_code == 200
    assert response.json()["next_action_at"] is None
    assert response.json()["next_action_note"] is None
    assert LEAD_NEXT_ACTION_EXTRA_KEY not in lead.extra
    assert lead.extra["provider_id"] == "keep-me"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"status": "invented"},
        {"status": None},
        {"next_action_at": FUTURE_NAIVE, "next_action_note": "Llamar"},
        {"next_action_at": PAST_AWARE, "next_action_note": "Llamar"},
        {"next_action_at": FUTURE_AWARE},
        {"next_action_note": "Llamar"},
        {"next_action_at": FUTURE_AWARE, "next_action_note": "   "},
    ],
)
def test_invalid_updates_are_rejected_without_a_commit(dev_auth, body):
    lead = _lead()
    session = LeadSession(lead)

    response = _patch(_client(session), lead.id, body)

    assert response.status_code == 422
    assert session.commits == 0


def test_another_tenants_lead_is_indistinguishable_from_an_unknown_id(dev_auth):
    foreign = _lead(agency_id=OTHER_AGENCY_ID)
    foreign_session = LeadSession(foreign)
    missing_session = LeadSession(None)

    foreign_response = _patch(
        _client(foreign_session), foreign.id, {"status": "won"}, agency_id=AGENCY_ID
    )
    missing_response = _patch(
        _client(missing_session), uuid.uuid4(), {"status": "won"}, agency_id=AGENCY_ID
    )

    assert foreign_response.status_code == missing_response.status_code == 404
    assert foreign_response.json() == missing_response.json() == {"detail": "lead_not_found"}
    assert foreign.status is LeadStatus.NEW
    assert foreign_session.commits == missing_session.commits == 0


def test_patch_route_carries_the_agency_member_guard():
    routes = [
        route
        for route in dashboard_router.routes
        if isinstance(route, APIRoute)
        and route.path.endswith("/leads/{lead_id}")
        and route.methods == {"PATCH"}
    ]
    assert len(routes) == 1

    seen: set[int] = set()

    def walk(dependency) -> bool:
        if id(dependency) in seen:
            return False
        seen.add(id(dependency))
        if dependency.call is require_agency_member:
            return True
        return any(walk(child) for child in dependency.dependencies)

    assert walk(routes[0].dependant), "lead management lost its agency/RBAC guard"
