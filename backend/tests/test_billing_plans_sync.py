"""Stripe plan sync keeps final prices tax-inclusive and migration-safe."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.core.settings import settings
from app.scripts import sync_stripe_plans as sync


@pytest.mark.parametrize(
    ("monthly", "cents"),
    [(99, 95_040), (199, 191_040), (399, 383_040)],
)
def test_annual_amount_is_twelve_months_minus_twenty_percent(monthly, cents):
    assert sync.annual_amount_cents(monthly) == cents


def test_lookup_keys_are_versioned_final_and_do_not_collide_with_legacy_keys():
    assert sync.monthly_lookup_key("pro") == "selai-pro-month-final-v1"
    assert sync.annual_lookup_key("pro") == "selai-pro-year-final-v1"
    assert sync.annual_lookup_key("pro") != "selai-pro-year"


def _plan(code="pro", price=199, **overrides):
    values = {
        "code": code,
        "name": code.title(),
        "description": "Plan de prueba",
        "monthly_price_eur": price,
        "stripe_product_id": "prod_1",
        "stripe_price_id": "price_m",
        "stripe_price_id_year": "price_y",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _product(product_id="prod_1", tax_code=sync.PRODUCT_TAX_CODE):
    return {
        "id": product_id,
        "active": True,
        "tax_code": tax_code,
        "metadata": {"plan_code": "pro"},
    }


def _price(
    price_id,
    *,
    amount,
    interval,
    tax_behavior=sync.PRICE_TAX_BEHAVIOR,
    product="prod_1",
    lookup_key=None,
):
    return {
        "id": price_id,
        "active": True,
        "product": product,
        "unit_amount": amount,
        "currency": "eur",
        "recurring": {"interval": interval},
        "tax_behavior": tax_behavior,
        "lookup_key": lookup_key,
    }


class _Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _Session:
    def __init__(self, plans):
        self.plans = plans

    async def execute(self, _statement):
        return _Result(self.plans)


@pytest.fixture
def stripe(monkeypatch):
    state = SimpleNamespace(
        gets=[],
        posts=[],
        products={},
        prices={},
        subscriptions={},
        sessions={},
        line_items={},
    )

    def _not_found(path):
        request = httpx.Request("GET", f"https://api.stripe.test{path}")
        response = httpx.Response(404, request=request)
        return httpx.HTTPStatusError("not found", request=request, response=response)

    async def _get(path, params=None):
        params = params or {}
        state.gets.append({"path": path, "params": params})
        if path == "/products/search":
            return {
                "data": [
                    product
                    for product in state.products.values()
                    if product.get("active") is not False
                    and (product.get("metadata") or {}).get("plan_code") == "pro"
                ]
            }
        if path.startswith("/products/"):
            product = state.products.get(path.removeprefix("/products/"))
            if product is None:
                raise _not_found(path)
            return product
        if path.startswith("/prices/"):
            price = state.prices.get(path.removeprefix("/prices/"))
            if price is None:
                raise _not_found(path)
            return price
        if path == "/prices" and "lookup_keys[]" in params:
            key = params["lookup_keys[]"]
            return {"data": [p for p in state.prices.values() if p.get("lookup_key") == key]}
        if path == "/prices":
            product_id = params.get("product")
            return {
                "data": [
                    price
                    for price in state.prices.values()
                    if price.get("product") == product_id and price.get("active") is not False
                ]
            }
        if path == "/checkout/sessions":
            return {
                "data": [
                    session
                    for session in state.sessions.values()
                    if session.get("status") == params.get("status", session.get("status"))
                ],
                "has_more": False,
            }
        if path.startswith("/checkout/sessions/") and path.endswith("/line_items"):
            session_id = path.removeprefix("/checkout/sessions/").removesuffix(
                "/line_items"
            )
            return {"data": state.line_items.get(session_id, []), "has_more": False}
        if path == "/subscriptions":
            price_id = params.get("price")
            return {
                "data": [
                    subscription
                    for subscription in state.subscriptions.values()
                    if any(
                        (item.get("price") or {}).get("id") == price_id
                        for item in ((subscription.get("items") or {}).get("data") or [])
                    )
                ],
                "has_more": False,
            }
        raise AssertionError(f"unexpected GET {path}")

    async def _post(path, form, *, idempotency_key=None):
        state.posts.append(
            {
                "path": path,
                "form": dict(form),
                "idempotency_key": idempotency_key,
            }
        )
        if path == "/products":
            product_id = f"prod_created_{len(state.products) + 1}"
            product = {
                "id": product_id,
                "active": True,
                "tax_code": form["tax_code"],
                "metadata": {"plan_code": form["metadata[plan_code]"]},
            }
            state.products[product_id] = product
            return product
        if path.startswith("/products/"):
            product = state.products[path.removeprefix("/products/")]
            product.update(
                {key: value for key, value in form.items() if not key.startswith("metadata[")}
            )
            if "metadata[plan_code]" in form:
                product.setdefault("metadata", {})["plan_code"] = form["metadata[plan_code]"]
            return product
        if path == "/prices":
            price_id = f"price_created_{len(state.prices) + 1}"
            price = _price(
                price_id,
                amount=int(form["unit_amount"]),
                interval=form["recurring[interval]"],
                tax_behavior=form["tax_behavior"],
                product=form["product"],
                lookup_key=form["lookup_key"],
            )
            state.prices[price_id] = price
            return price
        if path.startswith("/checkout/sessions/") and path.endswith("/expire"):
            session_id = path.removeprefix("/checkout/sessions/").removesuffix("/expire")
            state.sessions[session_id]["status"] = "expired"
            return state.sessions[session_id]
        if path.startswith("/subscriptions/"):
            subscription_id = path.removeprefix("/subscriptions/")
            subscription = state.subscriptions[subscription_id]
            item_id = form["items[0][id]"]
            item = next(
                item
                for item in subscription["items"]["data"]
                if item["id"] == item_id
            )
            item["price"] = {"id": form["items[0][price]"]}
            return subscription
        raise AssertionError(f"unexpected POST {path}")

    monkeypatch.setattr(sync, "_get", _get)
    monkeypatch.setattr(sync, "_post", _post)
    return state


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_test_1"))


def _use(monkeypatch, plans):
    @asynccontextmanager
    async def _scope():
        yield _Session(plans)

    monkeypatch.setattr(sync, "session_scope", _scope)


async def test_new_product_and_both_prices_have_the_fiscal_contract(monkeypatch, stripe, with_key):
    plan = _plan(stripe_product_id=None, stripe_price_id=None, stripe_price_id_year=None)
    _use(monkeypatch, [plan])

    await sync.main()

    product_create = next(call for call in stripe.posts if call["path"] == "/products")
    assert product_create["form"]["tax_code"] == "txcd_10103001"
    assert product_create["idempotency_key"] == "selai-product:pro:final-v1"
    price_creates = [call for call in stripe.posts if call["path"] == "/prices"]
    assert len(price_creates) == 2
    assert {call["form"]["tax_behavior"] for call in price_creates} == {"inclusive"}
    assert {call["form"]["lookup_key"] for call in price_creates} == {
        "selai-pro-month-final-v1",
        "selai-pro-year-final-v1",
    }
    assert {call["form"]["unit_amount"] for call in price_creates} == {
        "19900",
        "191040",
    }
    assert {call["idempotency_key"] for call in price_creates} == {
        "selai-price:selai-pro-month-final-v1",
        "selai-price:selai-pro-year-final-v1",
    }


async def test_existing_product_gets_the_saas_b2b_tax_code(monkeypatch, stripe, with_key):
    stripe.products["prod_1"] = _product(tax_code="txcd_99999999")
    stripe.prices["price_m"] = _price("price_m", amount=19_900, interval="month")
    stripe.prices["price_y"] = _price("price_y", amount=191_040, interval="year")
    _use(monkeypatch, [_plan()])

    await sync.main()

    assert stripe.products["prod_1"]["tax_code"] == "txcd_10103001"
    assert [call for call in stripe.posts if call["path"] == "/products/prod_1"] == [
        {
            "path": "/products/prod_1",
            "form": {"tax_code": "txcd_10103001"},
            "idempotency_key": "selai-product-normalize:prod_1:final-v1",
        }
    ]


async def test_inactive_product_id_is_replaced_with_an_active_product(
    monkeypatch, stripe, with_key
):
    inactive = _product("prod_inactive")
    inactive["active"] = False
    stripe.products["prod_inactive"] = inactive
    plan = _plan(
        stripe_product_id="prod_inactive",
        stripe_price_id=None,
        stripe_price_id_year=None,
    )
    _use(monkeypatch, [plan])

    await sync.main()

    assert plan.stripe_product_id != "prod_inactive"
    assert stripe.products[plan.stripe_product_id]["active"] is True
    created_prices = [call["form"] for call in stripe.posts if call["path"] == "/prices"]
    assert len(created_prices) == 2
    assert all(form["product"] == plan.stripe_product_id for form in created_prices)


async def test_product_id_for_another_plan_is_never_reused(monkeypatch, stripe, with_key):
    stripe.products["prod_starter"] = _product("prod_starter") | {
        "metadata": {"plan_code": "starter"}
    }
    stripe.products["prod_pro"] = _product("prod_pro")
    plan = _plan(
        stripe_product_id="prod_starter",
        stripe_price_id=None,
        stripe_price_id_year=None,
    )
    _use(monkeypatch, [plan])

    await sync.main()

    assert plan.stripe_product_id == "prod_pro"
    created_prices = [call["form"] for call in stripe.posts if call["path"] == "/prices"]
    assert len(created_prices) == 2
    assert all(form["product"] == "prod_pro" for form in created_prices)
    assert not any(call["path"] == "/products/prod_starter" for call in stripe.posts)


async def test_legacy_product_without_plan_metadata_is_self_healed(monkeypatch, stripe, with_key):
    product = _product()
    product["metadata"] = {}
    stripe.products["prod_1"] = product
    stripe.prices["price_m"] = _price("price_m", amount=19_900, interval="month")
    stripe.prices["price_y"] = _price("price_y", amount=191_040, interval="year")
    _use(monkeypatch, [_plan()])

    await sync.main()

    assert stripe.products["prod_1"]["metadata"]["plan_code"] == "pro"
    assert any(
        call["path"] == "/products/prod_1" and call["form"].get("metadata[plan_code]") == "pro"
        for call in stripe.posts
    )


async def test_deleted_stripe_ids_are_recovered_without_reusing_stale_values(
    monkeypatch, stripe, with_key
):
    plan = _plan(
        stripe_product_id="prod_deleted",
        stripe_price_id="price_month_deleted",
        stripe_price_id_year="price_year_deleted",
    )
    _use(monkeypatch, [plan])

    await sync.main()

    assert plan.stripe_product_id not in {None, "prod_deleted"}
    assert plan.stripe_price_id not in {None, "price_month_deleted"}
    assert plan.stripe_price_id_year not in {None, "price_year_deleted"}
    assert [call["path"] for call in stripe.posts].count("/products") == 1
    assert [call["path"] for call in stripe.posts].count("/prices") == 2


async def test_exclusive_legacy_ids_are_replaced_without_touching_old_prices(
    monkeypatch, stripe, with_key
):
    stripe.products["prod_1"] = _product()
    stripe.prices["price_m"] = _price(
        "price_m", amount=19_900, interval="month", tax_behavior="exclusive"
    )
    stripe.prices["price_y"] = _price(
        "price_y", amount=191_040, interval="year", tax_behavior="unspecified"
    )
    plan = _plan()
    _use(monkeypatch, [plan])

    await sync.main()

    assert plan.stripe_price_id not in {"price_m", "price_y"}
    assert plan.stripe_price_id_year not in {"price_m", "price_y"}
    assert stripe.prices["price_m"]["tax_behavior"] == "exclusive"
    assert stripe.prices["price_y"]["tax_behavior"] == "unspecified"
    assert not any(call["path"] in {"/prices/price_m", "/prices/price_y"} for call in stripe.posts)
    created = [call for call in stripe.posts if call["path"] == "/prices"]
    assert len(created) == 2
    assert all(call["form"]["tax_behavior"] == "inclusive" for call in created)


async def test_versioned_inclusive_prices_are_reused_to_migrate_plan_ids(
    monkeypatch, stripe, with_key
):
    stripe.products["prod_1"] = _product()
    stripe.prices.update(
        {
            "old_m": _price("old_m", amount=19_900, interval="month", tax_behavior="exclusive"),
            "old_y": _price("old_y", amount=191_040, interval="year", tax_behavior="exclusive"),
            "final_m": _price(
                "final_m",
                amount=19_900,
                interval="month",
                lookup_key=sync.monthly_lookup_key("pro"),
            ),
            "final_y": _price(
                "final_y",
                amount=191_040,
                interval="year",
                lookup_key=sync.annual_lookup_key("pro"),
            ),
        }
    )
    plan = _plan(stripe_price_id="old_m", stripe_price_id_year="old_y")
    _use(monkeypatch, [plan])

    await sync.main()

    assert plan.stripe_price_id == "final_m"
    assert plan.stripe_price_id_year == "final_y"
    assert stripe.posts == []


async def test_legacy_dependents_are_migrated_before_plan_pointer_moves(
    monkeypatch, stripe, with_key
):
    stripe.products["prod_1"] = _product()
    stripe.prices.update(
        {
            "old_m": _price(
                "old_m", amount=19_900, interval="month", tax_behavior="exclusive"
            ),
            "price_y": _price("price_y", amount=191_040, interval="year"),
            "final_m": _price(
                "final_m",
                amount=19_900,
                interval="month",
                lookup_key=sync.monthly_lookup_key("pro"),
            ),
        }
    )
    stripe.sessions["cs_open"] = {"id": "cs_open", "status": "open"}
    stripe.sessions["cs_other"] = {"id": "cs_other", "status": "open"}
    stripe.line_items["cs_open"] = [{"id": "li_1", "price": {"id": "old_m"}}]
    stripe.line_items["cs_other"] = [{"id": "li_2", "price": {"id": "other"}}]
    stripe.subscriptions["sub_live"] = {
        "id": "sub_live",
        "status": "active",
        "items": {"data": [{"id": "si_live", "price": {"id": "old_m"}}]},
    }
    stripe.subscriptions["sub_canceled"] = {
        "id": "sub_canceled",
        "status": "canceled",
        "items": {"data": [{"id": "si_canceled", "price": {"id": "old_m"}}]},
    }
    plan = _plan(stripe_price_id="old_m")
    _use(monkeypatch, [plan])

    await sync.main()

    assert plan.stripe_price_id == "final_m"
    assert stripe.sessions["cs_open"]["status"] == "expired"
    assert stripe.sessions["cs_other"]["status"] == "open"
    assert stripe.subscriptions["sub_live"]["items"]["data"][0]["price"]["id"] == "final_m"
    assert (
        stripe.subscriptions["sub_canceled"]["items"]["data"][0]["price"]["id"]
        == "old_m"
    )
    migrate = next(
        call for call in stripe.posts if call["path"] == "/subscriptions/sub_live"
    )
    assert migrate["form"] == {
        "items[0][id]": "si_live",
        "items[0][price]": "final_m",
        "proration_behavior": "none",
    }
    assert migrate["idempotency_key"].startswith(
        "selai-price-migration:sub_live:final_m:"
    )


async def test_ambiguous_subscription_blocks_legacy_pointer_migration(
    monkeypatch, stripe, with_key
):
    stripe.products["prod_1"] = _product()
    stripe.prices.update(
        {
            "old_m": _price(
                "old_m", amount=19_900, interval="month", tax_behavior="exclusive"
            ),
            "price_y": _price("price_y", amount=191_040, interval="year"),
            "final_m": _price(
                "final_m",
                amount=19_900,
                interval="month",
                lookup_key=sync.monthly_lookup_key("pro"),
            ),
        }
    )
    stripe.subscriptions["sub_ambiguous"] = {
        "id": "sub_ambiguous",
        "status": "active",
        "items": {
            "data": [
                {"id": "si_1", "price": {"id": "old_m"}},
                {"id": "si_2", "price": {"id": "old_m"}},
            ]
        },
    }
    plan = _plan(stripe_price_id="old_m")
    _use(monkeypatch, [plan])

    with pytest.raises(RuntimeError, match="ambiguous migration"):
        await sync.main()

    assert plan.stripe_price_id == "old_m"
    assert not any(call["path"].startswith("/subscriptions/") for call in stripe.posts)


async def test_sync_is_idempotent_when_plan_ids_are_already_compatible(
    monkeypatch, stripe, with_key
):
    stripe.products["prod_1"] = _product()
    stripe.prices["price_m"] = _price("price_m", amount=19_900, interval="month")
    stripe.prices["price_y"] = _price("price_y", amount=191_040, interval="year")
    plan = _plan()
    _use(monkeypatch, [plan])

    await sync.main()
    await sync.main()

    assert plan.stripe_price_id == "price_m"
    assert plan.stripe_price_id_year == "price_y"
    assert stripe.posts == []


async def test_matching_exclusive_price_is_never_reused(monkeypatch, stripe, with_key):
    stripe.products["prod_1"] = _product()
    stripe.prices["legacy_m"] = _price(
        "legacy_m", amount=19_900, interval="month", tax_behavior="exclusive"
    )
    stripe.prices["price_y"] = _price("price_y", amount=191_040, interval="year")
    plan = _plan(stripe_price_id=None)
    _use(monkeypatch, [plan])

    await sync.main()

    assert plan.stripe_price_id != "legacy_m"
    created = [call for call in stripe.posts if call["path"] == "/prices"]
    assert len(created) == 1
    assert created[0]["form"]["tax_behavior"] == "inclusive"


async def test_without_a_stripe_key_it_is_a_noop(monkeypatch, stripe):
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr(""))
    plan = _plan(stripe_price_id_year=None)
    _use(monkeypatch, [plan])

    await sync.main()

    assert stripe.posts == []
    assert stripe.gets == []
    assert plan.stripe_price_id_year is None
