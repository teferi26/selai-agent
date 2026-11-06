"""Termination retention is automatic, idempotent and tenant-scoped."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from celery.schedules import crontab

from app.services import retention, storage
from app.workers import tasks
from app.workers.celery_app import celery_app


class _Db:
    def __init__(self, subscriptions=None, agency=None):
        self.statements = []
        self.subscriptions = list(subscriptions or [])
        self.agency = agency
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: list(self.subscriptions))
        )

    async def get(self, _model, _key):
        return self.agency

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _agency(**overrides):
    values = {
        "id": uuid.uuid4(),
        "settings": {"whatsapp_access_token": "must-disappear"},
        "contact_phone": "+34123456789",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _subscription(days=30):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=uuid.uuid4(),
        status=SimpleNamespace(value="canceled"),
        canceled_at=datetime.now(UTC) - timedelta(days=days),
        voice_minutes_used=17,
        leads_processed=23,
    )


async def test_purge_removes_storage_blocks_users_and_keeps_only_marker(monkeypatch):
    agency = _agency()
    sub = _subscription()
    sub.agency_id = agency.id
    db = _Db()
    deleted = []
    events = []

    async def _inventory(_db, *, agency):
        assert _db is db
        events.append(("inventory", agency.id))
        return 7

    def _delete_storage(prefix):
        events.append(("storage", prefix))
        deleted.append(prefix)

    monkeypatch.setattr(retention, "queue_agency_external_erasures", _inventory)
    monkeypatch.setattr(retention, "delete_prefix", _delete_storage)
    now = datetime.now(UTC)

    changed = await retention.purge_terminated_agency_data(
        db,
        agency=agency,
        subscription=sub,
        purged_at=now,
    )

    assert changed is True
    assert deleted == [f"agencies/{agency.id}"]
    assert events == [
        ("inventory", agency.id),
        ("storage", f"agencies/{agency.id}"),
    ]
    assert agency.settings == {retention.PURGED_AT_KEY: now.isoformat()}
    assert agency.contact_phone is None
    assert sub.voice_minutes_used == 0
    assert sub.leads_processed == 0
    sql = "\n".join(str(statement) for statement in db.statements)
    for table in (
        "messages",
        "agent_runs",
        "leads",
        "conversations",
        "properties",
        "inbound_outbox",
        "webhook_outbox",
        "webhook_subscriptions",
        "device_tokens",
        "invitations",
        "message_templates",
        "users",
    ):
        assert table in sql


async def test_purge_replay_is_a_noop(monkeypatch):
    agency = _agency(settings={retention.PURGED_AT_KEY: "2026-09-29T03:15:00+00:00"})
    db = _Db()
    monkeypatch.setattr(
        retention,
        "delete_prefix",
        lambda _prefix: pytest.fail("storage must not be touched on a completed replay"),
    )

    changed = await retention.purge_terminated_agency_data(
        db,
        agency=agency,
        subscription=_subscription(),
        purged_at=datetime.now(UTC),
    )

    assert changed is False
    assert db.statements == []


def test_local_prefix_deletion_cannot_escape_tenant_root(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "LOCAL_ROOT", tmp_path.resolve())
    monkeypatch.setattr(storage, "_s3_enabled", lambda: False)
    target = tmp_path / "agencies" / "a" / "offers"
    sibling = tmp_path / "agencies" / "b"
    target.mkdir(parents=True)
    sibling.mkdir(parents=True)
    (target / "one.pdf").write_bytes(b"one")
    (target / "two.pdf").write_bytes(b"two")
    (sibling / "keep.pdf").write_bytes(b"keep")

    assert storage.delete_prefix("agencies/a") == 2
    assert not (tmp_path / "agencies" / "a").exists()
    assert (sibling / "keep.pdf").exists()
    with pytest.raises(ValueError):
        storage.delete_prefix("../")


async def test_daily_sweep_purges_eligible_agency_and_commits(monkeypatch):
    agency = _agency()
    sub = _subscription(days=30)
    sub.agency_id = agency.id
    db = _Db([sub], agency)

    @asynccontextmanager
    async def _scope():
        yield db

    async def _purge(_db, *, agency, subscription, purged_at):
        assert _db is db
        assert subscription is sub
        assert purged_at.tzinfo is not None
        agency.settings = {retention.PURGED_AT_KEY: purged_at.isoformat()}
        return True

    monkeypatch.setattr(tasks, "session_scope", _scope)
    monkeypatch.setattr(retention, "purge_terminated_agency_data", _purge)

    stats = await tasks._terminated_data_purge_sweep()

    assert stats == {"purged": 1, "skipped": 0, "failed": 0}
    assert db.commits == 1


def test_retention_purge_runs_daily_before_the_30_day_limit():
    entry = celery_app.conf.beat_schedule["lifecycle-terminated-data-purge-sweep"]
    assert entry["task"] == "lifecycle.terminated_data_purge_sweep"
    assert entry["schedule"] == crontab(minute=15, hour=3)
    assert tasks.TERMINATED_DATA_PURGE_AFTER_DAYS < 30


@pytest.mark.parametrize(
    ("status", "days", "expected"),
    [("canceled", 30, True), ("active", 30, False), ("canceled", 10, False)],
)
def test_candidate_predicate_requires_canceled_status_and_cutoff(status, days, expected):
    sub = _subscription(days=days)
    sub.status = SimpleNamespace(value=status)
    cutoff = datetime.now(UTC) - timedelta(days=29)
    assert retention.is_retention_purge_candidate(sub, cutoff=cutoff) is expected
