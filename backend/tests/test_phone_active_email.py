"""(190) "Tu número de IA ya está activo".

When Vapi has no number to hand out, the wizard tells the customer we will
provision one manually and *"te avisaremos por email"*. That email did not
exist: the agency sat in its trial waiting for a message that was never
coming. Assigning `ai_phone_number` through PATCH /settings now sends it —
with the forwarding codes, which are what makes the number usable — and
clears the pending-request flag.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import agency_settings as settings_module
from app.core.settings import settings
from app.db.session import get_db
from app.main import app
from app.services.emails import build_phone_active_email_html

AGENCY_ID = uuid.uuid4()
NUMBER = "+34910000000"


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
    def __init__(self, agency_settings: dict, owner=None):
        self.agency = SimpleNamespace(
            id=AGENCY_ID,
            name="Inmo Sol",
            slug="inmo-sol",
            timezone="Europe/Madrid",
            default_language="es",
            contact_email="info@inmosol.example.com",
            settings=dict(agency_settings),
        )
        self.owner = owner
        self.commits = 0

    async def get(self, model, pk, **kwargs):
        return self.agency if pk == AGENCY_ID else None

    async def execute(self, stmt):
        if "FROM users" in str(stmt):
            return _Result([self.owner] if self.owner else [])
        raise AssertionError(f"unexpected query: {stmt}")

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def sent(monkeypatch):
    box: list[dict] = []

    async def _send_email(*, to, subject, html, category=None):
        box.append({"to": to, "subject": subject, "html": html, "category": category})
        return True

    monkeypatch.setattr(settings_module, "send_email", _send_email)
    return box


def _client(monkeypatch, session):
    async def _override():
        yield session

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    app.dependency_overrides[get_db] = _override
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_db, None)


def _patch(client, **body):
    return client.patch(f"/api/v1/agencies/{AGENCY_ID}/settings", json=body)


# ---------------------------------------------------------------------------
# When the email fires
# ---------------------------------------------------------------------------


def test_assigning_a_requested_number_emails_the_owner(monkeypatch, sent):
    db = FakeSession(
        {"phone_number_requested": True},
        owner=SimpleNamespace(email="dueno@inmosol.example.com"),
    )
    client = _client(monkeypatch, db)

    resp = _patch(client, ai_phone_number=NUMBER)

    assert resp.status_code == 200
    assert len(sent) == 1
    assert sent[0]["to"] == "dueno@inmosol.example.com"
    assert sent[0]["subject"] == "Tu número de IA ya está activo"
    assert NUMBER in sent[0]["html"]


def test_the_pending_request_flag_is_cleared(monkeypatch, sent):
    db = FakeSession({"phone_number_requested": True})
    client = _client(monkeypatch, db)

    _patch(client, ai_phone_number=NUMBER)

    assert "phone_number_requested" not in db.agency.settings
    assert db.agency.settings["ai_phone_number"] == NUMBER


def test_nothing_is_sent_when_nobody_asked_for_a_number(monkeypatch, sent):
    """Vapi self-service already told the customer on screen; a number set
    without a pending request is not news."""
    db = FakeSession({})
    client = _client(monkeypatch, db)

    _patch(client, ai_phone_number=NUMBER)

    assert sent == []


def test_changing_an_already_active_number_does_not_re_announce(monkeypatch, sent):
    db = FakeSession({"ai_phone_number": "+34910000001", "phone_number_requested": True})
    client = _client(monkeypatch, db)

    _patch(client, ai_phone_number=NUMBER)

    assert sent == []


def test_clearing_the_number_sends_nothing(monkeypatch, sent):
    db = FakeSession({"phone_number_requested": True})
    client = _client(monkeypatch, db)

    _patch(client, ai_phone_number=None)

    assert sent == []


def test_a_patch_that_does_not_touch_the_number_sends_nothing(monkeypatch, sent):
    db = FakeSession({"phone_number_requested": True})
    client = _client(monkeypatch, db)

    _patch(client, ai_tone="formal")

    assert sent == []


def test_the_number_is_saved_even_if_the_email_blows_up(monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("resend down")

    monkeypatch.setattr(settings_module, "send_email", _boom)
    db = FakeSession({"phone_number_requested": True})
    client = _client(monkeypatch, db)

    resp = _patch(client, ai_phone_number=NUMBER)

    assert resp.status_code == 200
    assert resp.json()["settings"]["ai_phone_number"] == NUMBER
    assert db.commits == 1


def test_no_recipient_is_logged_not_crashed(monkeypatch, sent):
    db = FakeSession({"phone_number_requested": True})
    db.agency.contact_email = None
    client = _client(monkeypatch, db)

    assert _patch(client, ai_phone_number=NUMBER).status_code == 200
    assert sent == []


# ---------------------------------------------------------------------------
# The HTML
# ---------------------------------------------------------------------------


def test_the_email_carries_the_three_forwarding_codes():
    html = build_phone_active_email_html("Inmo Sol", NUMBER)
    assert f"**21*{NUMBER}#" in html
    assert f"**61*{NUMBER}#" in html
    assert "##21#" in html


def test_the_email_shows_the_number_and_the_agency():
    html = build_phone_active_email_html("Inmo Sol", NUMBER)
    assert "Inmo Sol" in html
    assert NUMBER in html
    assert "Tu número de IA ya está activo" in html
