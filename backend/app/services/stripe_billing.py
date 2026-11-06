"""Stripe integration: customer creation, checkout sessions, webhook parsing.

We don't use the official `stripe` python sdk to keep the dependency
footprint small — Stripe's REST API is straightforward over httpx.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)
_STRIPE_API = "https://api.stripe.com/v1"
START_NOW_METADATA_KEY = "selai_start_now"


def _api_key() -> str:
    return settings.stripe_secret_key.get_secret_value()


def _webhook_secret() -> str:
    return settings.stripe_webhook_secret.get_secret_value()


async def _post(
    path: str,
    form: dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    api_key = _api_key()
    if not api_key:
        log.warning("stripe.no_api_key", path=path)
        return {"id": "dev-stub", "url": "https://example.com/dev-stub"}
    headers = {"Authorization": f"Bearer {api_key}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post(f"{_STRIPE_API}{path}", data=form, headers=headers)
    if resp.status_code >= 400:
        log.error("stripe.error", status=resp.status_code, body=resp.text[:300])
        resp.raise_for_status()
    return resp.json()


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    api_key = _api_key()
    if not api_key:
        log.warning("stripe.no_api_key", path=path)
        return {"data": []}
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.get(f"{_STRIPE_API}{path}", params=params or {}, headers=headers)
    if resp.status_code >= 400:
        log.error("stripe.error", status=resp.status_code, body=resp.text[:300])
        resp.raise_for_status()
    return resp.json()


class StripeSubscriptionItemError(RuntimeError):
    """The remote subscription does not have exactly one item to swap.

    Everything we sell is one plan, one price, one item. A subscription with
    zero items (already ended?) or several (an add-on, a manual edit in the
    Stripe dashboard, or an earlier bug that appended instead of replacing)
    is a shape this code did not create and cannot safely guess at: picking
    the wrong item to rewrite would charge the customer for the wrong thing.
    So it refuses and the endpoint turns it into an error a human looks at.
    """

    def __init__(self, subscription_id: str, item_count: int):
        self.subscription_id = subscription_id
        self.item_count = item_count
        super().__init__(
            f"subscription {subscription_id} has {item_count} items, expected exactly 1"
        )


def _stripe_error_code(exc: httpx.HTTPStatusError) -> str | None:
    """The `error.code` Stripe put in the response body, or None.

    Codes are the only part of a Stripe error that is documented and stable
    (https://docs.stripe.com/error-codes), so nothing here ever matches on
    the human-readable message: Stripe rewords those without warning and a
    behaviour that hangs on prose is a behaviour that silently stops working.
    """
    response = exc.response
    if response is None:
        return None
    try:
        body = response.json()
    except Exception:  # not JSON, truncated, empty — nothing to match on
        return None
    code = ((body or {}).get("error") or {}).get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def _is_transient(exc: BaseException) -> bool:
    """Whether repeating the identical request could plausibly fix it.

    A 4xx is Stripe's verdict on the body we just sent; replaying it twice
    more only keeps the customer waiting at the "Contratar" button for the
    backoff (~3 s) before the same failure. 429 is the exception — rate
    limiting is precisely what exponential backoff is for.
    """
    if isinstance(exc, StripeSubscriptionItemError):
        # A wrong-shaped subscription will not reshape itself.
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        if response is None:
            return True
        code = response.status_code
        return (
            code == 429
            or _stripe_error_code(exc) == "idempotency_key_in_use"
            or not 400 <= code < 500
        )
    return True


_retry_stripe = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=8),
    retry=retry_if_exception(_is_transient),
    reraise=True,
)


@_retry_stripe
async def create_customer(*, email: str, agency_id: str, name: str) -> str:
    body = {
        "email": email,
        "name": name,
        "metadata[agency_id]": agency_id,
    }
    # A timeout after Stripe created the customer is indistinguishable from a
    # failed POST to us. Tenacity retries it, so the key is what turns that
    # replay (and two concurrent first checkouts) into the same customer.
    out = await _post(
        "/customers",
        body,
        idempotency_key=f"selai-customer:{agency_id}",
    )
    return out["id"]


@_retry_stripe
async def create_checkout_session(
    *,
    customer_id: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
    trial_days: int = 14,
    allow_promotion_codes: bool = False,
    start_now: bool = False,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """(183) `trial_days` <= 0 means *no* trial: charge on the spot.

    Stripe rejects `trial_period_days=0` (the minimum it accepts is 1), so
    the parameter is omitted entirely instead of sent as zero. The caller
    (`app/api/billing.py:remaining_trial_days`) decides how much trial is
    left; this function only knows how to say "none".
    """
    body = {
        "customer": customer_id,
        "mode": "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": success_url,
        "cancel_url": cancel_url,
    }
    if trial_days > 0 and not start_now:
        body["subscription_data[trial_period_days]"] = str(trial_days)
    if allow_promotion_codes:
        body["allow_promotion_codes"] = "true"
    if start_now:
        # Keep the customer's explicit choice on both objects. The Checkout
        # Session copy lets our completion webhook consume the remaining
        # local trial only after Stripe confirms payment; the subscription
        # copy makes the decision visible on later Stripe events/support.
        body[f"metadata[{START_NOW_METADATA_KEY}]"] = "true"
        body[f"subscription_data[metadata][{START_NOW_METADATA_KEY}]"] = "true"

    # All sellable Prices are tax-inclusive: the advertised amount remains the
    # final total while Stripe Tax calculates and itemizes the applicable tax.
    # Tax ID collection also gives Stripe the data needed to document B2B
    # reverse charge where applicable.
    tax = {
        "automatic_tax[enabled]": "true",
        "tax_id_collection[enabled]": "true",
    }
    # `customer_update` is only accepted alongside `customer` — sending it for
    # a session without one is a 400 from Stripe. Every call site passes a
    # customer (app/api/billing.py creates one before checking out), so this
    # branch is belt and braces rather than a real fork. Without it Stripe
    # cannot save the address it needs to compute the tax, and every later
    # invoice would be taxed off stale data.
    if customer_id:
        tax["customer_update[address]"] = "auto"
        tax["customer_update[name]"] = "auto"

    form = {**body, **tax}
    if not idempotency_key:
        return await _post("/checkout/sessions", form)
    return await _post("/checkout/sessions", form, idempotency_key=idempotency_key)


ProrationBehavior = Literal["create_prorations", "always_invoice", "none"]


@dataclass(frozen=True)
class PriceChange:
    """What happened when we asked Stripe to move a subscription's price.

    More than Stripe's raw response because the caller has to answer two
    questions it cannot answer on its own, and both are about money:

    - `changed` is False when the subscription was already on that price, so
      nothing was written and nothing will be billed;
    - `previous_interval` ("month"/"year") tells the caller whether the
      billing *period* is changing. Stripe: "switching a customer from a
      monthly subscription to a yearly subscription moves the billing date to
      the date of the switch", i.e. the year is billed then and there rather
      than prorated onto the next invoice.
    """

    subscription: dict[str, Any]
    previous_price_id: str | None
    previous_interval: str | None
    changed: bool

    @property
    def status(self) -> str | None:
        value = self.subscription.get("status")
        return value if isinstance(value, str) else None


@_retry_stripe
async def activate_trial_now(*, subscription_id: str, price_id: str) -> dict[str, Any]:
    """End an existing Stripe trial and bill the selected price immediately.

    This must update the current subscription, never create a Checkout
    Session: the customer already supplied a card when that trial began and
    a second session would create a second recurring charge.
    """
    if not _api_key():
        log.warning("stripe.no_api_key", path=f"/subscriptions/{subscription_id}")
        return {"id": subscription_id, "status": "active", "object": "subscription"}

    remote = await _get(f"/subscriptions/{subscription_id}")
    items = ((remote or {}).get("items") or {}).get("data") or []
    if len(items) != 1 or not items[0].get("id"):
        raise StripeSubscriptionItemError(subscription_id, len(items))

    return await _post(
        f"/subscriptions/{subscription_id}",
        {
            # Replace the one existing item while ending the trial. Omitting
            # its id appends a second price to the same subscription.
            "items[0][id]": str(items[0]["id"]),
            "items[0][price]": price_id,
            "trial_end": "now",
            # No paid period exists to prorate: the first period starts now at
            # the price the owner explicitly selected.
            "proration_behavior": "none",
            f"metadata[{START_NOW_METADATA_KEY}]": "true",
        },
        idempotency_key=f"selai-start-now:{subscription_id}",
    )


@_retry_stripe
async def update_subscription_price(
    *,
    subscription_id: str,
    price_id: str,
    proration_behavior: ProrationBehavior = "create_prorations",
) -> PriceChange:
    """Move an existing subscription onto another price, in place.

    THE point of this function: Stripe's subscription update *appends* a
    price unless you tell it which item to overwrite, so `items[0][id]` is
    mandatory. Without it the customer ends up paying Starter *plus* Pro on
    the same subscription — which is exactly the bug this replaces, where the
    panel sent everyone through checkout again and created a second
    subscription (99 + 199 EUR/month).

    Hence the read before the write: only Stripe knows the current
    subscription item id. The whole thing sits inside the retry, so a
    transient failure re-reads the item id instead of reusing a stale one.
    """
    if not _api_key():
        # Same contract as `_post`/`_get`: with no key configured (local dev,
        # CI) nothing is charged and nothing blows up. The stub reports the
        # subscription as active so the panel renders its success path.
        log.warning("stripe.no_api_key", path=f"/subscriptions/{subscription_id}")
        return PriceChange(
            subscription={"id": subscription_id, "status": "active", "object": "subscription"},
            previous_price_id=None,
            previous_interval=None,
            changed=True,
        )

    remote = await _get(f"/subscriptions/{subscription_id}")
    items = ((remote or {}).get("items") or {}).get("data") or []
    if len(items) != 1:
        raise StripeSubscriptionItemError(subscription_id, len(items))
    item = items[0]
    item_id = item.get("id")
    if not item_id:
        raise StripeSubscriptionItemError(subscription_id, 0)

    price = item.get("price") or item.get("plan") or {}
    current_price_id = price.get("id")
    current_interval = (price.get("recurring") or price).get("interval")

    if current_price_id and current_price_id == price_id:
        # Already on that price. Two causes, one right answer:
        #
        # - the customer double-clicked, or the panel retried a request whose
        #   response was lost. The write did happen the first time.
        # - somebody is asking for the plan they already have.
        #
        # Writing again buys nothing and is not free: it gives Stripe another
        # chance to emit prorations and another `customer.subscription.updated`
        # for a change that already landed. The cheapest correct action is
        # none, and "no write" is the only version of this that cannot cost
        # the customer money.
        log.info(
            "stripe.subscription_price_unchanged",
            subscription=subscription_id,
            price=price_id,
        )
        return PriceChange(
            subscription=remote or {},
            previous_price_id=current_price_id,
            previous_interval=current_interval,
            changed=False,
        )

    out = await _post(
        f"/subscriptions/{subscription_id}",
        {
            # `items[0][id]` = replace this item. Dropping it means "add one".
            "items[0][id]": str(item_id),
            "items[0][price]": price_id,
            "proration_behavior": proration_behavior,
        },
    )
    return PriceChange(
        subscription=out,
        previous_price_id=current_price_id,
        previous_interval=current_interval,
        changed=True,
    )


@_retry_stripe
async def create_billing_portal_session(*, customer_id: str, return_url: str) -> dict[str, Any]:
    """Create a session against the reviewed, pinned portal configuration.

    Development may omit the configuration and use Stripe's default portal.
    Production cannot reach this branch without a ``bpc_`` id because startup
    validation rejects it. Keeping the id in the request also prevents a later
    Dashboard default change from silently altering cancellation behaviour.
    """
    body = {"customer": customer_id, "return_url": return_url}
    configuration_id = settings.stripe_billing_portal_configuration_id.strip()
    if settings.app_env == "production" and (
        not configuration_id.startswith("bpc_") or len(configuration_id) < 8
    ):
        raise RuntimeError(
            "STRIPE_BILLING_PORTAL_CONFIGURATION_ID must be a saved bpc_ configuration"
        )
    if configuration_id:
        body["configuration"] = configuration_id
    return await _post(
        "/billing_portal/sessions",
        body,
    )


def verify_webhook_signature(payload: bytes, signature_header: str | None) -> bool:
    """Verify a Stripe-Signature header against the webhook secret.

    Stripe signs `<timestamp>.<raw payload>` with HMAC-SHA256. The header
    is `t=<timestamp>,v1=<sig1>,v1=<sig2>...`.
    """
    import hashlib
    import hmac

    secret = _webhook_secret()
    if not secret:
        return False
    if not signature_header:
        return False

    parts = dict(item.split("=", 1) for item in signature_header.split(",") if "=" in item)
    timestamp = parts.get("t")
    sig = parts.get("v1")
    if not (timestamp and sig):
        return False

    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
