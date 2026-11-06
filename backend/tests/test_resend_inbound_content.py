"""El webhook de Resend trae metadatos; el cuerpo hay que ir a buscarlo.

`email.received` entrega `email_id`, `from`, `to`, `subject` y la lista de
adjuntos — y nada más. Ni cuerpo ni cabeceras. Resend lo documenta así a
propósito: un correo con adjuntos no cabe en el cuerpo de una petición en un
entorno serverless.

Sin esto, un aviso de Idealista llega con un asunto de plantilla ("Nuevo
contacto para tu inmueble") y sin el nombre, el teléfono ni el mensaje, que es
justo lo que hace que un lead sea un lead. La conversación se abre vacía contra
un robot de noreply y la agencia no se entera de que existía nadie.

Estos tests fijan las dos mitades: que se pide el contenido, y que un fallo al
pedirlo NO se traga con un 200.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import email as email_module
from app.channels.email.receiving import (
    merge_inbound_content,
    resend_inbound_email_id,
)
from app.core.settings import settings
from app.db.session import get_db
from app.main import app

# ---------------------------------------------------------------------------
# 1. Qué eventos disparan la búsqueda del contenido
# ---------------------------------------------------------------------------


def test_only_email_received_asks_for_content():
    assert (
        resend_inbound_email_id({"type": "email.received", "data": {"email_id": "abc"}})
        == "abc"
    )


@pytest.mark.parametrize(
    "payload",
    [
        # Nuestros propios avisos de envío. El webhook puede estar suscrito a
        # más eventos de la cuenta; ninguno de ellos es un correo entrante y
        # ninguno debe gastar una llamada a la API.
        {"type": "email.sent", "data": {"email_id": "abc"}},
        {"type": "email.delivered", "data": {"email_id": "abc"}},
        {"type": "email.bounced", "data": {"email_id": "abc"}},
        {"type": "contact.created", "data": {"id": "abc"}},
        # Formas rotas.
        {"type": "email.received", "data": {}},
        {"type": "email.received", "data": None},
        {"type": "email.received"},
        {"data": {"email_id": "abc"}},
        {},
    ],
)
def test_everything_else_does_not(payload):
    assert resend_inbound_email_id(payload) is None


# ---------------------------------------------------------------------------
# 2. Cómo se funde el contenido con los metadatos
# ---------------------------------------------------------------------------


def test_merge_fills_body_and_headers():
    payload = {
        "type": "email.received",
        "data": {
            "email_id": "e1",
            "from": "cliente@mail.example.com",
            "to": ["inmo-sol-leads@leads.selai.es"],
            "subject": "Piso en Alicante",
        },
    }
    merge_inbound_content(
        payload,
        {
            "text": "Hola, quiero verlo el jueves.",
            "html": "<p>Hola</p>",
            "headers": {"Message-ID": "<m1@mail.example.com>"},
            "message_id": "<m1@mail.example.com>",
        },
    )
    data = payload["data"]
    assert data["text"] == "Hola, quiero verlo el jueves."
    assert data["headers"] == {"Message-ID": "<m1@mail.example.com>"}
    assert data["message_id"] == "<m1@mail.example.com>"


def test_merge_never_overwrites_the_delivered_recipient():
    """`to` decide de qué agencia es el lead.

    El destinatario del webhook es al que Resend entregó de verdad. El del
    objeto recuperado puede diferir (una lista de distribución, un Bcc), y
    dejarlo ganar mandaría el lead a otra agencia — o a ninguna.
    """
    payload = {
        "type": "email.received",
        "data": {
            "email_id": "e1",
            "from": "cliente@mail.example.com",
            "to": ["inmo-sol-leads@leads.selai.es"],
            "subject": "Piso",
        },
    }
    merge_inbound_content(
        payload,
        {
            "to": ["otra-agencia-leads@leads.selai.es"],
            "from": "suplantador@mail.example.com",
            "subject": "Otro asunto",
            "text": "cuerpo",
        },
    )
    data = payload["data"]
    assert data["to"] == ["inmo-sol-leads@leads.selai.es"]
    assert data["from"] == "cliente@mail.example.com"
    assert data["subject"] == "Piso"
    assert data["text"] == "cuerpo"


def test_merge_ignores_empty_values():
    payload = {"type": "email.received", "data": {"email_id": "e1", "text": "algo"}}
    merge_inbound_content(payload, {"text": "", "html": None, "headers": {}})
    assert payload["data"]["text"] == "algo"


# ---------------------------------------------------------------------------
# 3. Con qué credencial se lee
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reading_prefers_its_own_key(monkeypatch):
    """La clave de envío de Resend está restringida a "sending access".

    Leer un correo recibido devolvería 401 con ella. La respuesta correcta no
    es ampliar los permisos de la credencial que ya está en producción — una de
    acceso total también puede crear dominios y exportar contactos — sino darle
    a la lectura la suya.
    """
    from app.channels.email import receiving as mod

    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_solo_envio"))
    monkeypatch.setattr(settings, "resend_read_api_key", SecretStr("re_lectura"))

    usadas: list = []

    async def fake_get(email_id, api_key):
        usadas.append(api_key)
        return {"text": "cuerpo"}

    monkeypatch.setattr(mod, "_get", fake_get)
    assert await mod.fetch_inbound_content("e1") == {"text": "cuerpo"}
    assert usadas == ["re_lectura"]


@pytest.mark.anyio
async def test_reading_falls_back_to_the_sending_key(monkeypatch):
    """Un despliegue con una sola clave de acceso total sigue funcionando."""
    from app.channels.email import receiving as mod

    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_unica"))
    monkeypatch.setattr(settings, "resend_read_api_key", SecretStr(""))

    usadas: list = []

    async def fake_get(email_id, api_key):
        usadas.append(api_key)
        return {"text": "cuerpo"}

    monkeypatch.setattr(mod, "_get", fake_get)
    await mod.fetch_inbound_content("e1")
    assert usadas == ["re_unica"]


@pytest.mark.anyio
async def test_without_any_key_it_gives_up_quietly(monkeypatch):
    from app.channels.email import receiving as mod

    monkeypatch.setattr(settings, "resend_api_key", SecretStr(""))
    monkeypatch.setattr(settings, "resend_read_api_key", SecretStr(""))
    assert await mod.fetch_inbound_content("e1") is None


# ---------------------------------------------------------------------------
# 4. El recorrido completo por el endpoint
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, value, row=None):
        self._value = value
        self._row = row

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def first(self):
        return self._row


class _Session:
    def __init__(self, agency):
        self.agency = agency

    async def execute(self, stmt):
        sql = str(stmt).lower()
        if "messages" in sql:
            return _Result(None, row=None)
        if "agencies" in sql:
            return _Result(self.agency)
        return _Result(None)

    async def get(self, model, pk):
        return self.agency

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def route(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", SecretStr("webhook-secret"))

    agency = SimpleNamespace(
        id=uuid.uuid4(), slug="inmo-sol", contact_email="dueno@inmosol.example.com"
    )
    session = _Session(agency)

    seen: list = []

    async def fake_handle(db, payload):
        seen.append(payload)
        return SimpleNamespace(
            reply_text="Te cuento.", conversation_id=uuid.uuid4(), handed_off=False
        )

    monkeypatch.setattr(email_module, "handle_inbound", fake_handle)

    @asynccontextmanager
    async def _task_session():
        yield session

    monkeypatch.setattr(email_module, "task_session", _task_session)

    class _FakeResend:
        async def send(self, **kwargs):
            return "id"

    monkeypatch.setattr(email_module, "ResendClient", lambda: _FakeResend())

    fetched: list = []

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield SimpleNamespace(
        http=TestClient(app), seen=seen, fetched=fetched, monkeypatch=monkeypatch
    )
    app.dependency_overrides.pop(get_db, None)


def _metadata_only(subject="Nuevo contacto para tu inmueble"):
    """Exactamente lo que manda Resend: sin `text`, sin `headers`."""
    return {
        "type": "email.received",
        "created_at": "2026-08-02T10:00:00.000Z",
        "data": {
            "email_id": "0e0e0e0e-0000-4000-8000-000000000001",
            "from": "noreply@idealista.com",
            "to": ["inmo-sol-leads@leads.selai.es"],
            "subject": subject,
            "attachments": [],
        },
    }


def _post(route, body):
    return route.http.post(
        "/webhooks/email", json=body, headers={"X-Webhook-Secret": "webhook-secret"}
    )


def _stub_fetch(route, content):
    async def fake_fetch(email_id):
        route.fetched.append(email_id)
        return content

    route.monkeypatch.setattr(email_module, "fetch_inbound_content", fake_fetch)


def test_the_body_reaches_the_lead(route):
    """La regresión que motiva todo esto.

    Sin ir a por el contenido, `payload.text` sería "(sin contenido)" y el
    agente contestaría a un lead del que no sabe absolutamente nada.
    """
    _stub_fetch(
        route,
        {
            "text": "Miguel Ángel, 600123456. Me interesa el ático de Benalúa.",
            "headers": {"Message-ID": "<abc@idealista.com>"},
            "message_id": "<abc@idealista.com>",
        },
    )

    resp = _post(route, _metadata_only())

    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    assert route.fetched == ["0e0e0e0e-0000-4000-8000-000000000001"]
    assert len(route.seen) == 1
    text = route.seen[0].text
    assert "600123456" in text
    assert "Benalúa" in text
    assert "(sin contenido)" not in text


def test_a_failed_fetch_is_not_swallowed(route):
    """Un correo que no hemos podido leer no es un correo que descartar.

    Con un 200, Resend lo da por entregado y el lead se pierde para siempre,
    sin que nadie se entere. Con un 5xx lo reintenta.
    """
    async def fake_fetch(email_id):
        route.fetched.append(email_id)
        return None

    route.monkeypatch.setattr(email_module, "fetch_inbound_content", fake_fetch)

    resp = _post(route, _metadata_only())

    assert resp.status_code == 503
    assert route.fetched == ["0e0e0e0e-0000-4000-8000-000000000001"]
    assert route.seen == []  # no se abrió ninguna conversación a medias


def test_a_stray_message_costs_no_api_read(route):
    """La dirección de recepción acepta CUALQUIER cosa antes de la arroba.

    En el dominio gestionado de Resend, todo `<lo-que-sea>@<id>.resend.app` se
    entrega. Así que hay que resolver la agencia con el sobre, antes de tocar
    nada más: pedir el cuerpo de un correo que vamos a descartar gasta una
    lectura de API, y si esa lectura falla devolvemos 503 y Resend se pone a
    reintentar spam en bucle. Con 100 correos al día de cupo compartido entre
    lo que entra y lo que sale, eso vacía el cupo.
    """
    _stub_fetch(route, {"text": "no deberia pedirse"})

    # La sesión de prueba devuelve agencia para cualquier consulta, así que el
    # "no hay agencia" se fuerza aquí, que es justo la condición del test.
    async def sin_agencia(db, to_addr):
        return None

    route.monkeypatch.setattr(email_module, "_agency_for_email", sin_agencia)

    body = _metadata_only()
    body["data"]["to"] = ["cualquier-cosa@example-inbound.resend.app"]
    resp = _post(route, body)

    assert resp.status_code == 200
    assert resp.json()["status"] == "no_agency_match"
    assert route.fetched == []  # ni una llamada a la API
    assert route.seen == []


def test_other_events_never_ask_for_content(route):
    """El webhook puede estar suscrito a más eventos de la cuenta.

    Un `email.delivered` de nuestros propios envíos no es un correo entrante:
    no debe gastar una llamada a la API ni intentar abrir un lead.
    """
    called: list = []

    async def fake_fetch(email_id):
        called.append(email_id)
        return {"text": "no deberia pedirse"}

    route.monkeypatch.setattr(email_module, "fetch_inbound_content", fake_fetch)

    resp = _post(
        route,
        {
            "type": "email.delivered",
            "data": {"email_id": "otro", "to": ["cliente@mail.example.com"]},
        },
    )

    assert called == []
    assert resp.status_code == 200
    assert route.seen == []


def test_the_portal_lead_is_read_from_the_fetched_body(route):
    """El caso real: los datos del lead solo existen en el cuerpo recuperado."""
    _stub_fetch(
        route,
        {
            "text": (
                "Tienes un nuevo contacto\n"
                "Nombre: Lucía Martín\n"
                "Email: lucia.martin@mail.example.com\n"
                "Teléfono: 611223344\n"
                "Mensaje: ¿Sigue disponible el piso?\n"
            ),
            "headers": {"Message-ID": "<port1@idealista.com>"},
        },
    )

    resp = _post(route, _metadata_only())

    assert resp.status_code == 200
    assert len(route.seen) == 1
    payload = route.seen[0]
    # El remitente deja de ser el robot del portal y pasa a ser la persona.
    assert payload.identity.email == "lucia.martin@mail.example.com"
    assert payload.identity.phone_e164 == "+34611223344"  # normalizado a E.164
    assert payload.identity.display_name == "Lucía Martín"
    assert payload.identity.source_campaign == "idealista"
    # Y el texto que ve el agente es el mensaje de la persona, no la plantilla.
    assert "¿Sigue disponible el piso?" in payload.text
    assert "Tienes un nuevo contacto" not in payload.text
