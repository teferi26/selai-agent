"""Per-agency channel connections API (app/api/channels.py).

Uses the dev auth bypass (no Clerk JWKS) plus a fake DB session injected
via dependency_overrides — same spirit as test_voice_preview.py, no real
Postgres needed.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import channels as channels_module
from app.channels.whatsapp.security import agency_verify_token
from app.core.settings import settings
from app.db.models import ConversationChannel
from app.db.session import get_db
from app.main import app
from app.services.secret_store import decrypt_secret

TOKEN = "EAAG-agency-token"
PNID = "1234567890"
# (217) The Meta app secret is now required to connect: without it Meta's
# webhooks are signed with a secret we do not have and nothing ever arrives.
APP_SECRET = "meta-app-secret"


def _agency(settings_json: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Inmo Sol",
        slug="inmo-sol",
        contact_email="dueno@inmosol.example.com",
        settings=settings_json or {},
    )


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


def _channel_param(stmt) -> ConversationChannel | None:
    """Which channel a conversations query filters on.

    GET /channels now counts conversations twice — email and WhatsApp — and
    the two statements only differ in a bound parameter, so the fake session
    has to read that parameter instead of the SQL text.
    """
    for value in stmt.compile().params.values():
        if isinstance(value, ConversationChannel):
            return value
    return None


class FakeSession:
    """Answers the exact queries the channels endpoints issue."""

    def __init__(self, agency: SimpleNamespace):
        self.agency = agency
        self.email_stats: tuple[int, datetime | None] = (0, None)
        # (217) Real evidence that a WhatsApp webhook ever arrived.
        self.whatsapp_stats: tuple[int, datetime | None] = (0, None)
        self.webhook_count = 0
        self.commits = 0

    async def get(self, model, pk):
        if pk == self.agency.id:
            return self.agency
        return None

    async def execute(self, stmt):
        sql = str(stmt)
        if "conversations" in sql:
            if _channel_param(stmt) is ConversationChannel.WHATSAPP:
                return _FakeResult(self.whatsapp_stats)
            return _FakeResult(self.email_stats)
        if "webhook_subscriptions" in sql:
            return _FakeResult(self.webhook_count)
        if "subscriptions" in sql:
            # POST /phone is gated on the `voice` plan flag. No subscription
            # row (dev/seeded agencies) means no plan and no block; the plan
            # gate itself is covered in test_plan_gating.py.
            return _FakeResult(None)
        raise AssertionError(f"unexpected query: {sql}")

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    async def refresh(self, obj):
        pass


@pytest.fixture
def agency():
    return _agency()


@pytest.fixture
def db(agency):
    session = FakeSession(agency)

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield session
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    # Webhook config surfaced by the WhatsApp endpoints — pinned so the
    # assertions don't depend on the developer's .env.
    monkeypatch.setattr(settings, "public_base_url", "https://api.selai.es")
    monkeypatch.setattr(settings, "whatsapp_verify_token", SecretStr("verify-me"))
    return TestClient(app)


class _FakeHttpResponse:
    def __init__(self, status_code: int = 200, data: dict | None = None):
        self.status_code = status_code
        self._data = data or {}
        self.text = str(self._data)

    def json(self) -> dict:
        return self._data


class _FakeHttpClient:
    """Records every request; response/exception configurable per test."""

    calls: list[dict] = []
    response: _FakeHttpResponse | None = None
    exc: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def _respond(self, call: dict):
        type(self).calls.append(call)
        if type(self).exc is not None:
            raise type(self).exc
        return type(self).response

    async def get(self, url, params=None, headers=None):
        return await self._respond(
            {"method": "GET", "url": url, "params": params, "headers": headers}
        )

    async def post(self, url, json=None, headers=None):
        return await self._respond(
            {"method": "POST", "url": url, "json": json, "headers": headers}
        )


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr(channels_module.httpx, "AsyncClient", _FakeHttpClient)
    _FakeHttpClient.calls = []
    _FakeHttpClient.response = None
    _FakeHttpClient.exc = None
    return _FakeHttpClient


@pytest.fixture
def sent_emails(monkeypatch):
    sent: list[dict] = []

    async def _fake_send(to, subject, html):
        sent.append({"to": to, "subject": subject, "html": html})
        return True

    monkeypatch.setattr(channels_module, "send_email", _fake_send)
    return sent


def _url(agency, path=""):
    return f"/api/v1/agencies/{agency.id}/channels{path}"


# ---------------------------------------------------------------------------
# GET "" — status
# ---------------------------------------------------------------------------


def test_status_with_nothing_connected(client, agency, db):
    resp = client.get(_url(agency))
    assert resp.status_code == 200
    body = resp.json()
    assert body["whatsapp"] == {
        "connected": False,
        "credentials_unreadable": False,
        "phone_number": None,
        "verified_name": None,
        "webhook_callback_url": "https://api.selai.es/webhooks/whatsapp",
        # (217, revisión) Derived per agency, not the raw env value: that one
        # is shared by every tenant and this endpoint prints it to any member
        # of any agency. Both forms pass Meta's challenge, so nothing already
        # configured breaks — see test_whatsapp_217_adversarial.py.
        "webhook_verify_token": agency_verify_token(agency.id),
        "subscribed_apps": False,
        "app_secret_set": False,
        # Nothing connected ⇒ no number of its own ⇒ no secret to demand. The
        # flag exists so "no secret stored" only reads as "we are rejecting
        # everything" when the number really is on the agency's own Meta app.
        "app_secret_required": False,
        "inbound_count": 0,
        "last_inbound_at": None,
    }
    assert body["email"] == {
        "address": "inmo-sol-leads@leads.selai.es",
        "received_count": 0,
        "last_received_at": None,
    }
    assert body["phone"] == {"number": None, "status": "none"}
    # (195) Unverified until POST /webchat/verify actually finds the snippet.
    assert body["webchat"] == {
        "slug": "inmo-sol",
        "verified": False,
        "verified_url": None,
        "verified_at": None,
    }
    assert body["webhooks"] == {"count": 0}


def test_status_reflects_connections(client, agency, db):
    agency.settings = {
        "whatsapp_access_token": TOKEN,
        "whatsapp_phone_number_id": PNID,
        "whatsapp_display_number": "+34 600 11 22 33",
        "whatsapp_verified_name": "Inmo Sol",
        "ai_phone_number": "+18455550123",
    }
    last = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
    db.email_stats = (5, last)
    db.webhook_count = 2

    body = client.get(_url(agency)).json()
    assert body["whatsapp"]["connected"] is True
    assert body["whatsapp"]["phone_number"] == "+34 600 11 22 33"
    assert body["whatsapp"]["verified_name"] == "Inmo Sol"
    assert body["email"]["received_count"] == 5
    assert body["email"]["last_received_at"].startswith("2026-07-20T10:00:00")
    assert body["phone"] == {"number": "+18455550123", "status": "active"}
    assert body["webhooks"] == {"count": 2}


def test_status_requires_reconnect_for_unreadable_whatsapp_credentials(client, agency):
    agency.settings = {
        "whatsapp_access_token": "enc:v1:not-a-valid-token",
        "whatsapp_phone_number_id": PNID,
        "whatsapp_app_secret": "legacy-app-secret",
    }

    whatsapp = client.get(_url(agency)).json()["whatsapp"]

    assert whatsapp["connected"] is False
    assert whatsapp["credentials_unreadable"] is True
    assert whatsapp["app_secret_set"] is False


def test_status_requires_reconnect_for_unreadable_sms_credentials(client, agency):
    agency.settings = {
        "twilio_account_sid": "AC" + "a" * 32,
        "twilio_auth_token": "enc:v1:not-a-valid-token",
        "twilio_sms_number": "+34910000000",
    }

    sms = client.get(_url(agency)).json()["sms"]

    assert sms["configured"] is False
    assert sms["credentials_unreadable"] is True


def test_status_shows_requested_phone(client, agency):
    agency.settings = {"phone_number_requested": True}
    body = client.get(_url(agency)).json()
    assert body["phone"] == {"number": None, "status": "requested"}


def test_status_unknown_agency_is_404(client, agency):
    resp = client.get(f"/api/v1/agencies/{uuid.uuid4()}/channels")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST "/whatsapp" — connect
# ---------------------------------------------------------------------------


def _connect_body(**overrides) -> dict:
    base = {
        "phone_number_id": PNID,
        "waba_id": "9876543210",
        "access_token": TOKEN,
        "app_secret": APP_SECRET,
    }
    return {**base, **overrides}


def test_whatsapp_connect_success_persists_settings(client, agency, db, fake_http):
    fake_http.response = _FakeHttpResponse(
        200, {"display_phone_number": "+34 600 11 22 33", "verified_name": "Inmo Sol"}
    )

    resp = client.post(_url(agency, "/whatsapp"), json=_connect_body())
    assert resp.status_code == 200
    assert resp.json() == {
        "connected": True,
        "phone_number": "+34 600 11 22 33",
        "verified_name": "Inmo Sol",
        "subscribed_apps": True,
        "app_secret_set": True,
        "webhook_callback_url": "https://api.selai.es/webhooks/whatsapp",
        # Per agency — see test_status_with_nothing_connected.
        "webhook_verify_token": agency_verify_token(agency.id),
    }

    # Graph was called with the caller's credentials, not the env ones.
    call = fake_http.calls[0]
    assert call["url"] == f"https://graph.facebook.com/{settings.whatsapp_api_version}/{PNID}"
    assert call["params"] == {"fields": "display_phone_number,verified_name"}
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"

    stored = agency.settings
    assert stored["whatsapp_phone_number_id"] == PNID
    assert stored["whatsapp_waba_id"] == "9876543210"
    assert stored["whatsapp_access_token"] != TOKEN
    assert decrypt_secret(stored["whatsapp_access_token"]) == TOKEN
    assert stored["whatsapp_display_number"] == "+34 600 11 22 33"
    assert stored["whatsapp_verified_name"] == "Inmo Sol"
    assert stored["whatsapp_app_secret"] != APP_SECRET
    assert decrypt_secret(stored["whatsapp_app_secret"]) == APP_SECRET
    # connected_at is a valid ISO timestamp
    datetime.fromisoformat(stored["whatsapp_connected_at"])
    assert db.commits == 1


def test_whatsapp_connect_bad_token_is_422(client, agency, db, fake_http):
    fake_http.response = _FakeHttpResponse(401, {"error": {"message": "Invalid OAuth"}})

    resp = client.post(_url(agency, "/whatsapp"), json=_connect_body())
    assert resp.status_code == 422
    assert resp.json()["detail"] == "whatsapp_verification_failed"
    assert "whatsapp_access_token" not in agency.settings
    assert db.commits == 0


def test_whatsapp_connect_network_error_is_502(client, agency, db, fake_http):
    fake_http.exc = httpx.ConnectTimeout("no route to graph.facebook.com")

    resp = client.post(_url(agency, "/whatsapp"), json=_connect_body())
    assert resp.status_code == 502
    assert resp.json()["detail"] == "whatsapp_provider_error"
    assert db.commits == 0


def test_whatsapp_connect_graph_5xx_is_502(client, agency, fake_http):
    fake_http.response = _FakeHttpResponse(500, {"error": "boom"})
    resp = client.post(_url(agency, "/whatsapp"), json=_connect_body())
    assert resp.status_code == 502
    assert resp.json()["detail"] == "whatsapp_provider_error"


def test_whatsapp_connect_graph_non_json_200_is_502(client, agency, db, fake_http):
    class _BadJsonResponse(_FakeHttpResponse):
        def json(self) -> dict:
            raise ValueError("not json")

    fake_http.response = _BadJsonResponse(200)
    resp = client.post(_url(agency, "/whatsapp"), json=_connect_body())
    assert resp.status_code == 502
    assert resp.json()["detail"] == "whatsapp_provider_error"
    assert "whatsapp_access_token" not in agency.settings
    assert db.commits == 0


def test_whatsapp_connect_requires_fields(client, agency):
    resp = client.post(_url(agency, "/whatsapp"), json={"phone_number_id": PNID})
    assert resp.status_code == 422  # missing access_token


# ---------------------------------------------------------------------------
# DELETE "/whatsapp" — disconnect
# ---------------------------------------------------------------------------


def test_whatsapp_disconnect_clears_only_whatsapp_keys(client, agency, db):
    agency.settings = {
        "whatsapp_phone_number_id": PNID,
        "whatsapp_waba_id": "9876543210",
        "whatsapp_access_token": TOKEN,
        "whatsapp_app_secret": "app-secret",
        "whatsapp_display_number": "+34 600 11 22 33",
        "whatsapp_verified_name": "Inmo Sol",
        "whatsapp_connected_at": "2026-07-25T10:00:00+00:00",
        "whatsapp_subscribed_apps": True,
        "voice_id": "sonic-multilingual",
    }

    resp = client.delete(_url(agency, "/whatsapp"))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert agency.settings == {"voice_id": "sonic-multilingual"}
    assert db.commits == 1


# ---------------------------------------------------------------------------
# POST "/phone" — provision AI phone number
# ---------------------------------------------------------------------------


def test_phone_provision_success(client, agency, db, fake_http, sent_emails, monkeypatch):
    monkeypatch.setattr(settings, "vapi_api_key", SecretStr("vapi-test"))
    agency.settings = {"vapi_assistant_id": "asst_1"}
    fake_http.response = _FakeHttpResponse(201, {"id": "pn_1", "number": "+18455550123"})

    resp = client.post(_url(agency, "/phone"), json={})
    assert resp.status_code == 200
    assert resp.json() == {"status": "active", "number": "+18455550123"}

    call = fake_http.calls[0]
    assert call["url"] == "https://api.vapi.ai/phone-number"
    assert call["json"] == {"provider": "vapi", "assistantId": "asst_1"}
    assert call["headers"]["Authorization"] == "Bearer vapi-test"

    assert agency.settings["ai_phone_number"] == "+18455550123"
    assert agency.settings["vapi_phone_number_id"] == "pn_1"
    assert sent_emails == []


def test_phone_provision_vapi_rejects_falls_back_to_requested(
    client, agency, fake_http, sent_emails, monkeypatch
):
    monkeypatch.setattr(settings, "vapi_api_key", SecretStr("vapi-test"))
    agency.settings = {"vapi_assistant_id": "asst_1"}
    fake_http.response = _FakeHttpResponse(400, {"message": "no numbers available"})

    resp = client.post(_url(agency, "/phone"), json={})
    assert resp.status_code == 200
    assert resp.json() == {"status": "requested"}

    assert agency.settings["phone_number_requested"] is True
    assert "ai_phone_number" not in agency.settings

    assert len(sent_emails) == 1
    mail = sent_emails[0]
    assert mail["to"] == "hola@selai.es"
    assert mail["subject"] == "Solicitud de número de teléfono IA"
    assert "Inmo Sol" in mail["html"]
    assert "inmo-sol" in mail["html"]
    assert "dueno@inmosol.example.com" in mail["html"]


def test_phone_provision_without_vapi_key_is_requested(
    client, agency, fake_http, sent_emails, monkeypatch
):
    monkeypatch.setattr(settings, "vapi_api_key", SecretStr(""))

    resp = client.post(_url(agency, "/phone"), json={})
    assert resp.json() == {"status": "requested"}
    assert agency.settings["phone_number_requested"] is True
    assert fake_http.calls == []  # no Vapi call without a key
    assert len(sent_emails) == 1


def test_phone_provision_is_idempotent_when_already_active(
    client, agency, fake_http, sent_emails
):
    agency.settings = {"ai_phone_number": "+18455550123"}

    resp = client.post(_url(agency, "/phone"), json={})
    assert resp.json() == {"status": "active", "number": "+18455550123"}
    assert fake_http.calls == []
    assert sent_emails == []
