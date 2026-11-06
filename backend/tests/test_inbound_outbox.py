"""Durable inbound receipts: ack boundary, retries and worker races."""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.dialects import postgresql

from app.api import email as email_api
from app.api import whatsapp as whatsapp_api
from app.core.settings import settings
from app.db.models import ConversationChannel, InboundOutbox, SubscriptionStatus
from app.db.session import get_db
from app.main import app
from app.services.conversation_service import InboundPayload
from app.services.inbound_outbox import (
    complete_receipt,
    defer_receipt,
    deserialize_inbound,
    enqueue_inbound,
    idempotency_key,
    lock_receipt,
    serialize_inbound,
)
from app.services.lead_resolver import InboundIdentity
from app.workers import tasks as worker_tasks
from app.workers.celery_app import celery_app

AGENCY_ID = uuid.uuid4()


def _payload(
    channel: ConversationChannel = ConversationChannel.WHATSAPP,
    *,
    message_id: str | None = "provider-message-1",
) -> InboundPayload:
    return InboundPayload(
        agency_id=AGENCY_ID,
        identity=InboundIdentity(
            channel=channel,
            external_thread_id="thread-1",
            phone_e164="+34600111222",
            email="ana@example.com",
            whatsapp_wa_id="34600111222",
            display_name="Ana",
            language="es",
        ),
        text="¿Sigue disponible?",
        external_message_id=message_id,
        media=[{"type": "image", "id": "media-1"}],
        raw={"z": 2, "a": 1},
    )


def test_payload_round_trip_keeps_channel_identity_and_provider_id():
    original = _payload()

    restored = deserialize_inbound(serialize_inbound(original))

    assert restored == original
    assert restored.identity.channel is ConversationChannel.WHATSAPP


def test_provider_id_is_the_idempotency_anchor_not_dict_order():
    first = _payload()
    replay = _payload()
    replay.raw = dict(reversed((first.raw or {}).items()))

    assert idempotency_key(first) == idempotency_key(replay)
    replay.external_message_id = "provider-message-2"
    assert idempotency_key(first) != idempotency_key(replay)


def test_missing_provider_id_falls_back_to_a_canonical_payload_hash():
    first = _payload(message_id=None)
    replay = _payload(message_id=None)
    replay.raw = dict(reversed((first.raw or {}).items()))

    assert idempotency_key(first) == idempotency_key(replay)


class _RecordingDb:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace()


async def test_enqueue_is_postgres_only_idempotent_and_deterministic(monkeypatch):
    db = _RecordingDb()

    def redis_is_down(*_args, **_kwargs):
        raise ConnectionError("redis unavailable")

    # The inbound path must not call Celery/Redis before acknowledging.
    monkeypatch.setattr(worker_tasks.drain_inbound_outbox, "delay", redis_is_down)
    first = await enqueue_inbound(
        db,
        channel="whatsapp",
        payload=_payload(),
        context={"phone_number_id": "pnid-1"},
    )
    second = await enqueue_inbound(
        db,
        channel="whatsapp",
        payload=_payload(),
        context={"phone_number_id": "pnid-1"},
    )

    assert first == second
    compiled = db.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "ON CONFLICT ON CONSTRAINT uq_inbound_outbox_agency_channel_key" in sql
    assert "CAST(" in sql and "JSONB" in sql
    assert json.loads(compiled.params["param_1"])["text"] == "¿Sigue disponible?"


def test_schema_has_tenant_scoped_unique_key_and_pending_index():
    constraints = {constraint.name for constraint in InboundOutbox.__table__.constraints}
    assert "uq_inbound_outbox_agency_channel_key" in constraints
    assert "ck_inbound_outbox_channel" in constraints
    pending = {index.name: index for index in InboundOutbox.__table__.indexes}[
        "ix_inbound_outbox_pending"
    ]
    assert "processed_at IS NULL" in str(
        pending.dialect_options["postgresql"]["where"]
    )


def _receipt(*, channel: str = "whatsapp") -> InboundOutbox:
    return InboundOutbox(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        channel=channel,
        idempotency_key="a" * 64,
        payload=serialize_inbound(
            _payload(
                ConversationChannel.EMAIL
                if channel == "email"
                else ConversationChannel.WHATSAPP
            )
        ),
        context={},
        attempt_count=0,
        available_at=datetime.now(UTC),
    )


def test_failure_keeps_payload_and_schedules_exponential_retry():
    receipt = _receipt()
    before = datetime.now(UTC)

    defer_receipt(receipt, RuntimeError("postgres connection reset"))

    assert receipt.processed_at is None
    assert receipt.attempt_count == 1
    assert receipt.available_at > before
    assert "connection reset" in (receipt.last_error or "")
    assert receipt.payload["text"] == "¿Sigue disponible?"


def test_completion_keeps_tombstone_but_erases_lead_pii():
    receipt = _receipt(channel="email")
    receipt.context = {"portal": "idealista"}

    complete_receipt(receipt)

    assert receipt.processed_at is not None
    assert receipt.payload == {}
    assert receipt.context == {}
    assert receipt.idempotency_key == "a" * 64


class _ScalarResult:
    def __init__(self, value=None, *, row=None):
        self.value = value
        self.row = row

    def scalar_one_or_none(self):
        return self.value

    def first(self):
        return self.row


class _ReceiptDb:
    def __init__(
        self,
        receipt,
        *,
        already_processed: bool = False,
        subscription_status: SubscriptionStatus | None = None,
    ):
        self.receipt = receipt
        self.already_processed = already_processed
        self.subscription_status = subscription_status
        self.statements = []
        self.agency = SimpleNamespace(id=AGENCY_ID, slug="inmo-sol", settings={})

    async def execute(self, statement):
        self.statements.append(statement)
        sql = str(statement).lower()
        if "inbound_outbox" in sql:
            return _ScalarResult(self.receipt)
        if "subscriptions" in sql:
            return _ScalarResult(self.subscription_status)
        return _ScalarResult(
            None,
            row=(uuid.uuid4(),) if self.already_processed else None,
        )

    async def get(self, _model, _pk):
        return self.agency

    async def commit(self):
        pass

    async def rollback(self):
        pass


def _task_session(db):
    @asynccontextmanager
    async def factory():
        yield db

    return factory


async def test_processing_failure_is_deferred_and_then_resumed(monkeypatch):
    receipt = _receipt()
    db = _ReceiptDb(receipt)
    monkeypatch.setattr(whatsapp_api, "task_session", _task_session(db))

    async def fail_once(**_kwargs):
        raise RuntimeError("worker killed")

    monkeypatch.setattr(whatsapp_api, "_process_inbound", fail_once)
    await whatsapp_api.process_inbound_receipt(receipt_id=receipt.id)

    assert receipt.processed_at is None
    assert receipt.attempt_count == 1
    receipt.available_at = datetime.now(UTC)

    calls = []

    async def succeed(**kwargs):
        calls.append(kwargs["payload"].external_message_id)

    monkeypatch.setattr(whatsapp_api, "_process_inbound", succeed)
    await whatsapp_api.process_inbound_receipt(receipt_id=receipt.id)

    assert calls == ["provider-message-1"]
    assert receipt.processed_at is not None
    assert receipt.payload == {}


async def test_whatsapp_delivery_failure_retries_staged_reply_without_rerunning_turn(
    monkeypatch,
):
    receipt = _receipt()
    db = _ReceiptDb(receipt)
    monkeypatch.setattr(whatsapp_api, "task_session", _task_session(db))
    turns: list[str] = []
    sends: list[dict] = []

    async def process_once(**kwargs):
        turns.append(kwargs["payload"].external_message_id)
        return {"to": "34600111222", "text": "Sí, sigue disponible"}

    async def fail_then_send(_settings, _phone_number_id, reply):
        sends.append(dict(reply))
        if len(sends) == 1:
            raise ConnectionError("Meta timeout")

    monkeypatch.setattr(whatsapp_api, "_process_inbound", process_once)
    monkeypatch.setattr(whatsapp_api, "_deliver_pending_reply", fail_then_send)

    await whatsapp_api.process_inbound_receipt(receipt_id=receipt.id)

    assert turns == ["provider-message-1"]
    assert len(sends) == 1
    assert receipt.processed_at is None
    assert receipt.attempt_count == 1
    assert receipt.context["pending_reply"]["text"] == "Sí, sigue disponible"

    receipt.available_at = datetime.now(UTC)
    await whatsapp_api.process_inbound_receipt(receipt_id=receipt.id)

    assert turns == ["provider-message-1"]
    assert len(sends) == 2
    assert receipt.processed_at is not None
    assert receipt.payload == {}
    assert receipt.context == {}


async def test_email_delivery_failure_retries_staged_reply_without_rerunning_turn(
    monkeypatch,
):
    receipt = _receipt(channel="email")
    receipt.context = {"portal": None, "portal_unreadable": False}
    db = _ReceiptDb(receipt)
    monkeypatch.setattr(email_api, "task_session", _task_session(db))
    turns: list[str] = []
    sends: list[dict] = []

    async def process_once(**kwargs):
        turns.append(kwargs["payload"].external_message_id)
        return {
            "to": "ana@example.com",
            "subject": "Re: Piso",
            "text": "Sí, sigue disponible",
            "html": "<p>Sí, sigue disponible</p>",
            "in_reply_to": "provider-message-1",
            "references": ["provider-message-1"],
            "reply_to": "inmo-sol-leads@leads.selai.es",
        }

    async def fail_then_send(reply):
        sends.append(dict(reply))
        if len(sends) == 1:
            raise ConnectionError("Resend timeout")

    monkeypatch.setattr(email_api, "_process_inbound_email", process_once)
    monkeypatch.setattr(email_api, "_deliver_pending_reply", fail_then_send)

    await email_api.process_inbound_email_receipt(receipt_id=receipt.id)

    assert turns == ["provider-message-1"]
    assert len(sends) == 1
    assert receipt.processed_at is None
    assert receipt.attempt_count == 1
    assert receipt.context["pending_reply"]["subject"] == "Re: Piso"

    receipt.available_at = datetime.now(UTC)
    await email_api.process_inbound_email_receipt(receipt_id=receipt.id)

    assert turns == ["provider-message-1"]
    assert len(sends) == 2
    assert receipt.processed_at is not None
    assert receipt.payload == {}
    assert receipt.context == {}


async def test_purged_agency_receipt_is_scrubbed_without_recreating_whatsapp_data(
    monkeypatch,
):
    receipt = _receipt()
    db = _ReceiptDb(receipt)
    db.agency.settings = {"operational_data_purged_at": datetime.now(UTC).isoformat()}
    monkeypatch.setattr(whatsapp_api, "task_session", _task_session(db))

    async def must_not_process(**_kwargs):
        raise AssertionError("a purged agency receipt must never run the inbound turn")

    monkeypatch.setattr(whatsapp_api, "_process_inbound", must_not_process)

    await whatsapp_api.process_inbound_receipt(receipt_id=receipt.id)

    assert receipt.processed_at is not None
    assert receipt.payload == {}
    assert receipt.context == {}


async def test_canceled_agency_receipt_is_scrubbed_without_recreating_email_data(
    monkeypatch,
):
    receipt = _receipt(channel="email")
    db = _ReceiptDb(receipt, subscription_status=SubscriptionStatus.CANCELED)
    monkeypatch.setattr(email_api, "task_session", _task_session(db))

    async def must_not_process(**_kwargs):
        raise AssertionError("a canceled agency receipt must never run the inbound turn")

    monkeypatch.setattr(email_api, "_process_inbound_email", must_not_process)

    await email_api.process_inbound_email_receipt(receipt_id=receipt.id)

    assert receipt.processed_at is not None
    assert receipt.payload == {}
    assert receipt.context == {}


async def test_whatsapp_restart_rejoins_the_durable_debounce_buffer(monkeypatch):
    payload = _payload()
    conversation_id = uuid.uuid4()
    conversation = SimpleNamespace(
        id=conversation_id,
        extra={
            "pending_buffer": {
                "seq": 3,
                "items": [
                    {"id": payload.external_message_id, "text": payload.text}
                ],
            }
        },
    )
    db = _ReceiptDb(None)
    monkeypatch.setattr(whatsapp_api, "task_session", _task_session(db))

    async def resolve(*_args, **_kwargs):
        return SimpleNamespace(), conversation

    async def lock(*_args, **_kwargs):
        return conversation

    monkeypatch.setattr(whatsapp_api, "resolve_lead_and_conversation", resolve)
    monkeypatch.setattr(whatsapp_api, "_lock_conversation", lock)

    assert await whatsapp_api._buffer_inbound(
        AGENCY_ID, payload, resume_existing=True
    ) == (3, conversation_id)
    # The legacy/non-outbox path keeps treating it as an ordinary duplicate.
    assert await whatsapp_api._buffer_inbound(AGENCY_ID, payload) == (
        None,
        conversation_id,
    )


async def test_crash_after_domain_commit_does_not_answer_twice(monkeypatch):
    """A replay sees the inbound Message row and only closes the receipt."""
    receipt = _receipt()
    db = _ReceiptDb(receipt, already_processed=True)
    monkeypatch.setattr(whatsapp_api, "task_session", _task_session(db))

    async def must_not_run(**_kwargs):
        raise AssertionError("agent/reply ran twice")

    monkeypatch.setattr(whatsapp_api, "_process_inbound", must_not_run)
    await whatsapp_api.process_inbound_receipt(receipt_id=receipt.id)

    assert receipt.processed_at is not None
    assert receipt.payload == {}


async def test_receipt_claim_uses_skip_locked_for_worker_races():
    db = _ReceiptDb(None)

    assert await lock_receipt(db, uuid.uuid4(), channel="email") is None

    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql


class _RouteDb:
    def __init__(self, agency, *, fail_commit: bool = False):
        self.agency = agency
        self.fail_commit = fail_commit
        self.inserted = False
        self.committed = False

    async def execute(self, statement):
        sql = str(statement).lower()
        if sql.startswith("insert into inbound_outbox"):
            self.inserted = True
            return _ScalarResult()
        if "messages" in sql:
            return _ScalarResult(None, row=None)
        if "subscriptions" in sql:
            return _ScalarResult(None)
        if "agencies" in sql:
            return _ScalarResult(self.agency)
        return _ScalarResult(None)

    async def get(self, _model, _pk):
        return self.agency

    async def commit(self):
        assert self.inserted, "the 2xx boundary committed before staging the receipt"
        if self.fail_commit:
            raise RuntimeError("postgres unavailable")
        self.committed = True

    async def rollback(self):
        pass


def _override_db(db):
    async def override():
        yield db

    app.dependency_overrides[get_db] = override


def _email_body() -> dict:
    return {
        "From": "Ana <ana@example.com>",
        "To": "inmo-sol-leads@leads.selai.es",
        "Subject": "Piso",
        "MessageID": "<email-provider-1@example.com>",
        "TextBody": "Me interesa el piso",
    }


def _whatsapp_body() -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "pnid-agency"},
                            "contacts": [
                                {"wa_id": "34600111222", "profile": {"name": "Ana"}}
                            ],
                            "messages": [
                                {
                                    "from": "34600111222",
                                    "id": "wamid.DURABLE",
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


@pytest.mark.parametrize("channel", ["email", "whatsapp"])
def test_routes_commit_receipt_before_background_processing(monkeypatch, channel):
    agency = SimpleNamespace(
        id=AGENCY_ID,
        slug="inmo-sol",
        contact_email="owner@example.com",
        settings={
            "whatsapp_access_token": "token",
            "whatsapp_phone_number_id": "pnid-agency",
        },
    )
    db = _RouteDb(agency)
    _override_db(db)
    processed = []

    async def process(**_kwargs):
        assert db.committed
        processed.append(True)

    try:
        if channel == "email":
            monkeypatch.setattr(settings, "secret_key", SecretStr("webhook-secret"))
            monkeypatch.setattr(email_api, "process_inbound_email_receipt", process)
            response = TestClient(app).post(
                "/webhooks/email",
                json=_email_body(),
                headers={"X-Webhook-Secret": "webhook-secret"},
            )
        else:
            monkeypatch.setattr(settings, "app_env", "development")
            monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(""))
            monkeypatch.setattr(whatsapp_api, "process_inbound_receipt", process)
            response = TestClient(app).post(
                "/webhooks/whatsapp",
                json=_whatsapp_body(),
                headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert db.inserted and db.committed
    assert processed == [True]


@pytest.mark.parametrize("channel", ["email", "whatsapp"])
def test_routes_do_not_ack_when_the_receipt_commit_fails(monkeypatch, channel):
    agency = SimpleNamespace(
        id=AGENCY_ID,
        slug="inmo-sol",
        contact_email="owner@example.com",
        settings={
            "whatsapp_access_token": "token",
            "whatsapp_phone_number_id": "pnid-agency",
        },
    )
    db = _RouteDb(agency, fail_commit=True)
    _override_db(db)
    processed = []

    async def process(**_kwargs):
        processed.append(True)

    client = TestClient(app, raise_server_exceptions=False)
    try:
        if channel == "email":
            monkeypatch.setattr(settings, "secret_key", SecretStr("webhook-secret"))
            monkeypatch.setattr(email_api, "process_inbound_email_receipt", process)
            response = client.post(
                "/webhooks/email",
                json=_email_body(),
                headers={"X-Webhook-Secret": "webhook-secret"},
            )
        else:
            monkeypatch.setattr(settings, "app_env", "development")
            monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(""))
            monkeypatch.setattr(whatsapp_api, "process_inbound_receipt", process)
            response = client.post(
                "/webhooks/whatsapp",
                json=_whatsapp_body(),
                headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 500
    assert db.inserted and not db.committed
    assert processed == []


def test_inbound_drainer_is_registered_and_periodic():
    assert worker_tasks.drain_inbound_outbox.name == "channels.drain_inbound_outbox"
    assert "channels.drain_inbound_outbox" in celery_app.tasks
    schedule = celery_app.conf.beat_schedule["drain-inbound-outbox"]
    assert schedule["task"] == "channels.drain_inbound_outbox"
    assert float(schedule["schedule"]) <= 5.0
