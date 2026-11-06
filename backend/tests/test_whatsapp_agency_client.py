"""WhatsApp webhook uses the *agency's own* Cloud API credentials when the
agency connected its number from the dashboard, and falls back to the
global env client otherwise (app/api/whatsapp.py).

Since item 173 the reply is produced in a BackgroundTask on its own
session, so the fixtures hand `task_session` the same fake the request
uses, and turn the debounce window (216) off — what is under test here is
which credentials the reply goes out with, not the timing.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import whatsapp as wa_module
from app.core.settings import settings
from app.db.session import get_db
from app.main import app

WA_ID = "34600111222"
MSG_ID = "wamid.INBOUND1"


def _agency(settings_json: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), slug="inmo-sol", settings=settings_json or {})


def _webhook_body(phone_number_id: str = "pnid-agency") -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": phone_number_id},
                            "contacts": [
                                {"wa_id": WA_ID, "profile": {"name": "Juan Pérez"}}
                            ],
                            "messages": [
                                {
                                    "from": WA_ID,
                                    "id": MSG_ID,
                                    "type": "text",
                                    "text": {"body": "Hola, ¿sigue disponible el piso?"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def first(self):
        # Only the (173) dedup lookup calls this: nothing stored yet.
        return None


class FakeSession:
    """The route only runs the settings-match agency lookup here."""

    def __init__(self, agency):
        self.agency = agency

    async def execute(self, stmt):
        if "messages" in str(stmt).lower():
            return _ScalarResult(None)  # dedup lookup: not seen before
        return _ScalarResult(self.agency)

    async def commit(self):
        pass

    async def rollback(self):
        pass


class FallbackFakeSession:
    """The settings-mapping match finds nothing; exactly one agency exists,
    so the route reaches the single-agency fallback."""

    def __init__(self, agency):
        self.agency = agency

    async def execute(self, stmt):
        sql = str(stmt).lower()
        if "messages" in sql:
            return _ScalarResult(None)
        if "count(" in sql:
            return _ScalarResult(1)
        if "where" in sql:
            return _ScalarResult(None)  # no agency maps this phone_number_id
        return _ScalarResult(self.agency)  # select(Agency).limit(1)

    async def commit(self):
        pass

    async def rollback(self):
        pass


def fake_task_session(session):
    """Stand-in for the background task's own session (item 173)."""

    @asynccontextmanager
    async def _factory():
        yield session
        await session.commit()

    return _factory


class RecordingClient:
    """Stands in for WhatsAppClient; records constructor args and sends."""

    instances: list[RecordingClient] = []

    def __init__(self, access_token=None, phone_number_id=None):
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.sent: list[tuple[str, str]] = []
        self.marked_read: list[str] = []
        type(self).instances.append(self)

    async def send_text(self, to, text):
        self.sent.append((to, text))
        return "wamid.OUT1"

    async def mark_as_read(self, message_id):
        self.marked_read.append(message_id)


@pytest.fixture
def client_for(monkeypatch):
    def _build(agency: SimpleNamespace, session=None) -> TestClient:
        monkeypatch.setattr(settings, "app_env", "development")
        monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(""))
        monkeypatch.setattr(wa_module, "WhatsAppClient", RecordingClient)
        RecordingClient.instances = []

        async def fake_handle(db, payload):
            fake_handle.payloads.append(payload)
            return SimpleNamespace(
                reply_text="Sí, sigue disponible.",
                conversation_id=uuid.uuid4(),
                handed_off=False,
            )

        fake_handle.payloads = []
        monkeypatch.setattr(wa_module, "handle_inbound", fake_handle)

        session = session or FakeSession(agency)
        monkeypatch.setattr(wa_module, "task_session", fake_task_session(session))
        # (216) The debounce window is off: this module is about credentials.
        monkeypatch.setattr(wa_module, "DEBOUNCE_SECONDS", 0)

        async def _override():
            yield session

        app.dependency_overrides[get_db] = _override
        return TestClient(app)

    yield _build
    app.dependency_overrides.pop(get_db, None)


# Dev mode + empty app secret: any *well-formed* signature header passes.
# The shape ("sha256=" + 64 hex) is checked before anything else, so even in
# dev the header has to look like one Meta would send.
DEV_SIGNATURE = "sha256=" + "0" * 64


def _post(client: TestClient, body: dict):
    return client.post(
        "/webhooks/whatsapp", json=body, headers={"X-Hub-Signature-256": DEV_SIGNATURE}
    )


def test_reply_uses_agency_credentials_when_connected(client_for):
    agency = _agency(
        {
            "whatsapp_access_token": "EAAG-agency-token",
            "whatsapp_phone_number_id": "pnid-agency",
        }
    )
    resp = _post(client_for(agency), _webhook_body("pnid-agency"))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    assert len(RecordingClient.instances) == 1
    used = RecordingClient.instances[0]
    assert used.access_token == "EAAG-agency-token"
    assert used.phone_number_id == "pnid-agency"
    assert used.marked_read == [MSG_ID]
    assert used.sent == [(WA_ID, "Sí, sigue disponible.")]


def test_reply_falls_back_to_global_client_without_agency_credentials(client_for):
    agency = _agency({})  # nothing connected from the dashboard
    resp = _post(client_for(agency), _webhook_body("pnid-global"))
    assert resp.status_code == 200

    assert len(RecordingClient.instances) == 1
    used = RecordingClient.instances[0]
    # Constructed without per-agency credentials → env-configured client.
    assert used.access_token is None
    assert used.phone_number_id is None
    assert used.sent == [(WA_ID, "Sí, sigue disponible.")]


def test_fallback_drops_unknown_number_when_agency_connected_its_own(
    client_for, monkeypatch
):
    """Single-agency fallback + per-agency credentials: a webhook for a
    number that is neither the agency's connected one nor the global env one
    must be dropped — never answered from the agency's (different) number."""
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "pnid-global-env")
    agency = _agency(
        {
            "whatsapp_access_token": "EAAG-agency-token",
            "whatsapp_phone_number_id": "pnid-agency",
        }
    )
    client = client_for(agency, session=FallbackFakeSession(agency))

    resp = _post(client, _webhook_body("pnid-unknown"))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    # No client was constructed at all: nothing marked read, nothing sent.
    assert RecordingClient.instances == []


def test_fallback_on_global_env_number_replies_with_global_client(
    client_for, monkeypatch
):
    """When the inbound webhook targets the global env number, the
    single-agency fallback still applies, but the reply must go through the
    global client — the agency's own credentials belong to a different
    number."""
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "pnid-global-env")
    agency = _agency(
        {
            "whatsapp_access_token": "EAAG-agency-token",
            "whatsapp_phone_number_id": "pnid-agency",
        }
    )
    client = client_for(agency, session=FallbackFakeSession(agency))

    resp = _post(client, _webhook_body("pnid-global-env"))
    assert resp.status_code == 200

    assert len(RecordingClient.instances) == 1
    used = RecordingClient.instances[0]
    assert used.access_token is None  # global env client, not the agency's
    assert used.phone_number_id is None
    assert used.sent == [(WA_ID, "Sí, sigue disponible.")]
