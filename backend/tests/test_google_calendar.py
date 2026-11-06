"""Google Calendar: OAuth flow, secret handling, and the visit mirror.

Everything runs against a local FastAPI app carrying only the three routers
involved — `app.main` is shared and this item must not depend on the order it
registers things in. Google is faked at the `httpx.AsyncClient` level (same
approach as test_channels_api.py), so the assertions are about the *exact*
requests we send to Google, which is the part that has to match their docs.
"""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import google_calendar as calendar_api
from app.api import visits as visits_api
from app.core.settings import settings
from app.db.session import get_db
from app.services import google_calendar as gcal
from app.services.secret_store import decrypt_secret

AGENCY_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
REFRESH_TOKEN = "1//refresh-token-de-la-agencia"
ACCESS_TOKEN = "ya29.access-token"
CLIENT_ID = "123-abc.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-secreto"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code: int = 200, data: dict | None = None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.text = str(self._data)

    def json(self) -> dict:
        return self._data


class _FakeAsyncClient:
    """Records every request; responses are queued per test, in order."""

    calls: list[dict] = []
    responses: list[_Resp] = []
    exc: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, data=None, headers=None):
        type(self).calls.append(
            {"url": url, "json": json, "data": data, "headers": headers or {}}
        )
        if type(self).exc is not None:
            raise type(self).exc
        if not type(self).responses:
            raise AssertionError(f"unexpected POST to {url}")
        return type(self).responses.pop(0)


class FakeSession:
    """Answers the handful of `db.get` calls these endpoints make."""

    def __init__(self, agency=None, lead=None, property_=None):
        self.agency = agency
        self.lead = lead
        self.property_ = property_
        self.added: list = []
        self.commits = 0

    async def get(self, model, pk):
        name = getattr(model, "__name__", str(model))
        row = {
            "Agency": self.agency,
            "Lead": self.lead,
            "Property": self.property_,
        }.get(name)
        if row is not None and getattr(row, "id", None) == pk:
            return row
        return None

    def add(self, obj):
        # The real INSERT is what assigns the primary key; the response model
        # needs one.
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    async def refresh(self, obj):
        pass


def _agency(settings_json: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=AGENCY_ID,
        name="Inmo Sol",
        slug="inmo-sol",
        timezone="Europe/Madrid",
        settings=settings_json if settings_json is not None else {},
    )


def _connected_agency() -> SimpleNamespace:
    return _agency(
        {
            gcal.KEY_REFRESH_TOKEN: REFRESH_TOKEN,
            gcal.KEY_CALENDAR_ID: "primary",
            gcal.KEY_CONNECTED_AT: "2026-07-30T10:00:00+00:00",
        }
    )


def _lead() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        full_name="Marta Ruiz",
        phone_e164="+34600111222",
        email="marta@example.com",
    )


def _property() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        title="Piso en Chamberí",
        reference="REF-42",
        address="Calle Fuencarral 1, Madrid",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _google_config(monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "google_oauth_client_secret", SecretStr(CLIENT_SECRET))
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", "")
    monkeypatch.setattr(settings, "public_base_url", "https://api.selai.es")
    monkeypatch.setattr(settings, "secret_key", SecretStr("test-secret-key"))
    # Access tokens are cached process-wide; a leftover would make the next
    # test skip the refresh it is asserting on.
    gcal.reset_token_cache()
    yield
    gcal.reset_token_cache()


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr(gcal.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.responses = []
    _FakeAsyncClient.exc = None
    return _FakeAsyncClient


def _build_app(session: FakeSession) -> FastAPI:
    api = FastAPI()
    api.include_router(calendar_api.router)
    api.include_router(calendar_api.callback_router)
    api.include_router(visits_api.router)

    async def _override():
        yield session

    api.dependency_overrides[get_db] = _override
    return api


@pytest.fixture
def client_factory(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")  # dev auth bypass

    def _make(session: FakeSession) -> TestClient:
        return TestClient(_build_app(session))

    return _make


def _token_ok(**extra) -> _Resp:
    return _Resp(200, {"access_token": ACCESS_TOKEN, "expires_in": 3600, **extra})


def _event_ok(event_id: str = "evt-1", organizer_email: str | None = None) -> _Resp:
    data: dict = {"id": event_id, "htmlLink": "https://calendar.google.com/evt-1"}
    if organizer_email:
        data["organizer"] = {"email": organizer_email}
    return _Resp(200, data)


# ---------------------------------------------------------------------------
# 1. The authorization URL
# ---------------------------------------------------------------------------


def test_authorization_url_carries_the_documented_parameters():
    url = gcal.authorization_url(AGENCY_ID)
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )

    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert q["client_id"] == CLIENT_ID
    assert q["response_type"] == "code"
    assert q["scope"] == "https://www.googleapis.com/auth/calendar.events"
    # Without these two Google returns no refresh token (offline) or omits it
    # on every authorisation after the first (consent) — which is exactly the
    # reconnect-after-revoking case.
    assert q["access_type"] == "offline"
    assert q["prompt"] == "consent"
    assert q["include_granted_scopes"] == "true"
    # Derived from PUBLIC_BASE_URL, and it must be the route that exists.
    assert q["redirect_uri"] == "https://api.selai.es/api/v1/calendar/callback"
    assert gcal.parse_state(q["state"]) == AGENCY_ID


def test_authorization_url_needs_credentials(monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    with pytest.raises(gcal.CalendarNotConfiguredError):
        gcal.authorization_url(AGENCY_ID)


def test_explicit_redirect_uri_wins(monkeypatch):
    monkeypatch.setattr(
        settings, "google_oauth_redirect_uri", "https://otro.example/api/v1/calendar/callback"
    )
    assert gcal.redirect_uri() == "https://otro.example/api/v1/calendar/callback"


# ---------------------------------------------------------------------------
# 2. The signed state
# ---------------------------------------------------------------------------


def test_state_roundtrip():
    assert gcal.parse_state(gcal.build_state(AGENCY_ID)) == AGENCY_ID


def test_tampered_state_is_rejected():
    other = uuid.uuid4()
    state = gcal.build_state(AGENCY_ID)
    agency_hex, issued, digest = state.split(".")

    # The whole point: swapping in another agency's id keeps the signature of
    # the first one, so it must not validate.
    assert gcal.parse_state(f"{other.hex}.{issued}.{digest}") is None
    # A flipped byte of the signature.
    flipped = ("0" if digest[0] != "0" else "1") + digest[1:]
    assert gcal.parse_state(f"{agency_hex}.{issued}.{flipped}") is None
    # Shapes that are not ours at all.
    assert gcal.parse_state("") is None
    assert gcal.parse_state(None) is None
    assert gcal.parse_state("no-dots") is None
    assert gcal.parse_state(f"{agency_hex}.no-un-numero.{digest}") is None


def test_state_signed_with_another_secret_is_rejected(monkeypatch):
    state = gcal.build_state(AGENCY_ID)
    monkeypatch.setattr(settings, "secret_key", SecretStr("otra-clave"))
    assert gcal.parse_state(state) is None


def test_expired_state_is_rejected():
    old = int(time.time()) - gcal.STATE_TTL_SECONDS - 1
    assert gcal.parse_state(gcal.build_state(AGENCY_ID, issued_at=old)) is None
    # …and one minted in the future (a clock games attempt) too.
    future = int(time.time()) + 3600
    assert gcal.parse_state(gcal.build_state(AGENCY_ID, issued_at=future)) is None


# ---------------------------------------------------------------------------
# 3. Endpoints: authorize / status / disconnect
# ---------------------------------------------------------------------------


def _calendar_url(path: str = "") -> str:
    return f"/api/v1/agencies/{AGENCY_ID}/calendar{path}"


def test_authorize_endpoint_returns_the_consent_url(client_factory):
    client = client_factory(FakeSession(agency=_agency()))
    resp = client.get(_calendar_url("/authorize"))
    assert resp.status_code == 200
    assert gcal.parse_state(
        parse_qs(urlparse(resp.json()["authorization_url"]).query)["state"][0]
    ) == AGENCY_ID


def test_authorize_without_google_credentials_is_503(client_factory, monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    client = client_factory(FakeSession(agency=_agency()))
    resp = client.get(_calendar_url("/authorize"))
    assert resp.status_code == 503
    assert resp.json()["detail"] == "google_calendar_not_configured"


def test_status_of_a_disconnected_agency(client_factory):
    client = client_factory(FakeSession(agency=_agency()))
    body = client.get(_calendar_url()).json()
    assert body["connected"] is False
    assert body["configured"] is True
    assert body["revoked"] is False
    assert body["calendar_id"] is None


def test_status_never_exposes_the_refresh_token(client_factory):
    agency = _connected_agency()
    agency.settings[gcal.KEY_ACCOUNT_EMAIL] = "agenda@inmosol.example.com"
    client = client_factory(FakeSession(agency=agency))

    resp = client.get(_calendar_url())
    assert resp.status_code == 200
    assert REFRESH_TOKEN not in resp.text
    body = resp.json()
    assert body == {
        "connected": True,
        "configured": True,
        "calendar_id": "primary",
        "account_email": "agenda@inmosol.example.com",
        "connected_at": "2026-07-30T10:00:00Z",
        "revoked": False,
        "revoked_at": None,
        # Conectado y sin fallos: la copia en Google está saliendo.
        "sync_error": None,
        "sync_error_at": None,
    }


def test_refresh_token_is_not_in_the_settings_whitelist():
    """The other endpoint that prints `agency.settings` must not leak it."""
    from app.api.agency_settings import _EXPOSED_SETTINGS_KEYS

    assert not set(gcal.SETTINGS_KEYS) & set(_EXPOSED_SETTINGS_KEYS)


def test_disconnect_drops_every_key(client_factory):
    agency = _connected_agency()
    session = FakeSession(agency=agency)
    client = client_factory(session)

    assert client.delete(_calendar_url()).json() == {"ok": True}
    assert not set(gcal.SETTINGS_KEYS) & set(agency.settings)
    assert session.commits == 1


def test_disconnect_can_remove_a_corrupt_encrypted_token():
    agency = _connected_agency()
    agency.settings[gcal.KEY_REFRESH_TOKEN] = "enc:v1:not-a-valid-token"

    gcal.clear_connection(agency)

    assert gcal.KEY_REFRESH_TOKEN not in agency.settings


async def test_sync_fails_closed_when_the_encrypted_token_is_corrupt():
    agency = _connected_agency()
    agency.settings[gcal.KEY_REFRESH_TOKEN] = "enc:v1:not-a-valid-token"

    result = await gcal.sync_visit_to_calendar(
        agency=agency,
        visit=SimpleNamespace(),
        lead=_lead(),
        property_=_property(),
    )

    assert result is None
    assert agency.settings[gcal.KEY_LAST_ERROR] == "credentials_unreadable"


# ---------------------------------------------------------------------------
# 4. The callback
# ---------------------------------------------------------------------------


def _callback_url(**params) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"/api/v1/calendar/callback?{query}"


def test_callback_rejects_a_manipulated_state(client_factory, fake_http):
    agency = _agency()
    client = client_factory(FakeSession(agency=agency))

    victim_state = gcal.build_state(AGENCY_ID)
    forged = f"{uuid.uuid4().hex}.{victim_state.split('.')[1]}.{victim_state.split('.')[2]}"

    resp = client.get(_callback_url(code="4/authcode", state=forged))
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_state"
    # Nothing was exchanged and nothing was written.
    assert fake_http.calls == []
    assert agency.settings == {}


def test_callback_stores_the_refresh_token_without_exposing_it(client_factory, fake_http):
    agency = _agency()
    session = FakeSession(agency=agency)
    client = client_factory(session)
    fake_http.responses = [
        _Resp(200, {"access_token": ACCESS_TOKEN, "refresh_token": REFRESH_TOKEN, "expires_in": 3600})
    ]

    resp = client.get(_callback_url(code="4/authcode", state=gcal.build_state(AGENCY_ID)))
    assert resp.status_code == 200
    # The page the customer sees must not carry the credential.
    assert REFRESH_TOKEN not in resp.text
    assert "Calendario conectado" in resp.text

    # Exactly the documented exchange.
    (call,) = fake_http.calls
    assert call["url"] == "https://oauth2.googleapis.com/token"
    assert call["data"] == {
        "code": "4/authcode",
        "grant_type": "authorization_code",
        "redirect_uri": "https://api.selai.es/api/v1/calendar/callback",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }

    assert agency.settings[gcal.KEY_REFRESH_TOKEN] != REFRESH_TOKEN
    assert decrypt_secret(agency.settings[gcal.KEY_REFRESH_TOKEN]) == REFRESH_TOKEN
    assert agency.settings[gcal.KEY_CALENDAR_ID] == "primary"
    assert session.commits == 1

    # …and the panel still does not print it.
    assert REFRESH_TOKEN not in client.get(_calendar_url()).text


def test_callback_without_refresh_token_does_not_claim_success(client_factory, fake_http):
    agency = _agency()
    client = client_factory(FakeSession(agency=agency))
    fake_http.responses = [_token_ok()]  # access token only

    resp = client.get(_callback_url(code="4/authcode", state=gcal.build_state(AGENCY_ID)))
    assert resp.status_code == 200
    assert "No se ha podido conectar" in resp.text
    assert gcal.KEY_REFRESH_TOKEN not in agency.settings


def test_callback_when_the_user_cancels(client_factory, fake_http):
    agency = _agency()
    client = client_factory(FakeSession(agency=agency))
    resp = client.get(_callback_url(error="access_denied", state=gcal.build_state(AGENCY_ID)))
    assert resp.status_code == 200
    assert "No se ha conectado" in resp.text
    assert fake_http.calls == []


def test_reconnecting_clears_the_revoked_notice(client_factory, fake_http):
    agency = _agency({gcal.KEY_REVOKED_AT: "2026-07-31T09:00:00+00:00"})
    client = client_factory(FakeSession(agency=agency))
    fake_http.responses = [
        _Resp(200, {"access_token": ACCESS_TOKEN, "refresh_token": REFRESH_TOKEN, "expires_in": 3600})
    ]

    client.get(_callback_url(code="4/authcode", state=gcal.build_state(AGENCY_ID)))
    assert gcal.KEY_REVOKED_AT not in agency.settings
    assert client.get(_calendar_url()).json()["revoked"] is False


# ---------------------------------------------------------------------------
# 5. A visit creates the event
# ---------------------------------------------------------------------------


@pytest.fixture
def no_crm_events(monkeypatch):
    """The CRM dispatcher needs a real session; it is not what we test here."""

    async def _noop(*args, **kwargs):
        return False

    monkeypatch.setattr(visits_api, "emit_visit_scheduled", _noop)


def _visit_payload(lead, property_) -> dict:
    return {
        "lead_id": str(lead.id),
        "property_id": str(property_.id),
        # 17:00 UTC = 19:00 in Madrid, which is the hour the event must show.
        "scheduled_at": "2026-09-15T17:00:00+00:00",
        "duration_minutes": 45,
        "briefing": "Quiere ver el trastero.",
    }


def test_scheduling_a_visit_creates_the_calendar_event(
    client_factory, fake_http, no_crm_events
):
    agency, lead, prop = _connected_agency(), _lead(), _property()
    session = FakeSession(agency=agency, lead=lead, property_=prop)
    client = client_factory(session)
    fake_http.responses = [_token_ok(), _event_ok(organizer_email="agenda@inmosol.example.com")]

    resp = client.post(
        f"/api/v1/agencies/{AGENCY_ID}/visits", json=_visit_payload(lead, prop)
    )
    assert resp.status_code == 201

    token_call, insert_call = fake_http.calls
    assert token_call["data"]["grant_type"] == "refresh_token"
    assert token_call["data"]["refresh_token"] == REFRESH_TOKEN
    assert insert_call["url"] == (
        "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    )
    assert insert_call["headers"]["Authorization"] == f"Bearer {ACCESS_TOKEN}"

    body = insert_call["json"]
    assert "Marta Ruiz" in body["summary"]
    assert "Piso en Chamberí" in body["summary"]
    # The phone is why anyone opens this event on the way to the visit.
    assert "+34600111222" in body["description"]
    assert "Quiere ver el trastero." in body["description"]
    assert body["location"] == "Calle Fuencarral 1, Madrid"
    # The agency's hour, not the server's.
    assert body["start"] == {
        "dateTime": "2026-09-15T19:00:00+02:00",
        "timeZone": "Europe/Madrid",
    }
    assert body["end"] == {
        "dateTime": "2026-09-15T19:45:00+02:00",
        "timeZone": "Europe/Madrid",
    }

    (visit,) = session.added
    assert visit.calendar_event_id == "evt-1"
    # Learnt from the event Google echoed back: the only way to know which
    # account is connected without asking for a wider scope.
    assert agency.settings[gcal.KEY_ACCOUNT_EMAIL] == "agenda@inmosol.example.com"


def test_visit_without_calendar_connected_touches_nothing(
    client_factory, fake_http, no_crm_events
):
    agency, lead, prop = _agency(), _lead(), _property()
    session = FakeSession(agency=agency, lead=lead, property_=prop)
    client = client_factory(session)

    resp = client.post(
        f"/api/v1/agencies/{AGENCY_ID}/visits", json=_visit_payload(lead, prop)
    )
    assert resp.status_code == 201
    assert fake_http.calls == []
    assert session.added[0].calendar_event_id is None


def test_a_visit_of_another_tenant_is_a_404(client_factory, fake_http, no_crm_events):
    agency, lead, prop = _connected_agency(), _lead(), _property()
    lead.agency_id = uuid.uuid4()  # someone else's lead
    session = FakeSession(agency=agency, lead=lead, property_=prop)
    client = client_factory(session)

    resp = client.post(
        f"/api/v1/agencies/{AGENCY_ID}/visits", json=_visit_payload(lead, prop)
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "lead_not_found"
    assert session.added == []


# ---------------------------------------------------------------------------
# 6. Google failing must never cost the visit
# ---------------------------------------------------------------------------


def test_google_error_does_not_prevent_scheduling(client_factory, fake_http, no_crm_events):
    agency, lead, prop = _connected_agency(), _lead(), _property()
    session = FakeSession(agency=agency, lead=lead, property_=prop)
    client = client_factory(session)
    fake_http.responses = [_token_ok(), _Resp(500, {"error": "backendError"})]

    resp = client.post(
        f"/api/v1/agencies/{AGENCY_ID}/visits", json=_visit_payload(lead, prop)
    )
    assert resp.status_code == 201
    assert session.added[0].calendar_event_id is None
    # A 500 from Google says nothing about the grant: still connected.
    assert decrypt_secret(agency.settings[gcal.KEY_REFRESH_TOKEN]) == REFRESH_TOKEN


def test_google_unreachable_does_not_prevent_scheduling(
    client_factory, fake_http, no_crm_events
):
    agency, lead, prop = _connected_agency(), _lead(), _property()
    session = FakeSession(agency=agency, lead=lead, property_=prop)
    client = client_factory(session)
    fake_http.exc = RuntimeError("connection reset")

    resp = client.post(
        f"/api/v1/agencies/{AGENCY_ID}/visits", json=_visit_payload(lead, prop)
    )
    assert resp.status_code == 201
    assert session.added[0].calendar_event_id is None


# ---------------------------------------------------------------------------
# 7. invalid_grant — the customer revoked us from their Google account
# ---------------------------------------------------------------------------


def test_invalid_grant_marks_the_calendar_disconnected(
    client_factory, fake_http, no_crm_events
):
    agency, lead, prop = _connected_agency(), _lead(), _property()
    session = FakeSession(agency=agency, lead=lead, property_=prop)
    client = client_factory(session)
    fake_http.responses = [_Resp(400, {"error": "invalid_grant"})]

    resp = client.post(
        f"/api/v1/agencies/{AGENCY_ID}/visits", json=_visit_payload(lead, prop)
    )
    assert resp.status_code == 201  # the visit is still booked
    assert session.added[0].calendar_event_id is None

    # The dead credential is gone and the panel has something to say.
    assert gcal.KEY_REFRESH_TOKEN not in agency.settings
    status_body = client.get(_calendar_url()).json()
    assert status_body["connected"] is False
    assert status_body["revoked"] is True
    assert status_body["revoked_at"] is not None


# ---------------------------------------------------------------------------
# 8. Access token lifecycle
# ---------------------------------------------------------------------------


async def test_access_token_is_cached_until_it_expires(fake_http):
    fake_http.responses = [_token_ok(expires_in=3600)]
    first = await gcal.access_token_for(REFRESH_TOKEN)
    second = await gcal.access_token_for(REFRESH_TOKEN)
    assert first == second == ACCESS_TOKEN
    assert len(fake_http.calls) == 1  # one refresh, two uses


async def test_a_401_from_calendar_forces_one_refresh_and_retries(fake_http):
    fake_http.responses = [
        _token_ok(),
        _Resp(401, {"error": "invalid_credentials"}),
        _token_ok(),
        _event_ok("evt-retry"),
    ]
    event = await gcal.insert_event(
        refresh_token=REFRESH_TOKEN, calendar_id="primary", body={"summary": "x"}
    )
    assert event["id"] == "evt-retry"
    assert len(fake_http.calls) == 4


async def test_an_expired_cached_token_is_refreshed(fake_http):
    fake_http.responses = [_token_ok(expires_in=0), _token_ok()]
    await gcal.access_token_for(REFRESH_TOKEN)
    await gcal.access_token_for(REFRESH_TOKEN)
    assert len(fake_http.calls) == 2


async def test_revoked_refresh_token_raises_the_typed_error(fake_http):
    fake_http.responses = [_Resp(400, {"error": "invalid_grant"})]
    with pytest.raises(gcal.CalendarAccessRevokedError):
        await gcal.access_token_for(REFRESH_TOKEN)


# ---------------------------------------------------------------------------
# 9. Structural: connecting is an owner action
# ---------------------------------------------------------------------------


def _route(method: str, path: str):
    from fastapi.routing import APIRoute

    for route in calendar_api.router.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    raise AssertionError(f"{method} {path} is not registered")


def _guarded_by_role(route) -> bool:
    """Whether the route carries a `require_role` dependency (its closure is
    named "require_role.<locals>._dependency")."""
    seen: set[int] = set()

    def walk(dep) -> bool:
        if id(dep) in seen:
            return False
        seen.add(id(dep))
        if getattr(dep.call, "__qualname__", "").startswith("require_role"):
            return True
        return any(walk(sub) for sub in dep.dependencies)

    return walk(route.dependant)


def test_connecting_and_disconnecting_are_owner_only():
    base = "/api/v1/agencies/{agency_id}/calendar"
    assert _guarded_by_role(_route("GET", base + "/authorize"))
    assert _guarded_by_role(_route("DELETE", base))
    # Reading the status is not: any member has to be able to see whether the
    # agenda is being mirrored.
    assert not _guarded_by_role(_route("GET", base))
