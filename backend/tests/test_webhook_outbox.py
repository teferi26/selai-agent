"""Transactional CRM outbox: durability, retry, idempotency and races."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

from app.services import event_dispatcher as dispatcher
from app.workers import tasks as worker_tasks
from app.workers.celery_app import celery_app

AGENCY_ID = uuid.uuid4()


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _SubscriptionsResult:
    def __init__(self, subscriptions):
        self.subscriptions = subscriptions

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self.subscriptions))


class _EmitDb:
    def __init__(self, sub):
        self.sub = sub
        self.inserts = []

    async def execute(self, statement):
        if isinstance(statement, Select):
            return _SubscriptionsResult([self.sub])
        self.inserts.append(statement)
        return SimpleNamespace(rowcount=1)


def _subscription(*, active: bool = True, failures: int = 0):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        is_active=active,
        consecutive_failures=failures,
        last_success_at=None,
        last_failure_at=None,
        secret="unused-in-mocked-delivery",
        target_url="https://crm.example.test/hook",
    )


async def test_emit_persists_the_queue_without_touching_redis(monkeypatch):
    sub = _subscription()
    db = _EmitDb(sub)
    payload = {"lead_id": "lead-1", "name": "Ana"}

    def _broker_down(*_args, **_kwargs):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(worker_tasks.deliver_webhook, "delay", _broker_down)

    assert await dispatcher.emit(db, "lead.created", AGENCY_ID, payload) == 1
    assert len(db.inserts) == 1

    compiled = db.inserts[0].compile(dialect=postgresql.dialect())
    params = compiled.params
    assert params["subscription_id"] == sub.id
    assert params["agency_id"] == AGENCY_ID
    assert params["payload"] == payload
    assert "ON CONFLICT ON CONSTRAINT uq_webhook_outbox_subscription_dedup DO NOTHING" in str(
        compiled
    )


async def test_concurrent_replays_resolve_to_the_same_outbox_identity():
    sub = _subscription()
    db = _EmitDb(sub)
    payload = {"visit_id": "visit-1", "scheduled_at": "2026-09-03T10:00:00Z"}

    await dispatcher.emit(db, "visit.scheduled", AGENCY_ID, payload)
    await dispatcher.emit(db, "visit.scheduled", AGENCY_ID, dict(reversed(payload.items())))

    ids = [
        statement.compile(dialect=postgresql.dialect()).params["id"]
        for statement in db.inserts
    ]
    assert ids[0] == ids[1]


async def test_marker_is_not_written_when_the_outbox_cannot_be_staged(monkeypatch):
    row = SimpleNamespace(id=uuid.uuid4(), extra={})

    async def _database_failure(*_args, **_kwargs):
        raise RuntimeError("postgres write failed")

    monkeypatch.setattr(dispatcher, "emit", _database_failure)

    with pytest.raises(RuntimeError, match="postgres write failed"):
        await dispatcher.emit_once(
            SimpleNamespace(),
            "offer.created",
            AGENCY_ID,
            {"offer_id": str(row.id)},
            row=row,
        )

    assert dispatcher.EVENTS_MARKER_KEY not in row.extra


class _DrainDb:
    def __init__(self, item, sub, *, fail_commit: bool = False):
        self.item = item
        self.sub = sub
        self.fail_commit = fail_commit
        self.commits = 0
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        item, self.item = self.item, None
        return _ScalarResult(item)

    async def get(self, _model, _pk):
        return self.sub

    async def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("database disappeared before acknowledgement")


def _outbox(*, delivery_id: uuid.UUID | None = None):
    return SimpleNamespace(
        id=delivery_id or uuid.uuid4(),
        subscription_id=uuid.uuid4(),
        event="lead.created",
        payload={"lead_id": "lead-1", "email": "ana@example.com"},
        attempt_count=0,
        available_at=datetime.now(UTC),
        processed_at=None,
        delivered_at=None,
        last_error=None,
    )


async def test_success_is_committed_and_uses_a_stable_public_delivery_id(monkeypatch):
    item = _outbox()
    sub = _subscription()
    db = _DrainDb(item, sub)
    seen = []

    async def _delivered(_sub, _event, _payload, **kwargs):
        seen.append(kwargs["delivery_id"])
        return True, 204, None

    monkeypatch.setattr(dispatcher, "deliver_now", _delivered)

    stats = await dispatcher.drain_webhook_outbox(db)

    assert stats == {"delivered": 1, "retry": 0, "discarded": 0}
    assert seen == [item.id]
    assert item.processed_at is not None
    assert item.delivered_at is not None
    assert item.payload == {}, "completed rows must not retain lead PII"
    assert db.commits == 1


async def test_transient_failure_stays_durable_with_backoff(monkeypatch):
    item = _outbox()
    original_payload = dict(item.payload)
    sub = _subscription()
    db = _DrainDb(item, sub)

    async def _failed(*_args, **_kwargs):
        return False, 503, "http_503"

    monkeypatch.setattr(dispatcher, "deliver_now", _failed)
    before = datetime.now(UTC)

    stats = await dispatcher.drain_webhook_outbox(db)

    assert stats == {"delivered": 0, "retry": 1, "discarded": 0}
    assert item.processed_at is None
    assert item.attempt_count == 1
    assert item.available_at > before
    assert item.last_error == "http_503"
    assert item.payload == original_payload
    assert sub.consecutive_failures == 1
    assert db.commits == 1


async def test_crash_after_remote_2xx_retries_the_same_delivery_id(monkeypatch):
    delivery_id = uuid.uuid4()
    first_item = _outbox(delivery_id=delivery_id)
    sub = _subscription()
    first_db = _DrainDb(first_item, sub, fail_commit=True)
    seen = []

    async def _delivered(_sub, _event, _payload, **kwargs):
        seen.append(kwargs["delivery_id"])
        return True, 200, None

    monkeypatch.setattr(dispatcher, "deliver_now", _delivered)

    with pytest.raises(RuntimeError, match="before acknowledgement"):
        await dispatcher.drain_webhook_outbox(first_db)

    # PostgreSQL rolled the failed transaction back; reconstruct what the
    # next worker reads and prove the receiver sees the same idempotency key.
    replay = _outbox(delivery_id=delivery_id)
    await dispatcher.drain_webhook_outbox(_DrainDb(replay, sub))

    assert seen == [delivery_id, delivery_id]


async def test_claim_uses_skip_locked_so_two_workers_do_not_send_one_row():
    db = _DrainDb(None, None)

    await dispatcher.drain_webhook_outbox(db)

    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql


def test_wire_body_can_be_rebuilt_with_the_same_delivery_id():
    delivery_id = uuid.uuid4()
    kwargs = {
        "event": "lead.created",
        "agency_id": AGENCY_ID,
        "payload": {"lead_id": "lead-1"},
        "secret": "test-secret",
        "delivery_id": delivery_id,
    }

    first, _ = dispatcher.build_delivery(**kwargs)
    second, _ = dispatcher.build_delivery(**kwargs)

    assert first == second
    assert json.loads(first)["delivery_id"] == str(delivery_id)


def test_outbox_drainer_is_registered_and_periodic():
    assert worker_tasks.drain_webhook_outbox.name == "webhooks.drain_outbox"
    assert "webhooks.drain_outbox" in celery_app.tasks
    schedule = celery_app.conf.beat_schedule["drain-webhook-outbox"]
    assert schedule["task"] == "webhooks.drain_outbox"
    assert float(schedule["schedule"]) <= 10.0
