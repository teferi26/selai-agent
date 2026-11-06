"""GET /api/v1/agencies/{agency_id}/conversations/{conversation_id}.

The mobile conversation screen needs two facts before it can draw a footer:
who is answering (the AI or a human) and through which channel. Until this
endpoint existed it got them by downloading the whole inbox and searching it,
which meant the answer was unavailable past the inbox ceiling and cost a
listing per screen open.

The other half of what it fixes is a lie: `/conversations/{id}/messages`
filters messages by agency and conversation, so a thread that does not exist —
or belongs to somebody else — answers `[]` exactly like a brand new one, and
the app said "todavía no tiene historial" about a conversation it could not
see. This route answers 404 there.

Same harness as tests/test_lead_detail.py: dev auth bypass plus a fake DB
session injected through `dependency_overrides`, so no Postgres is needed.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent.prompts import CONVERSATION_NOTE_EXTRA_KEY
from app.agent.tools import HANDOFF_AT_KEY, HANDOFF_REASON_KEY, HANDOFF_URGENCY_KEY
from app.api.dashboard import router as dashboard_router
from app.core.auth import require_agency_member
from app.core.settings import settings
from app.db.models import ConversationChannel, ConversationStatus
from app.db.session import get_db
from app.main import app

AGENCY_ID = uuid.uuid4()
OTHER_AGENCY_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture
def dev_auth(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")


class ConversationSession:
    """Minimal AsyncSession double: only `db.get(Conversation, pk)` is used."""

    def __init__(self, conversation):
        self.conversation = conversation
        self.gets: list[tuple[str, uuid.UUID]] = []

    async def get(self, model, pk):
        name = getattr(model, "__name__", str(model))
        self.gets.append((name, pk))
        if name == "Conversation":
            conv = self.conversation
            return conv if conv is not None and pk == conv.id else None
        raise AssertionError(f"unexpected get({name})")


def _client(session) -> TestClient:
    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_db, None)


def _conversation(**overrides) -> SimpleNamespace:
    data = {
        "id": uuid.uuid4(),
        "agency_id": AGENCY_ID,
        "lead_id": uuid.uuid4(),
        "channel": ConversationChannel.WHATSAPP,
        "status": ConversationStatus.ACTIVE,
        "assigned_user_id": None,
        "extra": {},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _get(client, conversation_id, agency_id: uuid.UUID = AGENCY_ID):
    return client.get(f"/api/v1/agencies/{agency_id}/conversations/{conversation_id}")


# ---------------------------------------------------------------------------
# Happy path — the two facts the screen draws its footer from
# ---------------------------------------------------------------------------


def test_returns_status_and_channel(dev_auth):
    conv = _conversation()

    resp = _get(_client(ConversationSession(conv)), conv.id)

    assert resp.status_code == 200
    assert resp.json() == {
        "id": str(conv.id),
        "status": "active",
        "channel": "whatsapp",
        "lead_id": str(conv.lead_id),
        "assigned_user_id": None,
        "handoff_reason": None,
        "handoff_urgency": None,
        "handoff_at": None,
    }


def test_handed_off_is_reported_as_such(dev_auth):
    """The whole point: after taking control the app must be told so by the
    server, instead of remembering it in a local boolean that resets when the
    screen is left."""
    owner = uuid.uuid4()
    conv = _conversation(status=ConversationStatus.HANDED_OFF, assigned_user_id=owner)

    resp = _get(_client(ConversationSession(conv)), conv.id)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "handed_off"
    assert body["assigned_user_id"] == str(owner)


@pytest.mark.parametrize(
    "channel",
    [
        ConversationChannel.WHATSAPP,
        ConversationChannel.EMAIL,
        ConversationChannel.VOICE,
        ConversationChannel.SMS,
        ConversationChannel.WEB,
    ],
)
def test_every_channel_is_reported_verbatim(dev_auth, channel):
    """The app decides whether to show the composer from this value, so an
    unexpected spelling would silently hide (or wrongly offer) the box."""
    conv = _conversation(channel=channel)

    resp = _get(_client(ConversationSession(conv)), conv.id)

    assert resp.status_code == 200
    assert resp.json()["channel"] == channel.value


@pytest.mark.parametrize(
    "conv_status",
    [
        ConversationStatus.ACTIVE,
        ConversationStatus.SNOOZED,
        ConversationStatus.HANDED_OFF,
        ConversationStatus.CLOSED,
    ],
)
def test_every_status_is_reported_verbatim(dev_auth, conv_status):
    conv = _conversation(status=conv_status)

    resp = _get(_client(ConversationSession(conv)), conv.id)

    assert resp.status_code == 200
    assert resp.json()["status"] == conv_status.value


# ---------------------------------------------------------------------------
# (13) Why the AI handed over
#
# `escalate_to_human` has stored the reason in `conv.extra` since the handoff
# phase, and the push/email already carry it — but the screen that draws the
# "derivada a humano" banner had no way to read it, so whoever picked up the
# phone had to re-read the thread to find out why.
# ---------------------------------------------------------------------------


def test_the_escalation_reason_reaches_the_panel(dev_auth):
    conv = _conversation(
        status=ConversationStatus.HANDED_OFF,
        extra={
            HANDOFF_REASON_KEY: "Pide hablar con una persona sobre la comisión",
            HANDOFF_URGENCY_KEY: "high",
            HANDOFF_AT_KEY: "2026-07-30T10:15:00+00:00",
        },
    )

    body = _get(_client(ConversationSession(conv)), conv.id).json()

    assert body["handoff_reason"] == "Pide hablar con una persona sobre la comisión"
    assert body["handoff_urgency"] == "high"
    assert body["handoff_at"] == "2026-07-30T10:15:00Z"


def test_a_guardrail_handoff_without_a_reason_reports_null_not_empty_string(dev_auth):
    """`escalate_to_human` stores "" when the model called the tool with no
    reason. The panel needs null so it can print its own fallback copy."""
    conv = _conversation(
        status=ConversationStatus.HANDED_OFF,
        extra={HANDOFF_REASON_KEY: "", HANDOFF_URGENCY_KEY: "normal"},
    )

    body = _get(_client(ConversationSession(conv)), conv.id).json()

    assert body["handoff_reason"] is None
    assert body["handoff_at"] is None


def test_a_multiline_reason_is_collapsed(dev_auth):
    """The reason is rendered in a one-line banner; a model that answered with
    newlines used to break the layout."""
    conv = _conversation(
        status=ConversationStatus.HANDED_OFF,
        extra={HANDOFF_REASON_KEY: "  Está   muy\nmolesto\ncon la tasación "},
    )

    body = _get(_client(ConversationSession(conv)), conv.id).json()

    assert body["handoff_reason"] == "Está muy molesto con la tasación"


@pytest.mark.parametrize("urgency", ["urgentísimo", "", None, 7, {"level": "high"}])
def test_an_unknown_urgency_is_reported_as_null(dev_auth, urgency):
    """The panel colour-codes this value; a spelling it does not know must not
    reach it as a string it would render raw."""
    conv = _conversation(
        status=ConversationStatus.HANDED_OFF,
        extra={HANDOFF_REASON_KEY: "x", HANDOFF_URGENCY_KEY: urgency},
    )

    assert _get(_client(ConversationSession(conv)), conv.id).json()["handoff_urgency"] is None


def test_a_corrupt_handoff_timestamp_does_not_take_the_screen_down(dev_auth):
    conv = _conversation(
        status=ConversationStatus.HANDED_OFF, extra={HANDOFF_AT_KEY: "ayer por la tarde"}
    )

    resp = _get(_client(ConversationSession(conv)), conv.id)

    assert resp.status_code == 200
    assert resp.json()["handoff_at"] is None


def test_the_rest_of_conversation_extra_stays_private(dev_auth):
    """`conv.extra` is a grab bag: it also holds the agency's private note to
    the AI and the raw voice-call records. Only the handoff keys are exposed —
    a `**conv.extra` splat here would publish all of it."""
    conv = _conversation(
        status=ConversationStatus.HANDED_OFF,
        extra={
            HANDOFF_REASON_KEY: "Quiere negociar",
            CONVERSATION_NOTE_EXTRA_KEY: {"text": "No le ofrezcas el ático, está reservado"},
            "calls": [{"id": "call_1", "recording_url": "https://vapi/rec.mp3"}],
        },
    )

    resp = _get(_client(ConversationSession(conv)), conv.id)

    assert resp.json()["handoff_reason"] == "Quiere negociar"
    assert "ático" not in resp.text
    assert "recording_url" not in resp.text
    assert set(resp.json()) == {
        "id",
        "status",
        "channel",
        "lead_id",
        "assigned_user_id",
        "handoff_reason",
        "handoff_urgency",
        "handoff_at",
    }


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


def test_another_agencys_conversation_is_404_and_leaks_nothing(dev_auth):
    conv = _conversation(agency_id=OTHER_AGENCY_ID)

    resp = _get(_client(ConversationSession(conv)), conv.id)

    assert resp.status_code == 404
    assert resp.json()["detail"] == "conversation_not_found"
    # Not even the lead it belongs to.
    assert str(conv.lead_id) not in resp.text


def test_unknown_conversation_is_404_with_the_same_detail(dev_auth):
    """Identical answer to the foreign case: a push carrying an id from
    somebody else's tenant must be indistinguishable from a stale one."""
    resp = _get(_client(ConversationSession(None)), uuid.uuid4())

    assert resp.status_code == 404
    assert resp.json()["detail"] == "conversation_not_found"


def test_a_non_uuid_id_never_reaches_the_database(dev_auth):
    session = ConversationSession(_conversation())

    resp = _get(_client(session), "'; DROP TABLE conversations; --")

    assert resp.status_code == 422
    assert session.gets == []


# ---------------------------------------------------------------------------
# Structural
# ---------------------------------------------------------------------------


def test_route_is_registered_and_carries_the_agency_member_guard():
    """The tenancy dependency lives on the router, so splitting dashboard.py
    into several routers could publish this route unguarded. Asserted on the
    router itself for the same reason as tests/test_lead_detail.py.
    """
    from fastapi.routing import APIRoute

    routes = [
        r
        for r in dashboard_router.routes
        if isinstance(r, APIRoute) and r.path.endswith("/conversations/{conversation_id}")
    ]
    assert len(routes) == 1, "GET /conversations/{conversation_id} is not registered"
    route = routes[0]
    assert route.methods == {"GET"}

    seen: set[int] = set()

    def walk(dep) -> bool:
        if id(dep) in seen:
            return False
        seen.add(id(dep))
        if dep.call is require_agency_member:
            return True
        return any(walk(sub) for sub in dep.dependencies)

    assert walk(route.dependant), "the conversation detail route lost its tenancy guard"


def test_it_does_not_shadow_the_nested_conversation_routes():
    """`/conversations/{id}` must not swallow `/conversations/{id}/messages`
    and friends. They differ in segment count, but the assertion is cheap and
    the failure mode (every nested route 405s) is expensive.
    """
    from fastapi.routing import APIRoute

    paths = {
        r.path
        for r in dashboard_router.routes
        if isinstance(r, APIRoute) and "/conversations/{conversation_id}" in r.path
    }
    for suffix in ("/messages", "/handoff", "/resume", "/reply", "/note", "/lead"):
        assert any(p.endswith(suffix) for p in paths), f"lost /conversations/{{id}}{suffix}"
