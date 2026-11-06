"""(154, 155) POST /billing/checkout.

Two things this endpoint must get right:
- It never raises limits. Opening (and abandoning) the Agencia checkout used
  to be enough to get Agencia limits for free; `plan_id` now only moves in
  the webhook.
- It can charge annually. `interval="year"` picks the annual Stripe price,
  and says so honestly (409) when that price has not been synced yet.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import billing as billing_module
from app.core.settings import settings
from app.db.models import SubscriptionStatus
from app.db.session import get_db
from app.main import app
from app.services.stripe_billing import StripeSubscriptionItemError

AGENCY_ID = uuid.uuid4()


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


def _plan(code="pro", price=199, monthly="price_pro_m", yearly="price_pro_y"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        code=code,
        name=code.title(),
        description="",
        monthly_price_eur=price,
        features={},
        max_agents=5,
        max_properties=500,
        max_voice_minutes=600,
        is_active=True,
        stripe_product_id="prod_1",
        stripe_price_id=monthly,
        stripe_price_id_year=yearly,
    )


STARTER = _plan("starter", 99, "price_starter_m", "price_starter_y")
PRO = _plan()


class FakeSession:
    def __init__(self, plans, subscription=None, agency=None):
        self.plans = plans
        self.subscription = subscription
        self.agency = agency or SimpleNamespace(
            id=AGENCY_ID, name="Inmo Sol", slug="inmo-sol", contact_email="dueno@inmosol.example.com"
        )
        self.added: list = []
        self.commits = 0

    async def get(self, model, pk):
        return self.agency if pk == AGENCY_ID else None

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM plans" in sql:
            if "ORDER BY plans.monthly_price_eur" in sql:
                return _Result(sorted(self.plans, key=lambda p: p.monthly_price_eur))
            # Lookup by code: the only code the tests ask for is the one under test.
            return _Result(self.plans)
        if "FROM subscriptions" in sql:
            return _Result([self.subscription] if self.subscription else [])
        raise AssertionError(f"unexpected query: {sql}")

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.fixture
def db():
    session = FakeSession([PRO, STARTER])

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield session
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def stripe(monkeypatch):
    calls: list[dict] = []

    async def _create_customer(*, email, agency_id, name):
        calls.append({"op": "customer", "email": email, "agency_id": agency_id})
        return "cus_new"

    async def _create_checkout(*, customer_id, price_id, success_url, cancel_url, **extra):
        calls.append(
            {"op": "checkout", "customer": customer_id, "price": price_id, **extra}
        )
        return {"url": "https://checkout.stripe.com/s/1", "id": "cs_1"}

    async def _activate_trial(*, subscription_id, price_id):
        calls.append(
            {"op": "activate_trial", "subscription": subscription_id, "price": price_id}
        )
        return {"id": subscription_id, "status": "active"}

    monkeypatch.setattr(billing_module, "create_customer", _create_customer)
    monkeypatch.setattr(billing_module, "create_checkout_session", _create_checkout)
    monkeypatch.setattr(billing_module, "activate_trial_now", _activate_trial)
    return calls


@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    return TestClient(app)


def _body(**over):
    base = {
        "agency_id": str(AGENCY_ID),
        "plan_code": "pro",
        "success_url": "https://www.selai.es/billing?ok=1",
        "cancel_url": "https://www.selai.es/billing",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# (154) The plan is not applied before payment
# ---------------------------------------------------------------------------


def test_checkout_does_not_change_plan_of_existing_subscription(client, db, stripe):
    # El único estado con suscripción previa que puede llegar a un checkout
    # nuevo: TRIALING sin `stripe_subscription_id` (un checkout anterior
    # abandonado). Cualquier estado con suscripción de Stripe viva se corta
    # con 409 — ver los tests del guard más abajo.
    db.subscription = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        plan_id=STARTER.id,
        status=SubscriptionStatus.TRIALING,
        stripe_customer_id="cus_existing",
        stripe_subscription_id=None,
    )

    resp = client.post("/api/v1/billing/checkout", json=_body())

    assert resp.status_code == 200
    assert resp.json() == {"checkout_url": "https://checkout.stripe.com/s/1", "session_id": "cs_1"}
    # Still on Starter — the Agencia/Pro limits arrive with the webhook.
    assert db.subscription.plan_id == STARTER.id
    assert db.subscription.status == SubscriptionStatus.TRIALING
    assert db.commits == 1


def test_checkout_reuses_the_existing_stripe_customer(client, db, stripe):
    db.subscription = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        plan_id=STARTER.id,
        status=SubscriptionStatus.TRIALING,
        stripe_customer_id="cus_existing",
        stripe_subscription_id=None,
    )

    client.post("/api/v1/billing/checkout", json=_body())

    assert [c["op"] for c in stripe] == ["checkout"]
    assert stripe[0]["customer"] == "cus_existing"


# ---------------------------------------------------------------------------
# (n16) Una suscripción de Stripe viva NUNCA llega a un checkout nuevo
# ---------------------------------------------------------------------------
#
# La protección vivía solo en el navegador (el panel esconde el botón). Un
# POST directo abría un checkout real y Stripe creaba una SEGUNDA suscripción:
# la agencia pagaba 99 + 199 EUR al mes sin saberlo.


def _sub_with_live_stripe(status):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        plan_id=STARTER.id,
        status=status,
        stripe_customer_id="cus_existing",
        stripe_subscription_id="sub_viva",
    )


@pytest.mark.parametrize(
    "estado",
    [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.PAST_DUE],
)
def test_a_live_subscription_blocks_a_new_checkout(client, db, stripe, estado):
    db.subscription = _sub_with_live_stripe(estado)

    resp = client.post("/api/v1/billing/checkout", json=_body())

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "already_subscribed"
    assert stripe == []  # ni una llamada a Stripe


def test_start_now_ends_a_live_stripe_trial_without_new_checkout(client, db, stripe):
    db.subscription = _sub_with_live_stripe(SubscriptionStatus.TRIALING)
    old_plan = db.subscription.plan_id

    resp = client.post("/api/v1/billing/checkout", json=_body(start_now=True))

    assert resp.status_code == 200
    assert resp.json() == {
        "checkout_url": "https://www.selai.es/billing?ok=1",
        "session_id": "sub_viva",
    }
    assert stripe == [
        {"op": "activate_trial", "subscription": "sub_viva", "price": "price_pro_m"}
    ]
    # Stripe's webhook, not the initiating request, applies paid local state.
    assert db.subscription.status == SubscriptionStatus.TRIALING
    assert db.subscription.plan_id == old_plan
    assert db.commits == 0


def test_start_now_live_trial_refuses_an_ambiguous_subscription_shape(
    client, db, monkeypatch
):
    db.subscription = _sub_with_live_stripe(SubscriptionStatus.TRIALING)

    async def _raise(**kwargs):
        raise StripeSubscriptionItemError("sub_viva", 2)

    monkeypatch.setattr(billing_module, "activate_trial_now", _raise)
    resp = client.post("/api/v1/billing/checkout", json=_body(start_now=True))

    assert resp.status_code == 422
    assert resp.json()["detail"] == "subscription_items_unexpected"


@pytest.mark.parametrize(
    "estado", [SubscriptionStatus.UNPAID, SubscriptionStatus.PAUSED]
)
def test_a_broken_subscription_sends_you_to_the_portal(client, db, stripe, estado):
    db.subscription = _sub_with_live_stripe(estado)

    resp = client.post("/api/v1/billing/checkout", json=_body())

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "portal_required"
    assert stripe == []


def test_a_canceled_subscription_may_check_out_again(client, db, stripe):
    db.subscription = _sub_with_live_stripe(SubscriptionStatus.CANCELED)

    resp = client.post("/api/v1/billing/checkout", json=_body())

    assert resp.status_code == 200
    assert [c["op"] for c in stripe] == ["checkout"]


def test_first_checkout_creates_subscription_on_the_cheapest_plan(client, db, stripe):
    resp = client.post("/api/v1/billing/checkout", json=_body())

    assert resp.status_code == 200
    assert len(db.added) == 1
    created = db.added[0]
    # Not Pro: a checkout nobody paid must not hand out Pro's limits.
    assert created.plan_id == STARTER.id
    assert created.status == SubscriptionStatus.TRIALING
    assert created.stripe_customer_id == "cus_new"


# ---------------------------------------------------------------------------
# (155) Annual interval
# ---------------------------------------------------------------------------


def test_default_interval_is_monthly(client, db, stripe):
    client.post("/api/v1/billing/checkout", json=_body())
    assert stripe[-1]["price"] == "price_pro_m"


def test_interval_year_uses_the_annual_price(client, db, stripe):
    client.post("/api/v1/billing/checkout", json=_body(interval="year"))
    assert stripe[-1]["price"] == "price_pro_y"


def test_interval_month_is_explicit_too(client, db, stripe):
    client.post("/api/v1/billing/checkout", json=_body(interval="month"))
    assert stripe[-1]["price"] == "price_pro_m"


def test_unsynced_annual_price_is_409(client, db, stripe):
    db.plans = [_plan("pro", 199, "price_pro_m", None)]

    resp = client.post("/api/v1/billing/checkout", json=_body(interval="year"))

    assert resp.status_code == 409
    assert resp.json()["detail"] == "annual_price_not_synced"
    # Nothing was charged or created.
    assert stripe == []
    assert db.added == []
    assert db.commits == 0


def test_unknown_interval_is_rejected(client, db, stripe):
    resp = client.post("/api/v1/billing/checkout", json=_body(interval="week"))
    assert resp.status_code == 422


def test_plan_without_stripe_price_is_400(client, db, stripe):
    db.plans = [_plan("pro", 199, None, None)]
    resp = client.post("/api/v1/billing/checkout", json=_body())
    assert resp.status_code == 400
    assert resp.json()["detail"] == "plan_not_billable"


# ---------------------------------------------------------------------------
# GET /plans exposes whether each interval is chargeable
# ---------------------------------------------------------------------------


def test_plans_expose_yearly_billability(client, db):
    db.plans = [_plan("pro", 199, "price_pro_m", None), STARTER]

    rows = {p["code"]: p for p in client.get("/api/v1/billing/plans").json()}

    assert rows["pro"]["billable"] is True
    assert rows["pro"]["billable_yearly"] is False
    assert rows["starter"]["billable_yearly"] is True
