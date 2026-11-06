"""Revisión adversarial de la pista SMS.

Cada test aquí es un ataque concreto contra `app/api/sms.py`,
`app/api/channels.py` y `app/channels/sms/`, escrito primero contra el
código tal y como lo dejó el constructor y solo después arreglado:

1. El `auth_token` de Twilio saliendo en una respuesta. No en la de éxito
   (eso ya estaba cubierto) sino en el **422 de validación**: Pydantic v2
   mete el valor rechazado en `input`, y cuando el que falta es *otro*
   campo, `input` es el cuerpo entero — token incluido.
2. Dos agencias con el mismo `twilio_sms_number`: `scalar_one_or_none()`
   levanta `MultipleResultsFound` y el webhook devuelve 500 a Twilio, que
   reintenta. Es exactamente el fallo que WhatsApp ya tenía arreglado
   (`app/api/whatsapp.py`, `whatsapp.duplicate_phone_number_id`).
3. El número propio de selai reclamado por una agencia: el webhook de
   WhatsApp lo resuelve con `_is_our_own_number` *antes* de mirar de quién
   es el número; el de SMS no lo hacía y se quedaba sin token con el que
   comprobar la firma.
4. Twilio filtra `PhoneNumber` por coincidencia **parcial**, así que un
   número incompleto conecta un número distinto del que el dueño escribió.
5. El token no aparece en ningún log de ninguna de las dos rutas.

Sin Postgres y sin red: sesión falsa + httpx falso, igual que
`tests/test_sms_channel.py`, del que se reutilizan los ayudantes.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.exc import MultipleResultsFound

from app.api import channels as channels_module
from app.db.models import SubscriptionStatus
from app.db.session import get_db
from app.main import app
from app.services.secret_store import decrypt_secret
from tests import test_sms_channel as _sms
from tests.test_sms_channel import (
    ACCOUNT_SID,
    AUTH_TOKEN,
    GLOBAL_NUMBER,
    GLOBAL_TOKEN,
    NUMBER,
    NUMBER_SID,
    WEBHOOK_URL,
    WebhookSession,
    _agency,
    _connect_body,
    _connected_settings,
    _FakeResult,
    _number_payload,
    _post_webhook,
    _sign,
    _url,
)

# Las fixtures de la pista, reutilizadas tal cual. Por asignación y no con
# `import`: un `from … import client` haría que cada test que pide `client`
# fuese, para el linter, una redefinición del nombre importado.
agency = _sms.agency
client = _sms.client
db = _sms.db
fake_http = _sms.fake_http
handled = _sms.handled
inbound_form = _sms.inbound_form
webhook_env = _sms.webhook_env

OTHER_AGENCY_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# 1. El auth_token en las respuestas de error
# ---------------------------------------------------------------------------


def test_auth_token_does_not_leak_when_another_field_is_missing(client, agency, fake_http):
    """El ataque más barato de todos: un cliente que se deja un campo.

    Pydantic v2 pone en `input` el valor que rechazó, y para el error
    `missing` ese valor es **el cuerpo entero**. Un panel al que se le
    olvida mandar `phone_number` recibe de vuelta un JSON con el Auth Token
    dentro, que acaba en la consola del navegador, en Sentry y en cualquier
    log de errores 4xx.
    """
    resp = client.post(
        _url(agency, "/sms"),
        json={"account_sid": ACCOUNT_SID, "auth_token": AUTH_TOKEN},
    )

    assert resp.status_code == 422
    assert AUTH_TOKEN not in resp.text


def test_auth_token_does_not_leak_when_it_is_too_long(client, agency, fake_http):
    """Mismo agujero por la otra puerta: el propio campo falla la validación
    y el valor rechazado vuelve tal cual. Un dueño que pega de más (media
    página de la consola de Twilio) se lleva su token de vuelta en el
    cuerpo del 422."""
    long_token = "SUPERSECRETO" * 40  # > 256 caracteres

    resp = client.post(
        _url(agency, "/sms"), json=_connect_body(auth_token=long_token)
    )

    assert resp.status_code == 422
    assert "SUPERSECRETO" not in resp.text


def test_blank_credentials_are_rejected_with_a_readable_code(client, agency, fake_http):
    """Rechazar sigue siendo obligatorio (un token en blanco es una conexión
    verde que no puede validar ni una firma), pero con un código que el
    panel sepa pintar y sin devolver nada de lo que llegó."""
    resp = client.post(_url(agency, "/sms"), json=_connect_body(auth_token="   "))
    assert resp.status_code == 422
    assert resp.json()["detail"] == "auth_token_required"

    resp = client.post(_url(agency, "/sms"), json=_connect_body(account_sid="SK123"))
    assert resp.status_code == 422
    assert resp.json()["detail"] == "account_sid_invalid"


def test_no_credential_ever_reaches_a_log_line(client, agency, db, fake_http, monkeypatch):
    """Ni el token ni ningún valor del formulario entran en un log.

    `channels.sms.verification_failed` y `channels.sms.provider_error` se
    emiten justo donde el token está en la mano; basta con que alguien
    añada `token=…` o un `body=` que Twilio devuelva para que quede escrito
    en claro en el sistema de logs, que no tiene el mismo control de acceso
    que la base de datos.
    """
    emitted: list[str] = []

    class _RecordingLog:
        def __getattr__(self, _name):
            def _emit(event, **kw):
                emitted.append(f"{event} {kw}")

            return _emit

    monkeypatch.setattr(channels_module, "log", _RecordingLog())

    fake_http.response = SimpleNamespace(
        status_code=401,
        text='{"code": 20003, "message": "Authentication Error"}',
        json=lambda: {"code": 20003},
    )
    resp = client.post(_url(agency, "/sms"), json=_connect_body())

    assert resp.status_code == 422
    assert emitted, "el fallo de credenciales tiene que dejar rastro"
    assert not any(AUTH_TOKEN in line for line in emitted)


# ---------------------------------------------------------------------------
# 2. Dos agencias con el mismo número: el webhook no puede caerse
# ---------------------------------------------------------------------------


class _DuplicateNumberSession(WebhookSession):
    """La consulta por número encuentra dos filas.

    Nada lo impedía: `POST /channels/sms` solo le pregunta a Twilio si las
    credenciales de quien llama ven ese número, y dos inquilinos de selai
    del mismo grupo (el plan Agencia vende multi-oficina) comparten cuenta
    de Twilio y número. También pasa cuando un número se porta de una
    cuenta a otra y la agencia vieja nunca pulsó «desconectar».
    """

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
            if wanted is not None:
                raise MultipleResultsFound("two agencies stored this number")
            return _FakeResult(self.agencies[0] if self.agencies else None)
        raise AssertionError(f"unexpected query: {sql}")


def test_two_agencies_with_the_same_number_do_not_crash_the_webhook(
    webhook_env, handled, inbound_form
):
    """Un 500 aquí es un SMS perdido *y* un reintento de Twilio, en bucle,
    para las dos agencias a la vez, con el panel en verde. WhatsApp ya lo
    arregló (`whatsapp.duplicate_phone_number_id`); SMS repetía el fallo.

    Negarse tampoco les sirve el mensaje, pero es acotado, queda escrito en
    el log y no le entrega a un inquilino los leads del otro.
    """
    agency_a = _agency(_connected_settings())
    session = _DuplicateNumberSession([agency_a])

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    try:
        resp = _post_webhook(
            TestClient(app), inbound_form, _sign(WEBHOOK_URL, inbound_form, AUTH_TOKEN)
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code != 500
    assert resp.status_code == 401
    assert handled["payloads"] == []


def test_connect_refuses_a_number_another_agency_already_connected(
    client, agency, db, fake_http
):
    """La otra mitad: no crear el estado ambiguo.

    Quien conecta segundo se lleva un 409 en vez de dejar a los dos sin
    canal. Y no se persiste nada: un `_merge_settings` a medias aquí es
    justo el "settings a medias" que el resto del módulo evita.
    """
    db.number_owner = OTHER_AGENCY_ID
    fake_http.response = SimpleNamespace(
        status_code=200, text="{}", json=lambda: _number_payload()
    )

    resp = client.post(_url(agency, "/sms"), json=_connect_body())

    assert resp.status_code == 409
    assert resp.json()["detail"] == "sms_number_taken"
    assert "twilio_auth_token" not in agency.settings
    assert db.commits == 0


def test_connect_lets_the_same_agency_reconnect_its_own_number(
    client, agency, db, fake_http
):
    """El chequeo mira *otras* agencias: reconectar el propio número (rotar
    el token después de regenerarlo en Twilio) tiene que seguir yendo."""
    agency.settings = _connected_settings()
    db.number_owner = agency.id
    fake_http.response = SimpleNamespace(
        status_code=200, text="{}", json=lambda: _number_payload()
    )

    resp = client.post(_url(agency, "/sms"), json=_connect_body(auth_token="rotado"))

    assert resp.status_code == 200
    assert agency.settings["twilio_auth_token"] != "rotado"
    assert decrypt_secret(agency.settings["twilio_auth_token"]) == "rotado"


# ---------------------------------------------------------------------------
# 3. El número propio de selai
# ---------------------------------------------------------------------------


def test_our_own_number_is_verified_with_the_global_token(
    webhook_env, handled, inbound_form
):
    """Una agencia que tiene apuntado *nuestro* número global (el piloto,
    una fila puesta a mano antes de que existiera `POST /channels/sms`)
    recibe webhooks firmados por el proyecto de Twilio de selai, porque el
    número lo sirve selai. `owns_number` decía que era suyo, no había token
    de agencia que buscar, y el webhook contestaba 401 a todos los SMS de
    esa agencia para siempre.

    WhatsApp resuelve esto mirando `_is_our_own_number` **antes** que
    `_owns_number` (app/api/whatsapp.py); SMS no lo hacía.
    """
    legacy = _agency({"twilio_sms_number": GLOBAL_NUMBER})
    form = {**inbound_form, "To": GLOBAL_NUMBER}
    session = WebhookSession([legacy])

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    try:
        resp = _post_webhook(
            TestClient(app), form, _sign(WEBHOOK_URL, form, GLOBAL_TOKEN)
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    assert [p.agency_id for p in handled["payloads"]] == [legacy.id]


def test_an_agency_token_does_not_sign_for_our_own_number(
    webhook_env, handled, inbound_form
):
    """Y al revés: nuestro número lo firma nuestro proyecto y solo el
    nuestro. Aceptar además el token de la agencia que lo tenga apuntado
    convertiría a esa agencia en emisora válida del número compartido."""
    legacy = _agency({"twilio_sms_number": GLOBAL_NUMBER, "twilio_auth_token": AUTH_TOKEN})
    form = {**inbound_form, "To": GLOBAL_NUMBER}
    session = WebhookSession([legacy])

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    try:
        resp = _post_webhook(
            TestClient(app), form, _sign(WEBHOOK_URL, form, AUTH_TOKEN)
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 401
    assert handled["payloads"] == []


# ---------------------------------------------------------------------------
# 4. La coincidencia parcial de Twilio
# ---------------------------------------------------------------------------


def test_connect_refuses_a_number_twilio_only_matched_partially(
    client, agency, db, fake_http
):
    """`?PhoneNumber=` es un filtro con coincidencia parcial, no una
    búsqueda exacta: pedir un número incompleto devuelve *otro* número de
    la misma cuenta. Guardar el que contesta Twilio conecta un número que
    el dueño no escribió — verde en el panel, y los SMS del número que sí
    quería sin dueño.
    """
    other = "+34910000099"
    fake_http.response = SimpleNamespace(
        status_code=200,
        text="{}",
        json=lambda: _number_payload(phone_number=other, sid=NUMBER_SID),
    )

    resp = client.post(_url(agency, "/sms"), json=_connect_body())

    assert resp.status_code == 422
    assert resp.json()["detail"] == "sms_number_not_found"
    assert "twilio_sms_number" not in agency.settings


def test_connect_accepts_twilios_own_spelling_of_the_same_number(
    client, agency, db, fake_http
):
    """La comparación es entre números, no entre cadenas: Twilio puede
    devolverlo con espacios y sigue siendo el mismo número."""
    fake_http.response = SimpleNamespace(
        status_code=200,
        text="{}",
        json=lambda: _number_payload(phone_number="+34 910 00 00 00"),
    )

    resp = client.post(_url(agency, "/sms"), json=_connect_body())

    assert resp.status_code == 200
    assert agency.settings["twilio_sms_number"] == NUMBER


def test_a_junk_to_field_never_reaches_the_database(
    webhook_env, handled, inbound_form
):
    """`To` es entrada sin verificar, sin límite de tamaño, y se consulta
    *antes* de comprobar la firma en una ruta que el rate limiter se salta
    (middleware.py). La firma bien formada es un formato, no un secreto:
    cualquiera la falsifica. Un megabyte de `To` no puede convertirse en un
    megabyte de parámetro de consulta.
    """
    junk = "A" * 100_000
    form = {**inbound_form, "To": junk}
    agency = _agency(_connected_settings())
    session = WebhookSession([agency])

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    try:
        resp = _post_webhook(TestClient(app), form, "A" * 27 + "=")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 401
    # La consulta por número (la única que mete `To` en un parámetro) no se
    # llega a emitir; la basura cae en el mismo sitio que un número que
    # nadie tiene.
    assert [q for q in session.queries if "settings[" in q] == []

    # Control: con un número de verdad la consulta sí se hace, o el filtro
    # se estaría comiendo también el caso bueno.
    good = WebhookSession([_agency(_connected_settings())])

    async def _override_good():
        yield good

    app.dependency_overrides[get_db] = _override_good
    try:
        _post_webhook(TestClient(app), inbound_form, "A" * 27 + "=")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert [q for q in good.queries if "settings[" in q]


# ---------------------------------------------------------------------------
# 5. El gate de plan, antes de gastar nada
# ---------------------------------------------------------------------------


class _StarterSession(_sms.FakeSession):
    """La agencia tiene un plan Starter de verdad (`sms: False`)."""

    def __init__(self, agency):
        super().__init__(agency)
        self.plan = SimpleNamespace(
            id=uuid.uuid4(),
            code="starter",
            features={"sms": False, "voice": False},
            monthly_price_eur=99,
        )
        self.subscription = SimpleNamespace(
            id=uuid.uuid4(),
            agency_id=agency.id,
            plan_id=self.plan.id,
            status=SubscriptionStatus.ACTIVE,
            voice_minutes_used=0,
            trial_ends_at=None,
        )

    async def get(self, model, pk):
        if pk == self.plan.id:
            return self.plan
        return await super().get(model, pk)

    async def execute(self, stmt):
        if "FROM subscriptions" in str(stmt):
            return _FakeResult(self.subscription)
        return await super().execute(stmt)


def test_starter_is_refused_before_twilio_is_ever_asked(agency, fake_http, monkeypatch):
    """El gate va **antes** del viaje a Twilio, y eso es parte del arreglo,
    no un detalle: si se colara detrás, un Starter mandaría sus claves a
    verificar y, cualquier reordenación futura, las guardaría — vendiéndole
    un canal que el webhook tira igualmente.

    Se comprueba por donde se nota: ni una llamada a Twilio, ni un commit,
    ni una clave en `settings`.
    """
    session = _StarterSession(agency)

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    monkeypatch.setattr(_sms.settings, "app_env", "development")
    monkeypatch.setattr(_sms.settings, "clerk_jwks_url", "")
    try:
        resp = TestClient(app).post(_url(agency, "/sms"), json=_connect_body())
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "feature_not_in_plan"
    assert fake_http.calls == []
    assert agency.settings == {}
    assert session.commits == 0


# ---------------------------------------------------------------------------
# 6. Las credenciales de una agencia no hablan por otra
# ---------------------------------------------------------------------------

OTHER_NUMBER = "+34922222222"
OTHER_TOKEN = "token-de-la-otra"


def _two_connected_agencies():
    first = _agency(_connected_settings())
    second = _agency(
        _connected_settings(
            twilio_sms_number=OTHER_NUMBER,
            twilio_auth_token=OTHER_TOKEN,
            twilio_account_sid="AC" + "f" * 32,
        )
    )
    return first, second


def test_an_sms_is_answered_with_the_credentials_of_its_own_agency(
    webhook_env, handled, inbound_form
):
    """El SMS llega al número de la segunda agencia: ni el lead ni la
    factura pueden acabar en la primera. Lo que se mira es la configuración
    con la que se construye el cliente de salida, que es lo que decide desde
    qué número sale la respuesta y quién la paga.
    """
    first, second = _two_connected_agencies()
    form = {**inbound_form, "To": OTHER_NUMBER}
    session = WebhookSession([first, second])

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    try:
        resp = _post_webhook(
            TestClient(app), form, _sign(WEBHOOK_URL, form, OTHER_TOKEN)
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    assert [p.agency_id for p in handled["payloads"]] == [second.id]
    used = handled["clients"][0]["cfg"]
    assert used["twilio_auth_token"] == OTHER_TOKEN
    assert used["twilio_sms_number"] == OTHER_NUMBER
    assert AUTH_TOKEN not in used.values()


def test_one_agency_token_cannot_sign_for_another_agencys_number(
    webhook_env, handled, inbound_form
):
    """Y la firma de una agencia no vale para el número de la otra: si
    valiera, cualquiera con una cuenta de Twilio conectada podría inyectar
    leads (y disparar respuestas pagadas) en el inquilino de al lado."""
    first, second = _two_connected_agencies()
    session = WebhookSession([first, second])

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    try:
        # `To` es el número de la primera, firmado con el token de la segunda.
        resp = _post_webhook(
            TestClient(app), inbound_form, _sign(WEBHOOK_URL, inbound_form, OTHER_TOKEN)
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 401
    assert handled["payloads"] == []
    assert handled["clients"] == []
