"""Google Calendar, revisión adversarial: lo que rompe el piloto, no lo feliz.

Cada test de aquí nació de un ataque concreto contra
`app/services/google_calendar.py` + `app/api/google_calendar.py`:

1. El callback es público. Cualquier `state` mal formado tiene que ser un 400,
   nunca una excepción no controlada (un 500 anónimo es ruido en Sentry y una
   forma barata de hacer daño).
2. Google permite al usuario *desmarcar* el permiso en la pantalla de
   consentimiento (permisos granulares). Si guardamos ese token, el panel dice
   "Conectado" sobre una conexión que no puede crear un solo evento.
3. Un calendario que deja de funcionar en silencio es peor que uno desconectado:
   la propia pantalla promete "si Google falla, aquí te avisamos".
4. Reconectar con OTRA cuenta de Google no puede dejar el correo de la anterior
   pintado en el panel.
5. La URI de retorno configurada en el `.env` que se reparte apunta a una ruta
   que no existe: el consentimiento acabaría en un 404 después de que el
   cliente haya escrito su contraseña.
6. Un `code` de otra aplicación (o de otro intento) no puede tirar abajo una
   conexión que funciona.
7. Ni el refresh token ni el access token pueden acabar en un log.

El HTTP a Google se falsea a nivel de `httpx.AsyncClient`, así que lo que se
comprueba es la petición exacta que sale hacia Google.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from urllib.parse import quote

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

AGENCY_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
REFRESH_TOKEN = "1//token-de-refresco-de-la-agencia"
ACCESS_TOKEN = "ya29.access-token-adversarial"
CLIENT_ID = "999-zzz.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-secreto-adversarial"
EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"


# ---------------------------------------------------------------------------
# Andamiaje (independiente de test_google_calendar.py a propósito: los dos
# ficheros se editan por separado y ninguno debe romper al otro)
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code: int = 200, data: dict | None = None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.text = str(self._data)

    def json(self) -> dict:
        return self._data


class _FakeAsyncClient:
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
            raise AssertionError(f"POST inesperado a {url}")
        return type(self).responses.pop(0)


class FakeSession:
    def __init__(self, agency=None, lead=None, property_=None):
        self.agency = agency
        self.lead = lead
        self.property_ = property_
        self.added: list = []
        self.commits = 0

    async def get(self, model, pk):
        row = {
            "Agency": self.agency,
            "Lead": self.lead,
            "Property": self.property_,
        }.get(getattr(model, "__name__", str(model)))
        if row is not None and getattr(row, "id", None) == pk:
            return row
        return None

    def add(self, obj):
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


class _LogRecorder:
    """Guarda todo lo que se le pasa al logger para poder registrarlo."""

    def __init__(self) -> None:
        self.records: list[tuple] = []

    def _record(self, *args, **kwargs):
        self.records.append((args, kwargs))

    info = warning = error = exception = debug = _record

    def dump(self) -> str:
        return repr(self.records)


def _agency(settings_json: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=AGENCY_ID,
        name="Inmo Adversaria",
        slug="inmo-adversaria",
        timezone="Europe/Madrid",
        settings=settings_json if settings_json is not None else {},
    )


def _connected_agency(**extra) -> SimpleNamespace:
    return _agency(
        {
            gcal.KEY_REFRESH_TOKEN: REFRESH_TOKEN,
            gcal.KEY_CALENDAR_ID: "primary",
            gcal.KEY_CONNECTED_AT: "2026-07-30T10:00:00+00:00",
            **extra,
        }
    )


def _lead() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        full_name="Lucía Peña",
        phone_e164="+34600333444",
        email="lucia@example.com",
    )


def _property() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        title="Ático en Malasaña",
        reference="REF-7",
        address="Calle del Pez 3, Madrid",
    )


@pytest.fixture(autouse=True)
def _google_config(monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "google_oauth_client_secret", SecretStr(CLIENT_SECRET))
    monkeypatch.setattr(settings, "google_oauth_redirect_uri", "")
    monkeypatch.setattr(settings, "public_base_url", "https://api.selai.es")
    monkeypatch.setattr(settings, "secret_key", SecretStr("clave-de-test"))
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


@pytest.fixture
def client_factory(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")  # bypass de auth en dev

    def _make(session: FakeSession) -> TestClient:
        api = FastAPI()
        api.include_router(calendar_api.router)
        api.include_router(calendar_api.callback_router)
        api.include_router(visits_api.router)

        async def _override():
            yield session

        api.dependency_overrides[get_db] = _override
        return TestClient(api)

    return _make


@pytest.fixture
def no_crm_events(monkeypatch):
    async def _noop(*args, **kwargs):
        return False

    monkeypatch.setattr(visits_api, "emit_visit_scheduled", _noop)


def _calendar_url(path: str = "") -> str:
    return f"/api/v1/agencies/{AGENCY_ID}/calendar{path}"


def _visit_payload(lead, property_) -> dict:
    return {
        "lead_id": str(lead.id),
        "property_id": str(property_.id),
        "scheduled_at": "2026-10-02T16:00:00+00:00",
        "duration_minutes": 30,
        "briefing": "Interesada en la terraza.",
    }


def _token_ok(**extra) -> _Resp:
    return _Resp(200, {"access_token": ACCESS_TOKEN, "expires_in": 3600, **extra})


def _exchange_ok(**extra) -> _Resp:
    return _Resp(
        200,
        {
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "expires_in": 3600,
            "scope": EVENTS_SCOPE,
            **extra,
        },
    )


# ---------------------------------------------------------------------------
# 1. El callback es público: ningún `state` puede provocar un 500
# ---------------------------------------------------------------------------


def test_a_state_with_non_ascii_digits_is_a_400_not_a_500(client_factory, fake_http):
    """`int()` acepta dígitos unicode; la firma se calculaba en ASCII.

    `?state=<hex>.١٢٣.<firma>` pasaba el `int(...)` y reventaba al firmar, así
    que un anónimo convertía un endpoint público en un 500.
    """
    agency = _agency()
    client = client_factory(FakeSession(agency=agency))
    state = f"{AGENCY_ID.hex}.{quote('١٢٣')}.deadbeef"

    resp = client.get(f"/api/v1/calendar/callback?code=4/x&state={state}")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_state"
    assert fake_http.calls == []
    assert agency.settings == {}


@pytest.mark.parametrize(
    "state",
    [
        "..",
        "a.b.c",
        f"{AGENCY_ID.hex}..",
        f"{AGENCY_ID.hex}.{'9' * 5000}.deadbeef",  # int() se niega por encima de 4300 dígitos
        "%00.%00.%00",
        f"{AGENCY_ID.hex}.0.{'f' * 64}",
    ],
)
def test_no_state_shape_reaches_an_unhandled_error(client_factory, fake_http, state):
    client = client_factory(FakeSession(agency=_agency()))
    resp = client.get(f"/api/v1/calendar/callback?code=4/x&state={state}")
    assert resp.status_code == 400
    assert fake_http.calls == []


def test_an_expired_state_gets_a_page_and_a_forged_one_a_400(client_factory, fake_http):
    """Tardar diez minutos en la pantalla de Google no es un ataque: el cliente
    tiene que leer qué ha pasado, no un JSON en crudo. Un state que no hemos
    firmado sigue siendo un 400 seco."""
    import time as _time

    client = client_factory(FakeSession(agency=_agency()))
    old = gcal.build_state(AGENCY_ID, issued_at=int(_time.time()) - gcal.STATE_TTL_SECONDS - 60)

    resp = client.get(f"/api/v1/calendar/callback?code=4/x&state={old}")
    assert resp.status_code == 200
    assert "caducado" in resp.text
    assert fake_http.calls == []  # nada que intercambiar

    forged = f"{AGENCY_ID.hex}.{int(_time.time())}.{'0' * 64}"
    assert client.get(f"/api/v1/calendar/callback?code=4/x&state={forged}").status_code == 400


def test_the_callback_only_believes_the_signed_state(client_factory, fake_http):
    """Un `agency_id` en la query no manda: solo el state firmado."""
    agency = _agency()
    client = client_factory(FakeSession(agency=agency))
    resp = client.get(
        f"/api/v1/calendar/callback?code=4/x&agency_id={AGENCY_ID}&state=basura"
    )
    assert resp.status_code == 400
    assert agency.settings == {}
    assert fake_http.calls == []


# ---------------------------------------------------------------------------
# 2. Consentimiento granular: el cliente puede desmarcar el permiso
# ---------------------------------------------------------------------------


def test_a_consent_without_the_calendar_permission_is_not_stored(client_factory, fake_http):
    """Google devuelve token aunque el usuario desmarque la casilla.

    Guardarlo deja el panel diciendo "Conectado" sobre una conexión que
    fallará con 403 en cada visita, para siempre y en silencio.
    """
    agency = _agency()
    client = client_factory(FakeSession(agency=agency))
    fake_http.responses = [
        _Resp(
            200,
            {
                "access_token": ACCESS_TOKEN,
                "refresh_token": REFRESH_TOKEN,
                "expires_in": 3600,
                # El usuario solo dejó marcado su perfil.
                "scope": "https://www.googleapis.com/auth/userinfo.email",
            },
        )
    ]

    resp = client.get(
        f"/api/v1/calendar/callback?code=4/x&state={gcal.build_state(AGENCY_ID)}"
    )
    assert resp.status_code == 200
    assert gcal.KEY_REFRESH_TOKEN not in agency.settings
    assert "permiso" in resp.text.lower()
    assert client.get(_calendar_url()).json()["connected"] is False


def test_a_consent_with_extra_scopes_is_still_valid(client_factory, fake_http):
    """`include_granted_scopes=true` puede devolver permisos de antes."""
    agency = _agency()
    client = client_factory(FakeSession(agency=agency))
    fake_http.responses = [
        _exchange_ok(scope=f"{EVENTS_SCOPE} https://www.googleapis.com/auth/drive.file")
    ]

    client.get(f"/api/v1/calendar/callback?code=4/x&state={gcal.build_state(AGENCY_ID)}")
    assert decrypt_secret(agency.settings[gcal.KEY_REFRESH_TOKEN]) == REFRESH_TOKEN


def test_a_response_without_a_scope_field_is_accepted(client_factory, fake_http):
    """Si Google no dice qué concedió, no inventamos un fallo."""
    agency = _agency()
    client = client_factory(FakeSession(agency=agency))
    fake_http.responses = [
        _Resp(
            200,
            {"access_token": ACCESS_TOKEN, "refresh_token": REFRESH_TOKEN, "expires_in": 3600},
        )
    ]
    client.get(f"/api/v1/calendar/callback?code=4/x&state={gcal.build_state(AGENCY_ID)}")
    assert decrypt_secret(agency.settings[gcal.KEY_REFRESH_TOKEN]) == REFRESH_TOKEN


def test_only_the_events_scope_is_ever_requested():
    """Nada de leer la agenda ni de preguntar quién es el usuario."""
    assert gcal.SCOPES == (EVENTS_SCOPE,)
    source = (gcal.__file__ or "").replace("\\", "/")
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    for forbidden in (
        "auth/calendar.readonly",
        "auth/calendar.settings",
        "auth/userinfo",
        '"https://www.googleapis.com/auth/calendar"',
    ):
        assert forbidden not in text


# ---------------------------------------------------------------------------
# 3. Un calendario roto en silencio
# ---------------------------------------------------------------------------


def test_a_failing_sync_is_visible_in_the_panel(client_factory, fake_http, no_crm_events):
    """403 (permiso retirado a medias) o 500: la visita se agenda igual, pero
    el panel tiene que poder decir que la copia no salió. La propia pantalla
    promete "si Google falla, aquí te avisamos"."""
    agency, lead, prop = _connected_agency(), _lead(), _property()
    session = FakeSession(agency=agency, lead=lead, property_=prop)
    client = client_factory(session)
    fake_http.responses = [_token_ok(), _Resp(403, {"error": "insufficientPermissions"})]

    resp = client.post(f"/api/v1/agencies/{AGENCY_ID}/visits", json=_visit_payload(lead, prop))
    assert resp.status_code == 201

    body = client.get(_calendar_url()).json()
    assert body["connected"] is True  # el permiso sigue ahí, solo falla
    assert body["sync_error"] == "http_403"
    assert body["sync_error_at"] is not None


def test_a_successful_sync_clears_the_warning(client_factory, fake_http, no_crm_events):
    agency = _connected_agency(
        **{
            gcal.KEY_LAST_ERROR: "http_500",
            gcal.KEY_LAST_ERROR_AT: "2026-07-31T08:00:00+00:00",
        }
    )
    lead, prop = _lead(), _property()
    session = FakeSession(agency=agency, lead=lead, property_=prop)
    client = client_factory(session)
    fake_http.responses = [_token_ok(), _Resp(200, {"id": "evt-ok"})]

    client.post(f"/api/v1/agencies/{AGENCY_ID}/visits", json=_visit_payload(lead, prop))

    body = client.get(_calendar_url()).json()
    assert body["sync_error"] is None
    assert body["sync_error_at"] is None


def test_a_google_outage_does_not_wipe_the_connection(client_factory, fake_http, no_crm_events):
    agency, lead, prop = _connected_agency(), _lead(), _property()
    client = client_factory(FakeSession(agency=agency, lead=lead, property_=prop))
    fake_http.exc = RuntimeError("connection reset")

    resp = client.post(f"/api/v1/agencies/{AGENCY_ID}/visits", json=_visit_payload(lead, prop))
    assert resp.status_code == 201
    assert agency.settings[gcal.KEY_REFRESH_TOKEN] == REFRESH_TOKEN
    assert client.get(_calendar_url()).json()["sync_error"] == "google_unreachable"


# ---------------------------------------------------------------------------
# 4. Reconectar con otra cuenta de Google
# ---------------------------------------------------------------------------


def test_reconnecting_forgets_the_previous_google_account(client_factory, fake_http):
    """Tras una revocación conservamos el correo para poder decir de quién era.
    Al reconectar hay que olvidarlo: puede ser otra cuenta y el panel estaría
    mintiendo sobre dónde van las visitas."""
    agency = _agency(
        {
            gcal.KEY_ACCOUNT_EMAIL: "antiguo@inmosol.example.com",
            gcal.KEY_REVOKED_AT: "2026-07-31T09:00:00+00:00",
        }
    )
    client = client_factory(FakeSession(agency=agency))
    fake_http.responses = [_exchange_ok()]

    client.get(f"/api/v1/calendar/callback?code=4/x&state={gcal.build_state(AGENCY_ID)}")

    assert gcal.KEY_ACCOUNT_EMAIL not in agency.settings
    assert client.get(_calendar_url()).json()["account_email"] is None


def test_the_account_email_is_learnt_again_from_the_next_event(
    client_factory, fake_http, no_crm_events
):
    agency, lead, prop = _connected_agency(), _lead(), _property()
    client = client_factory(FakeSession(agency=agency, lead=lead, property_=prop))
    fake_http.responses = [
        _token_ok(),
        _Resp(200, {"id": "evt-1", "organizer": {"email": "nuevo@inmosol.example.com"}}),
    ]

    client.post(f"/api/v1/agencies/{AGENCY_ID}/visits", json=_visit_payload(lead, prop))
    assert agency.settings[gcal.KEY_ACCOUNT_EMAIL] == "nuevo@inmosol.example.com"


# ---------------------------------------------------------------------------
# 5. La URI de retorno
# ---------------------------------------------------------------------------


def test_a_redirect_uri_that_is_not_our_callback_is_ignored(monkeypatch):
    """El `.env` que se reparte trae `/oauth/google/callback`, una ruta que no
    existe: el cliente escribiría su contraseña para acabar en un 404."""
    monkeypatch.setattr(
        settings, "google_oauth_redirect_uri", "http://localhost:8000/oauth/google/callback"
    )
    assert gcal.redirect_uri() == "https://api.selai.es/api/v1/calendar/callback"


def test_a_redirect_uri_on_another_host_is_respected(monkeypatch):
    """Un despliegue con la API en otro hostname sigue mandando."""
    monkeypatch.setattr(
        settings, "google_oauth_redirect_uri", "https://otro.example/api/v1/calendar/callback"
    )
    assert gcal.redirect_uri() == "https://otro.example/api/v1/calendar/callback"


def test_the_authorization_url_and_the_exchange_use_the_same_uri(client_factory, fake_http):
    """Si difieren, Google contesta `redirect_uri_mismatch` y nadie sabe por qué."""
    from urllib.parse import parse_qs, urlparse

    agency = _agency()
    client = client_factory(FakeSession(agency=agency))
    fake_http.responses = [_exchange_ok()]

    url = client.get(_calendar_url("/authorize")).json()["authorization_url"]
    sent = parse_qs(urlparse(url).query)["redirect_uri"][0]

    client.get(f"/api/v1/calendar/callback?code=4/x&state={gcal.build_state(AGENCY_ID)}")
    assert fake_http.calls[0]["data"]["redirect_uri"] == sent


# ---------------------------------------------------------------------------
# 6. Un `code` que no es nuestro
# ---------------------------------------------------------------------------


def test_a_code_from_another_application_cannot_break_a_working_connection(
    client_factory, fake_http
):
    """Google contesta `invalid_grant` a un code que no emitió para nuestro
    client_id. Eso NO significa que el permiso guardado esté muerto."""
    agency = _connected_agency()
    session = FakeSession(agency=agency)
    client = client_factory(session)
    fake_http.responses = [_Resp(400, {"error": "invalid_grant"})]

    resp = client.get(
        f"/api/v1/calendar/callback?code=4/de-otra-app&state={gcal.build_state(AGENCY_ID)}"
    )
    assert resp.status_code == 200
    assert "No se ha podido conectar" in resp.text
    # La conexión que funcionaba sigue intacta y sin marca de revocación.
    assert agency.settings[gcal.KEY_REFRESH_TOKEN] == REFRESH_TOKEN
    assert gcal.KEY_REVOKED_AT not in agency.settings
    assert session.commits == 0


def test_a_failed_exchange_never_stores_anything(client_factory, fake_http):
    agency = _agency()
    session = FakeSession(agency=agency)
    client = client_factory(session)
    fake_http.responses = [_Resp(500, {"error": "internal"})]

    client.get(f"/api/v1/calendar/callback?code=4/x&state={gcal.build_state(AGENCY_ID)}")
    assert agency.settings == {}
    assert session.commits == 0


# ---------------------------------------------------------------------------
# 7. Secretos: ni en la respuesta ni en el log
# ---------------------------------------------------------------------------


async def test_no_token_ever_reaches_the_logs(fake_http, monkeypatch):
    recorder = _LogRecorder()
    monkeypatch.setattr(gcal, "log", recorder)
    agency = _connected_agency()
    visit = SimpleNamespace(
        id=uuid.uuid4(),
        scheduled_at=None,
        duration_minutes=30,
        pre_visit_briefing="",
        calendar_event_id=None,
    )

    # Un fallo de cada tipo: token, evento, y transporte.
    fake_http.responses = [_token_ok(), _Resp(500, {"error": "backendError"})]
    await gcal.sync_visit_to_calendar(agency, visit, lead=_lead(), property_=_property())
    fake_http.responses = [_Resp(400, {"error": "invalid_client"})]
    await gcal.sync_visit_to_calendar(agency, visit, lead=_lead(), property_=_property())
    fake_http.exc = RuntimeError("boom")
    await gcal.sync_visit_to_calendar(agency, visit, lead=_lead(), property_=_property())

    dump = recorder.dump()
    assert recorder.records, "el fallo tiene que dejar rastro"
    for secret in (REFRESH_TOKEN, ACCESS_TOKEN, CLIENT_SECRET):
        assert secret not in dump


def test_the_status_payload_has_no_secret_shaped_keys(client_factory):
    agency = _connected_agency(**{gcal.KEY_ACCOUNT_EMAIL: "agenda@inmosol.example.com"})
    client = client_factory(FakeSession(agency=agency))
    body = client.get(_calendar_url()).json()

    assert REFRESH_TOKEN not in repr(body)
    assert not [k for k in body if "token" in k or "secret" in k]


# ---------------------------------------------------------------------------
# 8. Sin credenciales de Google nada revienta
# ---------------------------------------------------------------------------


def test_a_deployment_without_google_credentials_still_books_visits(
    client_factory, fake_http, no_crm_events, monkeypatch
):
    """Credenciales guardadas por un entorno que sí las tenía, ejecutándose en
    uno que no: la visita se agenda y la conexión no se borra."""
    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    agency, lead, prop = _connected_agency(), _lead(), _property()
    client = client_factory(FakeSession(agency=agency, lead=lead, property_=prop))

    resp = client.post(f"/api/v1/agencies/{AGENCY_ID}/visits", json=_visit_payload(lead, prop))
    assert resp.status_code == 201
    assert fake_http.calls == []
    assert agency.settings[gcal.KEY_REFRESH_TOKEN] == REFRESH_TOKEN

    body = client.get(_calendar_url()).json()
    assert body["configured"] is False


def test_the_panel_survives_a_hand_edited_settings_blob(client_factory):
    """`agency.settings` es JSON editable a mano; una fecha basura no puede
    tumbar la página de ajustes."""
    agency = _connected_agency(
        **{
            gcal.KEY_CONNECTED_AT: "no-es-una-fecha",
            gcal.KEY_LAST_ERROR: {"raro": True},
            gcal.KEY_LAST_ERROR_AT: 12345,
        }
    )
    client = client_factory(FakeSession(agency=agency))
    resp = client.get(_calendar_url())
    assert resp.status_code == 200
    assert resp.json()["connected"] is True


# ---------------------------------------------------------------------------
# 9. Detalles del cliente HTTP
# ---------------------------------------------------------------------------


async def test_the_calendar_id_is_url_encoded(fake_http):
    """Solo guardamos "primary", pero el id sale de un JSON editable: una
    barra dentro convertiría la URL en otra llamada de la API de Google."""
    fake_http.responses = [_token_ok(), _Resp(200, {"id": "evt-1"})]
    await gcal.insert_event(
        refresh_token=REFRESH_TOKEN,
        calendar_id="equipo@group.calendar.google.com",
        body={"summary": "x"},
    )
    assert fake_http.calls[1]["url"] == (
        "https://www.googleapis.com/calendar/v3/calendars/"
        "equipo%40group.calendar.google.com/events"
    )


async def test_the_visit_the_agent_schedules_would_also_be_mirrored(fake_http):
    """Contrato del parche pendiente para `app/agent/tools.py`.

    Hoy la tool `schedule_visit` mira `User.google_refresh_token` (columna que
    no escribe nadie), así que las visitas que agenda el asistente —la mayoría—
    NO llegan al calendario. El parche es llamar a `sync_visit_to_calendar` con
    lo que esa función ya tiene a mano; esto fija que funciona con esos objetos
    tal cual, incluso sin lead.
    """
    agency, prop = _connected_agency(), _property()
    visit = SimpleNamespace(
        id=uuid.uuid4(),
        scheduled_at=None,  # lo pone el modelo; aquí solo importa que no rompa
        duration_minutes=45,
        pre_visit_briefing="Viene con su pareja.",
        calendar_event_id=None,
    )
    fake_http.responses = [_token_ok(), _Resp(200, {"id": "evt-agente"})]

    event_id = await gcal.sync_visit_to_calendar(agency, visit, lead=None, property_=prop)

    assert event_id == "evt-agente"
    assert visit.calendar_event_id == "evt-agente"
    body = fake_http.calls[1]["json"]
    assert "Ático en Malasaña" in body["summary"]
    assert "Viene con su pareja." in body["description"]


async def test_disconnecting_forgets_the_cached_access_token(fake_http):
    """Si el access token cacheado sobreviviera a la desconexión, seguiríamos
    escribiendo en el calendario de alguien que nos ha echado."""
    fake_http.responses = [_token_ok()]
    await gcal.access_token_for(REFRESH_TOKEN)
    agency = _connected_agency()

    gcal.clear_connection(agency)

    fake_http.responses = [_token_ok()]
    await gcal.access_token_for(REFRESH_TOKEN)
    assert len(fake_http.calls) == 2  # ha tenido que volver a pedirlo
