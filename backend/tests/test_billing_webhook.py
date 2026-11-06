"""(154) The Stripe webhook is the only thing that moves a plan, and it is
idempotent and order-safe.

Covered here:
- price_id -> Plan mapping on checkout.session.completed and
  customer.subscription.updated (monthly and annual price both map).
- dedup by event.id: the second delivery is a no-op.
- out-of-order guard: an event older than the newest one already applied for
  that customer cannot regress ACTIVE back to TRIALING.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import billing as billing_module
from app.db.models import SubscriptionStatus
from app.db.models.billing import ProcessedStripeEvent
from app.db.session import get_db
from app.main import app

CUSTOMER = "cus_1"
STRIPE_SUB = "sub_1"


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

    def scalar_one_or_none(self):
        if self._scalar is not None:
            return self._scalar
        return self._rows[0] if self._rows else None


def _plan(code, monthly, yearly=None, product=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        code=code,
        stripe_product_id=product or f"prod_{code}",
        stripe_price_id=monthly,
        stripe_price_id_year=yearly,
        is_active=True,
    )


class FakeSession:
    def __init__(self, subscription, plans, agency=None, owner=None):
        self.subscription = subscription
        self.plans = plans
        self.agency = agency
        self.owner = owner
        self.processed: dict[str, ProcessedStripeEvent] = {}
        self.pending: list = []
        self.commits = 0
        self.rollbacks = 0

    # -- session api ----------------------------------------------------
    async def get(self, model, pk):
        if model is ProcessedStripeEvent:
            return self.processed.get(pk)
        if self.agency is not None and pk == self.agency.id:
            return self.agency
        return None

    async def execute(self, stmt):
        sql = str(stmt)
        if "processed_stripe_events" in sql:
            stream = _bound_value(stmt, "cus_")
            values = [
                e.event_created
                for e in self.processed.values()
                if e.event_created is not None and e.object_id == stream
            ]
            return _Result([], scalar=max(values) if values else None)
        if "FROM plans" in sql:
            price = _bound_value(stmt, "price_")
            if price is not None:
                match = [
                    p for p in self.plans if price in (p.stripe_price_id, p.stripe_price_id_year)
                ]
            else:
                plan_code = next(
                    (
                        value
                        for value in stmt.compile().params.values()
                        if isinstance(value, str) and value in {p.code for p in self.plans}
                    ),
                    None,
                )
                match = [p for p in self.plans if p.code == plan_code and p.is_active]
            return _Result(match)
        if "FROM users" in sql:
            return _Result([self.owner] if self.owner else [])
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
        self.commits += 1

    async def rollback(self):
        self.pending = []
        self.rollbacks += 1


def _bound_value(stmt, prefix: str) -> str | None:
    """Pull a bound parameter with the given prefix out of the WHERE clause."""
    for value in stmt.compile().params.values():
        if isinstance(value, str) and value.startswith(prefix):
            return value
    return None


STARTER = _plan("starter", "price_starter_m", "price_starter_y")
PRO = _plan("pro", "price_pro_m", "price_pro_y")
AGENCIA = _plan("agencia", "price_agencia_m", "price_agencia_y")


def _subscription(
    plan_id=None,
    status=SubscriptionStatus.TRIALING,
    stripe_sub=None,
    trial_ends_at=None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=uuid.uuid4(),
        plan_id=plan_id or STARTER.id,
        status=status,
        stripe_customer_id=CUSTOMER,
        stripe_subscription_id=stripe_sub,
        current_period_end=None,
        canceled_at=None,
        # A subscription that is TRIALING has trial left by definition; since
        # (183) the checkout handler reads this to tell "signed up, still in
        # the trial" apart from "came back and paid on the spot".
        trial_ends_at=(
            trial_ends_at if trial_ends_at is not None else datetime.now(UTC) + timedelta(days=10)
        ),
    )


@pytest.fixture
def sub():
    return _subscription()


@pytest.fixture
def db(sub):
    session = FakeSession(sub, [STARTER, PRO, AGENCIA])

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield session
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setattr(billing_module, "verify_webhook_signature", lambda raw, sig: True)

    async def _unknown_price(path, params=None):
        if path.startswith("/prices/"):
            return {"id": path.removeprefix("/prices/"), "metadata": {}}
        raise AssertionError(f"unexpected Stripe call: {path}")

    monkeypatch.setattr(billing_module, "stripe_get", _unknown_price)
    return TestClient(app)


@pytest.fixture
def remote_subscription(monkeypatch):
    """Stub GET /subscriptions/{id} — what checkout.session.completed falls
    back to, since checkout sessions carry no line items."""
    state = {"price": "price_pro_m"}

    async def _get(path, params=None):
        assert path == f"/subscriptions/{STRIPE_SUB}"
        return {"items": {"data": [{"price": {"id": state["price"]}}]}}

    monkeypatch.setattr(billing_module, "stripe_get", _get)
    return state


def _post(client, event: dict):
    return client.post(
        "/api/v1/billing/webhook", json=event, headers={"stripe-signature": "t=1,v1=x"}
    )


def _event(event_type, obj, event_id="evt_1", created=1000):
    return {"id": event_id, "type": event_type, "created": created, "data": {"object": obj}}


def _sub_updated(status="active", price="price_pro_m", metadata=None, **kw):
    obj = {
        "id": STRIPE_SUB,
        "customer": CUSTOMER,
        "status": status,
        "items": {"data": [{"price": {"id": price}}]},
    }
    if metadata is not None:
        obj["metadata"] = metadata
    return _event(
        "customer.subscription.updated",
        obj,
        **kw,
    )


# ---------------------------------------------------------------------------
# price -> plan mapping
# ---------------------------------------------------------------------------


def test_checkout_completed_applies_the_paid_plan(client, db, sub, remote_subscription):
    resp = _post(
        client,
        _event("checkout.session.completed", {"customer": CUSTOMER, "subscription": STRIPE_SUB}),
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert sub.stripe_subscription_id == STRIPE_SUB
    assert sub.plan_id == PRO.id
    assert sub.status == SubscriptionStatus.TRIALING


def test_checkout_start_now_activates_and_consumes_local_trial(
    client, db, sub, remote_subscription
):
    previous_deadline = sub.trial_ends_at
    before = datetime.now(UTC)

    response = _post(
        client,
        _event(
            "checkout.session.completed",
            {
                "customer": CUSTOMER,
                "subscription": STRIPE_SUB,
                "metadata": {"selai_start_now": "true"},
            },
        ),
    )

    assert response.status_code == 200
    assert sub.status == SubscriptionStatus.ACTIVE
    assert before <= sub.trial_ends_at <= datetime.now(UTC)
    assert sub.trial_ends_at < previous_deadline


def test_checkout_without_start_now_preserves_local_trial(client, db, sub, remote_subscription):
    previous_deadline = sub.trial_ends_at

    response = _post(
        client,
        _event(
            "checkout.session.completed",
            {"customer": CUSTOMER, "subscription": STRIPE_SUB},
        ),
    )

    assert response.status_code == 200
    assert sub.status == SubscriptionStatus.TRIALING
    assert sub.trial_ends_at == previous_deadline


def test_start_now_checkout_delivery_is_idempotent(client, db, sub, remote_subscription):
    event = _event(
        "checkout.session.completed",
        {
            "customer": CUSTOMER,
            "subscription": STRIPE_SUB,
            "metadata": {"selai_start_now": "true"},
        },
        event_id="evt_start_now",
    )

    first = _post(client, event)
    consumed_at = sub.trial_ends_at
    second = _post(client, event)

    assert first.json() == {"status": "ok"}
    assert second.json() == {"status": "duplicate"}
    assert sub.trial_ends_at == consumed_at


def test_checkout_completed_maps_the_annual_price_too(client, db, sub, remote_subscription):
    remote_subscription["price"] = "price_agencia_y"

    _post(
        client,
        _event("checkout.session.completed", {"customer": CUSTOMER, "subscription": STRIPE_SUB}),
    )

    assert sub.plan_id == AGENCIA.id


def test_subscription_updated_applies_plan_and_status(client, db, sub):
    sub.stripe_subscription_id = STRIPE_SUB

    _post(client, _sub_updated(status="active", price="price_agencia_m"))

    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.plan_id == AGENCIA.id


def test_historical_price_maps_from_consistent_stripe_metadata(client, db, sub, monkeypatch):
    sub.stripe_subscription_id = STRIPE_SUB

    async def _get(path, params=None):
        assert path == "/prices/price_pro_historical"
        assert params == {"expand[]": "product"}
        return {
            "id": "price_pro_historical",
            "metadata": {"plan_code": "pro"},
            "product": {
                "id": "prod_pro",
                "metadata": {"plan_code": "pro"},
            },
        }

    monkeypatch.setattr(billing_module, "stripe_get", _get)

    response = _post(client, _sub_updated(price="price_pro_historical"))

    assert response.status_code == 200
    assert sub.plan_id == PRO.id


def test_historical_price_can_use_its_verified_product_metadata(client, db, sub, monkeypatch):
    sub.stripe_subscription_id = STRIPE_SUB
    calls: list[str] = []

    async def _get(path, params=None):
        calls.append(path)
        if path == "/prices/price_pro_reused":
            return {
                "id": "price_pro_reused",
                "metadata": {},
                "product": "prod_pro",
            }
        if path == "/products/prod_pro":
            return {
                "id": "prod_pro",
                "metadata": {"plan_code": "pro"},
            }
        raise AssertionError(path)

    monkeypatch.setattr(billing_module, "stripe_get", _get)

    response = _post(client, _sub_updated(price="price_pro_reused"))

    assert response.status_code == 200
    assert sub.plan_id == PRO.id
    assert calls == ["/prices/price_pro_reused", "/products/prod_pro"]


def test_historical_price_with_conflicting_metadata_is_not_trusted(client, db, sub, monkeypatch):
    sub.stripe_subscription_id = STRIPE_SUB
    before = sub.plan_id

    async def _get(path, params=None):
        return {
            "id": "price_pro_historical",
            "metadata": {"plan_code": "pro"},
            "product": {
                "id": "prod_pro",
                "metadata": {"plan_code": "agencia"},
            },
        }

    monkeypatch.setattr(billing_module, "stripe_get", _get)

    response = _post(client, _sub_updated(price="price_pro_historical"))

    assert response.status_code == 200
    assert sub.plan_id == before


def test_historical_price_from_another_product_is_not_trusted(client, db, sub, monkeypatch):
    sub.stripe_subscription_id = STRIPE_SUB
    before = sub.plan_id

    async def _get(path, params=None):
        return {
            "id": "price_pro_historical",
            "metadata": {"plan_code": "pro"},
            "product": {
                "id": "prod_not_pro",
                "metadata": {"plan_code": "pro"},
            },
        }

    monkeypatch.setattr(billing_module, "stripe_get", _get)

    response = _post(client, _sub_updated(price="price_pro_historical"))

    assert response.status_code == 200
    assert sub.plan_id == before


def test_start_now_subscription_update_consumes_the_remaining_trial(client, db, sub):
    sub.stripe_subscription_id = STRIPE_SUB
    previous_deadline = sub.trial_ends_at
    before = datetime.now(UTC)

    response = _post(
        client,
        _sub_updated(metadata={"selai_start_now": "true"}),
    )

    assert response.status_code == 200
    assert sub.status == SubscriptionStatus.ACTIVE
    assert before <= sub.trial_ends_at <= datetime.now(UTC)
    assert sub.trial_ends_at < previous_deadline


def test_unknown_price_leaves_the_plan_alone(client, db, sub):
    sub.stripe_subscription_id = STRIPE_SUB
    before = sub.plan_id

    _post(client, _sub_updated(price="price_desconocido"))

    assert sub.plan_id == before
    assert sub.status == SubscriptionStatus.ACTIVE


# ---------------------------------------------------------------------------
# dedup by event id
# ---------------------------------------------------------------------------


def test_same_event_twice_is_a_noop(client, db, sub):
    sub.stripe_subscription_id = STRIPE_SUB

    first = _post(client, _sub_updated(status="active", event_id="evt_dup", created=1000))
    assert first.json() == {"status": "ok"}
    assert db.commits == 1

    # Someone tampers with the subscription in between; the replay must not
    # rewrite it.
    sub.status = SubscriptionStatus.PAST_DUE

    second = _post(client, _sub_updated(status="active", event_id="evt_dup", created=1000))
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    assert sub.status == SubscriptionStatus.PAST_DUE
    assert db.commits == 1  # nothing new was written


def test_event_id_is_recorded_with_its_stream_and_timestamp(client, db, sub):
    sub.stripe_subscription_id = STRIPE_SUB

    _post(client, _sub_updated(event_id="evt_abc", created=1234))

    row = db.processed["evt_abc"]
    assert row.event_type == "customer.subscription.updated"
    assert row.object_id == CUSTOMER
    assert row.event_created == 1234


# ---------------------------------------------------------------------------
# out-of-order guard
# ---------------------------------------------------------------------------


def test_older_event_cannot_regress_active_to_trialing(client, db, sub, remote_subscription):
    sub.stripe_subscription_id = STRIPE_SUB

    _post(client, _sub_updated(status="active", event_id="evt_new", created=2000))
    assert sub.status == SubscriptionStatus.ACTIVE

    # Stripe retries the earlier checkout event out of order.
    stale = _post(
        client,
        _event(
            "checkout.session.completed",
            {"customer": CUSTOMER, "subscription": STRIPE_SUB},
            event_id="evt_old",
            created=1000,
        ),
    )

    assert stale.status_code == 200
    assert stale.json() == {"status": "stale"}
    assert sub.status == SubscriptionStatus.ACTIVE
    # Recorded, so a further retry of the same id short-circuits as duplicate.
    assert "evt_old" in db.processed


def test_same_timestamp_is_still_applied(client, db, sub):
    """Only strictly older events are stale — Stripe can emit two events in
    the same second and both are legitimate."""
    sub.stripe_subscription_id = STRIPE_SUB

    _post(client, _sub_updated(status="past_due", event_id="evt_a", created=1000))
    resp = _post(client, _sub_updated(status="active", event_id="evt_b", created=1000))

    assert resp.json() == {"status": "ok"}
    assert sub.status == SubscriptionStatus.ACTIVE


def test_newer_event_applies_normally(client, db, sub):
    sub.stripe_subscription_id = STRIPE_SUB

    _post(client, _sub_updated(status="trialing", event_id="evt_1", created=1000))
    _post(client, _sub_updated(status="active", event_id="evt_2", created=2000))

    assert sub.status == SubscriptionStatus.ACTIVE


def test_events_for_another_customer_do_not_shadow_ours(client, db, sub):
    """The ordering window is per customer, not global: another tenant's
    newer event must not make ours look stale."""
    sub.stripe_subscription_id = STRIPE_SUB
    db.processed["evt_other"] = ProcessedStripeEvent(
        event_id="evt_other",
        event_type="customer.subscription.updated",
        object_id="cus_someone_else",
        event_created=9999,
    )

    resp = _post(client, _sub_updated(status="active", event_id="evt_mine", created=1000))

    assert resp.json() == {"status": "ok"}
    assert sub.status == SubscriptionStatus.ACTIVE


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------


def test_unhandled_event_type_is_ignored_without_recording(client, db):
    resp = _post(client, _event("invoice.paid", {"customer": CUSTOMER}))
    assert resp.json() == {"status": "ignored"}
    assert db.processed == {}


def test_bad_signature_is_401(client, db, monkeypatch):
    monkeypatch.setattr(billing_module, "verify_webhook_signature", lambda raw, sig: False)
    resp = _post(client, _sub_updated())
    assert resp.status_code == 401


@pytest.mark.parametrize("livemode", [False, None])
def test_production_rejects_non_live_event_before_any_effect(
    client, db, sub, monkeypatch, livemode
):
    sub.stripe_subscription_id = STRIPE_SUB
    before_status = sub.status
    before_plan = sub.plan_id
    monkeypatch.setattr(billing_module.settings, "app_env", "production")
    event = _sub_updated(status="active", price="price_agencia_m")
    if livemode is not None:
        event["livemode"] = livemode

    response = _post(client, event)

    assert response.status_code == 400
    assert response.json()["detail"] == "stripe_test_event_in_production"
    assert sub.status == before_status
    assert sub.plan_id == before_plan
    assert db.commits == 0
    assert db.processed == {}


def test_production_accepts_a_live_event(client, db, sub, monkeypatch):
    sub.stripe_subscription_id = STRIPE_SUB
    monkeypatch.setattr(billing_module.settings, "app_env", "production")
    event = _sub_updated(status="active", price="price_agencia_m")
    event["livemode"] = True

    response = _post(client, event)

    assert response.status_code == 200
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.plan_id == AGENCIA.id


def test_subscription_deleted_cancels(client, db, sub):
    sub.stripe_subscription_id = STRIPE_SUB

    _post(
        client,
        _event("customer.subscription.deleted", {"id": STRIPE_SUB, "customer": CUSTOMER}),
    )

    assert sub.status == SubscriptionStatus.CANCELED
    assert sub.canceled_at is not None
