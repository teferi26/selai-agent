"""Item 189 — "Envíame un WhatsApp de prueba".

Connecting WhatsApp used to end with a green badge and no proof. The
agency found out the channel did not work when a real lead was already
lost. This endpoint sends one real message with the agency's own
credentials, so the owner sees it arrive on their own phone.

Same FakeSession / dependency_overrides style as test_channels_api.py.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import channels as channels_module
from app.api.channels import WHATSAPP_TEST_MESSAGE, normalize_msisdn
from app.core.settings import settings
from app.db.session import get_db
from app.main import app
from app.services.secret_store import encrypt_secret

TOKEN = "EAAG-agency-token"
PNID = "1234567890"

CONNECTED = {"whatsapp_access_token": TOKEN, "whatsapp_phone_number_id": PNID}


class FakeSession:
    def __init__(self, agency):
        self.agency = agency
        self.commits = 0

    async def get(self, model, pk):
        return self.agency if pk == self.agency.id else None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.fixture
def agency():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Inmo Sol",
        slug="inmo-sol",
        contact_email="dueno@inmosol.example.com",
        settings={**CONNECTED, "whatsapp_access_token": encrypt_secret(TOKEN)},
    )


@pytest.fixture
def client(monkeypatch, agency):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")

    async def _override():
        yield FakeSession(agency)

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


class RecordingClient:
    instances: list[RecordingClient] = []
    exc: Exception | None = None

    def __init__(self, access_token=None, phone_number_id=None):
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.sent: list[tuple[str, str]] = []
        type(self).instances.append(self)

    async def send_text(self, to, text):
        self.sent.append((to, text))
        if type(self).exc is not None:
            raise type(self).exc
        return "wamid.TEST"


@pytest.fixture
def wa_client(monkeypatch):
    monkeypatch.setattr(channels_module, "WhatsAppClient", RecordingClient)
    RecordingClient.instances = []
    RecordingClient.exc = None
    return RecordingClient


def _url(agency) -> str:
    return f"/api/v1/agencies/{agency.id}/channels/whatsapp/test"


# ---------------------------------------------------------------------------
# Phone normalization — pure
# ---------------------------------------------------------------------------


def test_a_spanish_mobile_typed_plainly_is_accepted():
    """What an owner actually types when asked for "tu móvil"."""
    assert normalize_msisdn("600 11 22 33") == "34600112233"
    assert normalize_msisdn("600112233") == "34600112233"


def test_an_international_number_is_kept_as_given():
    assert normalize_msisdn("+34 600 11 22 33") == "34600112233"
    assert normalize_msisdn("+44 7700 900123") == "447700900123"


def test_nonsense_is_refused():
    assert normalize_msisdn("") is None
    assert normalize_msisdn("hola") is None
    assert normalize_msisdn("123") is None
    assert normalize_msisdn("1" * 20) is None


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


def test_test_message_goes_out_with_the_agency_credentials(client, agency, wa_client):
    resp = client.post(_url(agency), json={"to_phone": "600112233"})
    assert resp.status_code == 200
    assert resp.json() == {
        "sent": True,
        "to_phone": "34600112233",
        "message": WHATSAPP_TEST_MESSAGE,
    }

    used = wa_client.instances[0]
    assert used.access_token == TOKEN
    assert used.phone_number_id == PNID
    assert used.sent == [("34600112233", "Mensaje de prueba de selai")]


def test_without_a_connection_it_is_501_not_an_error_about_the_input(
    client, agency, wa_client
):
    """Nothing is wrong with the request — the channel just isn't connected
    yet, and the dashboard has to say that instead of blaming the user."""
    agency.settings = {}
    resp = client.post(_url(agency), json={"to_phone": "600112233"})
    assert resp.status_code == 501
    assert resp.json()["detail"] == "whatsapp_not_connected"
    assert wa_client.instances == []


def test_a_number_that_is_not_one_is_422(client, agency, wa_client):
    resp = client.post(_url(agency), json={"to_phone": "no soy un teléfono"})
    assert resp.status_code == 422
    assert wa_client.instances == []


def test_meta_refusing_the_send_is_a_502(client, agency, wa_client):
    """The credentials are stored and look fine but Meta says no (token
    expired, number not registered). The owner must see a failure, not a
    "Comprobado" that lies."""
    wa_client.exc = RuntimeError("(#131030) recipient not in allowed list")
    resp = client.post(_url(agency), json={"to_phone": "600112233"})
    assert resp.status_code == 502
    assert resp.json()["detail"] == "whatsapp_send_failed"


def test_the_endpoint_requires_a_destination(client, agency):
    assert client.post(_url(agency), json={}).status_code == 422


def test_unknown_agency_is_404(client, wa_client):
    resp = client.post(
        f"/api/v1/agencies/{uuid.uuid4()}/channels/whatsapp/test",
        json={"to_phone": "600112233"},
    )
    assert resp.status_code == 404
    assert wa_client.instances == []
