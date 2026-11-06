"""Item 216 — WhatsApp messages are grouped before the agent answers.

"Hola" / "busco piso" / "en Alicante" arrives as three webhooks in four
seconds. One run each means three overlapping replies, which reads as a
robot, and three times the token bill. So each message goes into a buffer
on the conversation and only the last one to arrive runs — over everything
in the buffer, as one turn.

The buffer itself is pure (app/channels/whatsapp/debounce.py), so most of
this file needs no FastAPI and no database; the last section pins the route
wiring with the window shrunk to zero delay.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import whatsapp as wa_module
from app.channels.whatsapp.debounce import (
    DEFAULT_DEBOUNCE_SECONDS,
    MAX_SEEN_IDS,
    append_to_buffer,
    buffer_is_current,
    merge_texts,
    take_buffer,
)
from app.core.settings import settings
from app.db.models import ConversationChannel
from app.db.session import get_db
from app.main import app
from app.services.conversation_service import InboundPayload
from app.services.lead_resolver import InboundIdentity

WA_ID = "34600111222"


# ---------------------------------------------------------------------------
# The buffer
# ---------------------------------------------------------------------------


def test_first_message_opens_the_buffer():
    extra, seq = append_to_buffer({}, external_message_id="wamid.1", text="Hola")
    assert seq == 1
    assert extra["pending_buffer"]["items"] == [{"id": "wamid.1", "text": "Hola"}]


def test_each_message_takes_the_next_turn():
    extra, first = append_to_buffer({}, external_message_id="wamid.1", text="Hola")
    extra, second = append_to_buffer(
        extra, external_message_id="wamid.2", text="busco piso"
    )
    extra, third = append_to_buffer(
        extra, external_message_id="wamid.3", text="en Alicante"
    )
    assert [first, second, third] == [1, 2, 3]
    assert len(extra["pending_buffer"]["items"]) == 3


def test_the_original_extra_is_never_mutated():
    """`conv.extra` must be reassigned for SQLAlchemy to notice the change;
    mutating in place is the bug this convention exists to prevent."""
    original = {"calls": [{"id": "c1"}]}
    extra, _seq = append_to_buffer(original, external_message_id="wamid.1", text="Hola")
    assert original == {"calls": [{"id": "c1"}]}
    # Unrelated keys survive: the buffer shares `extra` with the voice records.
    assert extra["calls"] == [{"id": "c1"}]


def test_only_the_last_message_owns_the_run():
    extra, first = append_to_buffer({}, external_message_id="wamid.1", text="Hola")
    extra, second = append_to_buffer(extra, external_message_id="wamid.2", text="dos")
    assert buffer_is_current(extra, first) is False
    assert buffer_is_current(extra, second) is True


def test_an_empty_buffer_owns_nothing():
    assert buffer_is_current({}, 1) is False
    assert buffer_is_current(None, 1) is False


def test_taking_the_buffer_returns_every_text_in_order():
    extra = {}
    for i, text in enumerate(["Hola", "busco piso", "en Alicante"], start=1):
        extra, _ = append_to_buffer(extra, external_message_id=f"wamid.{i}", text=text)

    extra, texts = take_buffer(extra)
    assert texts == ["Hola", "busco piso", "en Alicante"]
    assert "pending_buffer" not in extra


def test_taking_the_buffer_remembers_what_it_took():
    """The window between "buffer drained" and "Message row written" is the
    one gap the dedup index cannot cover, so the ids are kept here too."""
    extra, _ = append_to_buffer({}, external_message_id="wamid.1", text="Hola")
    extra, _texts = take_buffer(extra)
    assert extra["recent_inbound_ids"] == ["wamid.1"]

    # And a retry of that same message is refused instead of re-buffered.
    _extra, seq = append_to_buffer(extra, external_message_id="wamid.1", text="Hola")
    assert seq is None


def test_a_duplicate_inside_the_window_is_refused():
    extra, _ = append_to_buffer({}, external_message_id="wamid.1", text="Hola")
    _extra, seq = append_to_buffer(extra, external_message_id="wamid.1", text="Hola")
    assert seq is None


def test_messages_without_an_id_are_always_buffered():
    """Nothing to compare is not the same as "already seen": dropping them
    would silence a provider that sends no id."""
    extra, first = append_to_buffer({}, external_message_id=None, text="Hola")
    _extra, second = append_to_buffer(extra, external_message_id=None, text="Hola")
    assert first == 1
    assert second == 2


def test_remembered_ids_are_capped():
    extra: dict = {"recent_inbound_ids": [f"old-{i}" for i in range(MAX_SEEN_IDS)]}
    extra, _ = append_to_buffer(extra, external_message_id="wamid.new", text="Hola")
    extra, _texts = take_buffer(extra)
    assert len(extra["recent_inbound_ids"]) == MAX_SEEN_IDS
    assert extra["recent_inbound_ids"][-1] == "wamid.new"
    assert "old-0" not in extra["recent_inbound_ids"]


def test_blank_messages_do_not_become_empty_lines():
    extra, _ = append_to_buffer({}, external_message_id="a", text="Hola")
    extra, _ = append_to_buffer(extra, external_message_id="b", text="   ")
    _extra, texts = take_buffer(extra)
    assert texts == ["Hola"]


def test_the_burst_reaches_the_agent_as_one_turn():
    assert merge_texts(["Hola", "busco piso", "en Alicante"]) == (
        "Hola\nbusco piso\nen Alicante"
    )
    assert merge_texts([]) == ""


def test_a_corrupt_buffer_does_not_crash_the_run():
    """`extra` is a free-form JSON column that humans and other features
    also write to; a wrong shape must degrade, not raise."""
    assert buffer_is_current({"pending_buffer": "nonsense"}, 1) is False
    extra, texts = take_buffer({"pending_buffer": {"items": "nonsense"}})
    assert texts == []
    assert "pending_buffer" not in extra
    _extra, seq = append_to_buffer({"pending_buffer": []}, external_message_id="a", text="x")
    assert seq == 1


def test_the_window_is_twelve_seconds():
    """Long enough to catch the burst, short enough that someone who wrote
    one sentence does not feel ignored."""
    assert DEFAULT_DEBOUNCE_SECONDS == 12.0


# ---------------------------------------------------------------------------
# Route wiring
# ---------------------------------------------------------------------------


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def first(self):
        return None


class BufferingSession:
    """Fake session that owns one conversation row, buffer included."""

    def __init__(self, agency, conversation):
        self.agency = agency
        self.conversation = conversation
        self.locked_for_update = 0

    async def execute(self, stmt):
        sql = str(stmt).lower()
        if "from conversations" in sql:
            if getattr(stmt, "_for_update_arg", None) is not None:
                self.locked_for_update += 1
            return _ScalarResult(self.conversation)
        if "messages" in sql:
            return _ScalarResult(None)
        return _ScalarResult(self.agency)

    async def get(self, model, pk):
        if pk == self.conversation.id:
            return self.conversation
        return None

    async def commit(self):
        pass

    async def rollback(self):
        pass


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


DEV_SIGNATURE = "sha256=" + "0" * 64


def _body(message_id: str, text: str) -> dict:
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
                                    "text": {"body": text},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(""))
    monkeypatch.setattr(wa_module, "WhatsAppClient", RecordingClient)
    # A real window would make the test sleep 12 s per message; the timing
    # is the one thing here that is not worth asserting.
    monkeypatch.setattr(wa_module, "DEBOUNCE_SECONDS", 0.001)
    RecordingClient.instances = []

    agency = SimpleNamespace(
        id=uuid.uuid4(),
        slug="inmo-sol",
        settings={
            "whatsapp_access_token": "EAAG-agency",
            "whatsapp_phone_number_id": "pnid-agency",
        },
    )
    conversation = SimpleNamespace(id=uuid.uuid4(), extra={})
    lead = SimpleNamespace(id=uuid.uuid4())
    session = BufferingSession(agency, conversation)

    async def fake_resolve(db, agency_id, identity):
        return lead, conversation

    monkeypatch.setattr(wa_module, "resolve_lead_and_conversation", fake_resolve)

    handled: list = []

    async def fake_handle(db, payload):
        handled.append(payload.text)
        return SimpleNamespace(
            reply_text="Te cuento.", conversation_id=conversation.id, handed_off=False
        )

    monkeypatch.setattr(wa_module, "handle_inbound", fake_handle)

    @asynccontextmanager
    async def _task_session():
        yield session

    monkeypatch.setattr(wa_module, "task_session", _task_session)

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield SimpleNamespace(handled=handled, conversation=conversation, session=session)
    app.dependency_overrides.pop(get_db, None)


def _post(client: TestClient, message_id: str, text: str):
    return client.post(
        "/webhooks/whatsapp",
        json=_body(message_id, text),
        headers={"X-Hub-Signature-256": DEV_SIGNATURE},
    )


def test_a_single_message_runs_once_with_its_own_text(env):
    client = TestClient(app)
    with client:
        assert _post(client, "wamid.1", "Hola").status_code == 200
    assert env.handled == ["Hola"]
    assert RecordingClient.instances[-1].sent == [(WA_ID, "Te cuento.")]


def test_the_buffer_is_emptied_after_the_run(env):
    client = TestClient(app)
    with client:
        _post(client, "wamid.1", "Hola")
    assert "pending_buffer" not in env.conversation.extra
    assert env.conversation.extra["recent_inbound_ids"] == ["wamid.1"]


def test_a_retry_inside_the_window_never_reaches_the_agent(env):
    """The route's dedup lookup cannot see a message that is buffered but
    not yet stored; the buffer refuses it instead."""
    client = TestClient(app)
    with client:
        _post(client, "wamid.1", "Hola")
        _post(client, "wamid.1", "Hola")
    assert env.handled == ["Hola"]


async def test_a_burst_produces_one_run_with_everything_in_it(env, monkeypatch):
    """The case the item is about: three webhooks land while the window is
    open, and the lead gets one answer to all three instead of three
    answers talking over each other."""
    monkeypatch.setattr(wa_module, "DEBOUNCE_SECONDS", 0.05)

    def _payload(message_id: str, text: str) -> InboundPayload:
        return InboundPayload(
            identity=InboundIdentity(
                channel=ConversationChannel.WHATSAPP,
                whatsapp_wa_id=WA_ID,
                external_thread_id=WA_ID,
            ),
            agency_id=uuid.uuid4(),
            text=text,
            external_message_id=message_id,
        )

    async def _fire(message_id: str, text: str, delay: float) -> None:
        await asyncio.sleep(delay)
        await wa_module.process_inbound(
            agency_id=uuid.uuid4(),
            agency_settings={},
            phone_number_id="pnid-agency",
            payload=_payload(message_id, text),
        )

    await asyncio.gather(
        _fire("wamid.1", "Hola", 0.0),
        _fire("wamid.2", "busco piso", 0.01),
        _fire("wamid.3", "en Alicante", 0.02),
    )

    assert env.handled == ["Hola\nbusco piso\nen Alicante"]
    # One reply, not three.
    assert sum(len(c.sent) for c in RecordingClient.instances) == 1


# ---------------------------------------------------------------------------
# Two messages at once — the race the buffer lives or dies by
# ---------------------------------------------------------------------------


class Row:
    """The conversation row as the database holds it: one copy, one lock."""

    def __init__(self):
        self.id = uuid.uuid4()
        self.extra: dict = {}
        self.lock = asyncio.Lock()


class RowSession:
    """A session over `Row`, with the two properties that make the race real.

    A session reads its *own* copy of `extra` and only publishes it on
    commit, so an unsynchronized read-modify-write loses the other writer's
    work — exactly what two Postgres sessions do. `SELECT ... FOR UPDATE`
    takes the row lock and re-reads; without it, both callers work from the
    same stale snapshot.
    """

    def __init__(self, row: Row):
        self.row = row
        self.conversation = SimpleNamespace(id=row.id, extra={})
        self._held = False

    async def read(self):
        """A round trip: the row is read *and then* the answer travels back,
        so the event loop gets its turn after the snapshot was taken. That
        ordering is the whole point — it is the window in which another
        session commits over what we are about to write."""
        snapshot = dict(self.row.extra)
        await asyncio.sleep(0)
        self.conversation.extra = snapshot
        return self.conversation

    async def execute(self, stmt):
        sql = str(stmt).lower()
        if "from conversations" in sql:
            if getattr(stmt, "_for_update_arg", None) is not None and not self._held:
                await self.row.lock.acquire()
                self._held = True
            return _ScalarResult(await self.read())
        await asyncio.sleep(0)
        return _ScalarResult(None)

    async def get(self, model, pk):
        return await self.read() if pk == self.row.id else None

    async def commit(self):
        self.row.extra = dict(self.conversation.extra)
        self._release()

    async def rollback(self):
        self._release()

    def _release(self):
        if self._held:
            self._held = False
            self.row.lock.release()


@pytest.fixture
def row(monkeypatch):
    """`_buffer_inbound` against a row two sessions contend for."""
    shared = Row()
    sessions: list[RowSession] = []

    async def fake_resolve(db, agency_id, identity):
        return SimpleNamespace(id=uuid.uuid4()), await db.read()

    monkeypatch.setattr(wa_module, "resolve_lead_and_conversation", fake_resolve)

    @asynccontextmanager
    async def _task_session():
        session = RowSession(shared)
        sessions.append(session)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    monkeypatch.setattr(wa_module, "task_session", _task_session)
    return SimpleNamespace(shared=shared, sessions=sessions)


def _inbound(message_id: str, text: str) -> InboundPayload:
    return InboundPayload(
        identity=InboundIdentity(
            channel=ConversationChannel.WHATSAPP,
            whatsapp_wa_id=WA_ID,
            external_thread_id=WA_ID,
        ),
        agency_id=uuid.uuid4(),
        text=text,
        external_message_id=message_id,
    )


async def test_two_messages_at_once_get_two_different_turns(row):
    """The bug this guards: unsynchronized, both webhooks read the same empty
    buffer, both write back one item, and both end up owning `seq == 1`. Both
    then pass "am I still the last one?" twelve seconds later and the lead
    gets two answers to one burst — with one of the two messages missing from
    each of them."""
    agency_id = uuid.uuid4()

    results = await asyncio.gather(
        wa_module._buffer_inbound(agency_id, _inbound("wamid.1", "Hola")),
        wa_module._buffer_inbound(agency_id, _inbound("wamid.2", "busco piso")),
    )

    seqs = sorted(seq for seq, _conv_id in results)
    assert seqs == [1, 2]
    items = row.shared.extra["pending_buffer"]["items"]
    assert [i["text"] for i in items] == ["Hola", "busco piso"]
    assert row.shared.extra["pending_buffer"]["seq"] == 2


async def test_only_the_last_turn_claims_the_buffer(row):
    """And the earlier run stays quiet instead of answering the same burst
    a second time."""
    agency_id = uuid.uuid4()
    (first, conv_id), (second, _) = await asyncio.gather(
        wa_module._buffer_inbound(agency_id, _inbound("wamid.1", "Hola")),
        wa_module._buffer_inbound(agency_id, _inbound("wamid.2", "busco piso")),
    )
    older, newer = sorted([first, second])

    async def _claim(seq):
        async with wa_module.task_session() as db:
            return await wa_module._claim_buffer(db, conv_id, seq)

    assert await _claim(older) is None
    assert await _claim(newer) == "Hola\nbusco piso"


async def test_a_claim_racing_a_new_message_does_not_swallow_it(row):
    """The other half of the race: a run draining the buffer must not write
    back over an append that landed while it was reading, which would leave
    that message with a `seq` nobody owns and nobody would ever answer."""
    agency_id = uuid.uuid4()
    _seq, conv_id = await wa_module._buffer_inbound(agency_id, _inbound("wamid.1", "Hola"))

    async def _claim():
        async with wa_module.task_session() as db:
            return await wa_module._claim_buffer(db, conv_id, 1)

    claimed, (new_seq, _) = await asyncio.gather(
        _claim(),
        wa_module._buffer_inbound(agency_id, _inbound("wamid.2", "y en Alicante")),
    )

    if claimed is None:
        # The append won: its own run owns everything, both texts included.
        assert new_seq == 2
        assert [i["text"] for i in row.shared.extra["pending_buffer"]["items"]] == [
            "Hola",
            "y en Alicante",
        ]
    else:
        # The claim won: the new message opened a fresh buffer of its own,
        # and that buffer is still there for its run to pick up.
        assert claimed == "Hola"
        assert [i["text"] for i in row.shared.extra["pending_buffer"]["items"]] == [
            "y en Alicante"
        ]


def test_the_buffer_is_read_under_a_row_lock(env):
    """A regression guard on the mechanism, not just on the outcome: drop the
    `FOR UPDATE` and the two tests above go back to passing by luck."""
    client = TestClient(app)
    with client:
        _post(client, "wamid.1", "Hola")
    # Once to append, once to claim.
    assert env.session.locked_for_update == 2
