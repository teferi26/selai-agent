"""Adversarial pass over POST /api/v1/support (app/api/support.py).

`tests/test_support_endpoint.py` covers the happy paths. This file attacks the
promises the endpoint makes to the panel and the app, and it deliberately does
**not** reuse that file's fixture: the doubles there replace the module logger
and `send_email`, which is exactly where a 500 could hide. Here the real
logger runs, and one test drives the real `send_email` against a fake httpx.

What is under attack:

1. A user with no agency, and a database that answers nothing, must still get
   a ticket out — never a 5xx.
2. A dead Resend must never become a 5xx either, *with the real logger in the
   loop*.
3. The rate limit must not block the first attempt, must not be resettable by
   the client, and must not be spent by requests that never became a ticket.
4. The ticket is internal: it must ignore the agency's email preferences and
   carry no unsubscribe header.
5. Anything a human typed lands in our inbox as text, never as markup.
6. Nothing the client sends can grow the email without bound.
7. The router is actually mounted, behind authentication and nothing else.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import support as support_module
from app.core.auth import AuthContext, require_agency_member, require_auth
from app.core.settings import settings
from app.db.session import get_db
from app.main import app
from app.services import emails
from app.services.emails import (
    INTERNAL_EMAIL,
    build_support_ticket_email_html,
    category_for,
    send_email,
)

AGENCY_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def _user(email: str = "marta@inmosol.example.com") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        email=email,
        full_name="Marta García",
        agency_id=AGENCY_ID,
        is_active=True,
    )


def _auth(user, clerk: str | None = None, email: str | None = "marta@inmosol.example.com") -> AuthContext:
    return AuthContext(
        clerk_user_id=clerk or ("user_" + uuid.uuid4().hex[:8]),
        email=email,
        raw_claims={},
        user=user,
    )


class DeadSession:
    """A database that is down: every call raises, like the outage the user is
    writing to us about."""

    async def get(self, *args, **kwargs):
        raise RuntimeError("connection refused")

    async def execute(self, *args, **kwargs):
        raise RuntimeError("connection refused")

    async def commit(self):
        raise RuntimeError("connection refused")

    async def rollback(self):
        pass


class SilentSession:
    """Reachable, but this agency simply is not there (deleted row, replica
    lag). `get_plan_state` gets a subscription of None."""

    def __init__(self, agency=None):
        self.agency = agency

    async def get(self, model, pk):
        return self.agency if getattr(model, "__name__", "") == "Agency" else None

    async def execute(self, stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def env(monkeypatch):
    """Endpoint under test with the **real** logger. Only the network is faked."""
    support_module._user_hits.clear()

    sent: list[dict] = []
    outcome = {"mode": "ok"}

    async def _fake_send(to, subject, html, category=None):
        sent.append({"to": to, "subject": subject, "html": html, "category": category})
        if outcome["mode"] == "raise":
            raise RuntimeError("resend exploded")
        return outcome["mode"] == "ok"

    monkeypatch.setattr(support_module, "send_email", _fake_send)

    state = {"session": SilentSession(), "auth": _auth(_user())}

    async def _override_auth():
        return state["auth"]

    async def _override_db():
        yield state["session"]

    app.dependency_overrides[require_auth] = _override_auth
    app.dependency_overrides[get_db] = _override_db
    # Own source IP: RateLimitMiddleware keeps per-IP buckets on the shared app.
    client = TestClient(app, client=(f"203.0.113.{uuid.uuid4().int % 200 + 1}", 4242))

    yield SimpleNamespace(client=client, sent=sent, outcome=outcome, state=state)

    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(get_db, None)
    support_module._user_hits.clear()


def _body(**overrides) -> dict:
    base = {"message": "No carga nada desde ayer.", "surface": "web"}
    return {**base, **overrides}


def _post(env, **overrides):
    return env.client.post("/api/v1/support", json=_body(**overrides))


# ---------------------------------------------------------------------------
# 1. It has to work when everything else does not
# ---------------------------------------------------------------------------


def test_a_user_with_no_agency_and_a_dead_database_still_sends_a_ticket(env):
    """The scenario the form exists for: no `User` row resolved *and* the DB
    unreachable. Anything but a 200 makes the rescue channel useless."""
    env.state["auth"] = _auth(None, email="nuevo@example.es")
    env.state["session"] = DeadSession()

    resp = _post(env)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert len(env.sent) == 1
    assert env.sent[0]["to"] == INTERNAL_EMAIL


def test_a_user_whose_agency_lookup_explodes_still_sends_a_ticket(env):
    env.state["session"] = DeadSession()

    resp = _post(env)

    assert resp.status_code == 200
    assert "(sin agencia)" in env.sent[0]["html"]
    # Identity comes from the auth context, which no DB failure can take away.
    assert "marta@inmosol.example.com" in env.sent[0]["html"]


def test_an_agency_without_a_subscription_is_not_an_error(env):
    env.state["session"] = SilentSession(
        agency=SimpleNamespace(id=AGENCY_ID, name="Inmo Sol", slug="inmo-sol", settings={})
    )

    assert _post(env).status_code == 200
    assert "(sin plan)" in env.sent[0]["html"]


# ---------------------------------------------------------------------------
# 2. A dead Resend is not the user's problem — real logger in the loop
# ---------------------------------------------------------------------------


def test_a_failed_send_answers_ok_with_the_real_logger(env):
    """The other suite replaces `log`, so the actual logging call — the one
    that runs in production, carrying the user's own text — is never executed.
    If it ever raises, the answer to "Resend is down" becomes a 500."""
    env.outcome["mode"] = "fail"

    resp = _post(env, message="Se cae al guardar\r\ny pierdo la ficha", screen="/leads")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_a_raising_send_answers_ok_with_the_real_logger(env):
    env.outcome["mode"] = "raise"

    assert _post(env).status_code == 200


def test_a_failed_send_with_no_agency_at_all_answers_ok(env):
    """Worst case stacked: no user row, dead DB and dead Resend."""
    env.state["auth"] = _auth(None, email=None)
    env.state["session"] = DeadSession()
    env.outcome["mode"] = "fail"

    assert _post(env).status_code == 200


# ---------------------------------------------------------------------------
# 3. Rate limit
# ---------------------------------------------------------------------------


def test_the_first_ticket_is_never_blocked(env):
    """A limiter that answers 429 to the first attempt would be worse than no
    limiter: the one message that mattered is the one that never arrives."""
    assert _post(env).status_code == 200
    assert len(env.sent) == 1


def test_a_busy_process_does_not_block_a_newcomer(env):
    for i in range(50):
        for _ in range(support_module.SUPPORT_MAX_PER_WINDOW):
            support_module._user_rate_limited(f"user:other-{i}")

    assert _post(env).status_code == 200


def test_the_blocked_attempt_sends_nothing_and_says_so_in_spanish(env):
    for _ in range(support_module.SUPPORT_MAX_PER_WINDOW):
        assert _post(env).status_code == 200

    resp = _post(env)

    assert resp.status_code == 429
    assert len(env.sent) == support_module.SUPPORT_MAX_PER_WINDOW
    detail = resp.json()["detail"]
    assert isinstance(detail, str)
    assert "Espera unos minutos" in detail and INTERNAL_EMAIL in detail


def test_a_new_clerk_session_does_not_hand_out_a_fresh_budget(env):
    """The obvious bypass: sign out and back in. The budget follows the user
    row, not the session."""
    user = env.state["auth"].user
    for _ in range(support_module.SUPPORT_MAX_PER_WINDOW):
        _post(env)

    env.state["auth"] = _auth(user, clerk="user_brand_new_session")

    assert _post(env).status_code == 429


def test_rejected_bodies_do_not_spend_the_budget(env):
    """A client bug that posts garbage must not lock the human out of the form
    when they finally type something valid."""
    for _ in range(10):
        assert _post(env, message="hi").status_code == 422

    assert _post(env).status_code == 200


def test_the_budget_is_released_by_time_not_by_a_new_request(env):
    key = "user:release"
    now = 1_000.0
    for _ in range(support_module.SUPPORT_MAX_PER_WINDOW):
        support_module._user_rate_limited(key, now=now)
    assert support_module._user_rate_limited(key, now=now) is True
    # A blocked attempt must not extend the ban, or a retry loop would hold
    # the user out forever.
    just_after = now + support_module.SUPPORT_WINDOW_SECONDS + 0.5
    assert support_module._user_rate_limited(key, now=just_after) is False


# ---------------------------------------------------------------------------
# 4. Internal mail: no preferences, no unsubscribe
# ---------------------------------------------------------------------------


def test_an_agency_that_switched_every_email_off_still_reaches_us(env):
    """`email_prefs` governs what we send *to* the agency. This one is a
    ticket the user just wrote to us; silencing it would be a bug with the
    shape of a feature."""
    env.state["session"] = SilentSession(
        agency=SimpleNamespace(
            id=AGENCY_ID,
            name="Inmo Sol",
            slug="inmo-sol",
            settings={"email_prefs": {"digest": False, "alerts": False, "lifecycle": False}},
        )
    )

    assert _post(env).status_code == 200
    assert len(env.sent) == 1
    assert env.sent[0]["category"] is None


class _FakeResponse:
    status_code = 200
    text = "{}"


class _FakeClient:
    last_payload: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).last_payload = json
        return _FakeResponse()


async def test_the_ticket_goes_on_the_wire_without_an_unsubscribe_header(monkeypatch):
    """End to end through the real `send_email`: `category_for("support_ticket")`
    has to resolve to None *and* that None has to mean "no List-Unsubscribe"."""
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test"))
    monkeypatch.setattr(emails.httpx, "AsyncClient", _FakeClient)
    _FakeClient.last_payload = None

    subject, html = build_support_ticket_email_html(message="hola qué tal", surface="web")
    assert await send_email(
        INTERNAL_EMAIL, subject, html, category=category_for("support_ticket")
    ) is True

    payload = _FakeClient.last_payload
    assert payload["to"] == [INTERNAL_EMAIL]
    assert "headers" not in payload


# ---------------------------------------------------------------------------
# 5. Everything a human typed is text, not markup
# ---------------------------------------------------------------------------


def test_the_message_cannot_close_the_element_it_lives_in(env):
    assert _post(env, message="roto</div><script>alert(1)</script><div>").status_code == 200
    html = env.sent[0]["html"]

    assert "<script>" not in html
    assert "</div><script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.parametrize(
    "field",
    ["screen", "app_version", "error_ref", "contact_email"],
)
def test_no_context_field_can_break_out_of_its_attribute_or_cell(env, field):
    payload = '"><img src=x onerror=alert(1)>'
    kwargs = {field: payload[:40] if field == "app_version" else payload}

    assert _post(env, **kwargs).status_code == 200
    html = env.sent[0]["html"]

    # The text may well *say* onerror; what it may not do is become a tag or
    # close the attribute it sits in.
    assert "<img" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&quot;&gt;&lt;img" in html


def test_the_reply_link_stays_a_mailto(env):
    """`contact_email` is free text by design (a typo must not lose the
    ticket), so it must not be able to turn the link into anything else."""
    assert _post(env, contact_email='x" onclick="alert(1)').status_code == 200
    html = env.sent[0]["html"]

    assert 'onclick="alert(1)"' not in html
    assert 'href="mailto:x&quot;' in html


def test_html_typed_by_a_human_survives_readable(env):
    """Escaping must not eat the ticket: someone reporting a problem with a
    `<b>` tag should still be readable at the other end."""
    assert _post(env, message="al pulsar <b>Guardar</b> & salir").status_code == 200

    assert "&lt;b&gt;Guardar&lt;/b&gt; &amp; salir" in env.sent[0]["html"]


# ---------------------------------------------------------------------------
# 6. Nothing the client sends can grow without bound
# ---------------------------------------------------------------------------


def test_every_field_at_its_maximum_still_fits_in_an_inbox(env):
    resp = _post(
        env,
        message="<" * 4000,  # worst case: every character expands to `&lt;`
        screen="s" * 200,
        app_version="v" * 40,
        error_ref="e" * 200,
        contact_email="c" * 200,
    )

    assert resp.status_code == 200
    # ~24 KB of escaped body plus the layout. The ceiling is generous on
    # purpose: it fails only if a length cap goes missing.
    assert len(env.sent[0]["html"]) < 100_000


def test_a_message_of_only_whitespace_is_not_a_ticket(env):
    """Five spaces clears `min_length=5` on paper. It is still an empty ticket,
    and five of them fill the inbox without saying anything."""
    assert _post(env, message="      ").status_code == 422
    assert _post(env, message=" \n\t \r\n ").status_code == 422
    assert env.sent == []


def test_a_pasted_trailing_newline_does_not_cost_the_user_their_ticket(env):
    """Trimming happens before the length check, so 4000 characters plus the
    newline the textarea added is still a valid message."""
    resp = _post(env, message="  " + "x" * 4000 + "\n")

    assert resp.status_code == 200
    assert "x" * 4000 in env.sent[0]["html"]


def test_blank_context_fields_read_as_absent(env):
    """Clients send "" rather than null more often than not; the email should
    say "(no indicada)" instead of showing an empty cell."""
    assert _post(env, screen="   ", app_version="", error_ref="", contact_email=" ").status_code == 200
    html = env.sent[0]["html"]

    assert "(no indicada)" in html
    assert "(ninguna)" in html
    # No contact given → the reply link falls back to the account email.
    assert 'href="mailto:marta@inmosol.example.com"' in html


def test_the_subject_stays_one_bounded_line():
    """The subject is a mail header and the agency name is typed by the
    customer. A newline in it has no business splitting the header, and an
    unbounded one risks the provider rejecting the whole ticket."""
    subject, _ = build_support_ticket_email_html(
        message="hola qué tal",
        surface="web",
        agency_name="Inmo Sol\r\nBcc: alguien@ajeno.es",
    )

    assert "\n" not in subject and "\r" not in subject
    assert "Bcc:" in subject  # collapsed, not silently dropped
    assert len(subject) < 200

    long_subject, _ = build_support_ticket_email_html(
        message="hola qué tal", surface="web", user_email="a" * 5_000 + "@b.es"
    )
    assert len(long_subject) < 200


# ---------------------------------------------------------------------------
# 7. The router is mounted, behind authentication and nothing else
# ---------------------------------------------------------------------------


def _dependency_calls(dependant) -> list:
    calls = [dependant.call]
    for sub in dependant.dependencies:
        calls.extend(_dependency_calls(sub))
    return calls


def _mounted_routes():
    """FastAPI 0.139 keeps an included router wrapped in `_IncludedRouter`
    instead of flattening its routes into `app.routes`, so reach through it."""
    for entry in app.routes:
        original = getattr(entry, "original_router", None)
        yield from (original.routes if original is not None else [entry])


def test_the_route_is_registered_in_main_and_only_needs_a_logged_in_user():
    route = next(
        r
        for r in _mounted_routes()
        if getattr(r, "path", None) == "/api/v1/support" and "POST" in getattr(r, "methods", ())
    )

    calls = _dependency_calls(route.dependant)
    assert require_auth in calls
    # Requiring agency membership is what would break the form for the user
    # who has no agency — the case it exists for.
    assert require_agency_member not in calls


def test_a_client_that_sends_one_field_too_many_is_not_punished(env):
    """Forward compatibility: a released app that starts attaching one more
    piece of context must not start getting 422s from a backend that predates
    it — the support form is the last thing that should break on upgrade."""
    resp = env.client.post(
        "/api/v1/support",
        json=_body(device_model="Pixel 8", os_version="Android 15"),
    )

    assert resp.status_code == 200
    assert len(env.sent) == 1


@pytest.mark.parametrize("value", ["Web", " web", "desktop", "", None])
def test_surface_is_exactly_web_or_mobile(env, value):
    """Documented for both clients: no case folding, no trimming, no guessing.
    It decides one label in the email, so silently accepting variants would
    only hide a client bug."""
    assert _post(env, surface=value).status_code == 422
    assert env.sent == []


def test_the_endpoint_answers_on_the_exact_path_the_clients_call(env):
    """The panel proxies to /api/backend/api/v1/support and the app posts to
    /api/v1/support. Both land here; a trailing slash must not 404."""
    assert env.client.post("/api/v1/support", json=_body()).status_code == 200
    assert env.client.post("/api/v1/support/", json=_body()).status_code == 200
