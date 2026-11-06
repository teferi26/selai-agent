"""Fiscal integrity of the Stripe checkout.

Sellable Stripe Prices are tax-inclusive, so the advertised amount remains
the final amount. Four Checkout parameters complete that contract, and all
four have to be there:

- `automatic_tax[enabled]` — Stripe computes the rate from the customer's
  location (and applies reverse charge for EU businesses outside Spain);
- `tax_id_collection[enabled]` — shows the NIF/VAT field, without which a
  Spanish business cannot be invoiced properly;
- `customer_update[address]` / `customer_update[name]` — let Stripe save what
  it collected onto the customer, so later invoices are taxed off fresh data
  rather than whatever the record held on day one.

`customer_update` is only accepted when the session carries a `customer`,
which every call site does; one test pins that dependency.

If Stripe Tax is not active, Checkout must fail closed. Retrying without the
tax block could create a subscription whose invoices lack the required fiscal
treatment, so no tax-related 4xx receives a fallback or retry.
"""

from __future__ import annotations

import httpx
import pytest

from app.services import stripe_billing


@pytest.fixture
def posted(monkeypatch):
    seen: dict = {}

    async def _post(path, form):
        seen["path"] = path
        seen["form"] = form
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


async def test_automatic_tax_is_enabled(posted):
    await _create()
    assert posted["form"]["automatic_tax[enabled]"] == "true"


async def test_tax_id_collection_is_enabled(posted):
    """A Spanish agency paying 199 EUR/month needs its NIF on the invoice to
    deduct the IVA; without this field Stripe never asks for it."""
    await _create()
    assert posted["form"]["tax_id_collection[enabled]"] == "true"


async def test_customer_update_saves_address_and_name(posted):
    await _create()
    assert posted["form"]["customer_update[address]"] == "auto"
    assert posted["form"]["customer_update[name]"] == "auto"


async def test_all_four_tax_parameters_travel_together(posted):
    """Any one of them missing degrades the invoice: this is the single
    assertion that fails if someone trims the body later."""
    await _create()

    assert {
        "automatic_tax[enabled]": "true",
        "tax_id_collection[enabled]": "true",
        "customer_update[address]": "auto",
        "customer_update[name]": "auto",
    }.items() <= posted["form"].items()


async def test_customer_update_is_omitted_without_a_customer(posted):
    """Stripe rejects `customer_update` unless `customer` is present, so the
    body must not carry it in that case."""
    await _create(customer_id="")

    assert "customer_update[address]" not in posted["form"]
    assert "customer_update[name]" not in posted["form"]
    # The tax parameters themselves do not depend on a customer.
    assert posted["form"]["automatic_tax[enabled]"] == "true"


async def test_tax_does_not_disturb_the_rest_of_the_body(posted):
    """Regression guard: the line item, mode and urls are what get charged."""
    await _create(trial_days=9, allow_promotion_codes=True)

    form = posted["form"]
    assert posted["path"] == "/checkout/sessions"
    assert form["customer"] == "cus_1"
    assert form["mode"] == "subscription"
    assert form["line_items[0][price]"] == "price_pro_m"
    assert form["line_items[0][quantity]"] == "1"
    assert form["subscription_data[trial_period_days]"] == "9"
    assert form["allow_promotion_codes"] == "true"


async def test_every_value_is_a_string(posted):
    """Form-encoded API: a bare `True` would be sent as "True", which Stripe
    does not accept."""
    await _create()
    assert all(isinstance(v, str) for v in posted["form"].values())


# ---------------------------------------------------------------------------
# Stripe Tax not activated yet
# ---------------------------------------------------------------------------

TAX_KEYS = (
    "automatic_tax[enabled]",
    "tax_id_collection[enabled]",
    "customer_update[address]",
    "customer_update[name]",
)


def _stripe_error(status_code: int, code: str | None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.stripe.com/v1/checkout/sessions")
    body = {"error": {"type": "invalid_request_error", "message": "nope"}}
    if code:
        body["error"]["code"] = code
    response = httpx.Response(status_code, json=body, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.fixture
def failing_post(monkeypatch):
    """Raise a configured Stripe error and record every attempted request."""
    state = {
        "error": _stripe_error(400, "stripe_tax_inactive"),
        "bodies": [],
        "idempotency_keys": [],
    }

    async def _post(path, form, *, idempotency_key=None):
        state["bodies"].append(form)
        state["idempotency_keys"].append(idempotency_key)
        if state["error"] is not None:
            raise state["error"]
        return {"id": "cs_1", "url": "https://checkout.stripe.com/s/1"}

    monkeypatch.setattr(stripe_billing, "_post", _post)
    return state


async def test_inactive_stripe_tax_fails_closed_without_retry(failing_post):
    with pytest.raises(httpx.HTTPStatusError):
        await _create()

    assert len(failing_post["bodies"]) == 1
    assert all(key in failing_post["bodies"][0] for key in TAX_KEYS)


async def test_inactive_tax_never_switches_to_an_alternate_idempotency_key(failing_post):
    with pytest.raises(httpx.HTTPStatusError):
        await _create(idempotency_key="checkout:agency-1:pro")

    assert failing_post["idempotency_keys"] == ["checkout:agency-1:pro"]
    assert len(failing_post["bodies"]) == 1


async def test_a_non_tax_error_is_not_swallowed(failing_post):
    """A bad price id must surface, not be retried without tax and then fail
    again wearing a confusing shape."""
    failing_post["error"] = _stripe_error(400, "resource_missing")

    with pytest.raises(httpx.HTTPStatusError):
        await _create()

    assert len(failing_post["bodies"]) == 1


async def test_a_customer_address_problem_is_not_swallowed(failing_post):
    """`customer_tax_location_invalid` is about the buyer, and Checkout
    collects the address itself. Dropping the tax for it would hide a problem
    that fixes itself."""
    failing_post["error"] = _stripe_error(400, "customer_tax_location_invalid")

    with pytest.raises(httpx.HTTPStatusError):
        await _create()

    assert len(failing_post["bodies"]) == 1


async def test_an_error_without_a_code_is_not_swallowed(failing_post):
    """Matching on Stripe's prose would be a behaviour that stops working the
    day Stripe rewords a message, so no code means no fallback."""
    failing_post["error"] = _stripe_error(400, None)

    with pytest.raises(httpx.HTTPStatusError):
        await _create()

    assert len(failing_post["bodies"]) == 1


async def test_a_rejected_body_is_not_sent_three_times(failing_post):
    """The retry decorator used to replay every failure, so a permanent 400
    cost the customer three calls and seconds of backoff before the same
    error. 4xx is a verdict, not a hiccup."""
    failing_post["error"] = _stripe_error(400, "resource_missing")

    with pytest.raises(httpx.HTTPStatusError):
        await _create()

    assert len(failing_post["bodies"]) == 1
