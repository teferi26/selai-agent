"""Item 159 — connecting WhatsApp must leave the webhook usable.

Covers:
- POST /channels/whatsapp auto-subscribes the app to the agency's WABA
  (POST /{waba_id}/subscribed_apps) and records the outcome, without ever
  breaking the connection when Meta refuses.
- Both POST /channels/whatsapp and GET /channels hand the dashboard the
  copyable webhook callback URL + verify token.

Same FakeSession / dependency_overrides style as test_channels_api.py.
"""
from __future__ import annotations

import uuid
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
WABA = "9876543210"
CALLBACK = "https://api.selai.es/webhooks/whatsapp"
# (217) Required to connect — see test_whatsapp_own_app.py for why.
APP_SECRET = "meta-app-secret"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def one(self):
        return self._value


class FakeSession:
    def __init__(self, agency: SimpleNamespace):
        self.agency = agency
        self.commits = 0
        # (217) GET /channels counts conversations per channel; both queries
        # look the same in SQL, so nothing here needs to tell them apart —
        # neither channel has ever received anything in this module.
        self.conversation_stats: tuple[int, object] = (0, None)

    async def get(self, model, pk):
        return self.agency if pk == self.agency.id else None

    async def execute(self, stmt):
        sql = str(stmt)
        # WhatsApp counts inbound messages joined to their conversation and
        # email counts conversations; both mention "conversations" and
        # neither channel has received anything in this module, so one branch
        # still covers both.
        if "conversations" in sql:
            assert any(
                isinstance(v, ConversationChannel) for v in stmt.compile().params.values()
            ), "a conversations count must always be scoped to one channel"
            return _FakeResult(self.conversation_stats)
        if "webhook_subscriptions" in sql:
            return _FakeResult(0)
        raise AssertionError(f"unexpected query: {sql}")

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


class _FakeHttpResponse:
    def __init__(self, status_code: int = 200, data: dict | None = None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.text = str(self._data)

    def json(self) -> dict:
        return self._data


class _ScriptedHttpClient:
    """Replies with a queue of responses, one per request, and records calls.

    connect_whatsapp issues two: GET (verify credentials) then POST
    (subscribed_apps).
    """

    calls: list[dict] = []
    responses: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def _respond(self, call: dict):
        type(self).calls.append(call)
        nxt = type(self).responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    async def get(self, url, params=None, headers=None):
        return await self._respond(
            {"method": "GET", "url": url, "params": params, "headers": headers}
        )

    async def post(self, url, json=None, headers=None):
        return await self._respond(
            {"method": "POST", "url": url, "json": json, "headers": headers}
        )


@pytest.fixture
def agency():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Inmo Sol",
        slug="inmo-sol",
        contact_email="dueno@inmosol.example.com",
        settings={},
    )


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
    monkeypatch.setattr(settings, "public_base_url", "https://api.selai.es/")
    monkeypatch.setattr(settings, "whatsapp_verify_token", SecretStr("verify-me"))
    return TestClient(app)


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setattr(channels_module.httpx, "AsyncClient", _ScriptedHttpClient)
    _ScriptedHttpClient.calls = []
    _ScriptedHttpClient.responses = []
    return _ScriptedHttpClient


def _verified_ok() -> _FakeHttpResponse:
    return _FakeHttpResponse(
        200, {"display_phone_number": "+34 600 11 22 33", "verified_name": "Inmo Sol"}
    )


def _connect(client, agency, **overrides):
    body = {
        "phone_number_id": PNID,
        "waba_id": WABA,
        "access_token": TOKEN,
        "app_secret": APP_SECRET,
        **overrides,
    }
    return client.post(f"/api/v1/agencies/{agency.id}/channels/whatsapp", json=body)


# ---------------------------------------------------------------------------
# Auto-subscribe
# ---------------------------------------------------------------------------


def test_connect_auto_subscribes_app_to_waba(client, agency, db, http):
    http.responses = [_verified_ok(), _FakeHttpResponse(200, {"success": True})]

    resp = _connect(client, agency)
    assert resp.status_code == 200
    assert resp.json()["subscribed_apps"] is True

    subscribe = http.calls[1]
    assert subscribe["method"] == "POST"
    assert subscribe["url"] == (
        f"https://graph.facebook.com/{settings.whatsapp_api_version}"
        f"/{WABA}/subscribed_apps"
    )
    assert subscribe["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert agency.settings["whatsapp_subscribed_apps"] is True


def test_connect_survives_subscribe_rejection(client, agency, db, http):
    """Meta refusing the subscription must never fail the connection: the
    credentials are valid, only the webhook needs manual setup."""
    http.responses = [
        _verified_ok(),
        _FakeHttpResponse(403, {"error": {"message": "missing permission"}}),
    ]

    resp = _connect(client, agency)
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["subscribed_apps"] is False

    assert agency.settings["whatsapp_access_token"] != TOKEN
    assert decrypt_secret(agency.settings["whatsapp_access_token"]) == TOKEN
    assert agency.settings["whatsapp_subscribed_apps"] is False
    assert db.commits == 1


def test_connect_survives_subscribe_network_error(client, agency, db, http):
    http.responses = [_verified_ok(), httpx.ConnectTimeout("graph unreachable")]

    resp = _connect(client, agency)
    assert resp.status_code == 200
    assert resp.json()["subscribed_apps"] is False
    assert agency.settings["whatsapp_subscribed_apps"] is False


def test_connect_without_waba_id_skips_subscribe(client, agency, db, http):
    http.responses = [_verified_ok()]

    resp = _connect(client, agency, waba_id=None)
    assert resp.status_code == 200
    assert resp.json()["subscribed_apps"] is False
    # Only the verification GET happened — no subscribe attempt is possible.
    assert len(http.calls) == 1
    assert http.calls[0]["method"] == "GET"


def test_connect_subscribe_success_false_is_not_subscribed(client, agency, db, http):
    """Graph answers 200 with {"success": false} — honest UI: not subscribed."""
    http.responses = [_verified_ok(), _FakeHttpResponse(200, {"success": False})]

    assert _connect(client, agency).json()["subscribed_apps"] is False


# ---------------------------------------------------------------------------
# Webhook values exposed to the dashboard
# ---------------------------------------------------------------------------


# (217, revisión) The verify token handed to the panel is derived per agency
# instead of being the raw `settings.whatsapp_verify_token`. That env value is
# one secret shared by every tenant and this endpoint printed it to any member
# of any agency: it could not be rotated for one customer, and a leak could
# not be attributed. Both forms still pass the Meta challenge (see
# test_whatsapp_217_adversarial.py), so nothing already configured breaks.
def _expected_token(agency) -> str:
    return agency_verify_token(agency.id)


def test_connect_returns_webhook_config(client, agency, http):
    http.responses = [_verified_ok(), _FakeHttpResponse(200, {"success": True})]

    body = _connect(client, agency).json()
    # public_base_url has a trailing slash — the URL must not double it.
    assert body["webhook_callback_url"] == CALLBACK
    assert body["webhook_verify_token"] == _expected_token(agency)
    assert body["webhook_verify_token"] != "verify-me"


def test_status_returns_webhook_config(client, agency):
    agency.settings = {
        "whatsapp_access_token": TOKEN,
        "whatsapp_phone_number_id": PNID,
        "whatsapp_subscribed_apps": True,
    }
    wa = client.get(f"/api/v1/agencies/{agency.id}/channels").json()["whatsapp"]
    assert wa["connected"] is True
    assert wa["webhook_callback_url"] == CALLBACK
    assert wa["webhook_verify_token"] == _expected_token(agency)
    assert wa["webhook_verify_token"] != "verify-me"
    assert wa["subscribed_apps"] is True


def test_status_subscribed_apps_defaults_to_false(client, agency):
    """An agency connected before auto-subscribe existed has no flag stored:
    it must read as "not subscribed", never as a silent success."""
    agency.settings = {
        "whatsapp_access_token": TOKEN,
        "whatsapp_phone_number_id": PNID,
    }
    wa = client.get(f"/api/v1/agencies/{agency.id}/channels").json()["whatsapp"]
    assert wa["subscribed_apps"] is False


# ---------------------------------------------------------------------------
# Per-agency app secret (item 160, storage side)
# ---------------------------------------------------------------------------


def test_connect_stores_app_secret(client, agency, http):
    http.responses = [_verified_ok(), _FakeHttpResponse(200, {"success": True})]

    _connect(client, agency, app_secret="  s3cr3t  ")
    assert agency.settings["whatsapp_app_secret"] != "s3cr3t"
    assert decrypt_secret(agency.settings["whatsapp_app_secret"]) == "s3cr3t"


def test_reconnecting_replaces_the_previous_secret(client, agency, http):
    """The fix for "I pasted the secret of the wrong app" is reconnecting
    with the right one, so the new value has to win outright — otherwise the
    agency keeps validating against a secret it believes it has replaced and
    every message bounces with no way to see why."""
    agency.settings = {"whatsapp_app_secret": "old-secret"}
    http.responses = [_verified_ok(), _FakeHttpResponse(200, {"success": True})]

    _connect(client, agency, app_secret="new-secret")
    assert agency.settings["whatsapp_app_secret"] != "new-secret"
    assert decrypt_secret(agency.settings["whatsapp_app_secret"]) == "new-secret"


def test_connect_reports_the_agency_secret_is_in_use(client, agency, http):
    """The panel has to say which signature we check: with a secret stored,
    ours stops being accepted for this number. Since 217 there is no other
    outcome — connecting without one is refused (test_whatsapp_own_app.py)."""
    http.responses = [_verified_ok(), _FakeHttpResponse(200, {"success": True})]
    assert _connect(client, agency, app_secret="s3cr3t").json()["app_secret_set"] is True


def test_status_reports_the_agency_secret_without_leaking_it(client, agency):
    agency.settings = {
        "whatsapp_access_token": TOKEN,
        "whatsapp_phone_number_id": PNID,
        "whatsapp_app_secret": "s3cr3t",
    }
    wa = client.get(f"/api/v1/agencies/{agency.id}/channels").json()["whatsapp"]
    assert wa["app_secret_set"] is True
    assert "s3cr3t" not in str(wa)


def test_status_app_secret_set_is_false_for_a_pre_217_connection(client, agency):
    """A number connected before the secret was mandatory. Every webhook for
    it is now rejected, so the flag must read False and the dashboard has to
    treat it as broken — not as "connected".

    `app_secret_required` is the other half: it is what turns "no secret
    stored" into "we are rejecting everything". Without it the panel would
    say the same thing to an agency sitting on *our* number, which needs no
    secret of its own and works perfectly.
    """
    agency.settings = {
        "whatsapp_access_token": TOKEN,
        "whatsapp_phone_number_id": PNID,
    }
    wa = client.get(f"/api/v1/agencies/{agency.id}/channels").json()["whatsapp"]
    assert wa["app_secret_set"] is False
    assert wa["app_secret_required"] is True
