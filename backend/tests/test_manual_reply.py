"""(168) POST /api/v1/agencies/{id}/conversations/{cid}/reply.

A human answers the lead from the dashboard: the text must leave through
the real channel (WhatsApp Cloud API or Resend), get stored as an OUTBOUND
/ AGENT_HUMAN message, and implicitly hand the conversation off so the AI
stops answering. Channels we cannot write to answer 501 instead of lying.

Dev auth bypass + fake DB session, same pattern as test_channels_api.py.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import dashboard as dashboard_module
from app.core.settings import settings
from app.db.models import (
    ConversationChannel,
    ConversationStatus,
    Message,
    MessageDirection,
    MessageRole,
)
from app.db.session import get_db
from app.main import app

AGENCY_ID = uuid.uuid4()


def _conversation(
    channel: ConversationChannel = ConversationChannel.WHATSAPP,
    conv_status: ConversationStatus = ConversationStatus.ACTIVE,
    agency_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency_id or AGENCY_ID,
        lead_id=uuid.uuid4(),
        channel=channel,
        status=conv_status,
    )


def _lead(conv: SimpleNamespace, **overrides) -> SimpleNamespace:
    data = {
        "id": conv.lead_id,
        "agency_id": conv.agency_id,
        "full_name": "Juan Pérez",
        "phone_e164": "+34600111222",
        "whatsapp_wa_id": "34600111222",
        "email": "juan@example.com",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _agency(settings_json: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=AGENCY_ID, name="Inmo Sol", slug="inmo-sol", settings=settings_json or {}
    )


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class FakeSession:
    """Serves conversation/lead/agency by id and the last-inbound query."""

    def __init__(self, conv, lead, agency, last_inbound=None):
        self.conv = conv
        self.lead = lead
        self.agency = agency
        self.last_inbound = last_inbound
        self.added: list = []
        self.commits = 0

    async def get(self, model, pk):
        name = getattr(model, "__name__", str(model))
        if name == "Conversation":
            return self.conv if self.conv is not None and pk == self.conv.id else None
        if name == "Lead":
            return self.lead if self.lead is not None and pk == self.lead.id else None
        if name == "Agency":
            return self.agency if self.agency is not None and pk == self.agency.id else None
        raise AssertionError(f"unexpected get({name})")

    async def execute(self, stmt):
        sql = str(stmt)
        assert "messages" in sql, f"unexpected query: {sql}"
        return _FakeResult([self.last_inbound] if self.last_inbound else [])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    @property
    def message(self) -> Message:
        messages = [o for o in self.added if isinstance(o, Message)]
        assert len(messages) == 1, f"expected 1 message, got {len(messages)}"
        return messages[0]


class FakeWhatsAppClient:
    """Records construction args + sends. Monkeypatched into the module."""

    instances: list[FakeWhatsAppClient] = []
    sent: list[tuple[str, str]] = []
    exc: Exception | None = None

    def __init__(self, access_token: str | None = None, phone_number_id: str | None = None):
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        type(self).instances.append(self)

    async def send_text(self, to: str, text: str) -> str:
        if type(self).exc is not None:
            raise type(self).exc
        type(self).sent.append((to, text))
        return "wamid.OUT1"


class FakeResendClient:
    sent: list[dict] = []
    exc: Exception | None = None

    async def send(self, **kwargs) -> str:
        if type(self).exc is not None:
            raise type(self).exc
        type(self).sent.append(kwargs)
        return "resend-1"


@pytest.fixture(autouse=True)
def clients(monkeypatch):
    FakeWhatsAppClient.instances = []
    FakeWhatsAppClient.sent = []
    FakeWhatsAppClient.exc = None
    FakeResendClient.sent = []
    FakeResendClient.exc = None
    monkeypatch.setattr(dashboard_module, "WhatsAppClient", FakeWhatsAppClient)
    monkeypatch.setattr(dashboard_module, "ResendClient", FakeResendClient)


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")

    def _build(session: FakeSession) -> TestClient:
        async def _override():
            yield session

        app.dependency_overrides[get_db] = _override
        return TestClient(app)

    yield _build
    app.dependency_overrides.pop(get_db, None)


def _post(client, conv, text: str = "Hola, te llamo en 10 minutos."):
    return client.post(
        f"/api/v1/agencies/{AGENCY_ID}/conversations/{conv.id}/reply",
        json={"text": text},
    )


# ---------------------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------------------


def test_whatsapp_reply_sends_and_stores_the_message(make_client):
    conv = _conversation()
    session = FakeSession(conv, _lead(conv), _agency())
    client = make_client(session)

    resp = _post(client, conv)

    assert resp.status_code == 200
    assert FakeWhatsAppClient.sent == [("34600111222", "Hola, te llamo en 10 minutos.")]
    assert FakeResendClient.sent == []

    message = session.message
    assert message.direction is MessageDirection.OUTBOUND
    assert message.role is MessageRole.AGENT_HUMAN
    assert message.content == "Hola, te llamo en 10 minutos."
    assert message.external_message_id == "wamid.OUT1"
    assert message.agency_id == AGENCY_ID
    assert message.conversation_id == conv.id
    assert session.commits == 1

    body = resp.json()
    assert body["role"] == "agent_human"
    assert body["external_message_id"] == "wamid.OUT1"
    assert body["id"] == str(message.id)


def test_whatsapp_uses_the_agency_credentials_when_connected(make_client):
    conv = _conversation()
    session = FakeSession(
        conv,
        _lead(conv),
        _agency(
            {
                "whatsapp_access_token": "EAAG-agency-token",
                "whatsapp_phone_number_id": "pnid-agency",
            }
        ),
    )

    _post(make_client(session), conv)

    built = FakeWhatsAppClient.instances[-1]
    assert built.access_token == "EAAG-agency-token"
    assert built.phone_number_id == "pnid-agency"


def test_whatsapp_falls_back_to_the_global_client_without_agency_credentials(make_client):
    conv = _conversation()
    session = FakeSession(conv, _lead(conv), _agency())

    _post(make_client(session), conv)

    built = FakeWhatsAppClient.instances[-1]
    assert built.access_token is None
    assert built.phone_number_id is None


def test_whatsapp_falls_back_to_the_phone_when_there_is_no_wa_id(make_client):
    conv = _conversation()
    session = FakeSession(conv, _lead(conv, whatsapp_wa_id=None), _agency())

    _post(make_client(session), conv)

    assert FakeWhatsAppClient.sent[0][0] == "+34600111222"


def test_lead_without_any_whatsapp_destination_is_rejected(make_client):
    conv = _conversation()
    session = FakeSession(
        conv, _lead(conv, whatsapp_wa_id=None, phone_e164=None), _agency()
    )

    resp = _post(make_client(session), conv)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "lead_without_whatsapp"
    assert session.added == []


def test_provider_failure_stores_nothing(make_client):
    FakeWhatsAppClient.exc = RuntimeError("Meta says no")
    conv = _conversation()
    session = FakeSession(conv, _lead(conv), _agency())

    resp = _post(make_client(session), conv)

    assert resp.status_code == 502
    assert resp.json()["detail"] == "send_failed"
    assert session.added == []
    assert session.commits == 0
    assert conv.status is ConversationStatus.ACTIVE


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def test_email_reply_goes_through_resend_with_re_subject(make_client):
    conv = _conversation(channel=ConversationChannel.EMAIL)
    inbound = SimpleNamespace(
        raw={"subject": "Consulta piso Chamberí"},
        external_message_id="<orig-1@mail.example.com>",
    )
    session = FakeSession(conv, _lead(conv), _agency(), last_inbound=inbound)

    resp = _post(make_client(session), conv)

    assert resp.status_code == 200
    assert FakeWhatsAppClient.sent == []
    sent = FakeResendClient.sent[-1]
    assert sent["to"] == "juan@example.com"
    assert sent["subject"] == "Re: Consulta piso Chamberí"
    assert sent["text"] == "Hola, te llamo en 10 minutos."
    assert session.message.external_message_id == "resend-1"


def test_email_subject_is_not_double_prefixed(make_client):
    conv = _conversation(channel=ConversationChannel.EMAIL)
    inbound = SimpleNamespace(
        raw={"subject": "Re: Consulta piso"},
        external_message_id="<orig-2@mail.example.com>",
    )
    session = FakeSession(conv, _lead(conv), _agency(), last_inbound=inbound)

    _post(make_client(session), conv)

    assert FakeResendClient.sent[-1]["subject"] == "Re: Consulta piso"


def test_email_without_previous_subject_uses_the_agency_name(make_client):
    conv = _conversation(channel=ConversationChannel.EMAIL)
    session = FakeSession(conv, _lead(conv), _agency())

    _post(make_client(session), conv)

    assert FakeResendClient.sent[-1]["subject"] == "Re: Tu consulta a Inmo Sol"


def test_email_lead_without_address_is_rejected(make_client):
    conv = _conversation(channel=ConversationChannel.EMAIL)
    session = FakeSession(conv, _lead(conv, email=None), _agency())

    resp = _post(make_client(session), conv)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "lead_without_email"
    assert FakeResendClient.sent == []


# ---------------------------------------------------------------------------
# Implicit handoff
# ---------------------------------------------------------------------------


def test_reply_hands_the_conversation_off_implicitly(make_client):
    conv = _conversation(conv_status=ConversationStatus.ACTIVE)
    session = FakeSession(conv, _lead(conv), _agency())

    resp = _post(make_client(session), conv)

    assert conv.status is ConversationStatus.HANDED_OFF
    assert resp.json()["handed_off"] is True


def test_reply_on_an_already_handed_off_conversation_keeps_the_status(make_client):
    conv = _conversation(conv_status=ConversationStatus.HANDED_OFF)
    session = FakeSession(conv, _lead(conv), _agency())

    _post(make_client(session), conv)

    assert conv.status is ConversationStatus.HANDED_OFF


# ---------------------------------------------------------------------------
# Unsupported channels + validation + tenancy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "channel",
    [ConversationChannel.WEB, ConversationChannel.SMS, ConversationChannel.VOICE],
)
def test_unwritable_channels_answer_501(make_client, channel):
    conv = _conversation(channel=channel)
    session = FakeSession(conv, _lead(conv), _agency())

    resp = _post(make_client(session), conv)

    assert resp.status_code == 501
    assert resp.json()["detail"] == "channel_not_supported"
    assert session.added == []
    assert FakeWhatsAppClient.sent == []
    assert FakeResendClient.sent == []


def test_another_agencys_conversation_is_not_found(make_client):
    conv = _conversation(agency_id=uuid.uuid4())
    session = FakeSession(conv, _lead(conv), _agency())

    resp = _post(make_client(session), conv)

    assert resp.status_code == 404
    assert resp.json()["detail"] == "conversation_not_found"


@pytest.mark.parametrize("text", ["", "   ", "x" * 4001])
def test_invalid_text_is_rejected(make_client, text):
    conv = _conversation()
    session = FakeSession(conv, _lead(conv), _agency())

    resp = _post(make_client(session), conv, text=text)

    assert resp.status_code == 422
    assert FakeWhatsAppClient.sent == []


def test_text_is_trimmed_before_sending(make_client):
    conv = _conversation()
    session = FakeSession(conv, _lead(conv), _agency())

    _post(make_client(session), conv, text="  Voy para allá  ")

    assert FakeWhatsAppClient.sent[0][1] == "Voy para allá"
    assert session.message.content == "Voy para allá"


def test_email_reply_carries_thread_and_reply_to(make_client):
    """(n22) La respuesta manual viaja con el hilo y con Reply-To a la
    dirección de leads: sin lo primero el buzón del cliente abre un hilo
    nuevo; sin lo segundo su contestación muere en el buzón de la plataforma
    en vez de volver a la conversación."""
    from app.api.channels import leads_email_for

    conv = _conversation(channel=ConversationChannel.EMAIL)
    inbound = SimpleNamespace(
        raw={"subject": "Consulta piso Chamberí"},
        external_message_id="<orig-99@mail.example.com>",
    )
    session = FakeSession(conv, _lead(conv), _agency(), last_inbound=inbound)

    _post(make_client(session), conv)

    kwargs = FakeResendClient.sent[-1]
    assert kwargs["in_reply_to"] == "<orig-99@mail.example.com>"
    assert kwargs["references"] == ["<orig-99@mail.example.com>"]
    assert kwargs["reply_to"] == leads_email_for("inmo-sol")
    # Y las dos partes presentables (el bug de los asteriscos vistos en un
    # buzón real): texto limpio + HTML mínimo.
    assert "html" in kwargs and kwargs["html"].startswith("<div")
