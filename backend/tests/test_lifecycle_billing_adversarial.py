"""Adversarial pass over the lifecycle/billing work (183, 188, 190, 233).

Each block is an attack that has to fail:

1. **Free trials on tap.** Cancel, come back, get another fortnight. The whole
   point of 183 is that this converges to zero, so it is exercised end to end
   through the real endpoints, not just against `remaining_trial_days`.
2. **The welcome email twice.** Stripe retries a delivery it thinks timed out;
   two concurrent deliveries race. Neither may put a second "gracias por tu
   pago" in the owner's inbox.
3. **An unsubscribe header on the dunning.** "We could not charge your card"
   is transactional. A `List-Unsubscribe` on it invites the customer to opt
   out of being told their agent is about to stop, and tells Gmail the
   message is bulk.
4. **A preference switch silencing something critical.** Dunning and "we have
   paused your account" must survive an agency that switched everything off.
5. **190 firing when nothing happened**, or announcing a blank number.
6. **`email_prefs` accepting junk** and storing it.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from app.api import agency_settings as settings_module
from app.api import billing as billing_module
from app.api.billing import TRIAL_DAYS, remaining_trial_days
from app.core.settings import settings
from app.db.models import SubscriptionStatus
from app.db.models.billing import ProcessedStripeEvent
from app.db.session import get_db
from app.main import app
from app.services import emails as emails_module
from app.services.emails import (
    EMAIL_CATEGORIES,
    EMAIL_CATEGORY_BY_KIND,
    category_for,
    email_prefs,
    should_send,
)

AGENCY_ID = uuid.uuid4()
CUSTOMER = "cus_1"
STRIPE_SUB = "sub_1"

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
    stripe_price_id="price_pro_m",
    stripe_price_id_year="price_pro_y",
)


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


class World:
    """One agency, one plan, one subscription — enough to drive checkout and
    the webhook against each other and watch what happens to the trial."""

    def __init__(self, subscription=None, owner_email="dueno@inmosol.example.com"):
        self.agency = SimpleNamespace(
            id=AGENCY_ID,
            name="Inmo Sol",
            slug="inmo-sol",
            timezone="Europe/Madrid",
            default_language="es",
            contact_email="info@inmosol.example.com",
            settings={},
        )
        self.subscription = subscription
        self.owner = SimpleNamespace(email=owner_email) if owner_email else None
        self.processed: dict[str, ProcessedStripeEvent] = {}
        self.pending: list = []
        self.log: list[str] = []
        self.fail_commit_with: Exception | None = None

    async def get(self, model, pk, **kwargs):
        if model is ProcessedStripeEvent:
            return self.processed.get(pk)
        return self.agency if pk == AGENCY_ID else None

    async def execute(self, stmt):
        sql = str(stmt)
        if "max(processed_stripe_events.event_created)" in sql:
            # Honra el filtro por tipo: la guarda anti-resurrección de la
            # compra pregunta SOLO por las bajas, y un fake que devuelve el
            # máximo de todo convierte cualquier evento posterior en una
            # cancelación imaginaria.
            wanted_types = {
                v
                for v in stmt.compile().params.values()
                if isinstance(v, str) and v.count(".") == 2 and v.startswith("customer.")
            }
            rows = [
                e
                for e in self.processed.values()
                if e.event_created
                and (not wanted_types or getattr(e, "event_type", None) in wanted_types)
            ]
            created = [e.event_created for e in rows]
            return _Result([max(created)] if created else [])
        if "FROM users" in sql:
            return _Result([self.owner] if self.owner else [])
        if "FROM plans" in sql:
            # Honour the price filter, so "Stripe reported a price we do not
            # sell" is actually reachable instead of always resolving to PLAN.
            prices = [
                v
                for v in stmt.compile().params.values()
                if isinstance(v, str) and v.startswith("price_")
            ]
            if prices and not {PLAN.stripe_price_id, PLAN.stripe_price_id_year} & set(prices):
                return _Result([])
            return _Result([PLAN])
        if "FROM subscriptions" in sql:
            return _Result([self.subscription] if self.subscription else [])
        raise AssertionError(f"unexpected query: {sql}")

    def add(self, obj):
        self.pending.append(obj)
        if getattr(obj, "agency_id", None) == AGENCY_ID and hasattr(obj, "trial_ends_at"):
            self.subscription = obj

    async def commit(self):
        if self.fail_commit_with is not None:
            exc, self.fail_commit_with = self.fail_commit_with, None
            self.pending = []
            raise exc
        for obj in self.pending:
            if isinstance(obj, ProcessedStripeEvent):
                self.processed[obj.event_id] = obj
                self.log.append(f"event:{obj.event_id}")
        self.pending = []
        self.log.append("commit")

    async def rollback(self):
        self.pending = []

    async def refresh(self, obj):
        pass


def _sub(trial_ends_at, status=SubscriptionStatus.TRIALING, stripe_sub=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        plan_id=PLAN.id,
        status=status,
        stripe_customer_id=CUSTOMER,
        stripe_subscription_id=stripe_sub,
        trial_ends_at=trial_ends_at,
        current_period_end=None,
        canceled_at=None,
    )


@pytest.fixture
def stripe_calls(monkeypatch):
    calls: list[dict] = []

    async def _create_customer(*, email, agency_id, name):
        return CUSTOMER

    async def _create_checkout(**kwargs):
        calls.append(kwargs)
        return {"url": "https://checkout.stripe.com/s/1", "id": "cs_1"}

    monkeypatch.setattr(billing_module, "create_customer", _create_customer)
    monkeypatch.setattr(billing_module, "create_checkout_session", _create_checkout)
    monkeypatch.setattr(billing_module, "verify_webhook_signature", lambda raw, sig: True)
    return calls


@pytest.fixture
def sent(monkeypatch):
    box: list[dict] = []

    async def _send_email(*, to, subject, html, category=None):
        box.append({"to": to, "subject": subject, "html": html, "category": category})
        return True

    monkeypatch.setattr(billing_module, "send_email", _send_email)
    monkeypatch.setattr(settings_module, "send_email", _send_email)
    return box


@pytest.fixture
def client(monkeypatch, world):
    async def _override():
        yield world

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def world():
    return World()


CHECKOUT_BODY = {
    "agency_id": str(AGENCY_ID),
    "plan_code": "pro",
    "success_url": "https://www.selai.es/billing?ok=1",
    "cancel_url": "https://www.selai.es/billing",
}


def _checkout(client):
    return client.post("/api/v1/billing/checkout", json=CHECKOUT_BODY)


def _webhook(client, event):
    return client.post(
        "/api/v1/billing/webhook", json=event, headers={"stripe-signature": "t=1,v1=x"}
    )


def _completed(event_id="evt_checkout", created=1000, stripe_sub=STRIPE_SUB):
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "created": created,
        "data": {
            "object": {
                "customer": CUSTOMER,
                "subscription": stripe_sub,
                "line_items": {"data": [{"price": {"id": "price_pro_m"}}]},
            }
        },
    }


def _deleted(event_id="evt_deleted", created=2000, stripe_sub=STRIPE_SUB):
    return {
        "id": event_id,
        "type": "customer.subscription.deleted",
        "created": created,
        "data": {"object": {"id": stripe_sub, "customer": CUSTOMER}},
    }


def _payment_failed(event_id="evt_payment_failed", created=2000):
    return {
        "id": event_id,
        "type": "invoice.payment_failed",
        "created": created,
        "data": {"object": {"id": "in_1", "customer": CUSTOMER}},
    }


# ---------------------------------------------------------------------------
# 1. Free trials on tap
# ---------------------------------------------------------------------------


def test_cancel_and_rebuy_never_hands_out_a_second_trial(client, world, stripe_calls, sent):
    """The whole cycle: sign up, burn the trial, pay, cancel, come back.

    The second checkout must ask Stripe for **no** trial. This is the
    regression 183 exists for — before it, every lap round this loop was
    another free fortnight, for ever.
    """
    world.subscription = _sub(datetime.now(UTC) + timedelta(days=TRIAL_DAYS))

    assert _checkout(client).status_code == 200
    assert stripe_calls[0]["trial_days"] == TRIAL_DAYS

    assert _webhook(client, _completed()).json() == {"status": "ok"}

    # The trial runs out while they are a paying customer, then they cancel.
    world.subscription.trial_ends_at = datetime.now(UTC) - timedelta(days=30)
    assert _webhook(client, _deleted()).json() == {"status": "ok"}
    assert world.subscription.status == SubscriptionStatus.CANCELED

    # Back a month later.
    assert _checkout(client).status_code == 200
    assert stripe_calls[1]["trial_days"] == 0


def test_the_loop_converges_instead_of_topping_the_trial_up(client, world, stripe_calls, sent):
    """Cancelling on day 2 does not restart the clock: what is left keeps
    shrinking, lap after lap, because the deadline is never rewritten."""
    world.subscription = _sub(datetime.now(UTC) + timedelta(days=12))

    _checkout(client)
    assert stripe_calls[0]["trial_days"] == 12

    _webhook(client, _completed())
    _webhook(client, _deleted())

    _checkout(client)
    assert stripe_calls[1]["trial_days"] == 12  # not 14, and not 12 + anything

    world.subscription.trial_ends_at = datetime.now(UTC) - timedelta(seconds=1)
    _checkout(client)
    assert stripe_calls[2]["trial_days"] == 0


def test_cancelling_does_not_touch_the_trial_deadline(client, world, stripe_calls, sent):
    """`trial_ends_at` is the only proof the trial was used. If cancellation
    cleared it, `remaining_trial_days` would read "never had one" and hand
    out a fresh fortnight."""
    deadline = datetime.now(UTC) - timedelta(days=3)
    world.subscription = _sub(deadline, status=SubscriptionStatus.ACTIVE, stripe_sub=STRIPE_SUB)

    _webhook(client, _deleted())

    assert world.subscription.trial_ends_at == deadline
    assert remaining_trial_days(world.subscription) == 0


@pytest.mark.parametrize("days_ago", [1, 30, 400, 4000])
def test_a_long_dead_trial_is_zero_and_never_negative(days_ago):
    """A negative number would reach Stripe as `trial_period_days=-400` and
    400 the checkout of a customer trying to give us money."""
    left = remaining_trial_days(_sub(datetime.now(UTC) - timedelta(days=days_ago)))
    assert left == 0


def test_a_returning_customer_is_marked_active_not_trialing(client, world, stripe_calls, sent):
    """They paid on the spot — there was no trial to grant. Calling them
    TRIALING with a deadline in the past shows a paying customer an expired
    trial until Stripe next changes something, and leaves them one missing
    `stripe_subscription_id IS NULL` filter away from being paused by
    `lifecycle.trial_expiry_sweep`.
    """
    world.subscription = _sub(
        datetime.now(UTC) - timedelta(days=200), status=SubscriptionStatus.CANCELED
    )

    _webhook(client, _completed())

    assert world.subscription.status == SubscriptionStatus.ACTIVE


def test_a_first_checkout_mid_trial_is_still_trialing(client, world, stripe_calls, sent):
    world.subscription = _sub(datetime.now(UTC) + timedelta(days=9))

    _webhook(client, _completed())

    assert world.subscription.status == SubscriptionStatus.TRIALING


def test_an_abandoned_checkout_does_not_reset_the_clock(client, world, stripe_calls, sent):
    """The row created by a first checkout carries a deadline, so walking
    away and coming back three days later costs those three days rather than
    minting a new fortnight."""
    _checkout(client)
    assert stripe_calls[0]["trial_days"] == TRIAL_DAYS
    created = world.subscription

    assert created is not None
    created.trial_ends_at = datetime.now(UTC) + timedelta(days=11)

    _checkout(client)
    assert stripe_calls[1]["trial_days"] == 11


def test_the_portal_does_not_try_to_smuggle_promotion_codes(client, world, monkeypatch):
    """183 asks for coupon support; it stops at checkout on purpose. Stripe's
    billing-portal *session* has no `allow_promotion_codes` parameter — the
    portal's coupon field is part of its dashboard configuration — so sending
    one would 400 every customer trying to manage their card."""
    seen: dict = {}

    async def _portal(*, customer_id, return_url, **extra):
        seen.update(extra)
        return {"url": "https://billing.stripe.com/p/1"}

    monkeypatch.setattr(billing_module, "create_billing_portal_session", _portal)
    world.subscription = _sub(None, status=SubscriptionStatus.ACTIVE, stripe_sub=STRIPE_SUB)

    resp = client.post(
        "/api/v1/billing/portal",
        json={"agency_id": str(AGENCY_ID), "return_url": "https://www.selai.es/billing"},
    )

    assert resp.status_code == 200
    assert seen == {}


# ---------------------------------------------------------------------------
# 2. The welcome email, once
# ---------------------------------------------------------------------------


def test_a_stripe_retry_of_the_same_delivery_mails_nobody_twice(
    client, world, stripe_calls, sent
):
    world.subscription = _sub(datetime.now(UTC) + timedelta(days=9))

    assert _webhook(client, _completed()).json() == {"status": "ok"}
    assert _webhook(client, _completed()).json() == {"status": "duplicate"}

    assert len(sent) == 1


def test_a_delivery_that_loses_the_insert_race_mails_nobody(client, world, stripe_calls, sent):
    """Two concurrent deliveries of the same event: both read the dedup table
    before either wrote it, so both run the handler and one loses on the
    unique insert. The loser must not have mailed anything — which is only
    true because the email waits for the commit.
    """
    world.subscription = _sub(datetime.now(UTC) + timedelta(days=9))
    world.fail_commit_with = IntegrityError("insert", {}, Exception("duplicate key"))

    assert _webhook(client, _completed()).json() == {"status": "duplicate"}
    assert sent == []


def test_nothing_is_mailed_before_the_event_is_recorded(client, world, stripe_calls, monkeypatch):
    """Ordering, not just counting: if the send happened first, a commit that
    failed afterwards would replay the whole thing on Stripe's retry."""
    order: list[str] = []

    async def _send_email(*, to, subject, html, category=None):
        order.append("email")
        return True

    monkeypatch.setattr(billing_module, "send_email", _send_email)
    world.subscription = _sub(datetime.now(UTC) + timedelta(days=9))
    world.log = order

    _webhook(client, _completed())

    assert order.index("event:evt_checkout") < order.index("email")


def test_dunning_is_post_commit_and_a_stripe_retry_does_not_duplicate_it(
    client, world, stripe_calls, monkeypatch
):
    order: list[str] = []

    async def _send_email(*, to, subject, html, category=None):
        order.append("email")
        return True

    monkeypatch.setattr(billing_module, "send_email", _send_email)
    world.subscription = _sub(
        datetime.now(UTC) - timedelta(days=1),
        status=SubscriptionStatus.ACTIVE,
        stripe_sub=STRIPE_SUB,
    )
    world.log = order

    assert _webhook(client, _payment_failed()).json() == {"status": "ok"}
    assert order.index("event:evt_payment_failed") < order.index("email")
    assert order.count("email") == 1
    assert world.agency.settings["dunning_count"] == 1

    assert _webhook(client, _payment_failed()).json() == {"status": "duplicate"}
    assert order.count("email") == 1


def test_dunning_is_not_sent_when_the_event_commit_loses_the_insert_race(
    client, world, stripe_calls, sent
):
    world.subscription = _sub(
        datetime.now(UTC) - timedelta(days=1),
        status=SubscriptionStatus.ACTIVE,
        stripe_sub=STRIPE_SUB,
    )
    world.fail_commit_with = IntegrityError("insert", {}, Exception("duplicate key"))

    assert _webhook(client, _payment_failed()).json() == {"status": "duplicate"}
    assert sent == []
    assert "dunning_count" not in world.agency.settings


def test_dunning_waits_through_a_failed_commit_then_sends_once_on_retry(
    client, world, stripe_calls, sent
):
    world.subscription = _sub(
        datetime.now(UTC) - timedelta(days=1),
        status=SubscriptionStatus.ACTIVE,
        stripe_sub=STRIPE_SUB,
    )
    world.fail_commit_with = RuntimeError("database unavailable")
    event = _payment_failed()

    with pytest.raises(RuntimeError, match="database unavailable"):
        _webhook(client, event)

    assert sent == []
    assert "evt_payment_failed" not in world.processed
    assert "dunning_count" not in world.agency.settings

    assert _webhook(client, event).json() == {"status": "ok"}
    assert len(sent) == 1
    assert world.agency.settings["dunning_count"] == 1


def test_a_second_purchase_by_the_same_agency_is_not_re_welcomed(
    client, world, stripe_calls, sent
):
    """A returning customer's checkout is a brand-new event id, so the dedup
    table cannot help: the durable flag on the agency has to."""
    world.subscription = _sub(datetime.now(UTC) + timedelta(days=9))

    _webhook(client, _completed(event_id="evt_a", created=1000))
    assert len(sent) == 1

    _webhook(client, _completed(event_id="evt_b", created=3000))
    assert len(sent) == 1


def test_an_unknown_price_is_retried_instead_of_swallowed(
    client, world, stripe_calls, sent, monkeypatch
):
    """(n18) El precio no corresponde a ningun plan nuestro y el cargo YA se
    hizo. Antes esto devolvia 200, se registraba como procesado, y la agencia
    se quedaba en el plan viejo para siempre: los reintentos de Stripe
    chocaban con el guard de duplicados y nadie se enteraba.

    Ahora responde 503 y NO lo registra, que es lo unico que deja a Stripe
    reintentar mientras se investiga. Sigue sin mandar correo: no se felicita
    por unos limites que no se han aplicado.
    """
    world.subscription = _sub(datetime.now(UTC) + timedelta(days=9))
    event = _completed()
    event["data"]["object"]["line_items"] = {"data": [{"price": {"id": "price_unknown"}}]}

    async def _no_fetch(path, params=None):
        return {}

    monkeypatch.setattr(billing_module, "stripe_get", _no_fetch)

    resp = _webhook(client, event)
    assert resp.status_code == 503
    assert sent == []
    # Y sin marcar como procesado: si se marcara, el reintento seria un no-op.
    assert world.processed == {}


# ---------------------------------------------------------------------------
# 3/4. Headers and preferences on the critical mail
# ---------------------------------------------------------------------------


class _FakeResponse:
    status_code = 200
    text = "{}"

    def json(self) -> dict:
        return {"id": "email_1"}


class _FakeClient:
    payloads: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).payloads.append(json)
        return _FakeResponse()


@pytest.fixture
def resend(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test"))
    monkeypatch.setattr(emails_module.httpx, "AsyncClient", _FakeClient)
    _FakeClient.payloads = []
    return _FakeClient


async def test_dunning_reaches_resend_with_no_unsubscribe_header(resend):
    """End to end through the real `send_email`, not just "category is None":
    the payload Resend receives must carry no `headers` at all."""
    world = World()
    sub = _sub(datetime.now(UTC) - timedelta(days=30), status=SubscriptionStatus.PAST_DUE)

    assert await billing_module.send_dunning_email(world, sub, attempt=1) is True

    payload = resend.payloads[0]
    assert "headers" not in payload
    assert "List-Unsubscribe" not in str(payload)


async def test_dunning_still_goes_out_with_every_switch_off(resend):
    """An agency that unsubscribed from digests, alerts and beats has not
    unsubscribed from "we could not charge your card" — there is no switch
    for it, and `should_send` is never consulted for it."""
    world = World()
    world.agency.settings = {"email_prefs": dict.fromkeys(EMAIL_CATEGORIES, False)}
    sub = _sub(None, status=SubscriptionStatus.PAST_DUE)

    assert await billing_module.send_dunning_email(world, sub, attempt=1) is True
    assert len(resend.payloads) == 1


async def test_the_payment_welcome_reaches_resend_without_headers(resend):
    world = World()
    sub = _sub(None)

    assert await billing_module.send_payment_welcome_email(world, sub, PLAN) is True
    assert "headers" not in resend.payloads[0]


@pytest.mark.parametrize(
    "kind", ["payment_failed", "trial_ended", "payment_welcome", "phone_active", "welcome"]
)
def test_critical_mail_is_classified_as_transactional(kind):
    """The registry is the contract with `app/workers/tasks.py`. Filing
    "hemos pausado tu cuenta" or the dunning under `lifecycle` would let a
    preference switch suppress a service notice."""
    assert category_for(kind) is None


@pytest.mark.parametrize("kind", ["weekly_digest", "hot_lead", "trial_ending", "winback"])
def test_optional_mail_is_classified_under_a_real_switch(kind):
    assert category_for(kind) in EMAIL_CATEGORIES


def test_the_registry_never_invents_a_category():
    assert set(EMAIL_CATEGORY_BY_KIND.values()) <= {None, *EMAIL_CATEGORIES}


def test_an_unclassified_email_defaults_to_transactional():
    """Fail safe: a builder somebody adds tomorrow and forgets to classify
    goes out to everyone rather than being silently opt-out-able."""
    assert category_for("some_email_nobody_classified_yet") is None


@pytest.mark.parametrize("category", [None, "dunning", "trial_ended", ""])
def test_should_send_never_blocks_something_it_does_not_govern(category):
    agency = SimpleNamespace(settings={"email_prefs": dict.fromkeys(EMAIL_CATEGORIES, False)})
    assert should_send(agency, category) is True


# ---------------------------------------------------------------------------
# 5. The panel and the sender must agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blob",
    [
        {},
        {"email_prefs": {}},
        {"email_prefs": {"digest": False}},
        {"email_prefs": {"digest": False, "alerts": False, "lifecycle": False}},
        {"email_prefs": {"digest": True, "alerts": False}},
        {"email_prefs": "corrupt"},
        {"email_prefs": {"unknown": False}},
    ],
)
def test_what_the_panel_shows_is_what_the_sender_does(monkeypatch, blob):
    """One implementation, two readers. If they drifted, the panel would show
    a switch as off while the mail kept going out — or the reverse, which is
    an agency silenced without asking."""
    world = World()
    world.agency.settings = dict(blob)

    async def _override():
        yield world

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    app.dependency_overrides[get_db] = _override
    try:
        shown = TestClient(app).get(f"/api/v1/agencies/{AGENCY_ID}/settings").json()
    finally:
        app.dependency_overrides.pop(get_db, None)

    for category in EMAIL_CATEGORIES:
        assert shown["settings"]["email_prefs"][category] is should_send(world.agency, category)
        assert email_prefs(world.agency.settings)[category] is should_send(world.agency, category)


# ---------------------------------------------------------------------------
# 6. email_prefs takes booleans or nothing
# ---------------------------------------------------------------------------


def _patch(client, **body):
    return client.patch(f"/api/v1/agencies/{AGENCY_ID}/settings", json=body)


@pytest.mark.parametrize(
    "value",
    ["maybe", 7, -1, [], {"nested": True}, [True], "sí"],
)
def test_a_junk_switch_is_422_and_stores_nothing(client, world, value):
    resp = _patch(client, email_prefs={"digest": value})

    assert resp.status_code == 422
    assert "email_prefs" not in world.agency.settings


@pytest.mark.parametrize("value", ["off", [], 3, True])
def test_a_junk_preferences_object_is_422(client, world, value):
    assert _patch(client, email_prefs=value).status_code == 422
    assert "email_prefs" not in world.agency.settings


def test_an_explicit_null_switch_leaves_the_stored_value_alone(client, world):
    world.agency.settings = {"email_prefs": {"digest": False}}

    resp = _patch(client, email_prefs={"digest": None, "alerts": False})

    assert resp.status_code == 200
    assert world.agency.settings["email_prefs"] == {
        "digest": False,
        "alerts": False,
        "lifecycle": True,
    }


def test_only_the_three_real_switches_are_ever_stored(client, world):
    _patch(client, email_prefs={"promos": False, "digest": False})

    assert set(world.agency.settings["email_prefs"]) == set(EMAIL_CATEGORIES)


# ---------------------------------------------------------------------------
# 7. (190) The number email fires on a real transition and nothing else
# ---------------------------------------------------------------------------


def test_a_whitespace_only_number_is_neither_stored_nor_announced(client, world, sent):
    """`"   "` is truthy in Python. Announcing it would send the customer an
    email with a blank number and three unusable forwarding codes."""
    world.agency.settings = {"phone_number_requested": True}

    resp = _patch(client, ai_phone_number="   ")

    assert resp.status_code == 200
    assert sent == []
    assert world.agency.settings["ai_phone_number"] is None


def test_a_number_is_stored_trimmed(client, world, sent):
    world.agency.settings = {"phone_number_requested": True}

    _patch(client, ai_phone_number="  +34910000000  ")

    assert world.agency.settings["ai_phone_number"] == "+34910000000"
    assert len(sent) == 1


def test_an_absurd_number_is_rejected_before_it_reaches_an_email(client, world, sent):
    """The value is interpolated raw into the email body and into the GSM
    codes; unbounded free text has no business there."""
    resp = _patch(client, ai_phone_number="+34" + "9" * 400)

    assert resp.status_code == 422
    assert "ai_phone_number" not in world.agency.settings
    assert sent == []


def test_re_patching_the_very_same_number_does_not_re_announce(client, world, sent):
    world.agency.settings = {"ai_phone_number": "+34910000000", "phone_number_requested": True}

    _patch(client, ai_phone_number="+34910000000")

    assert sent == []


def test_a_stale_request_flag_is_cleared_once_a_number_is_on_file(client, world, sent):
    """`POST /channels/phone` sets `ai_phone_number` without clearing the
    flag, so both keys can be true at once. Any PATCH that leaves a number
    behind settles it — without announcing anything, because the customer
    already has the number."""
    world.agency.settings = {"ai_phone_number": "+34910000000", "phone_number_requested": True}

    _patch(client, ai_phone_number="+34910000001")

    assert "phone_number_requested" not in world.agency.settings
    assert sent == []


def test_clearing_the_number_leaves_the_pending_request_pending(client, world, sent):
    world.agency.settings = {"phone_number_requested": True}

    _patch(client, ai_phone_number=None)

    assert world.agency.settings["phone_number_requested"] is True
    assert sent == []


def test_the_announcement_is_transactional(client, world, sent):
    world.agency.settings = {"phone_number_requested": True}

    _patch(client, ai_phone_number="+34910000000")

    assert sent[0]["category"] is None


def test_a_patch_of_something_else_never_announces(client, world, sent):
    world.agency.settings = {"phone_number_requested": True, "ai_phone_number": None}

    _patch(client, ai_tone="formal", email_prefs={"digest": False})

    assert sent == []
    assert world.agency.settings["phone_number_requested"] is True


# ---------------------------------------------------------------------------
# (n18) La compra no se pierde: ni por desorden ni por un fallo temporal
# ---------------------------------------------------------------------------


def _updated_evt(status="active", event_id="evt_upd", created=5000):
    return {
        "id": event_id,
        "type": "customer.subscription.updated",
        "created": created,
        "data": {"object": {"id": STRIPE_SUB, "customer": CUSTOMER, "status": status}},
    }


def test_a_purchase_that_arrives_after_a_later_event_still_applies(
    client, world, stripe_calls, sent
):
    """Stripe no garantiza el orden de entrega.

    Un `customer.subscription.updated` sellado un segundo después llega antes
    y, con el guard de orden aplicado a todo, la COMPRA se marcaba como
    "stale": se registraba sin aplicarse y la agencia que acababa de pagar se
    quedaba en el plan viejo para siempre. La compra está exenta del guard.
    """
    world.subscription = _sub(datetime.now(UTC) + timedelta(days=9))

    # Primero el evento posterior (created mayor).
    assert _webhook(client, _updated_evt(created=5000)).json()["status"] == "ok"
    # Y ahora la compra, más antigua: tiene que aplicarse igualmente.
    resp = _webhook(client, _completed(event_id="evt_late_purchase", created=1000))

    assert resp.json()["status"] == "ok"
    assert world.subscription.plan_id == PLAN.id
    assert len(sent) == 1  # y el correo de bienvenida sale una sola vez


def test_a_purchase_older_than_a_cancellation_does_not_resurrect_it(
    client, world, stripe_calls, sent
):
    """El precio de eximir la compra del guard: no puede revivir una cuenta
    dada de baja DESPUÉS. Se comprueba contra la baja, no contra el evento
    más reciente de cualquier tipo."""
    world.subscription = _sub(datetime.now(UTC) + timedelta(days=9))

    assert _webhook(client, _deleted(created=9000)).json()["status"] == "ok"
    resp = _webhook(client, _completed(event_id="evt_zombi", created=1000))

    assert resp.json()["status"] == "stale"
    assert sent == []


def test_a_failed_price_lookup_is_retried_not_recorded(
    client, world, stripe_calls, sent, monkeypatch
):
    """Si Stripe no responde al leer la suscripción, antes se devolvía None,
    el evento se marcaba como procesado y el pago se perdía en silencio."""
    world.subscription = _sub(datetime.now(UTC) + timedelta(days=9))

    async def _boom(path, params=None):
        raise RuntimeError("stripe caído")

    monkeypatch.setattr(billing_module, "stripe_get", _boom)

    event = _completed(event_id="evt_sin_precio")
    event["data"]["object"].pop("line_items")  # fuerza la consulta a Stripe

    resp = _webhook(client, event)
    assert resp.status_code == 503
    assert world.processed == {}
    assert sent == []
