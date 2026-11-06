"""Item 194 — CRM webhooks you can actually verify.

Three holes, one file:

- the secret was truncated to 8 characters in every response, so the CRM
  could never check the HMAC signature. It now comes back in full exactly
  once, in the reply to the POST that created the subscription;
- there was no way to try an endpoint without waiting for a real lead.
  `POST /{id}/test` sends a signed event now and reports the answer;
- a subscription auto-disabled after repeated failures died in silence.
  The dispatcher emails the agency when it happens.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.settings import settings
from app.db.session import get_db
from app.main import app
from app.services import event_dispatcher as dispatcher
from app.services import url_guard
from app.services.event_dispatcher import (
    DISABLE_AFTER_FAILURES,
    TEST_EVENT,
    build_delivery,
    build_webhook_disabled_email_html,
    record_delivery_failure,
    record_delivery_success,
)
from app.services.secret_store import decrypt_secret, encrypt_secret

AGENCY_ID = uuid.uuid4()
SECRET = "s3cret-signing-key"


def _sub(**overrides):
    base = dict(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        target_url="https://tu-crm.es/avisos-selai",
        events=["lead.created"],
        secret=SECRET,
        is_active=True,
        consecutive_failures=0,
        last_success_at=None,
        last_failure_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# The wire format
# ---------------------------------------------------------------------------


def test_the_signature_covers_the_bytes_we_actually_send():
    """Re-serializing the body on the way out would move a space and every
    signature check on the customer's side would start failing."""
    body, headers = build_delivery(
        event="lead.created", agency_id=AGENCY_ID, payload={"lead_id": "1"}, secret=SECRET
    )
    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert headers["X-Realtor-Signature"] == f"sha256={expected}"
    assert headers["X-Realtor-Event"] == "lead.created"
    assert headers["Content-Type"] == "application/json"


def test_every_delivery_carries_the_same_envelope():
    body, _headers = build_delivery(
        event=TEST_EVENT, agency_id=AGENCY_ID, payload={"hola": "mundo"}, secret=SECRET
    )
    parsed = json.loads(body)
    assert parsed["event"] == TEST_EVENT
    assert parsed["agency_id"] == str(AGENCY_ID)
    assert parsed["data"] == {"hola": "mundo"}
    # A delivery id so the receiver can drop its own duplicates.
    uuid.UUID(parsed["delivery_id"])


def test_two_deliveries_are_not_the_same_delivery():
    first, _ = build_delivery(
        event="lead.created", agency_id=AGENCY_ID, payload={}, secret=SECRET
    )
    second, _ = build_delivery(
        event="lead.created", agency_id=AGENCY_ID, payload={}, secret=SECRET
    )
    assert json.loads(first)["delivery_id"] != json.loads(second)["delivery_id"]


def test_the_test_event_is_not_subscribable():
    """Nobody subscribes to it; it only ever goes to the subscription being
    tested, so it must not show up in the panel's list of events."""
    assert TEST_EVENT not in dispatcher.SUPPORTED_EVENTS


# ---------------------------------------------------------------------------
# Failure bookkeeping + the email
# ---------------------------------------------------------------------------


class _NoopSession:
    def __init__(self, agency=None):
        self.agency = agency

    async def get(self, model, pk):
        return self.agency

    async def commit(self):
        pass


@pytest.fixture
def sent_emails(monkeypatch):
    sent: list[dict] = []

    async def _fake_send(to, subject, html):
        sent.append({"to": to, "subject": subject, "html": html})
        return True

    monkeypatch.setattr("app.services.emails.send_email", _fake_send)
    return sent


async def test_a_failure_counts_and_keeps_the_subscription_alive(sent_emails):
    sub = _sub(consecutive_failures=3)
    disabled = await record_delivery_failure(_NoopSession(), sub)
    assert disabled is False
    assert sub.consecutive_failures == 4
    assert sub.is_active is True
    assert sent_emails == []


async def test_crossing_the_limit_disables_and_tells_the_owner(sent_emails):
    agency = SimpleNamespace(id=AGENCY_ID, contact_email="dueno@inmosol.example.com")
    sub = _sub(consecutive_failures=DISABLE_AFTER_FAILURES - 1)

    disabled = await record_delivery_failure(_NoopSession(agency), sub)

    assert disabled is True
    assert sub.is_active is False
    assert len(sent_emails) == 1
    mail = sent_emails[0]
    assert mail["to"] == "dueno@inmosol.example.com"
    assert "pausado" in mail["subject"].lower()
    assert "tu-crm.es" in mail["html"]


async def test_the_owner_is_told_once_not_on_every_later_failure(sent_emails):
    """The transition happens exactly once — afterwards the deliverer skips
    inactive subscriptions — so the email cannot repeat for one outage."""
    agency = SimpleNamespace(id=AGENCY_ID, contact_email="dueno@inmosol.example.com")
    sub = _sub(consecutive_failures=DISABLE_AFTER_FAILURES + 5, is_active=False)

    disabled = await record_delivery_failure(_NoopSession(agency), sub)

    assert disabled is False
    assert sent_emails == []


async def test_a_success_clears_the_failure_run():
    sub = _sub(consecutive_failures=7)
    await record_delivery_success(_NoopSession(), sub)
    assert sub.consecutive_failures == 0
    assert sub.last_success_at is not None


def test_the_disabled_email_says_what_broke_and_what_to_do():
    html = build_webhook_disabled_email_html("https://tu-crm.es/avisos", 20)
    assert "https://tu-crm.es/avisos" in html
    assert "20" in html
    # Honest about the consequence, and about what is *not* lost.
    assert "no se ha perdido nada" in html
    assert "selai.es/setup?step=crm" in html


def test_the_disabled_email_escapes_the_url():
    """The target URL is user input and it lands inside HTML."""
    html = build_webhook_disabled_email_html(
        "https://x.es/?a=<script>alert(1)</script>", 20
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# The endpoints
# ---------------------------------------------------------------------------


class SubsSession:
    def __init__(self, sub=None):
        self.sub = sub
        self.added: list = []
        self.commits = 0
        self.rows: list = []

    async def execute(self, stmt):
        rows = list(self.rows)

        class _Rows:
            def scalars(self):
                return SimpleNamespace(all=lambda: rows)

        return _Rows()

    async def get(self, model, pk):
        if self.sub is not None and pk == self.sub.id:
            return self.sub
        return None

    def add(self, obj):
        # Mirror what the DB does for us: defaults the model declares.
        obj.id = getattr(obj, "id", None) or uuid.uuid4()
        obj.is_active = True
        obj.consecutive_failures = 0
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    async def delete(self, obj):
        pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")

    session = SubsSession()

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield SimpleNamespace(http=TestClient(app), session=session)
    app.dependency_overrides.pop(get_db, None)


def _url(path: str = "") -> str:
    return f"/api/v1/agencies/{AGENCY_ID}/webhook-subscriptions{path}"


def test_creating_a_subscription_shows_the_secret_once(client):
    resp = client.http.post(
        _url(), json={"target_url": "https://tu-crm.es/avisos", "events": ["lead.created"]}
    )
    assert resp.status_code == 201
    body = resp.json()
    full = body["secret_full_once"]
    assert full and len(full) > 8
    # And the preview is still the truncated form the list endpoint uses.
    assert body["secret_preview"].startswith(full[:8])
    assert body["secret_unreadable"] is False
    assert client.session.added[0].secret != full
    assert decrypt_secret(client.session.added[0].secret) == full


def test_listing_never_returns_the_secret(client):
    created = client.http.post(
        _url(), json={"target_url": "https://tu-crm.es/avisos", "events": ["lead.created"]}
    ).json()
    client.session.rows = [client.session.added[0]]

    listed = client.http.get(_url()).json()
    assert listed[0]["secret_full_once"] is None
    assert listed[0]["secret_preview"] != created["secret_full_once"]


def test_listing_marks_an_unreadable_signing_secret(client):
    client.session.rows = [_sub(secret="enc:v2:missing:not-a-token")]

    listed = client.http.get(_url()).json()

    assert listed[0]["secret_full_once"] is None
    assert listed[0]["secret_preview"] == "no disponible"
    assert listed[0]["secret_unreadable"] is True


def test_the_test_event_reports_a_successful_delivery(client, monkeypatch):
    sub = _sub()
    client.session.sub = sub
    calls: list[dict] = []

    async def _fake_deliver(subscription, event, payload, **kwargs):
        calls.append({"sub": subscription, "event": event, "payload": payload})
        return True, 200, None

    monkeypatch.setattr("app.api.webhooks_admin.deliver_now", _fake_deliver)

    resp = client.http.post(_url(f"/{sub.id}/test"))
    assert resp.status_code == 200
    assert resp.json() == {
        "delivered": True,
        "status_code": 200,
        "error": None,
        "is_active": True,
        "consecutive_failures": 0,
    }
    assert calls[0]["event"] == TEST_EVENT


def test_a_failing_test_event_is_reported_and_counted(client, monkeypatch):
    sub = _sub(consecutive_failures=2)
    client.session.sub = sub

    async def _fake_deliver(subscription, event, payload, **kwargs):
        return False, 500, "http_500"

    monkeypatch.setattr("app.api.webhooks_admin.deliver_now", _fake_deliver)

    body = client.http.post(_url(f"/{sub.id}/test")).json()
    assert body["delivered"] is False
    assert body["status_code"] == 500
    assert body["error"] == "http_500"
    # A test that keeps failing is a dead endpoint; pretending otherwise
    # would leave a broken subscription looking healthy.
    assert body["consecutive_failures"] == 3


def test_testing_another_agencys_subscription_is_a_404(client, monkeypatch):
    sub = _sub(agency_id=uuid.uuid4())
    client.session.sub = sub

    async def _fake_deliver(subscription, event, payload, **kwargs):
        raise AssertionError("must not deliver across tenants")

    monkeypatch.setattr("app.api.webhooks_admin.deliver_now", _fake_deliver)

    assert client.http.post(_url(f"/{sub.id}/test")).status_code == 404


# ---------------------------------------------------------------------------
# The target the button points at
# ---------------------------------------------------------------------------


@pytest.fixture
def dns(monkeypatch):
    """Pin name resolution: the guard is what is under test, not the DNS of
    whoever runs the suite."""
    mapping = {"tu-crm.es": ["93.184.216.34"]}

    class _Loop:
        async def getaddrinfo(self, host, port, **kwargs):
            addresses = mapping.get(host)
            if addresses is None:
                raise OSError("NXDOMAIN")
            return [(2, 1, 6, "", (addr, 0)) for addr in addresses]

    monkeypatch.setattr(url_guard.asyncio, "get_running_loop", lambda: _Loop())
    return mapping


async def test_the_test_button_refuses_to_call_the_metadata_endpoint(dns, monkeypatch):
    """"Enviar evento de prueba" is a request we issue from inside our own
    network at an address the customer typed. 169.254.169.254 answers with
    the machine's cloud credentials to anything that will call it."""

    async def _no_network(*args, **kwargs):
        raise AssertionError("the request must never leave the process")

    monkeypatch.setattr(dispatcher.httpx, "AsyncClient", _no_network)

    ok, status_code, error = await dispatcher.deliver_now(
        _sub(target_url="http://169.254.169.254/latest/meta-data/"), TEST_EVENT, {}
    )
    assert (ok, status_code, error) == (False, None, "blocked_target_url")


async def test_the_test_button_refuses_localhost_and_the_lan(dns, monkeypatch):
    async def _no_network(*args, **kwargs):
        raise AssertionError("the request must never leave the process")

    monkeypatch.setattr(dispatcher.httpx, "AsyncClient", _no_network)

    for url in (
        "http://127.0.0.1:5432/",
        "http://[::1]/hook",
        "http://10.0.0.7/hook",
        "file:///etc/passwd",
    ):
        ok, _status, error = await dispatcher.deliver_now(_sub(target_url=url), TEST_EVENT, {})
        assert (ok, error) == (False, "blocked_target_url"), url


async def test_a_public_target_is_delivered_and_not_followed(dns, monkeypatch):
    seen: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            seen["kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content, headers, extensions):
            seen["url"] = url
            seen["headers"] = headers
            seen["extensions"] = extensions
            return SimpleNamespace(status_code=204)

    monkeypatch.setattr(dispatcher.httpx, "AsyncClient", _FakeClient)

    ok, status_code, error = await dispatcher.deliver_now(
        _sub(target_url="https://tu-crm.es/avisos-selai"), TEST_EVENT, {"hola": "mundo"}
    )
    assert (ok, status_code, error) == (True, 204, None)
    # DNS is resolved once by the guard.  The actual socket goes to that
    # validated address, while Host + SNI preserve routing and certificate
    # verification for the customer's hostname.
    assert seen["url"] == "https://93.184.216.34:443/avisos-selai"
    assert seen["headers"]["Host"] == "tu-crm.es"
    assert seen["extensions"] == {"sni_hostname": "tu-crm.es"}
    # A 302 from the customer's endpoint would be a second target nobody
    # checked, and a webhook has nothing to follow anyway.
    assert seen["kwargs"]["follow_redirects"] is False
    assert seen["headers"]["X-Realtor-Event"] == TEST_EVENT


async def test_dns_rebinding_cannot_change_the_address_after_validation(monkeypatch):
    """The second DNS answer is private, but delivery never asks for it.

    A hostname request here would make httpx resolve again after the guard and
    let the attacker switch to loopback/LAN metadata.  The wire URL must carry
    the first, validated public address instead.
    """
    dns_calls: list[str] = []

    class _Loop:
        async def getaddrinfo(self, host, port, **kwargs):
            dns_calls.append(host)
            address = "93.184.216.34" if len(dns_calls) == 1 else "169.254.169.254"
            return [(2, 1, 6, "", (address, 0))]

    monkeypatch.setattr(url_guard.asyncio, "get_running_loop", lambda: _Loop())
    seen: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content, headers, extensions):
            seen.update(url=url, headers=headers, extensions=extensions)
            return SimpleNamespace(status_code=204)

    monkeypatch.setattr(dispatcher.httpx, "AsyncClient", _FakeClient)
    result = await dispatcher.deliver_now(
        _sub(target_url="https://rebind.example/webhook"), TEST_EVENT, {}
    )

    assert result == (True, 204, None)
    assert dns_calls == ["rebind.example"]
    assert seen["url"] == "https://93.184.216.34:443/webhook"
    assert seen["headers"]["Host"] == "rebind.example"
    assert seen["extensions"] == {"sni_hostname": "rebind.example"}


async def test_an_encrypted_signing_secret_is_decrypted_only_for_delivery(
    dns, monkeypatch
):
    seen: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content, headers, extensions):
            seen["body"] = content
            seen["signature"] = headers["X-Realtor-Signature"]
            return SimpleNamespace(status_code=204)

    monkeypatch.setattr(dispatcher.httpx, "AsyncClient", _FakeClient)
    encrypted = encrypt_secret(SECRET)

    result = await dispatcher.deliver_now(
        _sub(secret=encrypted), TEST_EVENT, {"hola": "mundo"}
    )

    expected = hmac.new(SECRET.encode(), seen["body"], hashlib.sha256).hexdigest()
    assert result == (True, 204, None)
    assert seen["signature"] == f"sha256={expected}"


async def test_an_unreadable_signing_secret_fails_before_any_network(monkeypatch):
    async def _no_network(*args, **kwargs):
        raise AssertionError("must not resolve or send with an unreadable secret")

    monkeypatch.setattr(dispatcher, "resolve_public_target", _no_network)

    result = await dispatcher.deliver_now(
        _sub(secret="enc:v2:missing:not-a-token"), TEST_EVENT, {}
    )

    assert result == (False, None, "signing_secret_unavailable")


def test_a_subscription_pointing_at_the_box_itself_is_refused_on_creation(client):
    """Caught while the person is still looking at the form, without asking
    DNS: a name that does not resolve yet is a normal thing to configure."""
    resp = client.http.post(
        _url(), json={"target_url": "http://127.0.0.1:8000/hook", "events": ["lead.created"]}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "blocked_target_url"
    assert client.session.added == []


def test_a_hostname_that_does_not_resolve_yet_can_still_be_configured(client):
    """The CRM may not be deployed the day the agency sets this up. The
    resolving check runs at delivery, where the request actually leaves."""
    resp = client.http.post(
        _url(), json={"target_url": "https://crm-que-aun-no-existe.es/hook", "events": ["lead.created"]}
    )
    assert resp.status_code == 201
