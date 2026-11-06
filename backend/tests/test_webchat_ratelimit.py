"""Item 171 — the public web chat is rate limited.

Two independent axes:
- Per IP, in RateLimitMiddleware: `/webhooks/web/` is no longer exempt and
  gets a tighter budget than the rest of the API. Signed provider webhooks
  (Meta, Twilio, Vapi) stay exempt.
- Per session, in app/api/web_chat.py: one browser session can't burn the
  LLM budget from rotating IPs.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import web_chat as web_chat_module
from app.core.middleware import RateLimitMiddleware
from app.core.settings import settings
from app.db.session import get_db
from app.main import app

SLUG = "inmo-sol"


# ---------------------------------------------------------------------------
# Per-IP limit (middleware), on an isolated app so buckets never leak
# between tests.
# ---------------------------------------------------------------------------


@pytest.fixture
def limited_client():
    mini = FastAPI()
    mini.add_middleware(RateLimitMiddleware, max_per_window=50, web_chat_max_per_window=3)

    @mini.get("/webhooks/web/{slug}/message")
    async def _web(slug: str):
        return {"ok": slug}

    @mini.get("/webhooks/web/widget.js")
    async def _widget():
        return {"ok": "widget"}

    @mini.get("/webhooks/whatsapp")
    async def _whatsapp():
        return {"ok": "wa"}

    @mini.get("/api/v1/whatever")
    async def _api():
        return {"ok": "api"}

    return TestClient(mini)


def test_web_chat_is_rate_limited_per_ip(limited_client):
    path = f"/webhooks/web/{SLUG}/message"
    for _ in range(3):
        assert limited_client.get(path).status_code == 200
    resp = limited_client.get(path)
    assert resp.status_code == 429
    assert resp.text == "rate_limited"


def test_web_chat_limit_is_per_ip_not_global(limited_client):
    path = f"/webhooks/web/{SLUG}/message"
    for _ in range(3):
        assert limited_client.get(path).status_code == 200
    assert limited_client.get(path).status_code == 429

    other_ip = TestClient(limited_client.app, client=("203.0.113.9", 1234))
    assert other_ip.get(path).status_code == 200


def test_web_chat_limit_covers_every_agency_slug(limited_client):
    """Budget is per IP, not per slug — otherwise the limit is bypassed by
    rotating the agency in the URL."""
    for slug in ("a-inmo", "b-inmo", "c-inmo"):
        assert limited_client.get(f"/webhooks/web/{slug}/message").status_code == 200
    assert limited_client.get("/webhooks/web/d-inmo/message").status_code == 429


def test_widget_js_keeps_the_default_budget(limited_client):
    """A static asset with no cost: a whole office behind one NAT IP must
    still be able to load it."""
    for _ in range(10):
        assert limited_client.get("/webhooks/web/widget.js").status_code == 200


def test_signed_provider_webhooks_stay_exempt(limited_client):
    for _ in range(10):
        assert limited_client.get("/webhooks/whatsapp").status_code == 200


def test_rest_of_the_api_keeps_the_default_budget(limited_client):
    for _ in range(10):
        assert limited_client.get("/api/v1/whatever").status_code == 200


def test_signed_whatsapp_webhook_survives_a_meta_burst():
    """Meta batches and retries: a legitimate burst well past the default
    budget must go through untouched. Its authentication is the HMAC."""
    mini = FastAPI()
    mini.add_middleware(RateLimitMiddleware, max_per_window=5, web_chat_max_per_window=2)

    @mini.post("/webhooks/whatsapp")
    async def _wa():
        return {"ok": True}

    client = TestClient(mini)
    for _ in range(50):
        assert client.post("/webhooks/whatsapp", json={}).status_code == 200


# ---------------------------------------------------------------------------
# Whose IP is it anyway (X-Forwarded-For)
# ---------------------------------------------------------------------------


def _xff_app(**kwargs) -> FastAPI:
    mini = FastAPI()
    mini.add_middleware(RateLimitMiddleware, max_per_window=3, **kwargs)

    @mini.get("/api/v1/whatever")
    async def _api():
        return {"ok": True}

    return mini


def test_forwarded_for_is_ignored_by_default():
    """No proxy declared: the header is decoration. Honouring it would hand
    every caller an unlimited supply of fresh buckets."""
    client = TestClient(_xff_app())
    for _ in range(3):
        assert client.get("/api/v1/whatever").status_code == 200
    for spoofed in ("1.2.3.4", "5.6.7.8", "9.10.11.12"):
        resp = client.get("/api/v1/whatever", headers={"X-Forwarded-For": spoofed})
        assert resp.status_code == 429


def test_forwarded_for_is_read_from_the_right_when_a_proxy_is_declared():
    """One proxy in front: it appends the address it saw, so the last entry
    is ours and everything to its left is whatever the client made up."""
    client = TestClient(_xff_app(trusted_proxy_hops=1))
    for _ in range(3):
        assert (
            client.get(
                "/api/v1/whatever", headers={"X-Forwarded-For": "spoof, 198.51.100.7"}
            ).status_code
            == 200
        )
    # Same real client, a different lie prepended: same bucket, still spent.
    resp = client.get(
        "/api/v1/whatever", headers={"X-Forwarded-For": "other-lie, 198.51.100.7"}
    )
    assert resp.status_code == 429
    # A genuinely different client is unaffected by the first one's budget.
    resp = client.get("/api/v1/whatever", headers={"X-Forwarded-For": "198.51.100.8"})
    assert resp.status_code == 200


def test_forwarded_for_split_across_headers_is_joined_in_order():
    """Proxies may emit several X-Forwarded-For headers instead of one list;
    reading only the first would leave the count off by a hop."""
    client = TestClient(_xff_app(trusted_proxy_hops=1))
    headers = [("x-forwarded-for", "spoof"), ("x-forwarded-for", "198.51.100.9")]
    for _ in range(3):
        assert client.get("/api/v1/whatever", headers=headers).status_code == 200
    assert client.get("/api/v1/whatever", headers=headers).status_code == 429
    # The spoofed left-hand value never became a bucket of its own.
    assert client.get("/api/v1/whatever", headers={"X-Forwarded-For": "spoof"}).status_code == 200


def test_proxy_hops_fall_back_to_the_peer_without_the_header():
    """Health checks and anything reaching the app directly still get a
    bucket instead of sharing an "unknown" one."""
    mini = _xff_app(trusted_proxy_hops=1)
    client = TestClient(mini, client=("203.0.113.5", 1234))
    other = TestClient(mini, client=("203.0.113.6", 1234))
    for _ in range(3):
        assert client.get("/api/v1/whatever").status_code == 200
    assert client.get("/api/v1/whatever").status_code == 429
    assert other.get("/api/v1/whatever").status_code == 200


# ---------------------------------------------------------------------------
# Per-session limit (app/api/web_chat.py) against the real endpoint.
# ---------------------------------------------------------------------------


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, agency):
        self.agency = agency

    async def execute(self, stmt):
        return _ScalarResult(self.agency)

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def web_client(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    web_chat_module._session_hits.clear()

    handled: list = []

    async def fake_handle(db, payload):
        handled.append(payload)
        return SimpleNamespace(
            reply_text="Hola, dime.", conversation_id=uuid.uuid4(), handed_off=False
        )

    monkeypatch.setattr(web_chat_module, "handle_inbound", fake_handle)

    agency = SimpleNamespace(id=uuid.uuid4(), slug=SLUG, settings={})

    async def _override():
        yield FakeSession(agency)

    app.dependency_overrides[get_db] = _override
    # A dedicated source IP per test run: the middleware buckets live on the
    # shared app instance and must not leak between tests.
    client = TestClient(app, client=(f"198.51.100.{uuid.uuid4().int % 200 + 1}", 4242))
    yield SimpleNamespace(client=client, handled=handled)
    app.dependency_overrides.pop(get_db, None)
    web_chat_module._session_hits.clear()


def _send(client, session_id: str, text: str = "Hola"):
    return client.post(
        f"/webhooks/web/{SLUG}/message",
        json={"session_id": session_id, "text": text},
    )


def test_session_is_rate_limited(web_client):
    session = "sess-" + uuid.uuid4().hex
    for _ in range(web_chat_module.SESSION_MAX_PER_WINDOW):
        assert _send(web_client.client, session).status_code == 200

    resp = _send(web_client.client, session)
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"
    # The agent never ran for the blocked message: no LLM cost, no junk lead.
    assert len(web_client.handled) == web_chat_module.SESSION_MAX_PER_WINDOW


def test_other_sessions_are_unaffected(web_client):
    session = "sess-" + uuid.uuid4().hex
    for _ in range(web_chat_module.SESSION_MAX_PER_WINDOW):
        _send(web_client.client, session)
    assert _send(web_client.client, session).status_code == 429

    assert _send(web_client.client, "sess-" + uuid.uuid4().hex).status_code == 200


def test_client_message_id_becomes_a_stable_provider_id(web_client, monkeypatch):
    """A browser retry carries the exact same de-duplication key."""
    async def no_existing_reply(_db, _agency_id, _external_message_id):
        return None

    monkeypatch.setattr(web_chat_module, "_replay_web_reply", no_existing_reply)
    session = "sess-" + uuid.uuid4().hex
    message_id = "msg-" + uuid.uuid4().hex
    response = web_client.client.post(
        f"/webhooks/web/{SLUG}/message",
        json={"session_id": session, "message_id": message_id, "text": "Hola"},
    )

    assert response.status_code == 200
    assert web_client.handled[-1].external_message_id == f"web:{session}:{message_id}"


def test_replayed_message_skips_agent_and_rate_limit(web_client, monkeypatch):
    session = "sess-" + uuid.uuid4().hex
    message_id = "msg-" + uuid.uuid4().hex

    async def existing_reply(_db, _agency_id, external_message_id):
        assert external_message_id == f"web:{session}:{message_id}"
        return "Respuesta ya guardada"

    monkeypatch.setattr(web_chat_module, "_replay_web_reply", existing_reply)
    response = web_client.client.post(
        f"/webhooks/web/{SLUG}/message",
        json={"session_id": session, "message_id": message_id, "text": "Hola"},
    )

    assert response.status_code == 200
    assert response.json() == {"reply": "Respuesta ya guardada"}
    assert web_client.handled == []
    assert session not in web_chat_module._session_hits


def test_tracked_sessions_stay_bounded():
    """The session id is chosen by the browser, so a flood of new ones must
    cost bounded memory (and bounded work per request)."""
    web_chat_module._session_hits.clear()
    for i in range(web_chat_module._MAX_TRACKED_SESSIONS + 500):
        web_chat_module._session_rate_limited(f"flood-{i}", now=1_000.0)
    assert len(web_chat_module._session_hits) <= web_chat_module._MAX_TRACKED_SESSIONS
    web_chat_module._session_hits.clear()


def test_a_blocked_session_cannot_evict_itself_with_new_ones():
    """Eviction is least-recently-seen: whoever keeps knocking stays at the
    front of the queue and stays blocked, however many ids they burn."""
    web_chat_module._session_hits.clear()
    now = 1_000.0
    abuser = "sess-abuser"
    for _ in range(web_chat_module.SESSION_MAX_PER_WINDOW):
        web_chat_module._session_rate_limited(abuser, now=now)
    assert web_chat_module._session_rate_limited(abuser, now=now) is True

    for i in range(web_chat_module._MAX_TRACKED_SESSIONS + 500):
        web_chat_module._session_rate_limited(f"flood-{i}", now=now)
        if i % 100 == 0:
            assert web_chat_module._session_rate_limited(abuser, now=now) is True

    assert web_chat_module._session_rate_limited(abuser, now=now) is True
    web_chat_module._session_hits.clear()


def test_session_window_expires(monkeypatch):
    """The budget is a rolling window, not a permanent ban."""
    web_chat_module._session_hits.clear()
    session = "sess-window"
    now = 1_000.0
    for _ in range(web_chat_module.SESSION_MAX_PER_WINDOW):
        assert web_chat_module._session_rate_limited(session, now=now) is False
    assert web_chat_module._session_rate_limited(session, now=now) is True

    later = now + web_chat_module.SESSION_WINDOW_SECONDS + 1
    assert web_chat_module._session_rate_limited(session, now=later) is False
    web_chat_module._session_hits.clear()
