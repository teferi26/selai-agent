"""Tests for portal lead extraction (Idealista, Fotocasa, Habitaclia and,
since item 193, pisos.com, yaencontre and Milanuncios).

The second half of the file leaves the parser and pins what the email route
*does* with it: a portal notification we cannot read must never be answered
at the portal's own noreply address, and the agency has to be told about it.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import email as email_module
from app.channels.email.alerts import (
    build_unreadable_portal_email_html,
    unreadable_portal_subject,
)
from app.core.settings import settings
from app.db.session import get_db
from app.main import app
from app.services.portal_lead_parser import (
    can_reply_to,
    detect_portal,
    is_noreply_address,
    parse_portal_email,
    portal_lead_is_usable,
)


def test_detects_idealista():
    assert detect_portal("noreply@no-reply.idealista.com") == "idealista"


def test_detects_fotocasa():
    assert detect_portal("Fotocasa <noreply@noreply.fotocasa.es>") == "fotocasa"


def test_unknown_sender_is_none():
    assert detect_portal("juan@mail.example.com") is None


IDEALISTA_BODY = """\
Has recibido una nueva consulta del anuncio idealista 99887766.

Nombre y apellidos: María García Ruiz
Email: maria.garcia@example.com
Teléfono: +34 612 345 678

Mensaje:
Hola, me interesa el piso de Malasaña. ¿Sigue disponible y se podría visitar el sábado por la mañana?

Ver el anuncio en https://www.idealista.com/inmueble/99887766/
Referencia: MAD-001
"""


def test_parses_idealista_full_body():
    pl = parse_portal_email(IDEALISTA_BODY, from_addr_raw="noreply@no-reply.idealista.com")
    assert pl is not None
    assert pl.portal == "idealista"
    assert pl.email == "maria.garcia@example.com"
    assert pl.phone == "+34612345678"
    assert pl.name == "María García Ruiz"
    assert pl.property_reference == "99887766"
    assert "Malasaña" in pl.message
    assert pl.property_url and "idealista.com" in pl.property_url


def test_returns_none_for_unknown_sender():
    pl = parse_portal_email("texto cualquiera", from_addr_raw="juan@mail.example.com")
    assert pl is None


def test_phone_default_spain_prefix():
    body = "Nombre: Juan\nEmail: juan@example.com\nTeléfono: 612345678"
    pl = parse_portal_email(body, from_addr_raw="noreply@noreply.fotocasa.es")
    assert pl is not None
    assert pl.phone == "+34612345678"


# ---------------------------------------------------------------------------
# Item 193 — the other portals, and never answering a machine
# ---------------------------------------------------------------------------


def test_detects_the_portals_that_were_missing():
    """A lead from pisos.com used to be treated as an ordinary email: the
    agent replied to the portal's noreply and the buyer never heard back."""
    assert detect_portal("Pisos.com <noreply@mail.pisos.com>") == "pisos.com"
    assert detect_portal("avisos@yaencontre.com") == "yaencontre"
    assert detect_portal("no-reply@no-reply.milanuncios.com") == "milanuncios"


def test_the_buyers_address_is_preferred_over_the_portals():
    body = (
        "Nuevo contacto en pisos.com\n"
        "Responder a: avisos@mail.pisos.com\n"
        "Nombre: Laura Gil\n"
        "Email: laura.gil@mail.example.com\n"
        "Teléfono: 612 345 678\n"
    )
    pl = parse_portal_email(body, from_addr_raw="noreply@mail.pisos.com")
    assert pl is not None
    assert pl.email == "laura.gil@mail.example.com"


def test_an_address_that_merely_contains_a_portal_name_is_still_a_lead():
    """A substring test would drop `juan.pisos.company@mail.example.com`, which is
    exactly the address the whole email exists to deliver."""
    body = "Nombre: Juan\nEmail: juan.pisos.company@mail.example.com\nTeléfono: 612345678"
    pl = parse_portal_email(body, from_addr_raw="noreply@mail.pisos.com")
    assert pl is not None
    assert pl.email == "juan.pisos.company@mail.example.com"


def test_portal_and_noreply_mailboxes_are_never_answered():
    for addr in (
        "noreply@no-reply.idealista.com",
        "avisos@yaencontre.com",
        "no-reply@cualquiercosa.es",
        "notificaciones@otracosa.com",
        "",
        "no-es-una-direccion",
    ):
        assert is_noreply_address(addr) is True
        assert can_reply_to(addr) is False


def test_a_real_person_can_be_answered():
    assert can_reply_to("Laura Gil <laura.gil@mail.example.com>") is True
    assert can_reply_to("maria.garcia@example.com") is True


def test_a_portal_email_without_contact_details_is_not_usable():
    """No phone and no email means there is nobody to answer: the agency
    has to be told instead of the lead disappearing."""
    body = "Un usuario ha mostrado interés en tu anuncio. Entra en tu panel para verlo."
    pl = parse_portal_email(body, from_addr_raw="noreply@no-reply.idealista.com")
    assert pl is not None  # we know which portal it is
    assert portal_lead_is_usable(pl) is False


def test_a_portal_email_with_a_phone_alone_is_usable():
    body = "Nombre: Juan\nTeléfono: 612345678"
    pl = parse_portal_email(body, from_addr_raw="noreply@noreply.habitaclia.com")
    assert portal_lead_is_usable(pl) is True


def test_nothing_at_all_is_not_usable():
    assert portal_lead_is_usable(None) is False


# ---------------------------------------------------------------------------
# Item 193 at the route level (app/api/email.py)
# ---------------------------------------------------------------------------


def _portal_webhook(body_text: str, sender: str) -> dict:
    return {
        "From": sender,
        "To": "inmo-sol-leads@leads.selai.es",
        "Subject": "Nuevo contacto",
        "MessageID": f"<{uuid.uuid4()}@portal>",
        "TextBody": body_text,
    }


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
    conversation_id = uuid.uuid4()

    async def fake_handle(db, payload):
        return SimpleNamespace(
            reply_text="Encantado, te cuento.",
            conversation_id=conversation_id,
            handed_off=False,
        )

    monkeypatch.setattr(email_module, "handle_inbound", fake_handle)

    @asynccontextmanager
    async def _task_session():
        yield session

    monkeypatch.setattr(email_module, "task_session", _task_session)

    sent: list = []

    class _FakeResend:
        async def send(self, **kwargs):
            sent.append(kwargs)
            return "id"

    monkeypatch.setattr(email_module, "ResendClient", lambda: _FakeResend())

    alerts: list = []

    async def fake_alert(db, agency_id, portal, conv_id, contact_phone=None):
        alerts.append(
            {
                "agency_id": agency_id,
                "portal": portal,
                "conv": conv_id,
                "phone": contact_phone,
            }
        )

    monkeypatch.setattr(email_module, "alert_unreadable_portal_email", fake_alert)

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield SimpleNamespace(
        http=TestClient(app), sent=sent, alerts=alerts, conversation_id=conversation_id
    )
    app.dependency_overrides.pop(get_db, None)


def _post(route, body: dict):
    return route.http.post(
        "/webhooks/email", json=body, headers={"X-Webhook-Secret": "webhook-secret"}
    )


def test_a_readable_portal_lead_is_answered_at_their_own_address(route):
    body = _portal_webhook(
        "Nombre: Laura Gil\nEmail: laura.gil@mail.example.com\nTeléfono: 612345678\n"
        "Mensaje: ¿sigue disponible?",
        "Pisos.com <noreply@mail.pisos.com>",
    )
    assert _post(route, body).status_code == 200
    assert route.alerts == []
    assert [m["to"] for m in route.sent] == ["laura.gil@mail.example.com"]


def test_an_unreadable_portal_lead_is_never_answered(route):
    """The only address we have is the portal's noreply. Writing there looks
    like an answer and is not one."""
    body = _portal_webhook(
        "Un usuario ha mostrado interés en tu anuncio. Entra en tu panel.",
        "Idealista <noreply@no-reply.idealista.com>",
    )
    assert _post(route, body).status_code == 200
    assert route.sent == []


def test_an_unreadable_portal_lead_is_escalated_to_the_agency(route):
    body = _portal_webhook(
        "Un usuario ha mostrado interés en tu anuncio. Entra en tu panel.",
        "Idealista <noreply@no-reply.idealista.com>",
    )
    _post(route, body)
    assert len(route.alerts) == 1
    assert route.alerts[0]["portal"] == "idealista"
    # With a link to where the raw email actually is.
    assert route.alerts[0]["conv"] == route.conversation_id


def test_a_noreply_sender_outside_the_portals_is_not_answered_either(route):
    body = _portal_webhook(
        "Aviso automático de tu gestor documental.", "no-reply@gestoria.es"
    )
    assert _post(route, body).status_code == 200
    assert route.sent == []
    # Not a portal, so nobody is alerted: it is not a lead we lost.
    assert route.alerts == []


def test_the_alert_email_names_the_portal_and_links_the_conversation():
    conv = uuid.uuid4()
    html = build_unreadable_portal_email_html("pisos.com", conv)
    assert "pisos.com" in html
    assert str(conv) in html
    assert unreadable_portal_subject("idealista") == (
        "Ha llegado un lead de Idealista que no hemos podido leer"
    )


def test_a_portal_lead_with_a_phone_but_no_email_is_not_answered_in_silence(route):
    """The parse "worked" — there is a phone, so the lead is usable and the
    conversation is created — but the only address on the email is the
    portal's noreply. The reply is suppressed, and suppressing it without
    telling anyone is the lost lead this item is about."""
    body = _portal_webhook(
        "Nombre: Laura Gil\nTeléfono: 612345678\nMensaje: ¿sigue disponible?",
        "Idealista <noreply@no-reply.idealista.com>",
    )
    assert _post(route, body).status_code == 200

    assert route.sent == []
    assert len(route.alerts) == 1
    assert route.alerts[0]["portal"] == "idealista"
    # And the alert carries the number, because the action is a call.
    assert route.alerts[0]["phone"] == "+34612345678"


def test_the_alert_with_a_phone_says_to_call_instead_of_to_read(route):
    html = build_unreadable_portal_email_html("idealista", uuid.uuid4(), "+34612345678")
    assert "+34612345678" in html
    # It must not claim we got nothing when the number is right there.
    assert "ni el teléfono ni el email" not in html
    assert unreadable_portal_subject("idealista", "+34612345678") == (
        "Un lead de Idealista espera que le llames tú"
    )


def test_a_lookalike_domain_is_not_treated_as_the_portal():
    """`endswith("idealista.com")` also matches `notidealista.com`: an
    ordinary sender would be classified as a portal and never answered, and
    a spoofed one would inherit the portal handling."""
    assert detect_portal("ventas@notidealista.com") is None
    assert detect_portal("ventas@mifotocasa.es") is None
    # The real thing, and its subdomains, still match.
    assert detect_portal("noreply@idealista.com") == "idealista"
    assert detect_portal("noreply@mail.idealista.com") == "idealista"


def test_a_lookalike_domain_can_still_be_replied_to():
    assert can_reply_to("ventas@notidealista.com") is True
    assert can_reply_to("ventas@idealista.com") is False
