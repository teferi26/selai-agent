"""Item 217 — an agency on its own Meta app cannot end up silently deaf.

The dashboard guide tells every agency to create its *own* app in
developers.facebook.com. Meta then signs that app's webhooks with that app's
secret. Until now the panel treated the secret as optional and promised
"si lo dejas vacío usamos nuestra comprobación general y tus mensajes entran
igual" — which was false for exactly the setup we ship: every webhook failed
the global check, every message was answered with a 401, and the wizard
showed the step in green.

This module pins the three halves of the fix:

1. the secret is required to connect, so the broken state cannot be created;
2. the webhook only accepts the right signer, and rejects (loudly, with a
   diagnosable log) instead of pretending;
3. GET /channels reports whether a real webhook ever arrived, so the panel
   can stop calling a channel "listo" on the strength of stored credentials.

Plus a guard on the copy itself: the sentences that made the promise must not
come back.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import channels as channels_module
from app.api import whatsapp as wa_module
from app.core.settings import settings
from app.db.models import ConversationChannel
from app.db.session import get_db
from app.main import app
from app.services.secret_store import decrypt_secret

WA_ID = "34600111222"
AGENCY_SECRET = "agency-app-secret"
GLOBAL_SECRET = "global-app-secret"
OUR_PNID = "pnid-selai"
AGENCY_PNID = "pnid-agency"
TOKEN = "EAAG-agency-token"

# Repo root, so the copy guard can read the dashboard sources.
_REPO = Path(__file__).resolve().parents[2]
_SETUP_DIR = _REPO / "dashboard" / "src" / "app" / "(app)" / "setup"
WHATSAPP_CONNECT_TSX = _SETUP_DIR / "WhatsAppConnect.tsx"
SETUP_CLIENT_TSX = _SETUP_DIR / "SetupClient.tsx"


# ---------------------------------------------------------------------------
# Webhook side — shared doubles
# ---------------------------------------------------------------------------


def _body(phone_number_id: str) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": phone_number_id},
                            "contacts": [{"wa_id": WA_ID, "profile": {"name": "Juan"}}],
                            "messages": [
                                {
                                    "from": WA_ID,
                                    "id": "wamid.OWNAPP1",
                                    "type": "text",
                                    "text": {"body": "Hola"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


def _sign(raw: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def first(self):
        return None


class WebhookSession:
    """Resolves every agency lookup to the one agency under test; the dedup
    lookup finds nothing."""

    def __init__(self, agency):
        self.agency = agency

    async def execute(self, stmt):
        if "messages" in str(stmt).lower():
            return _ScalarResult(None)
        return _ScalarResult(self.agency)

    async def commit(self):
        pass

    async def rollback(self):
        pass


class _DummySession:
    async def execute(self, stmt):
        return _ScalarResult(None)

    async def get(self, model, pk):
        return None

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


class RecordingClient:
    instances: list[RecordingClient] = []

    def __init__(self, access_token=None, phone_number_id=None):
        self.sent: list[tuple[str, str]] = []
        type(self).instances.append(self)

    async def send_text(self, to, text):
        self.sent.append((to, text))
        return "wamid.OUT"

    async def mark_as_read(self, message_id):
        pass

    async def download_media(self, media_id, **kwargs):
        return None


class RecordingLog:
    """Stand-in for the structlog logger: keeps (event, fields) so the tests
    can assert on the diagnosis, not just on the status code."""

    def __init__(self):
        self.entries: list[tuple[str, dict]] = []

    def _record(self, event, **fields):
        self.entries.append((event, fields))

    warning = _record
    info = _record
    error = _record
    exception = _record

    def fields_for(self, event: str) -> dict:
        for name, fields in self.entries:
            if name == event:
                return fields
        raise AssertionError(f"{event} was never logged; got {self.entries}")

    def events(self) -> list[str]:
        return [name for name, _ in self.entries]


@pytest.fixture
def webhook_env(monkeypatch):
    """Production-like: a global secret is configured and OUR_PNID is the
    number we own, so both branches of the choice are exercisable."""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(GLOBAL_SECRET))
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", OUR_PNID)
    monkeypatch.setattr(wa_module, "WhatsAppClient", RecordingClient)
    monkeypatch.setattr(wa_module, "task_session", _fake_task_session(_DummySession()))
    monkeypatch.setattr(wa_module, "DEBOUNCE_SECONDS", 0)
    RecordingClient.instances = []

    log = RecordingLog()
    monkeypatch.setattr(wa_module, "log", log)

    handled: list = []

    async def fake_handle(db, payload):
        handled.append(payload)
        return SimpleNamespace(
            reply_text="Hola, dime.", conversation_id=uuid.uuid4(), handed_off=False
        )

    monkeypatch.setattr(wa_module, "handle_inbound", fake_handle)
    return SimpleNamespace(handled=handled, log=log)


@pytest.fixture
def webhook_client():
    def _build(agency_settings: dict) -> TestClient:
        agency = SimpleNamespace(
            id=uuid.uuid4(), slug="inmo-sol", settings=agency_settings
        )

        async def _override():
            yield WebhookSession(agency)

        app.dependency_overrides[get_db] = _override
        return TestClient(app), agency

    yield _build
    app.dependency_overrides.pop(get_db, None)


def _post(client: TestClient, body: dict, secret: str):
    raw = json.dumps(body).encode()
    return client.post(
        "/webhooks/whatsapp",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(raw, secret),
        },
    )


OWN_APP = {
    "whatsapp_access_token": TOKEN,
    "whatsapp_phone_number_id": AGENCY_PNID,
    "whatsapp_app_secret": AGENCY_SECRET,
}
OWN_APP_NO_SECRET = {
    "whatsapp_access_token": TOKEN,
    "whatsapp_phone_number_id": AGENCY_PNID,
}


# ---------------------------------------------------------------------------
# The webhook: own app + secret works, own app without secret cannot
# ---------------------------------------------------------------------------


def test_own_app_with_its_secret_is_processed(webhook_env, webhook_client):
    """The scenario the guide actually produces: the agency's own Meta app
    signs with the agency's own secret, and the message goes through."""
    client, _agency = webhook_client(OWN_APP)
    resp = _post(client, _body(AGENCY_PNID), AGENCY_SECRET)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert len(webhook_env.handled) == 1
    assert webhook_env.handled[0].text == "Hola"


def test_unreadable_app_secret_returns_503_before_acknowledging(
    webhook_env, webhook_client
):
    broken = {**OWN_APP, "whatsapp_app_secret": "enc:v1:not-a-valid-token"}
    client, _agency = webhook_client(broken)

    resp = _post(client, _body(AGENCY_PNID), AGENCY_SECRET)

    assert resp.status_code == 503
    assert resp.json()["detail"] == "channel_credentials_unavailable"
    assert webhook_env.handled == []


def test_unreadable_access_token_returns_503_instead_of_losing_the_message(
    webhook_env, webhook_client
):
    broken = {**OWN_APP, "whatsapp_access_token": "enc:v1:not-a-valid-token"}
    client, _agency = webhook_client(broken)

    resp = _post(client, _body(AGENCY_PNID), AGENCY_SECRET)

    assert resp.status_code == 503
    assert resp.json()["detail"] == "channel_credentials_unavailable"
    assert webhook_env.handled == []


def test_own_number_without_a_stored_secret_is_rejected(webhook_env, webhook_client):
    """A connection made before the secret was required. Its webhooks are
    signed by an app whose secret we do not have, so there is nothing to check
    them against: 401 with a log that says which number and which agency,
    instead of a global check that could only ever fail."""
    client, agency = webhook_client(OWN_APP_NO_SECRET)
    resp = _post(client, _body(AGENCY_PNID), GLOBAL_SECRET)

    assert resp.status_code == 401
    assert webhook_env.handled == []
    assert RecordingClient.instances == []

    fields = webhook_env.log.fields_for("whatsapp.webhook.agency_without_app_secret")
    assert fields["phone_number_id"] == AGENCY_PNID
    assert fields["agency"] == str(agency.id)


def test_wrong_secret_logs_a_diagnosable_reason(webhook_env, webhook_client):
    """The usual support ticket is "no me entra nada" from an agency that
    pasted the secret of a different Meta app. The log has to separate that
    from an attack: known number, whose it is, which secret we checked."""
    client, agency = webhook_client(OWN_APP)
    resp = _post(client, _body(AGENCY_PNID), "secret-of-another-app")

    assert resp.status_code == 401
    assert webhook_env.handled == []

    fields = webhook_env.log.fields_for("whatsapp.webhook.bad_signature")
    assert fields == {
        "phone_number_id": AGENCY_PNID,
        "known_number": True,
        "agency": str(agency.id),
        "agency_owns_number": True,
        "agency_app_secret_set": True,
        "secret_checked": "agency",
    }


def test_unknown_number_bad_signature_is_logged_as_unknown(webhook_env, monkeypatch):
    """The other half of the diagnosis: a webhook for a number nobody has
    connected. Someone pointed their app at our callback URL — or a customer
    typed the wrong number id — and the log must not blame a tenant."""

    class _NoAgencySession:
        async def execute(self, stmt):
            return _ScalarResult(None)

        async def commit(self):
            pass

        async def rollback(self):
            pass

    async def _override():
        yield _NoAgencySession()

    app.dependency_overrides[get_db] = _override
    try:
        resp = _post(TestClient(app), _body("pnid-of-a-stranger"), "whatever")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 401
    fields = webhook_env.log.fields_for("whatsapp.webhook.bad_signature")
    assert fields["known_number"] is False
    assert fields["agency"] is None
    assert fields["agency_app_secret_set"] is False
    assert fields["secret_checked"] == "global"


def test_our_own_number_keeps_working_on_the_global_secret(webhook_env, webhook_client):
    """Agencies served by *our* shared number are on *our* Meta app: our
    secret is the sender of record and must keep validating, or tightening
    the rule for own-app tenants would take everyone else down with it."""
    client, _agency = webhook_client({})
    resp = _post(client, _body(OUR_PNID), GLOBAL_SECRET)

    assert resp.status_code == 200
    assert len(webhook_env.handled) == 1


def test_local_dev_without_any_secret_is_not_made_stricter(
    webhook_env, webhook_client, monkeypatch
):
    """`verify_signature` already waves everything through when running in
    development with no global secret configured. The new rules are about
    production trust, so they must not turn a local environment into the one
    place where a webhook is refused — there was never a check to fail."""
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(""))

    client, _agency = webhook_client(OWN_APP_NO_SECRET)
    resp = _post(client, _body(AGENCY_PNID), "anything-goes-here")

    assert resp.status_code == 200
    assert len(webhook_env.handled) == 1
    assert "whatsapp.webhook.agency_without_app_secret" not in webhook_env.log.events()


def test_production_never_takes_the_dev_shortcut(webhook_env, webhook_client):
    """Same agency, same missing secret, production: refused. The hatch above
    is bound to `app_env`, not to how convenient it would be."""
    client, _agency = webhook_client(OWN_APP_NO_SECRET)
    assert _post(client, _body(AGENCY_PNID), "anything-goes-here").status_code == 401


def test_an_agency_secret_cannot_speak_for_our_own_number(webhook_env, webhook_client):
    """The exclusivity runs both ways: a tenant's secret vouches for the
    number that tenant connected and for nothing else."""
    client, _agency = webhook_client(OWN_APP)
    resp = _post(client, _body(OUR_PNID), AGENCY_SECRET)

    assert resp.status_code == 401
    assert webhook_env.handled == []


# ---------------------------------------------------------------------------
# POST /channels/whatsapp — the state above cannot be created any more
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def one(self):
        return self._value


class ChannelsSession:
    def __init__(self, agency):
        self.agency = agency
        self.commits = 0
        self.stats: dict[ConversationChannel, tuple[int, datetime | None]] = {}

    async def get(self, model, pk):
        return self.agency if pk == self.agency.id else None

    async def execute(self, stmt):
        # Keyed off the channel, not the table: WhatsApp counts inbound
        # `messages` joined to their conversation, email counts
        # `conversations`. Both mention "conversations" in the SQL.
        sql = str(stmt)
        if "conversations" in sql:
            channel = next(
                (
                    v
                    for v in stmt.compile().params.values()
                    if isinstance(v, ConversationChannel)
                ),
                None,
            )
            return _FakeResult(self.stats.get(channel, (0, None)))
        if "webhook_subscriptions" in sql:
            return _FakeResult(0)
        raise AssertionError(f"unexpected query: {sql}")

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


class _FakeHttpResponse:
    def __init__(self, status_code: int = 200, data: dict | None = None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.text = str(self._data)

    def json(self) -> dict:
        return self._data


class _ScriptedHttpClient:
    calls: list[dict] = []
    responses: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def _respond(self, call: dict):
        type(self).calls.append(call)
        return type(self).responses.pop(0)

    async def get(self, url, params=None, headers=None):
        return await self._respond({"method": "GET", "url": url})

    async def post(self, url, json=None, headers=None):
        return await self._respond({"method": "POST", "url": url})


@pytest.fixture
def agency():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Inmo Sol",
        slug="inmo-sol",
        contact_email="dueno@inmosol.example.com",
        settings={},
    )


@pytest.fixture
def db(agency):
    session = ChannelsSession(agency)

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


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setattr(channels_module.httpx, "AsyncClient", _ScriptedHttpClient)
    _ScriptedHttpClient.calls = []
    _ScriptedHttpClient.responses = [
        _FakeHttpResponse(
            200, {"display_phone_number": "+34 600 11 22 33", "verified_name": "Inmo Sol"}
        ),
        _FakeHttpResponse(200, {"success": True}),
    ]
    return _ScriptedHttpClient


def _connect_url(agency) -> str:
    return f"/api/v1/agencies/{agency.id}/channels/whatsapp"


def test_connect_with_the_app_secret_stores_it(client, agency, db, http):
    resp = client.post(
        _connect_url(agency),
        json={
            "phone_number_id": AGENCY_PNID,
            "waba_id": "9876543210",
            "access_token": TOKEN,
            "app_secret": f"  {AGENCY_SECRET}  ",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["app_secret_set"] is True
    assert agency.settings["whatsapp_app_secret"] != AGENCY_SECRET
    assert decrypt_secret(agency.settings["whatsapp_app_secret"]) == AGENCY_SECRET


def test_connect_without_the_app_secret_is_refused(client, agency, db, http):
    """The whole point: the "connected but permanently deaf" state is no
    longer reachable, so no wizard can ever show it as done."""
    resp = client.post(
        _connect_url(agency),
        json={
            "phone_number_id": AGENCY_PNID,
            "waba_id": "9876543210",
            "access_token": TOKEN,
        },
    )
    assert resp.status_code == 422
    assert agency.settings == {}
    assert db.commits == 0
    # Nothing was even asked of Meta: the body never validated.
    assert http.calls == []


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_connect_with_a_blank_app_secret_is_refused(client, agency, db, http, blank):
    """`min_length=1` alone would let "   " through, and a whitespace secret
    stores as empty — straight back into the state above."""
    resp = client.post(
        _connect_url(agency),
        json={
            "phone_number_id": AGENCY_PNID,
            "access_token": TOKEN,
            "app_secret": blank,
        },
    )
    assert resp.status_code == 422
    assert agency.settings == {}
    assert db.commits == 0


# ---------------------------------------------------------------------------
# GET /channels — evidence, not configuration
# ---------------------------------------------------------------------------


def test_status_reports_no_inbound_when_nothing_ever_arrived(client, agency, db):
    agency.settings = {
        "whatsapp_access_token": TOKEN,
        "whatsapp_phone_number_id": AGENCY_PNID,
        "whatsapp_app_secret": AGENCY_SECRET,
    }
    wa = client.get(f"/api/v1/agencies/{agency.id}/channels").json()["whatsapp"]

    # Everything configuration can say is green…
    assert wa["connected"] is True
    assert wa["app_secret_set"] is True
    # …and still not one message has come in. That is what the panel shows.
    assert wa["inbound_count"] == 0
    assert wa["last_inbound_at"] is None


def test_status_reports_real_inbound_traffic(client, agency, db):
    """The WhatsApp figures moved from conversations to inbound messages in
    the review (a conversation is created once per lead, so its date answers
    "when did a new lead first write"). The double keys off the channel, so
    the assertions are unchanged — see test_whatsapp_217_adversarial.py for
    the one that pins the query itself."""
    last = datetime(2026, 7, 30, 9, 30, tzinfo=UTC)
    db.stats[ConversationChannel.WHATSAPP] = (3, last)
    db.stats[ConversationChannel.EMAIL] = (11, datetime(2026, 7, 1, tzinfo=UTC))
    agency.settings = {
        "whatsapp_access_token": TOKEN,
        "whatsapp_phone_number_id": AGENCY_PNID,
        "whatsapp_app_secret": AGENCY_SECRET,
    }

    body = client.get(f"/api/v1/agencies/{agency.id}/channels").json()
    assert body["whatsapp"]["inbound_count"] == 3
    assert body["whatsapp"]["last_inbound_at"].startswith("2026-07-30T09:30:00")
    # The two channels are counted separately — no cross-contamination.
    assert body["email"]["received_count"] == 11


# ---------------------------------------------------------------------------
# The copy: the promise that was false must not come back
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    assert path.exists(), f"missing dashboard source: {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "lie",
    [
        # The headline promise: "leave it empty and your messages arrive anyway".
        "usamos nuestra comprobación general",
        "tus mensajes entran igual",
        # The reassurance shown once connected without a secret.
        "No guardaste el secreto de tu app",
        "Funciona; añadirlo",
        # The old escape hatch: reconnecting with the field empty is no longer
        # possible, so telling people to do it would send them nowhere.
        "vuelve a conectar el número con ese campo vacío",
        "el campo vacío",
    ],
)
def test_the_false_promises_are_gone_from_the_panel(lie):
    assert lie not in _read(WHATSAPP_CONNECT_TSX)


def test_the_app_secret_is_no_longer_offered_as_optional():
    """The WABA id really is optional, so "(opcional)" still exists in the
    form — but not around the secret field, and not in its guide step."""
    source = _read(WHATSAPP_CONNECT_TSX)
    assert "Copia el secreto de la app (opcional)" not in source

    anchor = source.index('htmlFor="wa-app-secret"')
    label_block = source[anchor - 200 : anchor + 400]
    assert "opcional" not in label_block
    assert re.search(r"\brequired\b", label_block)


def test_the_panel_says_what_happens_without_the_secret():
    source = _read(WHATSAPP_CONNECT_TSX)
    assert "sin ese secreto no te entra ni un mensaje" in source
    assert "sin él no te llegará ningún mensaje" in source
    assert "Falta el secreto de tu app y por eso no entra ningún mensaje" in source


def test_the_panel_admits_when_no_message_has_arrived():
    """Wording adjusted in the review: "no hemos recibido ningún mensaje" was
    a claim we cannot make. When the stored secret belongs to a different
    Meta app, Meta *does* deliver and we reject — so the sentence was false
    in exactly the case the owner needs to diagnose, and it sent them back to
    re-send the test message they had already sent correctly. See
    test_whatsapp_217_adversarial.py."""
    source = _read(WHATSAPP_CONNECT_TSX)
    assert "Conectado, pero todavía no nos ha entrado ningún mensaje" in source
    assert "desde otro teléfono" in source


def test_the_wizard_shows_the_webhook_values_on_the_connected_step():
    """They used to live only in Ajustes → Canales, so the person stuck in
    the wizard had no way to finish the one step that makes messages arrive."""
    source = _read(SETUP_CLIENT_TSX)
    assert "WebhookStatusBox" in source
    assert "InboundStatusNotice" in source
