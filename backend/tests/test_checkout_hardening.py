"""(183) POST /billing/checkout no longer gives the trial away twice.

`trial_period_days=14` used to be unconditional, so cancelling and buying
again bought another free fortnight, as many times as the customer liked.
Checkout now derives the trial from the deadline the subscription already
carries, and turns on Stripe's promotion-code field so discounts exist at all.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import billing as billing_module
from app.api.billing import TRIAL_DAYS, remaining_trial_days
from app.core.settings import settings
from app.db.models import SubscriptionStatus
from app.db.session import get_db
from app.main import app
from app.services import stripe_billing

AGENCY_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# remaining_trial_days — the policy, in isolation
# ---------------------------------------------------------------------------


def _sub(trial_ends_at, status=SubscriptionStatus.TRIALING):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        plan_id=uuid.uuid4(),
        status=status,
        stripe_customer_id="cus_existing",
        # Sin suscripción de Stripe viva: es el escenario de estos tests
        # (checkout abandonado / trial consumido). Con una viva, el guard
        # anti doble suscripción corta con 409 antes de llegar aquí.
        stripe_subscription_id=None,
        trial_ends_at=trial_ends_at,
    )


def test_no_subscription_row_gets_the_full_trial():
    assert remaining_trial_days(None) == TRIAL_DAYS


def test_expired_trial_gets_nothing():
    assert remaining_trial_days(_sub(datetime.now(UTC) - timedelta(days=1))) == 0


def test_trial_that_ends_right_now_gets_nothing():
    assert remaining_trial_days(_sub(datetime.now(UTC))) == 0


def test_canceled_customer_buying_again_gets_no_second_trial():
    """The hole this closes: cancel, come back, pay from day one."""
    sub = _sub(datetime.now(UTC) - timedelta(days=90), status=SubscriptionStatus.CANCELED)
    assert remaining_trial_days(sub) == 0


def test_a_subscription_without_a_deadline_gets_nothing():
    """No deadline means nothing proves trial time is left — the row has
    already been through Stripe. Erring the other way hands out free months."""
    assert remaining_trial_days(_sub(None)) == 0


def test_trial_in_progress_keeps_only_what_is_left():
    assert remaining_trial_days(_sub(datetime.now(UTC) + timedelta(days=5, hours=1))) == 6
    assert remaining_trial_days(_sub(datetime.now(UTC) + timedelta(hours=2))) == 1


def test_trial_is_capped_at_the_advertised_length():
    sub = _sub(datetime.now(UTC) + timedelta(days=400))
    assert remaining_trial_days(sub) == TRIAL_DAYS


def test_naive_datetimes_are_read_as_utc():
    """Some drivers hand back naive datetimes; comparing them to an aware
    `now` would raise and take the whole checkout down."""
    naive = (datetime.now(UTC) + timedelta(days=3)).replace(tzinfo=None)
    assert remaining_trial_days(_sub(naive)) == 3


# ---------------------------------------------------------------------------
# The endpoint forwards the policy to Stripe
# ---------------------------------------------------------------------------


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


PLAN = SimpleNamespace(
    id=uuid.uuid4(),
    code="pro",
    name="Pro",
    description="",
    monthly_price_eur=199,
    features={},
    max_agents=5,
    max_properties=500,
    max_voice_minutes=600,
    is_active=True,
    stripe_product_id="prod_1",
    stripe_price_id="price_pro_m",
    stripe_price_id_year="price_pro_y",
)


class FakeSession:
    def __init__(self, subscription=None):
        self.subscription = subscription
        self.agency = SimpleNamespace(
            id=AGENCY_ID, name="Inmo Sol", slug="inmo-sol", contact_email="dueno@inmosol.example.com"
        )
        self.added: list = []

    async def get(self, model, pk):
        return self.agency if pk == AGENCY_ID else None

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM plans" in sql:
            return _Result([PLAN])
        if "FROM subscriptions" in sql:
            return _Result([self.subscription] if self.subscription else [])
        raise AssertionError(f"unexpected query: {sql}")

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def stripe_calls(monkeypatch):
    calls: list[dict] = []

    async def _create_customer(*, email, agency_id, name):
        return "cus_new"

    async def _create_checkout(**kwargs):
        calls.append(kwargs)
        return {"url": "https://checkout.stripe.com/s/1", "id": "cs_1"}

    monkeypatch.setattr(billing_module, "create_customer", _create_customer)
    monkeypatch.setattr(billing_module, "create_checkout_session", _create_checkout)
    return calls


def _client(monkeypatch, session):
    async def _override():
        yield session

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    app.dependency_overrides[get_db] = _override
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_db, None)


BODY = {
    "agency_id": str(AGENCY_ID),
    "plan_code": "pro",
    "success_url": "https://www.selai.es/billing?ok=1",
    "cancel_url": "https://www.selai.es/billing",
}


def test_checkout_offers_promotion_codes(monkeypatch, stripe_calls):
    client = _client(monkeypatch, FakeSession())
    assert client.post("/api/v1/billing/checkout", json=BODY).status_code == 200
    assert stripe_calls[0]["allow_promotion_codes"] is True


def test_first_checkout_still_gets_the_full_trial(monkeypatch, stripe_calls):
    client = _client(monkeypatch, FakeSession())
    client.post("/api/v1/billing/checkout", json=BODY)
    assert stripe_calls[0]["trial_days"] == TRIAL_DAYS


def test_second_checkout_after_the_trial_burned_gets_none(monkeypatch, stripe_calls):
    session = FakeSession(_sub(datetime.now(UTC) - timedelta(days=2)))
    client = _client(monkeypatch, session)
    client.post("/api/v1/billing/checkout", json=BODY)
    assert stripe_calls[0]["trial_days"] == 0


def test_checkout_mid_trial_keeps_the_remaining_days(monkeypatch, stripe_calls):
    session = FakeSession(_sub(datetime.now(UTC) + timedelta(days=8, hours=3)))
    client = _client(monkeypatch, session)
    client.post("/api/v1/billing/checkout", json=BODY)
    assert stripe_calls[0]["trial_days"] == 9


def test_checkout_start_now_forfeits_the_remaining_trial(monkeypatch, stripe_calls):
    session = FakeSession(_sub(datetime.now(UTC) + timedelta(days=8, hours=3)))
    client = _client(monkeypatch, session)
    response = client.post(
        "/api/v1/billing/checkout", json={**BODY, "start_now": True}
    )

    assert response.status_code == 200
    assert stripe_calls[0]["trial_days"] == 0
    assert stripe_calls[0]["start_now"] is True


def test_checkout_default_does_not_start_now(monkeypatch, stripe_calls):
    session = FakeSession(_sub(datetime.now(UTC) + timedelta(days=8, hours=3)))
    client = _client(monkeypatch, session)
    client.post("/api/v1/billing/checkout", json=BODY)

    assert stripe_calls[0]["trial_days"] == 9
    assert stripe_calls[0]["start_now"] is False


def test_repeated_checkout_uses_one_stable_stripe_key(monkeypatch, stripe_calls):
    session = FakeSession(_sub(datetime.now(UTC) + timedelta(days=8)))
    client = _client(monkeypatch, session)

    first = client.post("/api/v1/billing/checkout", json=BODY)
    second = client.post(
        "/api/v1/billing/checkout", json={**BODY, "start_now": True}
    )

    assert first.status_code == second.status_code == 200
    assert len(stripe_calls) == 2
    assert stripe_calls[0]["idempotency_key"] == stripe_calls[1]["idempotency_key"]
    assert stripe_calls[0]["idempotency_key"].endswith(":initial")


# ---------------------------------------------------------------------------
# What actually reaches Stripe's API
# ---------------------------------------------------------------------------


@pytest.fixture
def posted(monkeypatch):
    seen: dict = {}

    async def _post(path, form, *, idempotency_key=None):
        seen["path"] = path
        seen["form"] = form
        seen["idempotency_key"] = idempotency_key
        return {"id": "cs_1", "url": "https://checkout.stripe.com/s/1"}

    monkeypatch.setattr(stripe_billing, "_post", _post)
    return seen


async def _create(**over):
    kwargs = {
        "customer_id": "cus_1",
        "price_id": "price_pro_m",
        "success_url": "https://www.selai.es/ok",
        "cancel_url": "https://www.selai.es/no",
    }
    kwargs.update(over)
    return await stripe_billing.create_checkout_session(**kwargs)


async def test_zero_trial_days_omits_the_parameter(posted):
    """Stripe rejects `trial_period_days=0` (its minimum is 1), so "no trial"
    has to be the absence of the field, not a zero."""
    await _create(trial_days=0)
    assert "subscription_data[trial_period_days]" not in posted["form"]


async def test_positive_trial_days_are_sent(posted):
    await _create(trial_days=9)
    assert posted["form"]["subscription_data[trial_period_days]"] == "9"


async def test_start_now_is_persisted_without_a_stripe_trial(posted):
    # Even a caller that forgets to zero `trial_days` cannot send Stripe the
    # contradictory combination "charge now, but start with a trial".
    await _create(start_now=True)

    assert "subscription_data[trial_period_days]" not in posted["form"]
    assert posted["form"]["metadata[selai_start_now]"] == "true"
    assert posted["form"]["subscription_data[metadata][selai_start_now]"] == "true"


async def test_default_checkout_has_no_start_now_metadata(posted):
    await _create(trial_days=9)

    assert "metadata[selai_start_now]" not in posted["form"]
    assert "subscription_data[metadata][selai_start_now]" not in posted["form"]


async def test_checkout_idempotency_key_reaches_the_stripe_transport(posted):
    await _create(idempotency_key="selai-checkout:agency:initial")

    assert posted["idempotency_key"] == "selai-checkout:agency:initial"


async def test_customer_creation_is_idempotent_per_agency(posted):
    await stripe_billing.create_customer(
        email="owner@example.com", agency_id="agency-1", name="Inmo Sol"
    )

    assert posted["path"] == "/customers"
    assert posted["idempotency_key"] == "selai-customer:agency-1"


async def test_post_places_the_idempotency_key_in_stripes_header(monkeypatch):
    seen: dict = {}

    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {"id": "cs_1"}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, data, headers):
            seen.update(url=url, data=data, headers=headers)
            return _Response()

    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_live_test"))
    monkeypatch.setattr(stripe_billing.httpx, "AsyncClient", _Client)

    await stripe_billing._post(
        "/checkout/sessions",
        {"mode": "subscription"},
        idempotency_key="selai-checkout:agency:initial",
    )

    assert seen["headers"]["Idempotency-Key"] == "selai-checkout:agency:initial"


async def test_live_trial_is_ended_on_the_existing_subscription(monkeypatch, posted):
    async def _get(path, params=None):
        assert path == "/subscriptions/sub_1"
        return {"items": {"data": [{"id": "si_1", "price": {"id": "price_old"}}]}}

    monkeypatch.setattr(stripe_billing, "_get", _get)
    monkeypatch.setattr(stripe_billing, "_api_key", lambda: "sk_live_test")

    await stripe_billing.activate_trial_now(
        subscription_id="sub_1", price_id="price_pro_m"
    )

    assert posted["path"] == "/subscriptions/sub_1"
    assert posted["form"] == {
        "items[0][id]": "si_1",
        "items[0][price]": "price_pro_m",
        "trial_end": "now",
        "proration_behavior": "none",
        "metadata[selai_start_now]": "true",
    }
    assert posted["idempotency_key"] == "selai-start-now:sub_1"


async def test_promotion_codes_are_off_unless_asked_for(posted):
    await _create()
    assert "allow_promotion_codes" not in posted["form"]


async def test_promotion_codes_reach_stripe_as_a_form_boolean(posted):
    await _create(allow_promotion_codes=True)
    assert posted["form"]["allow_promotion_codes"] == "true"
