"""(188) The payment confirmation email.

The most committed moment in the whole product — the customer has just put
99-399 EUR/month on a card — used to pass in complete silence. Now
`checkout.session.completed` sends one email, once, saying what the plan
includes, what to do next, where the invoices are and how to reach us.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api import billing as billing_module
from app.api.billing import send_payment_welcome_email
from app.db.models import SubscriptionStatus
from app.services.emails import build_payment_welcome_email_html

CUSTOMER = "cus_1"
STRIPE_SUB = "sub_1"


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
    stripe_price_id="price_pro_m",
    stripe_price_id_year="price_pro_y",
    max_properties=500,
    max_agents=5,
    max_voice_minutes=600,
)


def _agency(settings=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Inmo Sol",
        slug="inmo-sol",
        contact_email="info@inmosol.example.com",
        settings=settings if settings is not None else {},
    )


def _subscription(agency_id, trial_ends_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency_id,
        plan_id=PLAN.id,
        status=SubscriptionStatus.TRIALING,
        stripe_customer_id=CUSTOMER,
        stripe_subscription_id=None,
        trial_ends_at=trial_ends_at,
    )


class FakeSession:
    def __init__(self, agency, subscription, owner=None):
        self.agency = agency
        self.subscription = subscription
        self.owner = owner

    async def get(self, model, pk):
        return self.agency if self.agency is not None and pk == self.agency.id else None

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM users" in sql:
            return _Result([self.owner] if self.owner else [])
        if "FROM plans" in sql:
            return _Result([PLAN])
        if "FROM subscriptions" in sql:
            return _Result([self.subscription] if self.subscription else [])
        raise AssertionError(f"unexpected query: {sql}")

    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def sent(monkeypatch):
    """Capture what would have gone out through Resend."""
    box: list[dict] = []

    async def _send_email(*, to, subject, html, category=None):
        box.append({"to": to, "subject": subject, "html": html, "category": category})
        return True

    monkeypatch.setattr(billing_module, "send_email", _send_email)
    return box


@pytest.fixture
def failing_send(monkeypatch):
    async def _send_email(*, to, subject, html, category=None):
        return False

    monkeypatch.setattr(billing_module, "send_email", _send_email)


# ---------------------------------------------------------------------------
# send_payment_welcome_email
# ---------------------------------------------------------------------------


async def test_welcome_goes_to_the_owner_not_the_generic_inbox(sent):
    agency = _agency()
    db = FakeSession(agency, _subscription(agency.id), owner=SimpleNamespace(email="dueno@inmosol.example.com"))

    assert await send_payment_welcome_email(db, db.subscription, PLAN) is True
    assert sent[0]["to"] == "dueno@inmosol.example.com"
    assert "Pro" in sent[0]["subject"]


async def test_welcome_falls_back_to_the_agency_contact_email(sent):
    agency = _agency()
    db = FakeSession(agency, _subscription(agency.id))

    await send_payment_welcome_email(db, db.subscription, PLAN)
    assert sent[0]["to"] == "info@inmosol.example.com"


async def test_welcome_lists_the_plan_limits_and_the_invoices_link(sent):
    agency = _agency()
    db = FakeSession(agency, _subscription(agency.id))

    await send_payment_welcome_email(db, db.subscription, PLAN)
    html = sent[0]["html"]
    assert "500" in html and "600" in html
    assert "www.selai.es/billing" in html
    assert "hola@selai.es" in html


async def test_welcome_is_transactional_so_it_carries_no_category(sent):
    """No unsubscribe header, no preference check: this is the receipt for
    money the customer just committed."""
    agency = _agency()
    db = FakeSession(agency, _subscription(agency.id))

    await send_payment_welcome_email(db, db.subscription, PLAN)
    assert sent[0]["category"] is None


async def test_welcome_marks_the_agency_so_it_never_goes_twice(sent):
    agency = _agency()
    db = FakeSession(agency, _subscription(agency.id))

    await send_payment_welcome_email(db, db.subscription, PLAN)
    assert agency.settings["payment_welcome_sent"]

    # A returning customer's second checkout is a brand-new Stripe event id,
    # so the webhook's dedup would let it through. This flag is the backstop.
    assert await send_payment_welcome_email(db, db.subscription, PLAN) is False
    assert len(sent) == 1


async def test_a_failed_send_is_not_marked_as_sent(failing_send):
    agency = _agency()
    db = FakeSession(agency, _subscription(agency.id))

    assert await send_payment_welcome_email(db, db.subscription, PLAN) is False
    assert "payment_welcome_sent" not in agency.settings


async def test_no_plan_means_no_email(sent):
    """An unknown Stripe price left the plan unresolved: better silence than
    an email that invents limits."""
    agency = _agency()
    db = FakeSession(agency, _subscription(agency.id))

    assert await send_payment_welcome_email(db, db.subscription, None) is False
    assert sent == []


async def test_a_broken_mailer_never_breaks_the_webhook(monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("resend down")

    monkeypatch.setattr(billing_module, "send_email", _boom)
    agency = _agency()
    db = FakeSession(agency, _subscription(agency.id))

    assert await send_payment_welcome_email(db, db.subscription, PLAN) is False


async def test_existing_dunning_state_survives_the_welcome(sent):
    """The settings blob is rewritten wholesale; nothing else may be lost."""
    agency = _agency({"voice_id": "sonic", "dunning_count": 2})
    db = FakeSession(agency, _subscription(agency.id))

    await send_payment_welcome_email(db, db.subscription, PLAN)
    assert agency.settings["voice_id"] == "sonic"
    assert agency.settings["dunning_count"] == 2


# ---------------------------------------------------------------------------
# Wiring: checkout.session.completed sends it
# ---------------------------------------------------------------------------


async def test_checkout_completed_defers_the_welcome_until_after_the_commit(sent):
    """The handler applies the plan and hands back the mail as a callable;
    the endpoint runs it only once the event is recorded (see the module
    docstring, invariant 4)."""
    agency = _agency()
    db = FakeSession(agency, _subscription(agency.id))

    after_commit = await billing_module._on_checkout_completed(
        db,
        {
            "subscription": STRIPE_SUB,
            "customer": CUSTOMER,
            "line_items": {"data": [{"price": {"id": "price_pro_m"}}]},
        },
    )

    assert db.subscription.stripe_subscription_id == STRIPE_SUB
    assert db.subscription.plan_id == PLAN.id
    assert sent == []

    await after_commit()
    assert len(sent) == 1


async def test_checkout_completed_without_a_subscription_row_is_retried(sent):
    """(n18) Sin fila de suscripcion no se puede aplicar la compra.

    Antes se salia en silencio devolviendo None, el despachador lo registraba
    como procesado y el pago se perdia. `/checkout` crea la fila antes de
    redirigir, asi que no encontrarla es una carrera — algo que un reintento
    arregla. Y sin correo: no se felicita por un plan que no se ha aplicado.
    """
    db = FakeSession(_agency(), None)

    with pytest.raises(billing_module.EventNotReadyError):
        await billing_module._on_checkout_completed(
            db, {"subscription": STRIPE_SUB, "customer": CUSTOMER}
        )
    assert sent == []


# ---------------------------------------------------------------------------
# The HTML itself
# ---------------------------------------------------------------------------


def _html(**over):
    kwargs = {
        "agency_name": "Inmo Sol",
        "plan_name": "Pro",
        "max_properties": 500,
        "max_agents": 5,
        "max_voice_minutes": 600,
    }
    kwargs.update(over)
    return build_payment_welcome_email_html(**kwargs)


def test_unfinished_setup_points_at_the_wizard():
    html = _html(setup_pending=True)
    assert "www.selai.es/setup" in html
    assert "Terminar la puesta en marcha" in html


def test_finished_setup_does_not_nag_about_the_wizard():
    html = _html(setup_pending=False)
    assert "Terminar la puesta en marcha" not in html
    assert "Ver mi dashboard" in html


def test_a_checkout_that_starts_a_trial_does_not_claim_a_payment():
    """Honesty: with days of trial left Stripe has taken nothing yet."""
    html = _html(trial_days_left=14)
    assert "Tu pago ha entrado" not in html
    assert "14 días" in html
    assert "cancelar sin pagar" in html


def test_a_checkout_that_charges_says_so():
    html = _html(trial_days_left=0)
    assert "Tu pago ha entrado" in html


async def test_the_trial_wording_follows_the_real_deadline(sent):
    agency = _agency()
    sub = _subscription(agency.id, trial_ends_at=datetime.now(UTC) + timedelta(days=6, hours=2))
    db = FakeSession(agency, sub)

    await send_payment_welcome_email(db, sub, PLAN)
    assert "7 días" in sent[0]["html"]
