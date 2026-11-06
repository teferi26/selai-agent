"""POST /api/v1/support — the in-app support form (app/api/support.py).

`require_auth` and `get_db` are replaced through `dependency_overrides` so
each test can choose exactly who is writing (a user with an agency, a user
whose agency does not load, a user with no agency at all) without a real
Postgres or a real Clerk. `send_email` and the module logger are
monkeypatched: no network, and the log becomes assertable — structlog prints
to stdout, so `caplog` would see nothing.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import support as support_module
from app.core.auth import AuthContext, require_auth
from app.db.session import get_db
from app.main import app
from app.services.emails import (
    INTERNAL_EMAIL,
    build_support_ticket_email_html,
    category_for,
)

AGENCY_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def _agency() -> SimpleNamespace:
    return SimpleNamespace(
        id=AGENCY_ID,
        name="Inmo Sol",
        slug="inmo-sol",
        contact_email="dueno@inmosol.example.com",
        settings={},
    )


def _user(email: str = "marta@inmosol.example.com") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        email=email,
        full_name="Marta García",
        agency_id=AGENCY_ID,
        is_active=True,
    )


def _auth(user: SimpleNamespace | None, email: str | None = "marta@inmosol.example.com") -> AuthContext:
    return AuthContext(
        clerk_user_id="user_" + uuid.uuid4().hex[:8],
        email=email,
        raw_claims={},
        user=user,
    )


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """Answers the only two things the endpoint asks the DB: the agency by id
    and the subscription/plan behind it. `boom` makes both explode, which is
    the "my agency does not load" case the form exists for."""

    def __init__(self, agency=None, subscription=None, plan=None):
        self.agency = agency
        self.subscription = subscription
        self.plan = plan
        self.boom = False

    async def get(self, model, pk):
        if self.boom:
            raise RuntimeError("db is down")
        name = getattr(model, "__name__", "")
        if name == "Agency":
            return self.agency if self.agency is not None and self.agency.id == pk else None
        if name == "Plan":
            return self.plan
        raise AssertionError(f"unexpected get: {name}")

    async def execute(self, stmt):
        if self.boom:
            raise RuntimeError("db is down")
        return _ScalarResult(self.subscription)

    async def commit(self):
        pass

    async def rollback(self):
        pass


class _RecordingLog:
    """structlog writes straight to stdout, so record the calls instead."""

    def __init__(self, sink: list):
        self._sink = sink

    def __getattr__(self, level: str):
        def _log(event: str, **kw):
            self._sink.append((level, event, kw))

        return _log


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def support(monkeypatch):
    support_module._user_hits.clear()

    sent: list[dict] = []
    outcome = {"mode": "ok"}  # "ok" | "fail" (returns False) | "raise"

    async def _fake_send(to, subject, html, category=None):
        sent.append({"to": to, "subject": subject, "html": html, "category": category})
        if outcome["mode"] == "raise":
            raise RuntimeError("resend exploded")
        return outcome["mode"] == "ok"

    monkeypatch.setattr(support_module, "send_email", _fake_send)

    logged: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(support_module, "log", _RecordingLog(logged))

    session = FakeSession(
        agency=_agency(),
        subscription=SimpleNamespace(plan_id=uuid.uuid4()),
        plan=SimpleNamespace(code="pro", name="Pro"),
    )
    ctx = {"auth": _auth(_user())}

    async def _override_auth():
        return ctx["auth"]

    async def _override_db():
        yield session

    app.dependency_overrides[require_auth] = _override_auth
    app.dependency_overrides[get_db] = _override_db
    # A dedicated source IP per test: the per-IP buckets of RateLimitMiddleware
    # live on the shared app instance.
    client = TestClient(app, client=(f"198.51.100.{uuid.uuid4().int % 200 + 1}", 4242))

    yield SimpleNamespace(
        client=client, sent=sent, outcome=outcome, ctx=ctx, session=session, logged=logged
    )

    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(get_db, None)
    support_module._user_hits.clear()


def _body(**overrides) -> dict:
    base = {
        "message": "El panel se queda en blanco al abrir Conversaciones.",
        "surface": "web",
        "screen": "/conversations",
        "app_version": "1.4.2",
        "error_ref": "digest-9f1c22",
        "contact_email": "otro@inmosol.example.com",
    }
    return {**base, **overrides}


def _post(support, **overrides):
    return support.client.post("/api/v1/support", json=_body(**overrides))


def _events(support, level: str) -> list[str]:
    return [event for lvl, event, _ in support.logged if lvl == level]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_ticket_carries_every_piece_of_context(support):
    resp = _post(support)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    assert len(support.sent) == 1
    mail = support.sent[0]
    assert mail["to"] == INTERNAL_EMAIL == "hola@selai.es"
    assert mail["subject"] == "Soporte selai · Inmo Sol"
    html = mail["html"]
    for fragment in (
        "El panel se queda en blanco al abrir Conversaciones.",
        "Marta García",
        "marta@inmosol.example.com",
        "otro@inmosol.example.com",
        "Inmo Sol",
        "inmo-sol",
        "Pro",
        "Panel web",
        "/conversations",
        "1.4.2",
        "digest-9f1c22",
    ):
        assert fragment in html, fragment
    assert "support.ticket_sent" in _events(support, "info")


def test_ticket_is_transactional_so_it_carries_no_unsubscribe_category(support):
    _post(support)
    # None → send_email adds no List-Unsubscribe headers, and no caller ever
    # filters it through the agency's email preferences.
    assert support.sent[0]["category"] is None
    assert category_for("support_ticket") is None


def test_only_message_and_surface_are_required(support):
    resp = support.client.post(
        "/api/v1/support", json={"message": "No puedo entrar", "surface": "mobile"}
    )
    assert resp.status_code == 200
    html = support.sent[0]["html"]
    assert "App móvil" in html
    assert "(no indicada)" in html  # pantalla y versión


# ---------------------------------------------------------------------------
# It has to work when the agency does not
# ---------------------------------------------------------------------------


def test_user_without_agency_still_gets_a_ticket_out(support):
    support.ctx["auth"] = _auth(None, email="sinagencia@example.es")

    resp = _post(support)
    assert resp.status_code == 200
    mail = support.sent[0]
    assert mail["subject"] == "Soporte selai · sinagencia@example.es"
    assert "(sin agencia)" in mail["html"]
    assert "(sin plan)" in mail["html"]


def test_a_broken_agency_lookup_does_not_break_the_form(support):
    support.session.boom = True

    resp = _post(support)
    assert resp.status_code == 200
    mail = support.sent[0]
    # Identity comes from the auth context, so it survives the DB failure.
    assert "marta@inmosol.example.com" in mail["html"]
    assert "(sin agencia)" in mail["html"]
    assert "support.context_unresolved" in _events(support, "exception")


def test_a_missing_agency_row_is_not_an_error(support):
    support.session.agency = None

    resp = _post(support)
    assert resp.status_code == 200
    assert "(sin agencia)" in support.sent[0]["html"]


# ---------------------------------------------------------------------------
# Email failures never reach the user
# ---------------------------------------------------------------------------


def test_email_failure_still_answers_ok_and_logs_the_ticket(support):
    support.outcome["mode"] = "fail"

    resp = _post(support)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    errors = [kw for lvl, event, kw in support.logged if event == "support.email_failed"]
    assert len(errors) == 1
    # The log is the only surviving copy, so it has to contain the message.
    assert "El panel se queda en blanco" in errors[0]["message"]
    assert errors[0]["contact_email"] == "otro@inmosol.example.com"


def test_email_raising_still_answers_ok(support):
    support.outcome["mode"] = "raise"

    resp = _post(support)
    assert resp.status_code == 200
    assert "support.email_error" in _events(support, "exception")
    assert "support.email_failed" in _events(support, "error")


# ---------------------------------------------------------------------------
# Body contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"message": "hola"},  # 4 < 5
        {"message": "x" * 4001},
        {"surface": "desktop"},
        {"screen": "s" * 201},
        {"app_version": "v" * 41},
        {"error_ref": "e" * 201},
        {"contact_email": "a" * 201},
    ],
    ids=[
        "message_too_short",
        "message_too_long",
        "unknown_surface",
        "screen_too_long",
        "version_too_long",
        "error_ref_too_long",
        "contact_email_too_long",
    ],
)
def test_invalid_bodies_are_422(support, overrides):
    assert _post(support, **overrides).status_code == 422
    assert support.sent == []


def test_missing_message_is_422(support):
    resp = support.client.post("/api/v1/support", json={"surface": "web"})
    assert resp.status_code == 422
    assert support.sent == []


def test_boundary_lengths_are_accepted(support):
    assert _post(support, message="12345", screen="s" * 200).status_code == 200


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


def test_the_sixth_ticket_in_the_window_is_429(support):
    for _ in range(support_module.SUPPORT_MAX_PER_WINDOW):
        assert _post(support).status_code == 200

    resp = _post(support)
    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert "Espera unos minutos" in detail
    assert INTERNAL_EMAIL in detail
    # The blocked attempt sent nothing: the point is to keep the inbox usable.
    assert len(support.sent) == support_module.SUPPORT_MAX_PER_WINDOW


def test_the_limit_is_per_user(support):
    for _ in range(support_module.SUPPORT_MAX_PER_WINDOW):
        _post(support)
    assert _post(support).status_code == 429

    support.ctx["auth"] = _auth(_user("otro@inmosol.example.com"))
    assert _post(support).status_code == 200


def test_the_window_is_rolling_not_a_ban():
    support_module._user_hits.clear()
    key = "user:rolling"
    now = 1_000.0
    for _ in range(support_module.SUPPORT_MAX_PER_WINDOW):
        assert support_module._user_rate_limited(key, now=now) is False
    assert support_module._user_rate_limited(key, now=now) is True

    later = now + support_module.SUPPORT_WINDOW_SECONDS + 1
    assert support_module._user_rate_limited(key, now=later) is False
    support_module._user_hits.clear()


def test_tracked_users_stay_bounded():
    support_module._user_hits.clear()
    for i in range(support_module._MAX_TRACKED_USERS + 200):
        support_module._user_rate_limited(f"user:{i}", now=1_000.0)
    assert len(support_module._user_hits) <= support_module._MAX_TRACKED_USERS
    support_module._user_hits.clear()


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


def test_builder_subject_prefers_the_agency_then_the_email():
    subject, _ = build_support_ticket_email_html(
        message="hola qué tal", surface="web", agency_name="Inmo Sol", user_email="a@b.es"
    )
    assert subject == "Soporte selai · Inmo Sol"

    subject, _ = build_support_ticket_email_html(
        message="hola qué tal", surface="web", user_email="a@b.es"
    )
    assert subject == "Soporte selai · a@b.es"

    subject, _ = build_support_ticket_email_html(message="hola qué tal", surface="mobile")
    assert subject == "Soporte selai · usuario sin identificar"


def test_builder_renders_the_reply_address_as_a_link():
    _, html = build_support_ticket_email_html(
        message="hola qué tal",
        surface="web",
        user_email="marta@inmosol.example.com",
        contact_email="otro@inmosol.example.com",
    )
    # The email arrives from our own inbox, so "reply" would answer ourselves.
    assert 'href="mailto:otro@inmosol.example.com"' in html


def test_builder_falls_back_to_the_account_email_when_there_is_no_contact():
    _, html = build_support_ticket_email_html(
        message="hola qué tal", surface="web", user_email="marta@inmosol.example.com"
    )
    assert 'href="mailto:marta@inmosol.example.com"' in html


def test_builder_escapes_the_message():
    _, html = build_support_ticket_email_html(
        message="al pulsar <b>Guardar</b> sale <script>alert(1)</script> & nada",
        surface="web",
    )
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&amp; nada" in html


def test_builder_keeps_line_breaks():
    _, html = build_support_ticket_email_html(message="línea 1\nlínea 2", surface="web")
    assert "línea 1<br>línea 2" in html


def test_builder_normalises_windows_line_breaks():
    _, html = build_support_ticket_email_html(message="línea 1\r\nlínea 2", surface="web")
    assert "línea 1<br>línea 2" in html
    assert "\r" not in html


def test_builder_escapes_the_context_fields_too():
    _, html = build_support_ticket_email_html(
        message="hola qué tal",
        surface="web",
        agency_name="Inmo <Sol>",
        agency_slug="inmo-sol",
        screen="/leads?q=<x>",
    )
    assert "Inmo &lt;Sol&gt;" in html
    assert "/leads?q=&lt;x&gt;" in html


def test_builder_labels_an_unknown_surface_verbatim():
    # The endpoint only accepts web|mobile; the builder must not swallow
    # anything a future caller passes.
    _, html = build_support_ticket_email_html(message="hola qué tal", surface="whatsapp")
    assert "whatsapp" in html
