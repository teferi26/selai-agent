"""The escalation reason has to disappear when the escalation is over.

`GET /conversations/{id}` started reporting `handoff_reason` / `handoff_urgency`
/ `handoff_at` so the panel can say *why* a thread was passed to a human. The
keys live in `conversation.extra`, written by `escalate_to_human`
(app/agent/tools.py) and by the guardrail (app/agent/runner.py).

Nothing ever removed them. "Devolver a la IA" (POST /resume, wired to the
button in dashboard/.../ResumeButton.tsx and to `resumeConversation` in
mobile/src/lib/api.ts) only flipped the status back to ACTIVE, so an
AI-attended conversation kept answering with the reason it was escalated for
*last time* — and a later manual handoff, which carries no reason of its own,
would have inherited it and shown it as the reason for THIS one.

Same harness as tests/test_conversation_state.py: dev auth bypass plus a fake
session, no Postgres.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent.prompts import CONVERSATION_NOTE_EXTRA_KEY
from app.agent.tools import HANDOFF_AT_KEY, HANDOFF_REASON_KEY, HANDOFF_URGENCY_KEY
from app.core.settings import settings
from app.db.models import ConversationChannel, ConversationStatus
from app.db.session import get_db
from app.main import app

AGENCY_ID = uuid.uuid4()

ESCALATED = {
    HANDOFF_REASON_KEY: "El lead exige hablar con una persona sobre la comisión",
    HANDOFF_URGENCY_KEY: "high",
    HANDOFF_AT_KEY: "2026-07-30T10:15:00+00:00",
}


@pytest.fixture
def dev_auth(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")


class Session:
    """Only `get`, `add` and `commit` are exercised by these three routes."""

    def __init__(self, conversation):
        self.conversation = conversation
        self.added: list = []
        self.commits = 0

    async def get(self, model, pk):
        name = getattr(model, "__name__", str(model))
        if name == "Conversation":
            conv = self.conversation
            return conv if conv is not None and pk == conv.id else None
        raise AssertionError(f"unexpected get({name})")

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _conversation(**overrides) -> SimpleNamespace:
    data = {
        "id": uuid.uuid4(),
        "agency_id": AGENCY_ID,
        "lead_id": uuid.uuid4(),
        "channel": ConversationChannel.WHATSAPP,
        "status": ConversationStatus.HANDED_OFF,
        "assigned_user_id": None,
        "extra": dict(ESCALATED),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _client(session) -> TestClient:
    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_db, None)


def _base(conv) -> str:
    return f"/api/v1/agencies/{AGENCY_ID}/conversations/{conv.id}"


# ---------------------------------------------------------------------------
# Giving the thread back to the AI ends the handoff
# ---------------------------------------------------------------------------


def test_resume_erases_the_stored_handoff_context(dev_auth):
    """Otherwise the reason outlives the situation it describes, and the next
    reader (panel, push, or a future export) cannot tell it is stale."""
    conv = _conversation()
    session = Session(conv)

    resp = _client(session).post(f"{_base(conv)}/resume")

    assert resp.status_code == 200
    assert conv.status is ConversationStatus.ACTIVE
    assert HANDOFF_REASON_KEY not in conv.extra
    assert HANDOFF_URGENCY_KEY not in conv.extra
    assert HANDOFF_AT_KEY not in conv.extra
    assert session.commits == 1


def test_resume_keeps_everything_else_in_extra(dev_auth):
    """`extra` is shared with the agency's private note and the raw call
    records; clearing the handoff must not be a whole-dict wipe."""
    conv = _conversation(
        extra={
            **ESCALATED,
            CONVERSATION_NOTE_EXTRA_KEY: {"text": "No le ofrezcas el ático"},
            "calls": [{"id": "call_1"}],
        }
    )

    _client(Session(conv)).post(f"{_base(conv)}/resume")

    assert conv.extra[CONVERSATION_NOTE_EXTRA_KEY] == {"text": "No le ofrezcas el ático"}
    assert conv.extra["calls"] == [{"id": "call_1"}]


def test_resume_replaces_the_dict_so_sqlalchemy_notices(dev_auth):
    """A JSON column is only persisted when the attribute is reassigned; an
    in-place `del` would commit nothing and the keys would come back on the
    next read."""
    conv = _conversation()
    before = conv.extra

    _client(Session(conv)).post(f"{_base(conv)}/resume")

    assert conv.extra is not before


def test_resume_on_a_thread_that_was_never_escalated_is_a_no_op(dev_auth):
    conv = _conversation(status=ConversationStatus.ACTIVE, extra={})

    resp = _client(Session(conv)).post(f"{_base(conv)}/resume")

    assert resp.status_code == 200
    assert conv.extra == {}


def test_resume_survives_a_null_extra(dev_auth):
    """Rows created before `conversations.extra` existed default to NULL."""
    conv = _conversation(extra=None)

    resp = _client(Session(conv)).post(f"{_base(conv)}/resume")

    assert resp.status_code == 200
    assert conv.extra == {}


def test_resume_still_404s_for_another_tenant(dev_auth):
    conv = _conversation(agency_id=uuid.uuid4())

    resp = _client(Session(conv)).post(f"{_base(conv)}/resume")

    assert resp.status_code == 404
    assert conv.extra == ESCALATED  # untouched


# ---------------------------------------------------------------------------
# Reading: the reason describes the handoff the thread is IN, not one it left
# ---------------------------------------------------------------------------


def test_an_ai_attended_conversation_reports_no_reason_even_with_stale_keys(dev_auth):
    """The rows resumed before the clear above shipped still carry the keys.
    The read side has to refuse them too, or every one of them shows a banner
    about an escalation that ended."""
    conv = _conversation(status=ConversationStatus.ACTIVE)

    body = _client(Session(conv)).get(_base(conv)).json()

    assert body["status"] == "active"
    assert body["handoff_reason"] is None
    assert body["handoff_urgency"] is None
    assert body["handoff_at"] is None


def test_a_snoozed_conversation_reports_no_reason_either(dev_auth):
    """SNOOZED is the AI's too — it is waiting, not handed over."""
    conv = _conversation(status=ConversationStatus.SNOOZED)

    body = _client(Session(conv)).get(_base(conv)).json()

    assert body["handoff_reason"] is None


def test_a_handed_off_conversation_still_reports_why(dev_auth):
    conv = _conversation()

    body = _client(Session(conv)).get(_base(conv)).json()

    assert body["handoff_reason"] == ESCALATED[HANDOFF_REASON_KEY]
    assert body["handoff_urgency"] == "high"
    assert body["handoff_at"] == "2026-07-30T10:15:00Z"


def test_a_closed_conversation_keeps_the_reason_as_history(dev_auth):
    """A finished thread that a human took over is exactly the case where
    "¿por qué acabó aquí?" is worth answering."""
    conv = _conversation(status=ConversationStatus.CLOSED)

    body = _client(Session(conv)).get(_base(conv)).json()

    assert body["handoff_reason"] == ESCALATED[HANDOFF_REASON_KEY]


# ---------------------------------------------------------------------------
# The misattribution this is really about
# ---------------------------------------------------------------------------


def test_a_manual_handoff_does_not_inherit_the_ais_old_reason(dev_auth):
    """escalate → resume → a human takes the thread from the panel. The manual
    handoff has no reason of its own, so anything shown here would be the AI's
    reason from before, presented as the reason for this one."""
    conv = _conversation()
    client = _client(Session(conv))

    client.post(f"{_base(conv)}/resume")
    handoff = client.post(f"{_base(conv)}/handoff", json={"note": "Lo llevo yo"})

    assert handoff.status_code == 200
    body = client.get(_base(conv)).json()
    assert body["status"] == "handed_off"
    assert body["handoff_reason"] is None
    assert body["handoff_at"] is None
