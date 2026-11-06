"""Item 160 — per-agency webhook signature verification.

An agency that connects its own Meta app stores that app's secret; Meta
signs its webhooks with it, not with ours. The route resolves the agency
from the (still unverified) payload and checks the signature against that
secret — and only against it.

(217) The global secret is no longer a fallback for an agency's own number:
it is used for the number *we* own and for payloads no agency claims. See
`test_webhook_rejects_a_connected_number_without_its_own_secret` for why
that changed.

Also pins the security invariant: nothing with side effects runs before the
signature validates.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import whatsapp as wa_module
from app.channels.whatsapp.security import verify_signature
from app.core.settings import settings
from app.db.session import get_db
from app.main import app

WA_ID = "34600111222"
AGENCY_SECRET = "agency-app-secret"
GLOBAL_SECRET = "global-app-secret"


def _body(phone_number_id: str = "pnid-agency") -> dict:
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
                                    "id": "wamid.SIG1",
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
        # Only the (173) dedup lookup calls this: nothing stored yet.
        return None


class FakeSession:
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


class RoutingSession:
    """Multi-tenant stand-in: resolves each lookup to the agency that owns
    the phone_number_id bound into the statement, like the real query does.
    Unknown numbers resolve to nothing (and, with several agencies around,
    the single-agency fallback never applies)."""

    def __init__(self, by_pnid: dict[str, object]):
        self.by_pnid = by_pnid

    async def execute(self, stmt):
        sql = str(stmt).lower()
        if "messages" in sql:
            return _ScalarResult(None)
        if "count(" in sql:
            # More than one agency: the single-agency fallback stays off.
            return _ScalarResult(len(self.by_pnid))
        values = set(stmt.compile().params.values())
        for pnid, agency in self.by_pnid.items():
            if pnid in values:
                return _ScalarResult(agency)
        return _ScalarResult(None)

    async def commit(self):
        pass

    async def rollback(self):
        pass


def _fake_task_session(session):
    """The background task's own session (item 173), faked."""

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


class _DummySession:
    """Enough of a session for the background task once `handle_inbound` is
    faked: it never touches the DB, it only needs to exist and close."""

    async def execute(self, stmt):
        return _ScalarResult(None)

    async def get(self, model, pk):
        return None

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def env(monkeypatch):
    """Production-like: a global app secret is configured, so the dev
    "allow everything" branch of verify_signature is out of the picture."""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(GLOBAL_SECRET))
    monkeypatch.setattr(wa_module, "WhatsAppClient", RecordingClient)
    # (173) The reply now runs in a BackgroundTask on its own session, and
    # (216) the debounce window is irrelevant to what this module pins:
    # which signatures are accepted and whose messages they may carry.
    monkeypatch.setattr(wa_module, "task_session", _fake_task_session(_DummySession()))
    monkeypatch.setattr(wa_module, "DEBOUNCE_SECONDS", 0)
    RecordingClient.instances = []

    handled: list = []

    async def fake_handle(db, payload):
        handled.append(payload)
        return SimpleNamespace(
            reply_text="Hola, dime.", conversation_id=uuid.uuid4(), handed_off=False
        )

    monkeypatch.setattr(wa_module, "handle_inbound", fake_handle)
    return SimpleNamespace(handled=handled)


@pytest.fixture
def client_for():
    def _build(agency_settings: dict) -> TestClient:
        agency = SimpleNamespace(
            id=uuid.uuid4(), slug="inmo-sol", settings=agency_settings
        )

        async def _override():
            yield FakeSession(agency)

        app.dependency_overrides[get_db] = _override
        return TestClient(app)

    yield _build
    app.dependency_overrides.pop(get_db, None)


def _post(client: TestClient, body: dict, signature: str | None):
    raw = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post("/webhooks/whatsapp", content=raw, headers=headers)


CONNECTED = {
    "whatsapp_access_token": "EAAG-agency",
    "whatsapp_phone_number_id": "pnid-agency",
    "whatsapp_app_secret": AGENCY_SECRET,
}


# ---------------------------------------------------------------------------
# verify_signature unit level
# ---------------------------------------------------------------------------


def test_verify_signature_accepts_explicit_secret(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(GLOBAL_SECRET))
    raw = b'{"a":1}'
    assert verify_signature(raw, _sign(raw, AGENCY_SECRET), AGENCY_SECRET) is True
    # The global secret must not validate a payload signed by the agency app.
    assert verify_signature(raw, _sign(raw, AGENCY_SECRET)) is False


def test_verify_signature_falls_back_to_global_when_secret_empty(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(GLOBAL_SECRET))
    raw = b'{"a":1}'
    assert verify_signature(raw, _sign(raw, GLOBAL_SECRET), "") is True
    assert verify_signature(raw, _sign(raw, GLOBAL_SECRET), None) is True


def test_verify_signature_rejects_missing_or_malformed_header(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(GLOBAL_SECRET))
    raw = b'{"a":1}'
    assert verify_signature(raw, None, AGENCY_SECRET) is False
    assert verify_signature(raw, "sha1=deadbeef", AGENCY_SECRET) is False
    assert verify_signature(raw, "sha256=", AGENCY_SECRET) is False


# ---------------------------------------------------------------------------
# GET handshake
# ---------------------------------------------------------------------------


def test_verify_handshake_accepts_the_configured_token(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_verify_token", SecretStr("verify-me"))
    resp = TestClient(app).get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "12345",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "12345"


def test_verify_handshake_rejects_everyone_when_no_token_is_configured(monkeypatch):
    """An empty verify token used to match an empty query value, which would
    let anyone point their own Meta app at our callback URL."""
    monkeypatch.setattr(settings, "whatsapp_verify_token", SecretStr(""))
    resp = TestClient(app).get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "x"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Route level
# ---------------------------------------------------------------------------


def test_webhook_accepts_agency_signature(env, client_for):
    body = _body()
    resp = _post(client_for(CONNECTED), body, _sign(json.dumps(body).encode(), AGENCY_SECRET))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert len(env.handled) == 1


def test_webhook_rejects_wrong_signature(env, client_for):
    body = _body()
    resp = _post(
        client_for(CONNECTED), body, _sign(json.dumps(body).encode(), "not-the-secret")
    )
    assert resp.status_code == 401
    # Nothing ran: no agent, no client, no reply.
    assert env.handled == []
    assert RecordingClient.instances == []


def test_webhook_rejects_a_connected_number_without_its_own_secret(env, client_for):
    """(217) This used to be the documented fallback: an agency that stored
    no secret was validated with the global one.

    It was wrong in the only scenario the product actually ships. The
    dashboard guide has every agency create its *own* Meta app, and Meta
    signs that app's webhooks with that app's secret — so the global check
    could only ever answer "no", and it did, silently, on every message,
    while the wizard showed the step in green. The fallback did not make
    those agencies work; it made their failure invisible.

    So an agency-owned number with no stored secret is now a rejection with a
    log that names the problem. POST /channels/whatsapp requires the secret,
    which means only connections made before 217 can be in this state, and
    those need a human to reconnect them — not a guess that cannot be right.
    """
    body = _body()
    settings_without_secret = {
        k: v for k, v in CONNECTED.items() if k != "whatsapp_app_secret"
    }
    resp = _post(
        client_for(settings_without_secret),
        body,
        _sign(json.dumps(body).encode(), GLOBAL_SECRET),
    )
    assert resp.status_code == 401
    assert env.handled == []
    assert RecordingClient.instances == []


def test_global_signature_is_rejected_for_agency_with_own_secret(env, client_for):
    """The agency's own secret is exclusive.

    The global secret is shared by every tenant that connected through our
    Meta app; if it also validated webhooks for an agency running its own
    app, anyone who ever obtains it (leak, ex-employee, another tenant's
    integration) could forge leads into precisely the tenant that isolated
    itself. Signed-by-us is not signed-by-them.
    """
    body = _body()
    resp = _post(
        client_for(CONNECTED), body, _sign(json.dumps(body).encode(), GLOBAL_SECRET)
    )
    assert resp.status_code == 401
    assert env.handled == []
    assert RecordingClient.instances == []


def test_global_signature_cannot_smuggle_a_message_into_a_protected_agency(
    env, monkeypatch
):
    """Two changes in one webhook: the first for *our own* number (which the
    global signature legitimately vouches for), the second for an agency
    running its own Meta app. The second must not be processed.

    (217) The first change used to be a secretless agency's own number; that
    combination no longer reaches this code at all — it is a 401 — so the
    scenario is rebuilt on the one case the global secret still speaks for.
    """
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "pnid-ours")
    on_our_number = SimpleNamespace(
        id=uuid.uuid4(),
        slug="abierta",
        # No number of its own: this agency is served by our shared number.
        settings={},
    )
    protected = SimpleNamespace(
        id=uuid.uuid4(),
        slug="protegida",
        settings={
            "whatsapp_access_token": "EAAG-protected",
            "whatsapp_phone_number_id": "pnid-protected",
            "whatsapp_app_secret": AGENCY_SECRET,
        },
    )
    by_pnid = {"pnid-ours": on_our_number, "pnid-protected": protected}

    async def _override():
        yield RoutingSession(by_pnid)

    app.dependency_overrides[get_db] = _override
    try:
        body = _body("pnid-ours")
        smuggled = _body("pnid-protected")["entry"][0]
        smuggled["changes"][0]["value"]["messages"][0]["id"] = "wamid.SMUGGLED"
        body["entry"].append(smuggled)

        resp = _post(TestClient(app), body, _sign(json.dumps(body).encode(), GLOBAL_SECRET))
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    # Only the message for the number the global secret vouches for ran.
    assert [p.agency_id for p in env.handled] == [on_our_number.id]


def test_agency_signature_cannot_reach_another_agency(env, monkeypatch):
    """Signed with agency A's secret, but carrying a message addressed to
    agency B's number: B's conversation must not be touched."""
    attacker = SimpleNamespace(
        id=uuid.uuid4(),
        slug="atacante",
        settings={
            "whatsapp_access_token": "EAAG-attacker",
            "whatsapp_phone_number_id": "pnid-attacker",
            "whatsapp_app_secret": AGENCY_SECRET,
        },
    )
    victim = SimpleNamespace(
        id=uuid.uuid4(),
        slug="victima",
        settings={
            "whatsapp_access_token": "EAAG-victim",
            "whatsapp_phone_number_id": "pnid-victim",
        },
    )
    by_pnid = {"pnid-attacker": attacker, "pnid-victim": victim}

    async def _override():
        yield RoutingSession(by_pnid)

    app.dependency_overrides[get_db] = _override
    try:
        body = _body("pnid-attacker")
        foreign = _body("pnid-victim")["entry"][0]
        foreign["changes"][0]["value"]["messages"][0]["id"] = "wamid.FOREIGN"
        body["entry"].append(foreign)

        resp = _post(TestClient(app), body, _sign(json.dumps(body).encode(), AGENCY_SECRET))
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    assert [p.agency_id for p in env.handled] == [attacker.id]


def test_our_own_number_still_validates_globally_for_an_agency_with_a_secret(
    env, client_for, monkeypatch
):
    """The agency's secret speaks for *its* number. A lead texting the number
    we own is our app's webhook, signed with our secret, and must keep
    working — otherwise adding a secret silently kills the shared number."""
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "pnid-ours")
    body = _body("pnid-ours")
    resp = _post(
        client_for(CONNECTED), body, _sign(json.dumps(body).encode(), GLOBAL_SECRET)
    )
    assert resp.status_code == 200
    assert len(env.handled) == 1


def test_webhook_rejects_missing_signature_header(env, client_for):
    resp = _post(client_for(CONNECTED), _body(), None)
    assert resp.status_code == 401
    assert env.handled == []


def test_missing_signature_does_no_database_work(env, monkeypatch):
    """The endpoint is exempt from the rate limiter, so an unsigned flood
    must not reach the database at all."""

    class ExplodingSession:
        async def execute(self, stmt):
            raise AssertionError("no query may run before the signature validates")

        async def commit(self):
            pass

        async def rollback(self):
            pass

    async def _override():
        yield ExplodingSession()

    app.dependency_overrides[get_db] = _override
    try:
        resp = _post(TestClient(app), _body(), None)
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert resp.status_code == 401


def test_webhook_rejects_invalid_json_as_unauthenticated(env, client_for):
    """An unparseable body names no tenant, so no per-agency secret can be
    looked up: it is unauthenticated, not merely malformed. Answering 400
    here would tell an attacker which payloads we manage to parse."""
    resp = client_for(CONNECTED).post(
        "/webhooks/whatsapp",
        content=b"not json at all",
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(b"not json at all", AGENCY_SECRET),
        },
    )
    assert resp.status_code == 401
    assert env.handled == []


def test_invalid_json_from_our_own_app_is_a_400(env, client_for):
    """Authenticated by the global secret and still unusable: that is our bug
    or Meta's, and it deserves a 4xx that says so."""
    raw = b"not json at all"
    resp = client_for({}).post(
        "/webhooks/whatsapp",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(raw, GLOBAL_SECRET),
        },
    )
    assert resp.status_code == 400
    assert env.handled == []


def test_webhook_rejects_malformed_signature_header_without_touching_the_db(env):
    """"sha256=" + 64 hex or nothing: the shape gate runs before the body is
    parsed, so junk headers cost one regex and no query."""

    class ExplodingSession:
        async def execute(self, stmt):
            raise AssertionError("no query may run for a malformed signature")

        async def commit(self):
            pass

        async def rollback(self):
            pass

    async def _override():
        yield ExplodingSession()

    app.dependency_overrides[get_db] = _override
    try:
        client = TestClient(app)
        for header in ("sha256=deadbeef", "sha1=" + "a" * 64, "a" * 64, "sha256="):
            assert _post(client, _body(), header).status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert env.handled == []


def test_signature_is_checked_over_the_exact_raw_body(env, client_for):
    """Re-serializing the parsed body would change the bytes and break real
    Meta signatures — the check must use the raw request body."""
    # Odd spacing on purpose; no "messages" key, so it parses to nothing.
    raw = (
        b'{"entry": [ {"changes": [ {"value":'
        b' {"metadata": {"phone_number_id": "pnid-agency"}} } ] } ]}'
    )
    resp = client_for(CONNECTED).post(
        "/webhooks/whatsapp",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(raw, AGENCY_SECRET),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored"}
