"""Item 173 — the same inbound message is never processed twice.

Meta and Resend retry a webhook they think we failed to answer. Before
this, the retry ran the whole pipeline again and the lead got a second
answer to a message they sent once — the failure that most directly
contradicts what the product sells.

Three layers are pinned here:

- `inbound_already_processed`: the query, agency-scoped, cheap, and
  honest about payloads that carry no id at all;
- the routes: the check runs *before* anything with side effects, and the
  duplicate is answered 200 so the provider stops retrying;
- the schema: a partial unique index, because the check alone loses the
  race between two workers holding the two copies.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import email as email_module
from app.api import whatsapp as wa_module
from app.channels.dedup import inbound_already_processed
from app.core.settings import settings
from app.db.models import Message
from app.db.session import get_db
from app.main import app

AGENCY_ID = uuid.uuid4()
WA_ID = "34600111222"
MSG_ID = "wamid.RETRIED"


# ---------------------------------------------------------------------------
# inbound_already_processed — the query
# ---------------------------------------------------------------------------


class _RowsResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _QuerySession:
    def __init__(self, row):
        self.row = row
        self.statements: list[str] = []

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        return _RowsResult(self.row)


def test_known_message_id_is_a_duplicate():
    db = _QuerySession(row=(uuid.uuid4(),))
    assert asyncio.run(inbound_already_processed(db, AGENCY_ID, MSG_ID)) is True


def test_unknown_message_id_is_not_a_duplicate():
    db = _QuerySession(row=None)
    assert asyncio.run(inbound_already_processed(db, AGENCY_ID, MSG_ID)) is False


def test_missing_message_id_is_never_a_duplicate_and_costs_no_query():
    """A provider that sends no id gives us nothing to compare. Treating
    that as "already seen" would drop every one of its messages."""
    db = _QuerySession(row=(uuid.uuid4(),))
    assert asyncio.run(inbound_already_processed(db, AGENCY_ID, None)) is False
    assert asyncio.run(inbound_already_processed(db, AGENCY_ID, "")) is False
    assert db.statements == []


def test_lookup_is_scoped_to_the_agency():
    """Two tenants may legitimately hold the same provider id; without the
    agency filter one would silence the other's leads."""
    db = _QuerySession(row=None)
    asyncio.run(inbound_already_processed(db, AGENCY_ID, MSG_ID))
    sql = db.statements[0].lower()
    assert "agency_id" in sql
    assert "external_message_id" in sql


def test_lookup_only_looks_at_inbound_rows():
    """Outbound rows carry whatever the sending client returned — including
    the literal "dev-stub" when a client is not configured. One of those
    answering "already seen" would drop a real lead's message."""
    db = _QuerySession(row=None)
    asyncio.run(inbound_already_processed(db, AGENCY_ID, MSG_ID))
    assert "direction" in db.statements[0].lower()


def test_the_same_provider_id_in_two_agencies_is_two_messages():
    """One email addressed to two agencies' inboxes carries one Message-ID.
    Both are real leads and both have to be answered."""
    stored: set[tuple[uuid.UUID, str]] = set()

    class _TenantSession:
        def __init__(self, agency_id):
            self.agency_id = agency_id

        async def execute(self, stmt):
            compiled = stmt.compile()
            params = compiled.params
            key = (params["agency_id_1"], params["external_message_id_1"])
            return _RowsResult((uuid.uuid4(),) if key in stored else None)

    first, second = uuid.uuid4(), uuid.uuid4()
    assert asyncio.run(inbound_already_processed(_TenantSession(first), first, MSG_ID)) is False
    stored.add((first, MSG_ID))
    # Same provider id, different tenant: not a duplicate.
    assert asyncio.run(inbound_already_processed(_TenantSession(second), second, MSG_ID)) is False
    stored.add((second, MSG_ID))
    # And the retry of the first one still is.
    assert asyncio.run(inbound_already_processed(_TenantSession(first), first, MSG_ID)) is True


# ---------------------------------------------------------------------------
# The schema guarantee
# ---------------------------------------------------------------------------


def test_messages_have_a_partial_unique_index_on_the_provider_id():
    """The check above races with itself across workers; this is what makes
    the second insert fail instead of producing a second reply."""
    indexes = {ix.name: ix for ix in Message.__table__.indexes}
    ix = indexes.get("uq_messages_agency_external_id")
    assert ix is not None, "missing dedup index"
    assert ix.unique is True
    assert [c.name for c in ix.columns] == ["agency_id", "external_message_id"]
    where = str(ix.dialect_options["postgresql"]["where"])
    assert "external_message_id IS NOT NULL" in where
    # Inbound only. Outbound rows store whatever the sending client returned,
    # and an unconfigured client returns the literal "dev-stub" for every
    # send: a direction-blind unique index turns the second follow-up on such
    # a deployment into an IntegrityError.
    assert "direction = 'inbound'" in where


def test_the_dedup_index_does_not_collide_with_the_unconfigured_send_marker():
    """`app/services/followups.py` stores the id the client returned, and
    `WhatsAppClient`/`ResendClient` return "dev-stub" when they have no
    credentials. Every one of those rows is outbound, which is exactly why
    the index has to be."""
    from app.channels.email.client import ResendClient  # noqa: F401
    from app.db.models import MessageDirection

    ix = {i.name: i for i in Message.__table__.indexes}["uq_messages_agency_external_id"]
    where = str(ix.dialect_options["postgresql"]["where"])
    assert MessageDirection.INBOUND.value in where
    assert MessageDirection.OUTBOUND.value not in where


# ---------------------------------------------------------------------------
# WhatsApp route
# ---------------------------------------------------------------------------


class _ScalarResult:
    def __init__(self, value, *, row=None):
        self._value = value
        self._row = row

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def first(self):
        return self._row


class WaSession:
    """Answers the agency lookup, and the dedup lookup per `seen`."""

    def __init__(self, agency, *, seen: bool):
        self.agency = agency
        self.seen = seen

    async def execute(self, stmt):
        if "messages" in str(stmt).lower():
            return _ScalarResult(None, row=(uuid.uuid4(),) if self.seen else None)
        return _ScalarResult(self.agency)

    async def commit(self):
        pass

    async def rollback(self):
        pass


def _fake_task_session(session):
    @asynccontextmanager
    async def _factory():
        yield session
        await session.commit()

    return _factory


def _wa_body(message_id: str = MSG_ID) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "pnid-agency"},
                            "contacts": [{"wa_id": WA_ID, "profile": {"name": "Ana"}}],
                            "messages": [
                                {
                                    "from": WA_ID,
                                    "id": message_id,
                                    "type": "text",
                                    "text": {"body": "¿Sigue disponible?"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


class RecordingClient:
    instances: list[RecordingClient] = []

    def __init__(self, access_token=None, phone_number_id=None):
        self.sent: list[tuple[str, str]] = []
        self.marked_read: list[str] = []
        type(self).instances.append(self)

    async def send_text(self, to, text):
        self.sent.append((to, text))
        return "wamid.OUT"

    async def mark_as_read(self, message_id):
        self.marked_read.append(message_id)

    async def download_media(self, media_id, **kwargs):
        return None


DEV_SIGNATURE = "sha256=" + "0" * 64


@pytest.fixture
def wa(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(""))
    monkeypatch.setattr(wa_module, "WhatsAppClient", RecordingClient)
    monkeypatch.setattr(wa_module, "DEBOUNCE_SECONDS", 0)
    RecordingClient.instances = []

    handled: list = []

    async def fake_handle(db, payload):
        handled.append(payload)
        return SimpleNamespace(
            reply_text="Sí, sigue disponible.",
            conversation_id=uuid.uuid4(),
            handed_off=False,
        )

    monkeypatch.setattr(wa_module, "handle_inbound", fake_handle)

    agency = SimpleNamespace(
        id=AGENCY_ID,
        slug="inmo-sol",
        settings={
            "whatsapp_access_token": "EAAG-agency",
            "whatsapp_phone_number_id": "pnid-agency",
        },
    )

    def _build(*, seen: bool) -> TestClient:
        session = WaSession(agency, seen=seen)
        monkeypatch.setattr(wa_module, "task_session", _fake_task_session(session))

        async def _override():
            yield session

        app.dependency_overrides[get_db] = _override
        return TestClient(app)

    yield SimpleNamespace(build=_build, handled=handled)
    app.dependency_overrides.pop(get_db, None)


def test_whatsapp_first_delivery_is_processed(wa):
    resp = wa.build(seen=False).post(
        "/webhooks/whatsapp", json=_wa_body(), headers={"X-Hub-Signature-256": DEV_SIGNATURE}
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert len(wa.handled) == 1
    assert RecordingClient.instances[0].sent == [(WA_ID, "Sí, sigue disponible.")]


def test_whatsapp_retry_is_answered_200_without_side_effects(wa):
    """Meta's retry: 200 so it stops, `duplicate` so the logs say why, and
    not a single thing done — no agent run, no client, no second reply."""
    resp = wa.build(seen=True).post(
        "/webhooks/whatsapp", json=_wa_body(), headers={"X-Hub-Signature-256": DEV_SIGNATURE}
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "duplicate"}
    assert wa.handled == []
    assert RecordingClient.instances == []


def test_whatsapp_reply_is_produced_after_the_response(wa):
    """The pipeline moved to a BackgroundTask: what Meta waits for is the
    signature check and the dedup lookup, not Claude."""
    client = wa.build(seen=False)
    with client:
        resp = client.post(
            "/webhooks/whatsapp",
            json=_wa_body(),
            headers={"X-Hub-Signature-256": DEV_SIGNATURE},
        )
    assert resp.status_code == 200
    # Background work ran (TestClient drains it) and used its own session.
    assert len(wa.handled) == 1


# ---------------------------------------------------------------------------
# Email route
# ---------------------------------------------------------------------------


class EmailSession:
    def __init__(self, agency, *, seen: bool):
        self.agency = agency
        self.seen = seen

    async def execute(self, stmt):
        sql = str(stmt).lower()
        if "messages" in sql:
            return _ScalarResult(None, row=(uuid.uuid4(),) if self.seen else None)
        if "agencies" in sql:
            return _ScalarResult(self.agency)
        return _ScalarResult(None)

    async def get(self, model, pk):
        return self.agency

    async def commit(self):
        pass

    async def rollback(self):
        pass


def _email_body(message_id: str = "<mid-1@mail.com>") -> dict:
    return {
        "From": "Ana <ana@mail.example.com>",
        "To": "inmo-sol-leads@leads.selai.es",
        "Subject": "Piso en Malasaña",
        "MessageID": message_id,
        "TextBody": "Hola, ¿sigue disponible el piso?",
    }


@pytest.fixture
def mail(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", SecretStr("webhook-secret"))

    handled: list = []

    async def fake_handle(db, payload):
        handled.append(payload)
        return SimpleNamespace(
            reply_text="Sí, sigue disponible.",
            conversation_id=uuid.uuid4(),
            handed_off=False,
        )

    monkeypatch.setattr(email_module, "handle_inbound", fake_handle)

    sent: list = []

    class _FakeResend:
        async def send(self, **kwargs):
            sent.append(kwargs)
            return "id"

    monkeypatch.setattr(email_module, "ResendClient", lambda: _FakeResend())

    agency = SimpleNamespace(id=AGENCY_ID, slug="inmo-sol", contact_email="d@x.es")

    def _build(*, seen: bool) -> TestClient:
        session = EmailSession(agency, seen=seen)
        monkeypatch.setattr(email_module, "task_session", _fake_task_session(session))

        async def _override():
            yield session

        app.dependency_overrides[get_db] = _override
        return TestClient(app)

    yield SimpleNamespace(build=_build, handled=handled, sent=sent)
    app.dependency_overrides.pop(get_db, None)


def test_email_first_delivery_is_accepted_and_answered(mail):
    resp = mail.build(seen=False).post(
        "/webhooks/email",
        json=_email_body(),
        headers={"X-Webhook-Secret": "webhook-secret"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "accepted"}
    assert len(mail.handled) == 1
    assert mail.sent and mail.sent[0]["to"] == "ana@mail.example.com"


def test_email_retry_is_a_duplicate_with_no_reply(mail):
    resp = mail.build(seen=True).post(
        "/webhooks/email",
        json=_email_body(),
        headers={"X-Webhook-Secret": "webhook-secret"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "duplicate"}
    assert mail.handled == []
    assert mail.sent == []


def test_email_webhook_secret_uses_constant_time_comparison(mail):
    """Contract 4: `!=` leaked the matching prefix through timing, and this
    secret is the only thing between a stranger and injected leads."""
    import inspect

    source = inspect.getsource(email_module.inbound)
    assert "compare_digest" in source
    resp = mail.build(seen=False).post(
        "/webhooks/email",
        json=_email_body(),
        headers={"X-Webhook-Secret": "wrong"},
    )
    assert resp.status_code == 401
    assert mail.handled == []
