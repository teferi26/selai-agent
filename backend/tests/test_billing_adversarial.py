"""Adversarial review of the BILLING track.

Every test here started life as a way to break the shipped behaviour and is
kept as the regression guard for the fix:

- an out-of-order Stripe delivery emitted in the *same second* walking a
  subscription backwards (ACTIVE -> TRIALING, CANCELED -> ACTIVE);
- Stripe's per-retry `invoice.payment_failed` events flooding the owner with
  dunning email and restarting the 3/7-day clock;
- a PAUSED (expired-trial) agency whose AI agent keeps answering calls;
- the same `end-of-call-report` billing its minutes twice when the caller
  withheld their number;
- a paused trial whose owner is never actually told;
- an abandoned checkout leaving a trial that no sweep can ever expire.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import billing as billing_module
from app.api import voice as voice_module
from app.core.settings import settings
from app.db.models import ConversationStatus, SubscriptionStatus
from app.db.models.billing import ProcessedStripeEvent
from app.db.session import get_db
from app.main import app
from app.workers import tasks

CUSTOMER = "cus_adv"
STRIPE_SUB = "sub_adv"


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows, scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        if self._scalar is not None:
            return self._scalar
        return self._rows[0] if self._rows else None


def _bound(stmt, prefix: str) -> str | None:
    for value in stmt.compile().params.values():
        if isinstance(value, str) and value.startswith(prefix):
            return value
    return None


def _scope(session):
    @asynccontextmanager
    async def _fake():
        yield session

    return _fake


def _wanted(stmt) -> set:
    """The subscription statuses a compiled query actually filters on."""
    return {
        value
        for value in stmt.compile().params.values()
        if isinstance(value, SubscriptionStatus)
    }


# ---------------------------------------------------------------------------
# 1. Webhook — out-of-order deliveries inside the same second
# ---------------------------------------------------------------------------


class WebhookSession:
    def __init__(self, subscription, agency=None):
        self.subscription = subscription
        self.agency = agency
        self.processed: dict[str, ProcessedStripeEvent] = {}
        self.pending: list = []

    async def get(self, model, pk):
        if model is ProcessedStripeEvent:
            return self.processed.get(pk)
        if self.agency is not None and pk == self.agency.id:
            return self.agency
        return None

    async def execute(self, stmt):
        sql = str(stmt)
        if "processed_stripe_events" in sql:
            stream = _bound(stmt, "cus_")
            seen = [
                e.event_created
                for e in self.processed.values()
                if e.event_created is not None and e.object_id == stream
            ]
            return _Result([], scalar=max(seen) if seen else None)
        if "FROM plans" in sql:
            return _Result([])
        if "FROM users" in sql:
            return _Result([])
        if "FROM subscriptions" in sql:
            return _Result([self.subscription] if self.subscription else [])
        raise AssertionError(f"unexpected query: {sql}")

    def add(self, obj):
        self.pending.append(obj)

    async def commit(self):
        for obj in self.pending:
            if isinstance(obj, ProcessedStripeEvent):
                self.processed[obj.event_id] = obj
        self.pending = []

    async def rollback(self):
        self.pending = []


def _webhook_subscription(status=SubscriptionStatus.TRIALING):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=uuid.uuid4(),
        plan_id=uuid.uuid4(),
        status=status,
        stripe_customer_id=CUSTOMER,
        stripe_subscription_id=STRIPE_SUB,
        current_period_end=None,
        canceled_at=None,
    )


@pytest.fixture
def wsub():
    return _webhook_subscription()


@pytest.fixture
def wdb(wsub):
    session = WebhookSession(wsub)

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield session
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def wclient(monkeypatch, wdb):
    monkeypatch.setattr(billing_module, "verify_webhook_signature", lambda raw, sig: True)
    return TestClient(app)


def _post(client, event: dict):
    return client.post(
        "/api/v1/billing/webhook", json=event, headers={"stripe-signature": "t=1,v1=x"}
    )


def _updated(status: str, event_id: str, created: int = 1000):
    return {
        "id": event_id,
        "type": "customer.subscription.updated",
        "created": created,
        "data": {
            "object": {
                "id": STRIPE_SUB,
                "customer": CUSTOMER,
                "status": status,
                "items": {"data": []},
            }
        },
    }


def test_same_second_delivery_cannot_walk_active_back_to_trialing(wclient, wdb, wsub):
    """`event.created` has one-second resolution, so the timestamp guard only
    drops *strictly* older deliveries. Stripe emits several subscription
    events in the same second when a trial converts; if the `trialing` one is
    delivered last, a paying agency must not be pushed back into a trial."""
    _post(wclient, _updated("active", "evt_active", created=1000))
    assert wsub.status == SubscriptionStatus.ACTIVE

    resp = _post(wclient, _updated("trialing", "evt_trialing", created=1000))

    assert resp.status_code == 200
    assert wsub.status == SubscriptionStatus.ACTIVE


def test_same_second_delivery_cannot_revive_a_canceled_subscription(wclient, wdb, wsub):
    """Cancellation emits `updated` and `deleted` together. If `deleted`
    lands first, the trailing `updated` must not resurrect the account."""
    _post(
        wclient,
        {
            "id": "evt_deleted",
            "type": "customer.subscription.deleted",
            "created": 2000,
            "data": {"object": {"id": STRIPE_SUB, "customer": CUSTOMER}},
        },
    )
    assert wsub.status == SubscriptionStatus.CANCELED

    _post(wclient, _updated("active", "evt_late_active", created=2000))

    assert wsub.status == SubscriptionStatus.CANCELED


def test_forward_transitions_still_apply(wclient, wdb, wsub):
    """The guard must only block regressions, never real lifecycle moves."""
    _post(wclient, _updated("active", "evt_1", created=1000))
    assert wsub.status == SubscriptionStatus.ACTIVE

    _post(wclient, _updated("past_due", "evt_2", created=1001))
    assert wsub.status == SubscriptionStatus.PAST_DUE

    _post(wclient, _updated("active", "evt_3", created=1002))
    assert wsub.status == SubscriptionStatus.ACTIVE


@pytest.fixture
def known_plan(monkeypatch):
    """El precio del checkout resuelve a un plan nuestro.

    Antes estos tests no lo montaban y el handler acababa saliendo por la
    rama de "plan desconocido" — que ademas intentaba una llamada REAL a
    Stripe. Ese silencio era el bug n18: ahora el handler exige aplicar el
    plan, asi que el escenario tiene que ser completo.
    """
    plan = SimpleNamespace(id=uuid.uuid4(), code="pro", monthly_price_eur=199)

    async def _price(obj, stripe_subscription_id):
        return "price_pro_month"

    async def _plan(db, price_id):
        return plan

    monkeypatch.setattr(billing_module, "_price_id_for_checkout", _price)
    monkeypatch.setattr(billing_module, "_plan_for_price", _plan)
    return plan


async def test_replayed_checkout_cannot_demote_a_past_due_subscription(known_plan):
    """A `checkout.session.completed` arriving while the account is chasing a
    failed payment must not blank the dunning state by dropping it back into
    a trial."""
    sub = _webhook_subscription(status=SubscriptionStatus.PAST_DUE)
    db = WebhookSession(sub)

    await billing_module._on_checkout_completed(
        db, {"customer": CUSTOMER, "subscription": STRIPE_SUB}
    )

    assert sub.status == SubscriptionStatus.PAST_DUE


@pytest.mark.parametrize(
    "status", [SubscriptionStatus.PAUSED, SubscriptionStatus.CANCELED]
)
async def test_checkout_still_revives_an_account_that_comes_back_and_pays(status, known_plan):
    """The mirror image, and the reason the guard cannot simply freeze every
    non-active status: an expired trial or a former customer who signs up
    again gets a brand-new Stripe subscription, and must stop being blocked.

    It revives straight into ACTIVE, not TRIALING: since (183) this customer
    has no trial left, so the checkout charged the card on the spot. Saying
    "trialing" would leave a paying customer showing an expired trial until
    Stripe next changed something on the subscription — possibly a month.
    """
    sub = _webhook_subscription(status=status)
    db = WebhookSession(sub)

    await billing_module._on_checkout_completed(
        db, {"customer": CUSTOMER, "subscription": "sub_new"}
    )

    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.stripe_subscription_id == "sub_new"


# ---------------------------------------------------------------------------
# 2. Dunning — Stripe retries must not turn into an email flood
# ---------------------------------------------------------------------------


class DunningSession:
    def __init__(self, subscriptions, agencies, owner=None):
        self.subscriptions = list(subscriptions)
        self.agencies = {a.id: a for a in agencies}
        self.owner = owner

    async def get(self, model, pk):
        return self.agencies.get(pk)

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM users" in sql:
            return _Result([self.owner] if self.owner else [])
        if "FROM subscriptions" in sql:
            return _Result(self.subscriptions)
        raise AssertionError(f"unexpected query: {sql}")

    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass


def _dunning_agency():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Inmo Sol",
        contact_email="dueno@inmosol.example.com",
        settings={},
    )


@pytest.fixture
def sent(monkeypatch):
    box: list[dict] = []

    async def _send(to, subject, html):
        box.append({"to": to, "subject": subject, "html": html})
        return True

    monkeypatch.setattr(billing_module, "send_email", _send)
    return box


def _elapsed(agency, days: float) -> None:
    """Pretend `days` have passed since the dunning episode started."""
    blob = dict(agency.settings)
    if "dunning_started_at" in blob:
        blob["dunning_started_at"] = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    agency.settings = blob


async def test_stripe_retries_do_not_flood_the_owner(monkeypatch, sent):
    """Stripe fires one `invoice.payment_failed` per retry attempt, each with
    its own event id, so the webhook dedup never sees them as duplicates.
    Every one of them used to send another "attempt 1" email *and* reset
    `dunning_count`, which re-armed the daily sweep — the owner ended up with
    a mailbox full of the same warning. We promise three."""
    agency = _dunning_agency()
    sub = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency.id,
        status=SubscriptionStatus.ACTIVE,
        stripe_customer_id=CUSTOMER,
        stripe_subscription_id=STRIPE_SUB,
    )
    db = DunningSession([sub], [agency])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    stripe_retry_days = {0, 3, 5, 7, 11}
    for day in range(15):
        if day in stripe_retry_days:
            after_commit = await billing_module._on_payment_failed(db, {"customer": CUSTOMER})
            assert after_commit is not None
            await after_commit()
        _elapsed(agency, day)
        await tasks._dunning_sweep()

    assert len(sent) == 3, f"{len(sent)} dunning emails sent, expected at most 3"


async def test_payment_failed_after_cancellation_does_not_revive_the_account(sent):
    """Stripe can still fail to collect a final invoice once a subscription
    is gone. PAST_DUE is deliberately *not* a blocking status (it is the
    grace period while we chase the card), so writing it over CANCELED would
    hand a departed customer their service back — and start emailing them."""
    agency = _dunning_agency()
    sub = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency.id,
        status=SubscriptionStatus.CANCELED,
        stripe_customer_id=CUSTOMER,
        stripe_subscription_id=STRIPE_SUB,
    )
    db = DunningSession([sub], [agency])

    after_commit = await billing_module._on_payment_failed(db, {"customer": CUSTOMER})

    assert sub.status == SubscriptionStatus.CANCELED
    assert after_commit is None
    assert sent == []


async def test_a_new_incident_after_recovery_starts_a_fresh_sequence(monkeypatch, sent):
    """The throttle must not be permanent: once the payment recovers, the
    next failure has to be able to warn the owner again."""
    agency = _dunning_agency()
    sub = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency.id,
        status=SubscriptionStatus.ACTIVE,
        stripe_customer_id=CUSTOMER,
        stripe_subscription_id=STRIPE_SUB,
    )
    db = DunningSession([sub], [agency])

    after_commit = await billing_module._on_payment_failed(db, {"customer": CUSTOMER})
    assert after_commit is not None
    await after_commit()
    assert len(sent) == 1

    await billing_module._on_subscription_updated(
        db,
        {"id": STRIPE_SUB, "customer": CUSTOMER, "status": "active", "items": {"data": []}},
    )
    assert "dunning_count" not in agency.settings

    after_commit = await billing_module._on_payment_failed(db, {"customer": CUSTOMER})
    assert after_commit is not None
    await after_commit()
    assert len(sent) == 2


# ---------------------------------------------------------------------------
# 3. PAUSED has to block the endpoint that actually spends money
# ---------------------------------------------------------------------------


class VoiceBrainSession:
    def __init__(self, subscription, plan=None):
        self.subscription = subscription
        self.plan = plan

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM subscriptions" in sql:
            return _Result([], scalar=self.subscription)
        raise AssertionError(f"unexpected query: {sql}")

    async def get(self, model, pk):
        return self.plan

    async def commit(self):
        pass

    async def rollback(self):
        pass


AGENCY_ID = uuid.uuid4()


@pytest.fixture
def voice_client(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")

    async def _resolve_agency(db, metadata):
        return AGENCY_ID

    monkeypatch.setattr(voice_module, "_resolve_agency", _resolve_agency)
    return TestClient(app)


def _wire_voice_db(session):
    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_db, None)


def _llm_body():
    return {
        "messages": [{"role": "user", "content": "¿Tenéis pisos en Chamberí?"}],
        "metadata": {"agency_id": str(AGENCY_ID)},
        "call": {"id": "call_paused"},
    }


def _reply_of(resp) -> str:
    return resp.json()["choices"][0]["message"]["content"]


def test_paused_agency_is_not_served_by_the_voice_brain(voice_client, monkeypatch):
    """`BLOCKING_STATUSES` only bites where somebody calls it. Nothing gated
    the inbound brain, so an expired trial kept answering calls (and burning
    Claude + Vapi money) exactly as if it were paying."""
    handled: list = []

    async def _handle_inbound(db, payload):
        handled.append(payload)
        return SimpleNamespace(reply_text="Sí, tenemos tres pisos en Chamberí.")

    monkeypatch.setattr(voice_module, "handle_inbound", _handle_inbound)

    sub = SimpleNamespace(
        status=SubscriptionStatus.PAUSED, plan_id=uuid.uuid4(), voice_minutes_used=0
    )
    _wire_voice_db(VoiceBrainSession(sub, plan=SimpleNamespace(id=uuid.uuid4())))

    resp = voice_client.post("/voice/llm", json=_llm_body())

    assert resp.status_code == 200
    assert handled == [], "the agent answered a paused agency's call"
    assert "Chamberí" not in _reply_of(resp)


def test_active_agency_is_still_served(voice_client, monkeypatch):
    async def _handle_inbound(db, payload):
        return SimpleNamespace(reply_text="Sí, tenemos tres pisos en Chamberí.")

    monkeypatch.setattr(voice_module, "handle_inbound", _handle_inbound)

    sub = SimpleNamespace(
        status=SubscriptionStatus.ACTIVE, plan_id=uuid.uuid4(), voice_minutes_used=0
    )
    _wire_voice_db(VoiceBrainSession(sub, plan=SimpleNamespace(id=uuid.uuid4())))

    resp = voice_client.post("/voice/llm", json=_llm_body())

    assert "Chamberí" in _reply_of(resp)


def test_agency_without_a_subscription_is_still_served(voice_client, monkeypatch):
    """Dev environments and the public demo have no subscription row; the
    gate must not lock them out."""

    async def _handle_inbound(db, payload):
        return SimpleNamespace(reply_text="Sí, tenemos tres pisos en Chamberí.")

    monkeypatch.setattr(voice_module, "handle_inbound", _handle_inbound)
    _wire_voice_db(VoiceBrainSession(subscription=None))

    resp = voice_client.post("/voice/llm", json=_llm_body())

    assert "Chamberí" in _reply_of(resp)


# ---------------------------------------------------------------------------
# 4. Voice minutes — dedup must not depend on who the caller is
# ---------------------------------------------------------------------------


class CallSession:
    """Models the part of the schema `_on_call_end` walks: conversations
    looked up by the call id, the agency's subscription, and message counts."""

    def __init__(self, subscription, world):
        self.subscription = subscription
        self.world = world
        self.added: list = []

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM conversations" in sql:
            call_id = _bound(stmt, "call_")
            match = [c for c in self.world.conversations if c.external_thread_id == call_id]
            return _Result(match[:1])
        if "FROM subscriptions" in sql:
            return _Result([], scalar=self.subscription)
        if "count" in sql.lower():
            return _Result([], scalar=0)
        raise AssertionError(f"unexpected query: {sql}")

    async def get(self, model, pk):
        return self.world.leads.get(pk)

    def add(self, obj):
        self.added.append(obj)


class _World:
    """Stands in for `resolve_lead_and_conversation` with its real matching
    rule: leads are found by phone/email/wa_id, so a caller who withheld the
    number matches nothing and gets a brand-new lead + conversation."""

    def __init__(self):
        self.conversations: list = []
        self.leads: dict = {}

    async def resolve(self, db, agency_id, identity):
        if identity.phone_e164:
            for conv in self.conversations:
                if conv.phone == identity.phone_e164:
                    return self.leads[conv.lead_id], conv
        lead = SimpleNamespace(id=uuid.uuid4(), extra={})
        self.leads[lead.id] = lead
        conv = SimpleNamespace(
            id=uuid.uuid4(),
            lead_id=lead.id,
            extra={},
            status=ConversationStatus.ACTIVE,
            external_thread_id=identity.external_thread_id,
            phone=identity.phone_e164,
        )
        self.conversations.append(conv)
        return lead, conv


def _call_body(call_id="call_anon", duration=125, number=None):
    return {
        "message": {
            "type": "end-of-call-report",
            "call": {"id": call_id, "customer": {"number": number} if number else {}},
            "messages": [],
            "durationSeconds": duration,
            "summary": "Preguntaba por el ático de Chamberí.",
        }
    }


@pytest.fixture
def call_world(monkeypatch):
    world = _World()

    async def _resolve_agency(db, metadata):
        return AGENCY_ID

    monkeypatch.setattr(voice_module, "_resolve_agency", _resolve_agency)
    monkeypatch.setattr(voice_module, "resolve_lead_and_conversation", world.resolve)
    return world


async def test_retried_call_report_does_not_double_bill_a_withheld_number(call_world):
    """The dedup lives in `conversation.extra`, but a caller with no number
    resolves to a *new* conversation on every delivery — so Vapi's retry
    billed the same call twice and duplicated it in the dashboard."""
    sub = SimpleNamespace(agency_id=AGENCY_ID, voice_minutes_used=0)
    db = CallSession(sub, call_world)

    await voice_module._on_call_end(db, _call_body(duration=125))
    await voice_module._on_call_end(db, _call_body(duration=125))

    assert sub.voice_minutes_used == 3
    assert len(call_world.conversations) == 1
    assert len(call_world.conversations[0].extra["calls"]) == 1


async def test_identified_caller_is_still_deduped(call_world):
    sub = SimpleNamespace(agency_id=AGENCY_ID, voice_minutes_used=0)
    db = CallSession(sub, call_world)

    await voice_module._on_call_end(db, _call_body(duration=61, number="+34600111222"))
    await voice_module._on_call_end(db, _call_body(duration=61, number="+34600111222"))

    assert sub.voice_minutes_used == 2


async def test_two_different_calls_are_both_billed(call_world):
    sub = SimpleNamespace(agency_id=AGENCY_ID, voice_minutes_used=0)
    db = CallSession(sub, call_world)

    await voice_module._on_call_end(db, _call_body(call_id="call_one", duration=30))
    await voice_module._on_call_end(db, _call_body(call_id="call_two", duration=200))

    assert sub.voice_minutes_used == 1 + 4


async def test_call_start_does_not_open_a_second_conversation(call_world):
    """`call-started` followed by its retry used to open two conversations
    for the same anonymous call."""
    sub = SimpleNamespace(agency_id=AGENCY_ID, voice_minutes_used=0)
    db = CallSession(sub, call_world)

    body = {"message": {"type": "call-started", "call": {"id": "call_anon", "customer": {}}}}
    await voice_module._on_call_start(db, body)
    await voice_module._on_call_start(db, body)

    assert len(call_world.conversations) == 1


# ---------------------------------------------------------------------------
# 5. A paused trial whose owner was never told
# ---------------------------------------------------------------------------


class SweepSession:
    def __init__(self, subscriptions, agencies, owner=None, send_ok=True):
        self.subscriptions = list(subscriptions)
        self.agencies = {a.id: a for a in agencies}
        self.owner = owner
        self.queries: list[str] = []
        self.commits = 0

    async def commit(self):
        # The sweep now persists the pause and the "already emailed" flag per
        # agency rather than once at the end: with `task_acks_late` a worker
        # killed mid-sweep replays the task, and whatever was still pending in
        # the session never reached disk.
        self.commits += 1

    async def rollback(self):
        pass

    async def get(self, model, pk):
        return self.agencies.get(pk)

    async def execute(self, stmt):
        sql = str(stmt)
        self.queries.append(sql)
        if "FROM users" in sql:
            return _Result([self.owner] if self.owner else [])
        if "count" in sql.lower():
            return _Result([], scalar=0)
        if "FROM subscriptions" in sql or "subscriptions." in sql:
            return _Result([s for s in self.subscriptions if s.status in _wanted(stmt)])
        raise AssertionError(f"unexpected query: {sql}")


def _trial_agency():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Inmo Demo",
        contact_email="dueno@inmodemo.example.com",
        settings={},
    )


def _trial_sub(agency_id, status=SubscriptionStatus.TRIALING):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency_id,
        status=status,
        trial_ends_at=datetime.now(UTC) - timedelta(days=1),
        stripe_subscription_id=None,
        created_at=datetime.now(UTC) - timedelta(days=15),
    )


async def test_a_failed_notification_is_retried_the_next_day(monkeypatch):
    """The sweep pauses first and emails second, on purpose. But the query
    only looked at TRIALING rows, so once paused the agency fell out of the
    sweep for good: the account stopped working and nobody was ever told."""
    outbox: list[dict] = []
    failing = {"down": True}

    async def _send(to, subject, html):
        if failing["down"]:
            return False
        outbox.append({"to": to})
        return True

    monkeypatch.setattr("app.services.emails.send_email", _send)

    agency = _trial_agency()
    sub = _trial_sub(agency.id)
    db = SweepSession([sub], [agency])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._trial_expiry_sweep()
    assert sub.status == SubscriptionStatus.PAUSED
    assert outbox == []
    assert "trial_ended_email_sent" not in agency.settings

    # Resend is back up; tomorrow's sweep has to pick the agency up again.
    failing["down"] = False
    await tasks._trial_expiry_sweep()

    assert len(outbox) == 1
    assert agency.settings["trial_ended_email_sent"] is True


async def test_an_already_notified_pause_is_not_emailed_again(monkeypatch):
    outbox: list[dict] = []

    async def _send(to, subject, html):
        outbox.append({"to": to})
        return True

    monkeypatch.setattr("app.services.emails.send_email", _send)

    agency = _trial_agency()
    agency.settings = {"trial_ended_email_sent": True}
    sub = _trial_sub(agency.id, status=SubscriptionStatus.PAUSED)
    db = SweepSession([sub], [agency])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._trial_expiry_sweep()

    assert outbox == []


def test_sweep_still_ignores_agencies_with_a_card(monkeypatch):
    """The safety net stays: a subscription linked to Stripe is never paused."""
    captured: list[str] = []

    class _Capturing(SweepSession):
        async def execute(self, stmt):
            captured.append(str(stmt))
            return await super().execute(stmt)

    db = _Capturing([], [])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    import asyncio

    asyncio.run(tasks._trial_expiry_sweep())

    sql = captured[0]
    assert "stripe_subscription_id IS NULL" in sql
    assert "trial_ends_at IS NOT NULL" in sql


# ---------------------------------------------------------------------------
# 6. An abandoned checkout must not leave an immortal trial
# ---------------------------------------------------------------------------


class CheckoutSession:
    def __init__(self, plans, agency):
        self.plans = plans
        self.agency = agency
        self.added: list = []
        self.commits = 0

    async def get(self, model, pk):
        return self.agency if pk == self.agency.id else None

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM plans" in sql:
            if "ORDER BY plans.monthly_price_eur" in sql:
                return _Result(sorted(self.plans, key=lambda p: p.monthly_price_eur))
            return _Result(self.plans)
        if "FROM subscriptions" in sql:
            return _Result([])
        raise AssertionError(f"unexpected query: {sql}")

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


def test_abandoned_checkout_leaves_a_trial_the_sweep_can_expire(monkeypatch):
    """A first checkout creates the subscription row that will hold the
    Stripe customer. Without a `trial_ends_at` that row is invisible to
    `lifecycle.trial_expiry_sweep` — the agency gets the cheapest plan for
    ever, for free, by opening a checkout and closing the tab."""
    agency = SimpleNamespace(
        id=uuid.uuid4(), name="Inmo Sol", slug="inmo-sol", contact_email="dueno@inmosol.example.com"
    )
    starter = SimpleNamespace(
        id=uuid.uuid4(),
        code="starter",
        monthly_price_eur=99,
        stripe_price_id="price_starter_m",
        stripe_price_id_year="price_starter_y",
        is_active=True,
    )
    session = CheckoutSession([starter], agency)

    async def _override():
        yield session

    async def _create_customer(*, email, agency_id, name):
        return "cus_new"

    async def _create_checkout(*, customer_id, price_id, success_url, cancel_url, **extra):
        return {"url": "https://checkout.stripe.com/s/1", "id": "cs_1"}

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    monkeypatch.setattr(billing_module, "create_customer", _create_customer)
    monkeypatch.setattr(billing_module, "create_checkout_session", _create_checkout)
    app.dependency_overrides[get_db] = _override

    client = TestClient(app)
    resp = client.post(
        "/api/v1/billing/checkout",
        json={
            "agency_id": str(agency.id),
            "plan_code": "starter",
            "success_url": "https://www.selai.es/billing?ok=1",
            "cancel_url": "https://www.selai.es/billing",
        },
    )

    assert resp.status_code == 200
    created = session.added[0]
    assert created.status == SubscriptionStatus.TRIALING
    assert created.trial_ends_at is not None
    assert created.trial_ends_at > datetime.now(UTC)
