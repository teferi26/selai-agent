"""Create and migrate the Stripe Products and Prices used by active plans.

The advertised amount is the final amount charged to the customer. Stripe
Prices are therefore tax-inclusive and Products carry the B2B SaaS tax code.
Existing incompatible Prices are never changed or archived: they may already
belong to subscriptions. Before the local Plan pointer moves, every open
Checkout using the old Price is expired and every live subscription item is
migrated in place without proration. This prevents old renewals from silently
keeping a different tax contract.

Runs on container boot (after ``seed_plans``) and is idempotent. Without a
Stripe secret it logs and exits successfully.

    python -m app.scripts.sync_stripe_plans
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from sqlalchemy import select

from app.core.settings import settings
from app.db.models import Plan
from app.db.session import session_scope
from app.services.stripe_billing import _get, _post

ANNUAL_DISCOUNT = 0.2
PRODUCT_TAX_CODE = "txcd_10103001"
PRICE_TAX_BEHAVIOR = "inclusive"
PRICE_LOOKUP_VERSION = "final-v1"


def annual_amount_cents(monthly_price_eur: int) -> int:
    """Return twelve monthly payments minus the annual discount, in cents."""
    return round(monthly_price_eur * 12 * (1 - ANNUAL_DISCOUNT) * 100)


def price_lookup_key(plan_code: str, interval: str) -> str:
    if interval not in {"month", "year"}:
        raise ValueError(f"unsupported billing interval: {interval}")
    return f"selai-{plan_code}-{interval}-{PRICE_LOOKUP_VERSION}"


def monthly_lookup_key(plan_code: str) -> str:
    return price_lookup_key(plan_code, "month")


def annual_lookup_key(plan_code: str) -> str:
    return price_lookup_key(plan_code, "year")


def _object_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        object_id = value.get("id")
        return object_id if isinstance(object_id, str) else None
    return None


def _price_is_compatible(
    price: dict[str, Any],
    *,
    product_id: str,
    unit_amount: int,
    interval: str,
) -> bool:
    return (
        price.get("active") is not False
        and _object_id(price.get("product")) == product_id
        and price.get("unit_amount") == unit_amount
        and price.get("currency") == "eur"
        and (price.get("recurring") or {}).get("interval") == interval
        and price.get("tax_behavior") == PRICE_TAX_BEHAVIOR
    )


async def _get_or_none(path: str) -> dict[str, Any] | None:
    try:
        return await _get(path)
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise


async def _find_product(plan_code: str) -> dict[str, Any] | None:
    out = await _get(
        "/products/search",
        {"query": f"metadata['plan_code']:'{plan_code}' AND active:'true'"},
    )
    data = out.get("data") or []
    return data[0] if data else None


async def _ensure_product(plan: Plan) -> str:
    product = None
    if plan.stripe_product_id:
        product = await _get_or_none(f"/products/{plan.stripe_product_id}")
        if product is not None:
            stored_plan_code = (product.get("metadata") or {}).get("plan_code")
            if product.get("active") is False or (
                stored_plan_code and stored_plan_code != plan.code
            ):
                product = None

    if product is None:
        product = await _find_product(plan.code)
        if product is not None:
            plan.stripe_product_id = product["id"]
            print(f"[{plan.code}] product reutilizado -> {product['id']}")

    if product is None:
        product = await _post(
            "/products",
            {
                "name": f"selai {plan.name}",
                "description": plan.description,
                "tax_code": PRODUCT_TAX_CODE,
                "metadata[plan_code]": plan.code,
            },
            idempotency_key=f"selai-product:{plan.code}:{PRICE_LOOKUP_VERSION}",
        )
        plan.stripe_product_id = product["id"]
        print(f"[{plan.code}] product creado -> {product['id']}")

    product_id = product["id"]
    product_update: dict[str, str] = {}
    if _object_id(product.get("tax_code")) != PRODUCT_TAX_CODE:
        product_update["tax_code"] = PRODUCT_TAX_CODE
    if (product.get("metadata") or {}).get("plan_code") != plan.code:
        product_update["metadata[plan_code]"] = plan.code
    if product_update:
        await _post(
            f"/products/{product_id}",
            product_update,
            idempotency_key=(
                f"selai-product-normalize:{product_id}:{PRICE_LOOKUP_VERSION}"
            ),
        )
        print(f"[{plan.code}] product normalizado -> {product_id}")
    return product_id


async def _find_price(
    product_id: str,
    unit_amount: int,
    interval: str = "month",
) -> dict[str, Any] | None:
    out = await _get("/prices", {"product": product_id, "active": "true", "limit": "100"})
    return next(
        (
            price
            for price in out.get("data") or []
            if _price_is_compatible(
                price,
                product_id=product_id,
                unit_amount=unit_amount,
                interval=interval,
            )
        ),
        None,
    )


async def _find_price_by_lookup_key(lookup_key: str) -> dict[str, Any] | None:
    """Return the Price using our immutable, versioned lookup key."""
    out = await _get("/prices", {"lookup_keys[]": lookup_key, "limit": "1"})
    data = out.get("data") or []
    return data[0] if data else None


async def _ensure_price(
    *,
    plan: Plan,
    current_price_id: str | None,
    product_id: str,
    unit_amount: int,
    interval: str,
) -> str:
    if current_price_id:
        current = await _get_or_none(f"/prices/{current_price_id}")
        if current is not None and _price_is_compatible(
            current,
            product_id=product_id,
            unit_amount=unit_amount,
            interval=interval,
        ):
            return current_price_id

    lookup_key = price_lookup_key(plan.code, interval)
    by_lookup = await _find_price_by_lookup_key(lookup_key)
    if by_lookup is not None and _price_is_compatible(
        by_lookup,
        product_id=product_id,
        unit_amount=unit_amount,
        interval=interval,
    ):
        return by_lookup["id"]

    existing = await _find_price(product_id, unit_amount, interval)
    if existing is not None:
        return existing["id"]

    if by_lookup is not None:
        raise RuntimeError(
            f"Stripe lookup key {lookup_key!r} belongs to an incompatible Price; "
            "refusing to guess or overwrite it"
        )

    created = await _post(
        "/prices",
        {
            "product": product_id,
            "unit_amount": str(unit_amount),
            "currency": "eur",
            "recurring[interval]": interval,
            "tax_behavior": PRICE_TAX_BEHAVIOR,
            "lookup_key": lookup_key,
            "metadata[plan_code]": plan.code,
            "metadata[billing_interval]": interval,
            "metadata[price_contract]": PRICE_LOOKUP_VERSION,
        },
        idempotency_key=f"selai-price:{lookup_key}",
    )
    return created["id"]


async def _list_all(path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    """Return every Stripe list item, refusing malformed pagination.

    Migration code must not silently stop at Stripe's first 100 objects. A
    `has_more` page without a usable last id is an unsafe API shape, so boot is
    blocked instead of leaving an unknown tail on the legacy Price.
    """
    items: list[dict[str, Any]] = []
    starting_after: str | None = None
    while True:
        page_params = {**params, "limit": "100"}
        if starting_after:
            page_params["starting_after"] = starting_after
        page = await _get(path, page_params)
        data = page.get("data") or []
        if not isinstance(data, list):
            raise RuntimeError(f"Stripe returned malformed list data for {path}")
        typed = [item for item in data if isinstance(item, dict)]
        if len(typed) != len(data):
            raise RuntimeError(f"Stripe returned a malformed item for {path}")
        items.extend(typed)
        if not page.get("has_more"):
            return items
        last_id = _object_id(typed[-1]) if typed else None
        if not last_id or last_id == starting_after:
            raise RuntimeError(f"Stripe pagination did not advance for {path}")
        starting_after = last_id


async def _expire_open_checkout_sessions(old_price_id: str) -> int:
    """Expire every still-open Checkout Session using a legacy Price."""
    expired = 0
    sessions = await _list_all("/checkout/sessions", {"status": "open"})
    for session in sessions:
        session_id = _object_id(session)
        if not session_id:
            raise RuntimeError("Stripe returned an open Checkout Session without id")
        line_items = await _list_all(
            f"/checkout/sessions/{session_id}/line_items",
            {},
        )
        if not any(_object_id(item.get("price")) == old_price_id for item in line_items):
            continue
        await _post(
            f"/checkout/sessions/{session_id}/expire",
            {},
            idempotency_key=(
                f"selai-expire-checkout:{session_id}:{old_price_id}:"
                f"{PRICE_LOOKUP_VERSION}"
            ),
        )
        expired += 1
    return expired


async def _migrate_subscriptions(old_price_id: str, new_price_id: str) -> int:
    """Swap a legacy Price on every renewable subscription, without proration."""
    migrated = 0
    subscriptions = await _list_all(
        "/subscriptions",
        {"price": old_price_id, "status": "all"},
    )
    for subscription in subscriptions:
        subscription_id = _object_id(subscription)
        if not subscription_id:
            raise RuntimeError("Stripe returned a subscription without id")
        if subscription.get("status") in {"canceled", "incomplete_expired"}:
            continue
        items = ((subscription.get("items") or {}).get("data") or [])
        matching = [
            item
            for item in items
            if isinstance(item, dict)
            and _object_id(item.get("price") or item.get("plan")) == old_price_id
        ]
        if len(matching) != 1 or not _object_id(matching[0]):
            raise RuntimeError(
                f"subscription {subscription_id} has {len(matching)} items on "
                f"legacy price {old_price_id}; refusing an ambiguous migration"
            )
        item_id = _object_id(matching[0])
        await _post(
            f"/subscriptions/{subscription_id}",
            {
                "items[0][id]": item_id,
                "items[0][price]": new_price_id,
                "proration_behavior": "none",
            },
            idempotency_key=(
                f"selai-price-migration:{subscription_id}:{new_price_id}:"
                f"{PRICE_LOOKUP_VERSION}"
            ),
        )
        migrated += 1
    return migrated


async def _migrate_price_dependents(old_price_id: str | None, new_price_id: str) -> None:
    """Close the legacy-Price race before the database starts selling the new one."""
    if not old_price_id or old_price_id == new_price_id:
        return

    # The sync runs before the web process accepts traffic. Expiring Checkouts
    # first prevents a legacy session from creating a new legacy subscription
    # after the subscription scan. A second expiry pass catches a session that
    # was opened by an older replica during the first pass.
    expired = await _expire_open_checkout_sessions(old_price_id)
    migrated = await _migrate_subscriptions(old_price_id, new_price_id)
    expired += await _expire_open_checkout_sessions(old_price_id)
    if expired or migrated:
        print(
            f"[{old_price_id}] dependencias migradas -> {new_price_id}: "
            f"{migrated} suscripciones, {expired} checkouts expirados"
        )


async def main() -> None:
    if not settings.stripe_secret_key.get_secret_value():
        print("sync_stripe_plans: sin STRIPE_SECRET_KEY, omitiendo (no-op).")
        return

    async with session_scope() as db:
        plans = (await db.execute(select(Plan).where(Plan.is_active.is_(True)))).scalars().all()
        if not plans:
            print("sync_stripe_plans: no hay planes en la DB, omitiendo.")
            return

        for plan in plans:
            product_id = await _ensure_product(plan)

            monthly_id = await _ensure_price(
                plan=plan,
                current_price_id=plan.stripe_price_id,
                product_id=product_id,
                unit_amount=plan.monthly_price_eur * 100,
                interval="month",
            )
            if monthly_id != plan.stripe_price_id:
                await _migrate_price_dependents(plan.stripe_price_id, monthly_id)
                plan.stripe_price_id = monthly_id
                print(f"[{plan.code}] price mensual migrado -> {monthly_id}")

            yearly_id = await _ensure_price(
                plan=plan,
                current_price_id=plan.stripe_price_id_year,
                product_id=product_id,
                unit_amount=annual_amount_cents(plan.monthly_price_eur),
                interval="year",
            )
            if yearly_id != plan.stripe_price_id_year:
                await _migrate_price_dependents(plan.stripe_price_id_year, yearly_id)
                plan.stripe_price_id_year = yearly_id
                print(f"[{plan.code}] price anual migrado -> {yearly_id}")

    print("sync_stripe_plans: done.")


if __name__ == "__main__":
    asyncio.run(main())
