"""Per-agency Twilio SMS: connect/disconnect, status, and the webhook.

The web sells SMS in Pro and Agencia, and until now there was no way for an
agency to put its own Twilio credentials in: every message went out on
selai's global account and every inbound signature was checked against
selai's global token. These tests pin the three things that fix costs:

1. `POST /channels/sms` verifies against Twilio before persisting, and the
   auth token never appears in any response.
2. The inbound webhook resolves the tenant from the `To` number *first* and
   validates the signature with **that** agency's token — the same bug
   WhatsApp had in item 217.
3. "Connected" is not "working": only a real inbound SMS sets
   `last_inbound_at`.

Fake session + fake httpx, same spirit as test_channels_api.py — no
Postgres and no network.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import channels as channels_module
from app.api import sms as sms_module
from app.channels.sms.client import TwilioSMSClient, client_for_settings
from app.channels.sms.config import credentials_from_settings, normalize_e164
from app.core.settings import settings
from app.db.models import ConversationChannel
from app.db.session import get_db
from app.main import app
from app.services.secret_store import decrypt_secret

ACCOUNT_SID = "AC" + "0" * 32
AUTH_TOKEN = "agency-auth-token"
NUMBER = "+34910000000"
GLOBAL_NUMBER = "+34911111111"
GLOBAL_TOKEN = "selai-global-token"
NUMBER_SID = "PN" + "1" * 32

WEBHOOK_URL = "https://api.selai.es/webhooks/sms"


def _connected_settings(**overrides) -> dict:
    base = {
        "twilio_account_sid": ACCOUNT_SID,
        "twilio_auth_token": AUTH_TOKEN,
        "twilio_sms_number": NUMBER,
        "twilio_sms_number_sid": NUMBER_SID,
        "twilio_sms_friendly_name": "Inmo Sol SMS",
        "twilio_sms_connected_at": "2026-07-30T10:00:00+00:00",
    }
    return {**base, **overrides}


def _agency(settings_json: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Inmo Sol",
        slug="inmo-sol",
        contact_email="dueno@inmosol.example.com",
        # Read by GET /settings, which this module calls to prove the token
        # does not leak through the *other* endpoint that serves the blob.
        timezone="Europe/Madrid",
        default_language="es",
        settings=settings_json if settings_json is not None else {},
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _ScalarsResult:
    """`.scalars().first()` — lo que usa la consulta de "¿de quién es este
    número?", que va con `limit(1)` para no romperse si ya hay dos filas."""

    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value


def _channel_param(stmt) -> ConversationChannel | None:
    for value in stmt.compile().params.values():
        if isinstance(value, ConversationChannel):
            return value
    return None


class FakeSession:
    """Answers the queries the channels endpoints issue."""

    def __init__(self, agency: SimpleNamespace):
        self.agency = agency
        self.email_stats: tuple[int, datetime | None] = (0, None)
        self.whatsapp_stats: tuple[int, datetime | None] = (0, None)
        self.sms_stats: tuple[int, datetime | None] = (0, None)
        self.webhook_count = 0
        self.commits = 0
        # Quién tiene ya guardado el número que POST /sms intenta conectar.
        # Por defecto nadie; `tests/test_sms_adversarial.py` lo cambia.
        self.number_owner: uuid.UUID | None = None

    async def get(self, model, pk):
        return self.agency if pk == self.agency.id else None

    async def execute(self, stmt):
        sql = str(stmt)
        # La clave del JSON viaja como parámetro, no dentro del SQL.
        if "agencies" in sql and "twilio_sms_number" in list(
            stmt.compile().params.values()
        ):
            return _ScalarsResult(self.number_owner)
        if "conversations" in sql:
            channel = _channel_param(stmt)
            if channel is ConversationChannel.WHATSAPP:
                return _FakeResult(self.whatsapp_stats)
            if channel is ConversationChannel.SMS:
                return _FakeResult(self.sms_stats)
            return _FakeResult(self.email_stats)
        if "webhook_subscriptions" in sql:
            return _FakeResult(self.webhook_count)
        if "subscriptions" in sql:
            # No subscription row (dev/seeded agency) ⇒ no plan ⇒ no gate.
            # The gate itself is exercised in test_plan_gating.py and, for the
            # webhook, in `test_webhook_plan_gate_still_blocks_starter` below.
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
    monkeypatch.setattr(settings, "public_base_url", "https://api.selai.es")
    monkeypatch.setattr(settings, "whatsapp_verify_token", SecretStr("verify-me"))
    return TestClient(app)


class _FakeHttpResponse:
    def __init__(self, status_code: int = 200, data: dict | None = None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.text = json.dumps(self._data)

    def json(self) -> dict:
        return self._data


class _FakeHttpClient:
    """Records every request; response/exception configurable per test."""

    calls: list[dict] = []
    init_kwargs: list[dict] = []
    response: _FakeHttpResponse | None = None
    exc: Exception | None = None

    def __init__(self, *args, **kwargs):
        type(self).init_kwargs.append(kwargs)

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

    async def post(self, url, json=None, data=None, headers=None):
        return await self._respond(
            {"method": "POST", "url": url, "json": json, "data": data}
        )


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr(channels_module.httpx, "AsyncClient", _FakeHttpClient)
    _FakeHttpClient.calls = []
    _FakeHttpClient.init_kwargs = []
    _FakeHttpClient.response = None
    _FakeHttpClient.exc = None
    return _FakeHttpClient


def _number_payload(**overrides) -> dict:
    record = {
        "sid": NUMBER_SID,
        "phone_number": NUMBER,
        "friendly_name": "Inmo Sol SMS",
        "capabilities": {"voice": True, "sms": True, "mms": True, "fax": False},
    }
    record.update(overrides)
    return {"incoming_phone_numbers": [record]}


def _url(agency, path=""):
    return f"/api/v1/agencies/{agency.id}/channels{path}"


def _connect_body(**overrides) -> dict:
    base = {
        "account_sid": ACCOUNT_SID,
        "auth_token": AUTH_TOKEN,
        "phone_number": NUMBER,
    }
    return {**base, **overrides}


# ---------------------------------------------------------------------------
# normalize_e164
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+34 910 00 00 00", "+34910000000"),
        ("+34910000000", "+34910000000"),
        # A bare 9-digit number is what an owner types when asked for "tu
        # número"; assuming Spain is the difference between working and a
        # rejection they cannot explain.
        ("910000000", "+34910000000"),
        ("0034910000000", "+34910000000"),
        ("+1 (845) 555-0123", "+18455550123"),
        ("no soy un teléfono", None),
        ("", None),
        (None, None),
        ("12345", None),
    ],
)
def test_normalize_e164(raw, expected):
    assert normalize_e164(raw) == expected


# ---------------------------------------------------------------------------
# POST /channels/sms — connect
# ---------------------------------------------------------------------------


def test_connect_verifies_against_twilio_and_persists(client, agency, db, fake_http):
    fake_http.response = _FakeHttpResponse(200, _number_payload())

    resp = client.post(_url(agency, "/sms"), json=_connect_body())
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "connected": True,
        "phone_number": NUMBER,
        "account_sid": ACCOUNT_SID,
        "friendly_name": "Inmo Sol SMS",
        "webhook_url": WEBHOOK_URL,
    }

    # The real Twilio endpoint, with the caller's credentials as HTTP Basic
    # and the number as an exact filter — one call proves both that the
    # credentials work and that the number belongs to that account.
    call = fake_http.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == (
        f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/IncomingPhoneNumbers.json"
    )
    assert call["params"]["PhoneNumber"] == NUMBER
    assert fake_http.init_kwargs[0]["auth"] == (ACCOUNT_SID, AUTH_TOKEN)

    stored = agency.settings
    assert stored["twilio_account_sid"] == ACCOUNT_SID
    assert stored["twilio_auth_token"] != AUTH_TOKEN
    assert decrypt_secret(stored["twilio_auth_token"]) == AUTH_TOKEN
    # Twilio's own spelling, which is what arrives in the webhook's `To`.
    assert stored["twilio_sms_number"] == NUMBER
    assert stored["twilio_sms_number_sid"] == NUMBER_SID
    assert stored["twilio_sms_friendly_name"] == "Inmo Sol SMS"
    datetime.fromisoformat(stored["twilio_sms_connected_at"])
    assert db.commits == 1


def test_connect_normalizes_the_number_the_owner_typed(client, agency, db, fake_http):
    fake_http.response = _FakeHttpResponse(200, _number_payload())

    resp = client.post(_url(agency, "/sms"), json=_connect_body(phone_number="910 000 000"))
    assert resp.status_code == 200
    # E.164 with "+" is the only shape Twilio's filter matches.
    assert fake_http.calls[0]["params"]["PhoneNumber"] == NUMBER


def test_connect_bad_credentials_is_422(client, agency, db, fake_http):
    fake_http.response = _FakeHttpResponse(401, {"message": "Authenticate"})

    resp = client.post(_url(agency, "/sms"), json=_connect_body())
    assert resp.status_code == 422
    assert resp.json()["detail"] == "sms_verification_failed"
    assert agency.settings == {}
    assert db.commits == 0


def test_connect_number_not_in_account_is_422(client, agency, db, fake_http):
    # Credentials fine, number missing: it lives in a subaccount or in the
    # other Twilio project the owner also has open.
    fake_http.response = _FakeHttpResponse(200, {"incoming_phone_numbers": []})

    resp = client.post(_url(agency, "/sms"), json=_connect_body())
    assert resp.status_code == 422
    assert resp.json()["detail"] == "sms_number_not_found"
    assert agency.settings == {}
    assert db.commits == 0


def test_connect_voice_only_number_is_422(client, agency, db, fake_http):
    fake_http.response = _FakeHttpResponse(
        200,
        _number_payload(capabilities={"voice": True, "sms": False, "mms": False}),
    )

    resp = client.post(_url(agency, "/sms"), json=_connect_body())
    assert resp.status_code == 422
    assert resp.json()["detail"] == "sms_number_not_sms_capable"
    assert agency.settings == {}


def test_connect_twilio_5xx_is_502(client, agency, db, fake_http):
    fake_http.response = _FakeHttpResponse(500, {"message": "boom"})

    resp = client.post(_url(agency, "/sms"), json=_connect_body())
    assert resp.status_code == 502
    assert resp.json()["detail"] == "sms_provider_error"
    assert db.commits == 0


def test_connect_network_error_is_502(client, agency, db, fake_http):
    fake_http.exc = httpx.ConnectTimeout("no route to api.twilio.com")

    resp = client.post(_url(agency, "/sms"), json=_connect_body())
    assert resp.status_code == 502
    assert resp.json()["detail"] == "sms_provider_error"
    assert agency.settings == {}
    assert db.commits == 0


def test_connect_rejects_a_sid_that_is_not_an_account_sid(client, agency, fake_http):
    # "SK…" is an API key, not an Account SID: the URL would 404 and the
    # owner would go regenerate a token that was never the problem.
    resp = client.post(_url(agency, "/sms"), json=_connect_body(account_sid="SK" + "0" * 32))
    assert resp.status_code == 422
    assert fake_http.calls == []


def test_connect_rejects_blank_token(client, agency, fake_http):
    resp = client.post(_url(agency, "/sms"), json=_connect_body(auth_token="   "))
    assert resp.status_code == 422
    assert fake_http.calls == []


def test_connect_rejects_a_number_that_is_not_a_number(client, agency, fake_http):
    resp = client.post(_url(agency, "/sms"), json=_connect_body(phone_number="123456"))
    assert resp.status_code == 422
    assert resp.json()["detail"] == "invalid_phone"
    assert fake_http.calls == []


# ---------------------------------------------------------------------------
# The auth token never leaves the backend
# ---------------------------------------------------------------------------


def test_auth_token_is_in_no_response_anywhere(client, agency, db, fake_http):
    fake_http.response = _FakeHttpResponse(200, _number_payload())

    connect = client.post(_url(agency, "/sms"), json=_connect_body())
    assert AUTH_TOKEN not in connect.text

    status_resp = client.get(_url(agency))
    assert AUTH_TOKEN not in status_resp.text
    assert "auth_token" not in status_resp.text

    settings_resp = client.get(f"/api/v1/agencies/{agency.id}/settings")
    assert settings_resp.status_code == 200
    assert AUTH_TOKEN not in settings_resp.text

    delete = client.delete(_url(agency, "/sms"))
    assert AUTH_TOKEN not in delete.text


# ---------------------------------------------------------------------------
# GET /channels — status
# ---------------------------------------------------------------------------


def test_status_when_sms_is_not_connected(client, agency):
    body = client.get(_url(agency)).json()
    assert body["sms"] == {
        "configured": False,
        "credentials_unreadable": False,
        "phone_number": None,
        "account_sid": None,
        "friendly_name": None,
        "webhook_url": WEBHOOK_URL,
        "inbound_count": 0,
        "last_inbound_at": None,
    }


def test_status_connected_but_nothing_ever_arrived(client, agency, db):
    agency.settings = _connected_settings()

    sms = client.get(_url(agency)).json()["sms"]
    # Credentials that verify prove we can *send*. Until an SMS actually
    # comes in, the webhook in the Twilio console is unproven.
    assert sms["configured"] is True
    assert sms["phone_number"] == NUMBER
    assert sms["account_sid"] == ACCOUNT_SID
    assert sms["inbound_count"] == 0
    assert sms["last_inbound_at"] is None


def test_status_reports_real_inbound_evidence(client, agency, db):
    agency.settings = _connected_settings()
    last = datetime(2026, 7, 30, 9, 30, tzinfo=UTC)
    db.sms_stats = (7, last)
    # The WhatsApp counters must not bleed into the SMS card.
    db.whatsapp_stats = (99, datetime(2026, 1, 1, tzinfo=UTC))

    sms = client.get(_url(agency)).json()["sms"]
    assert sms["inbound_count"] == 7
    assert sms["last_inbound_at"].startswith("2026-07-30T09:30:00")


def test_status_half_stored_credentials_are_not_configured(client, agency):
    # A blob with a number but no token can neither send nor validate a
    # signature. Reporting it as connected is the green tick this whole
    # module exists to avoid.
    agency.settings = {"twilio_sms_number": NUMBER}
    assert client.get(_url(agency)).json()["sms"]["configured"] is False


# ---------------------------------------------------------------------------
# DELETE /channels/sms
# ---------------------------------------------------------------------------


def test_disconnect_clears_only_the_sms_keys(client, agency, db):
    agency.settings = {
        **_connected_settings(),
        "whatsapp_access_token": "EAAG-x",
        "voice_id": "sonic-multilingual",
    }

    resp = client.delete(_url(agency, "/sms"))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert agency.settings == {
        "whatsapp_access_token": "EAAG-x",
        "voice_id": "sonic-multilingual",
    }
    assert db.commits == 1


# ---------------------------------------------------------------------------
# TwilioSMSClient — per-agency credentials
# ---------------------------------------------------------------------------


def test_client_uses_the_agency_credentials(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "ACglobal")
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr(GLOBAL_TOKEN))
    monkeypatch.setattr(settings, "twilio_from_number", GLOBAL_NUMBER)

    client = client_for_settings(_connected_settings(), NUMBER)
    assert client.account_sid == ACCOUNT_SID
    assert client.auth_token == AUTH_TOKEN
    assert client.from_number == NUMBER


def test_client_falls_back_to_the_global_account(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "ACglobal")
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr(GLOBAL_TOKEN))
    monkeypatch.setattr(settings, "twilio_from_number", GLOBAL_NUMBER)

    client = client_for_settings({}, GLOBAL_NUMBER)
    assert client.account_sid == "ACglobal"
    assert client.from_number == GLOBAL_NUMBER


def test_client_does_not_answer_from_a_number_the_lead_never_texted(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "ACglobal")
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr(GLOBAL_TOKEN))
    monkeypatch.setattr(settings, "twilio_from_number", GLOBAL_NUMBER)

    # The SMS arrived on selai's global number (single-agency fallback), so
    # the agency's own credentials must not be used for the reply.
    client = client_for_settings(_connected_settings(), GLOBAL_NUMBER)
    assert client.from_number == GLOBAL_NUMBER
    assert client.auth_token == GLOBAL_TOKEN


def test_client_sends_with_its_own_credentials(monkeypatch):
    sent: list[dict] = []

    class _Resp:
        status_code = 201

        @staticmethod
        def json():
            return {"sid": "SM123"}

    class _Client:
        def __init__(self, *args, **kwargs):
            sent.append({"auth": kwargs.get("auth")})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, data=None):
            sent[-1].update({"url": url, "data": data})
            return _Resp()

    monkeypatch.setattr("app.channels.sms.client.httpx.AsyncClient", _Client)

    sms_client = TwilioSMSClient(
        account_sid=ACCOUNT_SID, auth_token=AUTH_TOKEN, from_number=NUMBER
    )
    import asyncio

    assert asyncio.run(sms_client.send("+34600111222", "Hola")) == "SM123"
    assert sent[0]["auth"] == (ACCOUNT_SID, AUTH_TOKEN)
    assert sent[0]["url"].endswith(f"/Accounts/{ACCOUNT_SID}/Messages.json")
    assert sent[0]["data"]["From"] == NUMBER


def test_credentials_need_all_three_values():
    assert credentials_from_settings(_connected_settings()) is not None
    assert credentials_from_settings({"twilio_account_sid": ACCOUNT_SID}) is None
    assert credentials_from_settings(_connected_settings(twilio_auth_token="")) is None
    assert credentials_from_settings(None) is None


# ---------------------------------------------------------------------------
# The inbound webhook
# ---------------------------------------------------------------------------


def _sign(url: str, form: dict, token: str) -> str:
    payload = url + "".join(k + form[k] for k in sorted(form))
    digest = hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


class WebhookSession:
    """Just enough session for the inbound route."""

    def __init__(self, agencies: list[SimpleNamespace]):
        self.agencies = agencies
        self.commits = 0
        self.queries: list[str] = []

    async def execute(self, stmt):
        sql = str(stmt)
        self.queries.append(sql)
        if "count" in sql.lower() and "agencies" in sql:
            return _FakeResult(len(self.agencies))
        if "agencies" in sql:
            params = stmt.compile().params
            wanted = next(
                (v for v in params.values() if isinstance(v, str) and v.startswith("+")),
                None,
            )
            if wanted is None:  # the `select(Agency).limit(1)` fallback
                return _FakeResult(self.agencies[0] if self.agencies else None)
            match = next(
                (
                    a
                    for a in self.agencies
                    if (a.settings or {}).get("twilio_sms_number") == wanted
                ),
                None,
            )
            return _FakeResult(match)
        raise AssertionError(f"unexpected query: {sql}")

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.fixture
def webhook_env(monkeypatch):
    """A production-shaped environment: signatures are really checked."""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "public_base_url", "https://api.selai.es")
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr(GLOBAL_TOKEN))
    monkeypatch.setattr(settings, "twilio_account_sid", "ACglobal")
    monkeypatch.setattr(settings, "twilio_from_number", GLOBAL_NUMBER)


@pytest.fixture
def handled(monkeypatch):
    """Records what reached the agent and what was sent back."""
    seen: dict = {"payloads": [], "clients": [], "sent": []}

    async def _fake_handle_inbound(db, payload):
        seen["payloads"].append(payload)
        return SimpleNamespace(reply_text="Te llamamos ahora mismo")

    async def _fake_blocked(db, agency_id):
        return False

    async def _fake_ensure_feature(db, agency_id, feature):
        return None

    class _RecordingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def send(self, to, body):
            seen["sent"].append({"to": to, "body": body})
            return "SM1"

    def _fake_client_for_settings(cfg, to_number=None):
        seen["clients"].append({"cfg": dict(cfg or {}), "to": to_number})
        return _RecordingClient()

    monkeypatch.setattr(sms_module, "handle_inbound", _fake_handle_inbound)
    monkeypatch.setattr(sms_module, "is_subscription_blocked", _fake_blocked)
    monkeypatch.setattr(sms_module, "ensure_feature", _fake_ensure_feature)
    monkeypatch.setattr(sms_module, "client_for_settings", _fake_client_for_settings)
    return seen


def _webhook_client(agencies: list[SimpleNamespace]) -> TestClient:
    session = WebhookSession(agencies)

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


@pytest.fixture
def inbound_form() -> dict:
    return {
        "From": "+34600111222",
        "To": NUMBER,
        "Body": "Hola, me interesa el piso",
        "MessageSid": "SM123",
    }


def _post_webhook(client: TestClient, form: dict, signature: str | None):
    headers = {"X-Twilio-Signature": signature} if signature is not None else {}
    return client.post("/webhooks/sms", data=form, headers=headers)


def test_webhook_accepts_a_signature_made_with_the_agency_token(
    webhook_env, handled, inbound_form
):
    agency = _agency(_connected_settings())
    client = _webhook_client([agency])
    try:
        resp = _post_webhook(
            client, inbound_form, _sign(WEBHOOK_URL, inbound_form, AUTH_TOKEN)
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert handled["payloads"][0].agency_id == agency.id
    # The reply goes out on the agency's own account, for the number the lead
    # actually texted.
    assert handled["clients"] == [{"cfg": _connected_settings(), "to": NUMBER}]
    assert handled["sent"] == [
        {"to": "+34600111222", "body": "Te llamamos ahora mismo"}
    ]


def test_webhook_returns_503_for_an_unreadable_agency_token(
    webhook_env, handled, inbound_form
):
    agency = _agency(
        {**_connected_settings(), "twilio_auth_token": "enc:v1:not-a-valid-token"}
    )
    client = _webhook_client([agency])
    try:
        resp = _post_webhook(
            client, inbound_form, _sign(WEBHOOK_URL, inbound_form, AUTH_TOKEN)
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 503
    assert resp.json()["detail"] == "channel_credentials_unavailable"
    assert handled["payloads"] == []


def test_webhook_rejects_the_global_token_for_a_connected_number(
    webhook_env, handled, inbound_form
):
    # The heart of item 217, ported to SMS: selai's global token is shared by
    # every tenant riding on our Twilio project. Accepting it for a number an
    # agency connected with its own account would let anyone holding it forge
    # leads into the one tenant that isolated itself.
    agency = _agency(_connected_settings())
    client = _webhook_client([agency])
    try:
        resp = _post_webhook(
            client, inbound_form, _sign(WEBHOOK_URL, inbound_form, GLOBAL_TOKEN)
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 401
    assert handled["payloads"] == []
    assert handled["sent"] == []


def test_webhook_rejects_a_connected_number_with_no_stored_token(
    webhook_env, handled, inbound_form
):
    # Hand-edited legacy row: its Twilio account signs with a token we do not
    # have, so the global check could only ever say "no". Reject with a log
    # that says why, never fall back.
    agency = _agency(_connected_settings(twilio_auth_token=""))
    client = _webhook_client([agency])
    try:
        resp = _post_webhook(
            client, inbound_form, _sign(WEBHOOK_URL, inbound_form, GLOBAL_TOKEN)
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 401
    assert handled["payloads"] == []


def test_webhook_uses_the_global_token_for_our_own_number(
    webhook_env, handled, inbound_form
):
    # A number served by selai's own Twilio project: our token is the sender
    # of record, even though the conversation lands in an agency.
    form = {**inbound_form, "To": GLOBAL_NUMBER}
    agency = _agency({})
    client = _webhook_client([agency])
    try:
        resp = _post_webhook(client, form, _sign(WEBHOOK_URL, form, GLOBAL_TOKEN))
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    assert handled["payloads"][0].agency_id == agency.id


def test_webhook_rejects_a_well_formed_signature_from_the_wrong_token(
    webhook_env, handled, inbound_form
):
    # Right shape, wrong key: this is the branch that actually runs the HMAC
    # comparison, not the cheap shape gate above it.
    agency = _agency(_connected_settings())
    client = _webhook_client([agency])
    try:
        resp = _post_webhook(
            client, inbound_form, _sign(WEBHOOK_URL, inbound_form, "otro-token")
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 401
    assert handled["payloads"] == []


def test_webhook_rejects_a_malformed_signature_before_any_query(
    webhook_env, handled, inbound_form
):
    # The route is exempt from the rate limiter and resolves the tenant with
    # a query *before* verifying, so a garbage header must not reach the DB.
    agency = _agency(_connected_settings())
    session = WebhookSession([agency])

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    try:
        resp = _post_webhook(TestClient(app), inbound_form, "not-even-close")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 401
    assert session.queries == []
    assert handled["payloads"] == []


def test_webhook_rejects_a_missing_signature(webhook_env, handled, inbound_form):
    agency = _agency(_connected_settings())
    client = _webhook_client([agency])
    try:
        resp = _post_webhook(client, inbound_form, None)
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 401
    assert handled["payloads"] == []


def test_webhook_accepts_the_literal_request_url_too(
    webhook_env, handled, inbound_form
):
    # Belt and braces for the URL half of the signature: Twilio hashes the
    # URL it was configured with, and behind TLS termination this process
    # sees a different string. Both candidates are checked, so a deployment
    # whose console points at the literal URL keeps working.
    agency = _agency(_connected_settings())
    client = _webhook_client([agency])
    try:
        resp = _post_webhook(
            client,
            inbound_form,
            _sign("http://testserver/webhooks/sms", inbound_form, AUTH_TOKEN),
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200


def test_webhook_signature_covers_the_form_fields(webhook_env, handled, inbound_form):
    # Signature computed over a different body than the one posted: an
    # attacker replaying a valid signature with edited text must not pass.
    agency = _agency(_connected_settings())
    signature = _sign(WEBHOOK_URL, inbound_form, AUTH_TOKEN)
    tampered = {**inbound_form, "Body": "Transfiere 3.000 € a esta cuenta"}
    client = _webhook_client([agency])
    try:
        resp = _post_webhook(client, tampered, signature)
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 401
    assert handled["payloads"] == []


def test_webhook_plan_gate_still_blocks_starter(
    webhook_env, handled, inbound_form, monkeypatch
):
    # SMS is Pro and up. A Starter's inbound message must cost nothing: no
    # agent run, no outbound Twilio message selai would pay for.
    from fastapi import HTTPException as _HTTPException

    async def _not_in_plan(db, agency_id, feature):
        assert feature == "sms"
        raise _HTTPException(403, {"code": "feature_not_in_plan"})

    monkeypatch.setattr(sms_module, "ensure_feature", _not_in_plan)

    agency = _agency(_connected_settings())
    client = _webhook_client([agency])
    try:
        resp = _post_webhook(
            client, inbound_form, _sign(WEBHOOK_URL, inbound_form, AUTH_TOKEN)
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    # 200 on purpose: a 4xx only makes Twilio retry.
    assert resp.status_code == 200
    assert resp.json() == {"status": "feature_not_in_plan"}
    assert handled["payloads"] == []
    assert handled["sent"] == []


def test_webhook_blocked_subscription_is_not_served(
    webhook_env, handled, inbound_form, monkeypatch
):
    async def _blocked(db, agency_id):
        return True

    monkeypatch.setattr(sms_module, "is_subscription_blocked", _blocked)

    agency = _agency(_connected_settings())
    client = _webhook_client([agency])
    try:
        resp = _post_webhook(
            client, inbound_form, _sign(WEBHOOK_URL, inbound_form, AUTH_TOKEN)
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    assert resp.json() == {"status": "subscription_blocked"}
    assert handled["payloads"] == []


def test_webhook_does_not_fall_back_onto_an_unrelated_number(
    webhook_env, handled, inbound_form
):
    # Single agency, but the SMS arrived on a number that is neither its own
    # nor ours. Filing it under this agency would answer a stranger from a
    # number they never texted.
    form = {**inbound_form, "To": "+34999999999"}
    agency = _agency(_connected_settings())
    client = _webhook_client([agency])
    try:
        resp = _post_webhook(client, form, _sign(WEBHOOK_URL, form, GLOBAL_TOKEN))
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.json() == {"status": "no_agency_match"}
    assert handled["payloads"] == []
