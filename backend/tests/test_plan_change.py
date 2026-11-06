"""POST /billing/change-plan — upgrading must not open a second subscription.

The bug this covers cost real money: the panel only knew how to open a
checkout, so an agency on Starter that bought Pro ended up with two live
Stripe subscriptions and was charged 99 + 199 EUR/month. The fix changes the
price of the subscription the customer already has, which in Stripe's API
means one thing above all others: send `items[0][id]`. Omit it and Stripe
*appends* the new price to the subscription instead of replacing it — the
same double charge, one level deeper.

So the assertions here are mostly about what reaches Stripe's form:
`items[0][id]`, the right price, `create_prorations`, and a POST to
`/subscriptions/{id}` rather than to `/subscriptions`.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import billing as billing_module
from app.core.auth import AuthContext, require_auth
from app.core.settings import settings
from app.db.models import SubscriptionStatus, UserRole
from app.db.models.billing import ProcessedStripeEvent
from app.db.session import get_db
from app.main import app
from app.services import stripe_billing
from app.services.stripe_billing import (
    PriceChange,
    StripeSubscriptionItemError,
    update_subscription_price,
)

AGENCY_ID = uuid.uuid4()
STRIPE_SUB = "sub_live_1"
CUSTOMER = "cus_1"


# ---------------------------------------------------------------------------
# Service: what actually reaches Stripe
# ---------------------------------------------------------------------------


@pytest.fixture
def api_key(monkeypatch):
    """`update_subscription_price` short-circuits to a stub without a key, so
    every test that wants to inspect the HTTP call needs one configured."""
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_test_x"))


def _item(price_id="price_old", interval="month", item_id="si_1"):
    """One subscription item as Stripe returns it."""
    return {"id": item_id, "price": {"id": price_id, "recurring": {"interval": interval}}}


@pytest.fixture
def stripe_http(monkeypatch, api_key):
    """Fake `_get`/`_post` recording every call made to Stripe."""
    calls = SimpleNamespace(get=[], post=[], items=[_item()])

    async def _get(path, params=None):
        calls.get.append(path)
        return {"id": STRIPE_SUB, "items": {"data": calls.items}}

    async def _post(path, form):
        calls.post.append({"path": path, "form": form})
        return {"id": STRIPE_SUB, "status": "active"}

    monkeypatch.setattr(stripe_billing, "_get", _get)
    monkeypatch.setattr(stripe_billing, "_post", _post)
    return calls


async def test_update_replaces_the_existing_item(stripe_http):
    """The whole point: `items[0][id]` names the item to overwrite. Without
    it Stripe adds a second price and the customer pays both."""
    await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")

    form = stripe_http.post[0]["form"]
    assert form["items[0][id]"] == "si_1"
    assert form["items[0][price]"] == "price_pro_m"


async def test_the_current_item_id_is_read_from_stripe(stripe_http):
    """Only Stripe knows the subscription item id, hence the read before the
    write — it is never guessed or derived from our own rows."""
    await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")
    assert stripe_http.get == [f"/subscriptions/{STRIPE_SUB}"]


async def test_it_updates_the_subscription_instead_of_creating_one(stripe_http):
    """POST /subscriptions creates; POST /subscriptions/{id} updates. This
    endpoint must never be able to mint a second subscription."""
    await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")

    assert [c["path"] for c in stripe_http.post] == [f"/subscriptions/{STRIPE_SUB}"]


async def test_default_proration_does_not_charge_on_the_spot(stripe_http):
    """`create_prorations` puts the adjustment on the next invoice, which is
    what the pricing page promises. `always_invoice` would charge the card
    the second the customer clicks."""
    await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")

    assert stripe_http.post[0]["form"]["proration_behavior"] == "create_prorations"


async def test_proration_behavior_is_forwarded(stripe_http):
    await update_subscription_price(
        subscription_id=STRIPE_SUB, price_id="price_pro_m", proration_behavior="none"
    )
    assert stripe_http.post[0]["form"]["proration_behavior"] == "none"


async def test_every_value_is_a_string(stripe_http):
    """Stripe's API is form-encoded; httpx will not serialise ints for us."""
    await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")
    assert all(isinstance(v, str) for v in stripe_http.post[0]["form"].values())


@pytest.mark.parametrize(
    "items",
    [
        pytest.param([], id="no_items"),
        pytest.param([_item("price_a"), _item("price_b", item_id="si_2")], id="two_items"),
        pytest.param([{"price": {"id": "price_a"}}], id="item_without_id"),
    ],
)
async def test_an_unexpected_shape_is_refused_without_writing(stripe_http, items):
    """Zero or several items is a subscription this code did not create.
    Picking one at random would bill the customer for the wrong thing, so it
    refuses — and, critically, sends no POST at all."""
    stripe_http.items = items

    with pytest.raises(StripeSubscriptionItemError):
        await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")

    assert stripe_http.post == []


async def test_a_refusal_is_not_retried(stripe_http):
    """A wrong-shaped subscription will not fix itself; retrying it three
    times only delays the 409 and hammers Stripe."""
    stripe_http.items = []

    with pytest.raises(StripeSubscriptionItemError):
        await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")

    assert len(stripe_http.get) == 1


async def test_without_an_api_key_nothing_is_called(monkeypatch):
    """Same contract as the rest of the module: no key (local dev, CI) means
    a stub, not an exception."""
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr(""))

    async def _boom(*a, **kw):
        raise AssertionError("no HTTP call may be made without an api key")

    monkeypatch.setattr(stripe_billing, "_get", _boom)
    monkeypatch.setattr(stripe_billing, "_post", _boom)

    out = await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")
    assert out.status == "active"


# ---------------------------------------------------------------------------
# Writing the same price twice
# ---------------------------------------------------------------------------


async def test_the_price_it_already_has_is_not_written_again(stripe_http):
    """Double click, or a retry of a request whose response was lost. The
    write already landed; repeating it gives Stripe a second chance to
    prorate and to fire another `customer.subscription.updated` for a change
    that happened once. No write is the only version that cannot cost money."""
    stripe_http.items = [_item("price_pro_m")]

    out = await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")

    assert stripe_http.post == []
    assert out.changed is False
    assert out.previous_price_id == "price_pro_m"


async def test_two_identical_calls_write_exactly_once(stripe_http):
    """The realistic double-submit: the second call sees Stripe already on
    the new price and stops."""
    await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")
    # Stripe now reports the new price, as it would to the second request.
    stripe_http.items = [_item("price_pro_m")]
    await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")

    assert len(stripe_http.post) == 1


async def test_a_different_price_is_still_written(stripe_http):
    out = await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")
    assert out.changed is True
    assert out.previous_price_id == "price_old"
    assert len(stripe_http.post) == 1


async def test_the_previous_interval_is_reported(stripe_http):
    """The caller needs it to know whether the billing date moves: Stripe
    bills a monthly->yearly switch on the day of the switch, it does not
    prorate it onto a next invoice."""
    stripe_http.items = [_item("price_pro_m", interval="month")]

    out = await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_y")

    assert out.previous_interval == "month"


async def test_a_legacy_plan_shape_still_yields_an_interval(stripe_http):
    """Old API versions return `plan` instead of `price`, with the interval
    at the top level. Reading it wrong would silently drop the warning."""
    stripe_http.items = [{"id": "si_1", "plan": {"id": "price_old", "interval": "year"}}]

    out = await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")

    assert out.previous_interval == "year"
    assert out.previous_price_id == "price_old"


# ---------------------------------------------------------------------------
# What is worth retrying
# ---------------------------------------------------------------------------


def _http_error(status_code: int, code: str | None = None) -> httpx.HTTPStatusError:
    body = {"error": {"code": code, "message": "nope"}} if code else {"error": {"message": "nope"}}
    request = httpx.Request("POST", "https://api.stripe.com/v1/x")
    response = httpx.Response(status_code, json=body, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.parametrize("status_code", [400, 402, 404])
async def test_a_4xx_is_not_retried(stripe_http, monkeypatch, status_code):
    """Stripe already judged this exact body. Sending it twice more only
    keeps the customer staring at a spinner through the backoff."""
    attempts = {"n": 0}

    async def _post(path, form):
        attempts["n"] += 1
        raise _http_error(status_code)

    monkeypatch.setattr(stripe_billing, "_post", _post)

    with pytest.raises(httpx.HTTPStatusError):
        await update_subscription_price(subscription_id=STRIPE_SUB, price_id="price_pro_m")

    assert attempts["n"] == 1


@pytest.mark.parametrize(
    ("exc", "retryable"),
    [
        pytest.param(_http_error(400), False, id="bad_request"),
        pytest.param(_http_error(404), False, id="not_found"),
        pytest.param(_http_error(429, "rate_limit"), True, id="rate_limited"),
        pytest.param(
            _http_error(409, "idempotency_key_in_use"),
            True,
            id="same_payment_still_in_flight",
        ),
        pytest.param(_http_error(500), True, id="stripe_broke"),
        pytest.param(_http_error(503), True, id="stripe_unavailable"),
        pytest.param(httpx.ConnectTimeout("slow"), True, id="network"),
        pytest.param(StripeSubscriptionItemError("sub_1", 2), False, id="wrong_shape"),
    ],
)
def test_only_what_a_repeat_could_fix_is_retried(exc, retryable):
    """Driven directly instead of through tenacity so the assertion costs no
    seconds of real backoff."""
    assert stripe_billing._is_transient(exc) is retryable


# ---------------------------------------------------------------------------
# Endpoint
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

    def scalar_one_or_none(self):
        if self._scalar is not None:
            return self._scalar
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


def _subscription(status=SubscriptionStatus.ACTIVE, stripe_sub=STRIPE_SUB, plan=STARTER):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        plan_id=plan.id,
        status=status,
        stripe_customer_id=CUSTOMER,
        stripe_subscription_id=stripe_sub,
        current_period_end=None,
        canceled_at=None,
    )


def _bound_values(stmt) -> list[str]:
    return [v for v in stmt.compile().params.values() if isinstance(v, str)]


class FakeSession:
    """Answers the queries of both /change-plan and the webhook, so one test
    can prove the plan moves on the webhook and not before."""

    def __init__(self, subscription=None, plans=(PRO, STARTER)):
        self.subscription = subscription
        self.plans = list(plans)
        self.agency = SimpleNamespace(
            id=AGENCY_ID, name="Inmo Sol", slug="inmo-sol", contact_email="d@inmosol.example.com",
            settings={},
        )
        self.added: list = []
        self.commits = 0
        # Every statement, so a test can assert *what* was filtered on.
        self.statements: list[dict] = []

    async def get(self, model, pk):
        if model is ProcessedStripeEvent:
            return None
        return self.agency if pk == AGENCY_ID else None

    async def execute(self, stmt):
        sql = str(stmt)
        try:
            self.statements.append({"sql": sql, "params": stmt.compile().params})
        except Exception:  # not a compilable select (func.max etc.)
            self.statements.append({"sql": sql, "params": {}})
        if "processed_stripe_events" in sql:
            return _Result([], scalar=None)
        if "FROM plans" in sql:
            wanted = _bound_values(stmt)
            # /change-plan looks a plan up by code; the webhook by price id.
            match = [
                p
                for p in self.plans
                if p.code in wanted
                or p.stripe_price_id in wanted
                or p.stripe_price_id_year in wanted
            ]
            return _Result(match)
        if "FROM subscriptions" in sql:
            return _Result([self.subscription] if self.subscription else [])
        if "FROM users" in sql:
            return _Result([])
        raise AssertionError(f"unexpected query: {sql}")

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.fixture
def db():
    session = FakeSession(_subscription())

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield session
    app.dependency_overrides.pop(get_db, None)


class _Calls(list):
    """The recorded calls (so `stripe == [...]` still reads well) plus the
    `PriceChange` the fake service should hand back."""

    def __init__(self):
        super().__init__()
        self.previous_price_id = "price_starter_m"
        self.previous_interval = "month"
        self.changed = True


@pytest.fixture
def stripe(monkeypatch):
    """Records calls to the *service* layer, and fails loudly if the endpoint
    ever reaches for the checkout instead."""
    calls = _Calls()

    async def _update(*, subscription_id, price_id, proration_behavior):
        calls.append(
            {
                "subscription_id": subscription_id,
                "price_id": price_id,
                "proration_behavior": proration_behavior,
            }
        )
        return PriceChange(
            subscription={"id": subscription_id, "status": "active"},
            previous_price_id=calls.previous_price_id,
            previous_interval=calls.previous_interval,
            changed=calls.changed,
        )

    async def _no_checkout(**kwargs):
        raise AssertionError("change-plan must never open a checkout session")

    async def _no_customer(**kwargs):
        raise AssertionError("change-plan must never create a customer")

    monkeypatch.setattr(billing_module, "update_subscription_price", _update)
    monkeypatch.setattr(billing_module, "create_checkout_session", _no_checkout)
    monkeypatch.setattr(billing_module, "create_customer", _no_customer)
    return calls


@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    return TestClient(app)


def _body(**over):
    base = {"agency_id": str(AGENCY_ID), "plan_code": "pro"}
    base.update(over)
    return base


def test_change_plan_updates_the_existing_subscription(client, db, stripe):
    resp = client.post("/api/v1/billing/change-plan", json=_body())

    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "status": "active",
        "changed": True,
        "billing_cycle_reset": False,
    }
    assert stripe == [
        {
            "subscription_id": STRIPE_SUB,
            "price_id": "price_pro_m",
            "proration_behavior": "create_prorations",
        }
    ]


def test_change_plan_never_creates_a_second_subscription(client, db, stripe):
    """The `_no_checkout`/`_no_customer` fakes assert this by exploding; the
    row count assertion covers our own side."""
    assert client.post("/api/v1/billing/change-plan", json=_body()).status_code == 200
    assert db.added == []


def test_the_plan_does_not_move_in_the_response(client, db, stripe):
    """Limits only ever rise once Stripe confirms the money (module
    invariant 1). The endpoint answers 200 while the row still says Starter."""
    client.post("/api/v1/billing/change-plan", json=_body())

    assert db.subscription.plan_id == STARTER.id
    assert db.commits == 0


def test_the_plan_moves_when_the_webhook_arrives(client, db, stripe, monkeypatch):
    """End to end: /change-plan asks Stripe, `customer.subscription.updated`
    is what actually applies Pro."""
    monkeypatch.setattr(billing_module, "verify_webhook_signature", lambda raw, sig: True)

    client.post("/api/v1/billing/change-plan", json=_body())
    assert db.subscription.plan_id == STARTER.id

    resp = client.post(
        "/api/v1/billing/webhook",
        json={
            "id": "evt_1",
            "created": 1_700_000_000,
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": STRIPE_SUB,
                    "customer": CUSTOMER,
                    "status": "active",
                    "items": {"data": [{"price": {"id": "price_pro_m"}}]},
                }
            },
        },
    )

    assert resp.status_code == 200
    assert db.subscription.plan_id == PRO.id


def test_annual_interval_charges_the_annual_price(client, db, stripe):
    client.post("/api/v1/billing/change-plan", json=_body(interval="year"))
    assert stripe[0]["price_id"] == "price_pro_y"


def test_unsynced_annual_price_is_409(client, db, stripe):
    """Same honest failure as /checkout: we will not silently bill monthly."""
    db.plans = [_plan("pro", 199, "price_pro_m", None)]

    resp = client.post("/api/v1/billing/change-plan", json=_body(interval="year"))

    assert resp.status_code == 409
    assert resp.json()["detail"] == "annual_price_not_synced"
    assert stripe == []


def test_plan_without_a_stripe_price_is_400(client, db, stripe):
    db.plans = [_plan("pro", 199, None, None)]
    resp = client.post("/api/v1/billing/change-plan", json=_body())
    assert resp.status_code == 400
    assert resp.json()["detail"] == "plan_not_billable"


def test_unknown_interval_is_rejected(client, db, stripe):
    assert client.post("/api/v1/billing/change-plan", json=_body(interval="week")).status_code == 422


def test_no_subscription_row_is_409_checkout_required(client, db, stripe):
    db.subscription = None

    resp = client.post("/api/v1/billing/change-plan", json=_body())

    assert resp.status_code == 409
    assert resp.json()["detail"] == "checkout_required"
    assert stripe == []


def test_subscription_without_a_stripe_id_is_409_checkout_required(client, db, stripe):
    """A trialing agency that never paid has no Stripe subscription to edit."""
    db.subscription = _subscription(status=SubscriptionStatus.TRIALING, stripe_sub=None)

    resp = client.post("/api/v1/billing/change-plan", json=_body())

    assert resp.status_code == 409
    assert resp.json()["detail"] == "checkout_required"
    assert stripe == []


@pytest.mark.parametrize(
    "dead",
    [SubscriptionStatus.CANCELED, SubscriptionStatus.UNPAID, SubscriptionStatus.PAUSED],
)
def test_a_dead_subscription_is_409_checkout_required(client, db, stripe, dead):
    db.subscription = _subscription(status=dead)

    resp = client.post("/api/v1/billing/change-plan", json=_body())

    assert resp.status_code == 409
    assert resp.json()["detail"] == "checkout_required"
    assert stripe == []


@pytest.mark.parametrize(
    "live",
    [SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE],
)
def test_a_live_subscription_can_change_plan(client, db, stripe, live):
    """PAST_DUE included on purpose: it is the grace period while we chase the
    card, and a customer in trouble must be able to downgrade."""
    db.subscription = _subscription(status=live)

    assert client.post("/api/v1/billing/change-plan", json=_body()).status_code == 200
    assert len(stripe) == 1


def test_an_unexpected_item_shape_is_refused(client, db, monkeypatch):
    """Better to refuse than to rewrite the wrong item: it is the customer's
    money either way."""

    async def _raise(**kwargs):
        raise StripeSubscriptionItemError(STRIPE_SUB, 2)

    monkeypatch.setattr(billing_module, "update_subscription_price", _raise)

    resp = client.post("/api/v1/billing/change-plan", json=_body())

    assert resp.status_code == 422
    assert resp.json()["detail"] == "subscription_items_unexpected"
    assert db.subscription.plan_id == STARTER.id
    assert db.commits == 0


def test_the_items_refusal_is_never_a_409(client, db, monkeypatch):
    """The panel turns a 409 into "open a checkout", and a checkout is what
    creates a SECOND subscription — the 99+199 bug. A subscription that
    already carries one item too many is the last one that should get
    another. So this refusal must never wear the code that means "buy again".
    """

    async def _raise(**kwargs):
        raise StripeSubscriptionItemError(STRIPE_SUB, 2)

    monkeypatch.setattr(billing_module, "update_subscription_price", _raise)

    assert client.post("/api/v1/billing/change-plan", json=_body()).status_code != 409


def test_checkout_required_is_the_only_409(client, db, stripe, monkeypatch):
    """The other side of the same contract, so a future refusal cannot be
    given a 409 by accident."""
    db.subscription = None
    resp = client.post("/api/v1/billing/change-plan", json=_body())

    assert resp.status_code == 409
    assert resp.json()["detail"] == "checkout_required"


# ---------------------------------------------------------------------------
# Changing the billing period is not a proration
# ---------------------------------------------------------------------------


def test_the_annual_switch_is_flagged_as_a_billing_cycle_reset(client, db, stripe):
    """Stripe moves the billing date to the day of the switch when the
    interval changes, so the year is charged then — it is not an adjustment
    "on your next invoice". The panel is told, because /me only reports the
    plan and it cannot work this out on its own."""
    stripe.previous_interval = "month"

    resp = client.post("/api/v1/billing/change-plan", json=_body(interval="year"))

    assert resp.json()["billing_cycle_reset"] is True


def test_a_change_within_the_same_interval_is_a_plain_proration(client, db, stripe):
    """Monthly -> monthly keeps the billing date, so `create_prorations` does
    exactly what the pricing page promises."""
    stripe.previous_interval = "month"

    resp = client.post("/api/v1/billing/change-plan", json=_body())

    assert resp.json()["billing_cycle_reset"] is False


def test_an_unknown_previous_interval_is_not_reported_as_a_reset(client, db, stripe):
    """No data is not evidence of a reset: claiming one would scare a customer
    off a change that costs them nothing extra."""
    stripe.previous_interval = None

    resp = client.post("/api/v1/billing/change-plan", json=_body(interval="year"))

    assert resp.json()["billing_cycle_reset"] is False


def test_a_no_op_is_never_a_billing_cycle_reset(client, db, stripe):
    """Nothing was written, so nothing can be billed — whatever the
    intervals say."""
    stripe.changed = False
    stripe.previous_interval = "month"

    resp = client.post("/api/v1/billing/change-plan", json=_body(interval="year"))

    assert resp.json() == {
        "ok": True,
        "status": "active",
        "changed": False,
        "billing_cycle_reset": False,
    }


def test_a_repeated_click_reports_that_nothing_changed(client, db, stripe):
    """Two rapid clicks: the second reaches Stripe, finds the price already
    set and writes nothing. The endpoint says so instead of promising a
    charge that does not exist — and the row still has not moved."""
    stripe.changed = False

    resp = client.post("/api/v1/billing/change-plan", json=_body())

    assert resp.status_code == 200
    assert resp.json()["changed"] is False
    assert db.subscription.plan_id == STARTER.id
    assert db.commits == 0


def test_two_rapid_changes_leave_no_local_state_behind(client, db, stripe):
    """The endpoint writes nothing at all, so racing calls cannot produce an
    incoherent row: whatever order Stripe settles on arrives by webhook."""
    client.post("/api/v1/billing/change-plan", json=_body())
    client.post("/api/v1/billing/change-plan", json=_body(plan_code="starter"))

    assert len(stripe) == 2
    assert db.subscription.plan_id == STARTER.id
    assert db.commits == 0
    assert db.added == []


def test_another_agencys_subscription_cannot_be_changed(client, db, stripe, monkeypatch):
    """Membership is enforced exactly like /checkout does."""

    def _deny(auth, agency_id):
        raise billing_module.HTTPException(403, "agency_access_denied")

    monkeypatch.setattr(billing_module, "ensure_agency_access", _deny)

    resp = client.post("/api/v1/billing/change-plan", json=_body())

    assert resp.status_code == 403
    assert stripe == []


def test_a_real_outsider_is_rejected_by_the_real_check(client, db, stripe):
    """The test above proves the call happens; this one proves the check
    itself bites. A user of another agency, no monkeypatching, real
    `ensure_agency_access`: 403 and not a single Stripe call, because this
    endpoint moves the bill of whichever agency it is pointed at."""
    outsider = AuthContext(
        clerk_user_id="user_outsider",
        email="fuera@otra.es",
        raw_claims={},
        # OWNER a propósito: el gate de rol (n15) corre antes que el de
        # agencia, y lo que este test demuestra es el SEGUNDO — que ni un
        # owner legítimo de otra agencia puede tocar la facturación de esta.
        user=SimpleNamespace(
            is_active=True, agency_id=uuid.uuid4(), role=UserRole.OWNER
        ),
    )
    app.dependency_overrides[require_auth] = lambda: outsider
    try:
        resp = client.post("/api/v1/billing/change-plan", json=_body())
    finally:
        app.dependency_overrides.pop(require_auth, None)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "agency_access_denied"
    assert stripe == []


def test_the_subscription_is_looked_up_by_the_requested_agency(client, db, stripe):
    """Defence in depth: even with the access check satisfied, the row we
    edit is selected by `agency_id`, so there is no way to reach a
    subscription that belongs to somebody else."""
    client.post("/api/v1/billing/change-plan", json=_body())

    (stmt,) = [s for s in db.statements if "FROM subscriptions" in s["sql"]]
    assert str(AGENCY_ID) in [str(v) for v in stmt["params"].values()]
