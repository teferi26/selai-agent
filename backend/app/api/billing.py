"""Billing endpoints — list plans, start checkout, receive Stripe webhooks.

Two invariants worth stating out loud, because both were holes before:

1. **Limits only ever go up after Stripe says the money moved.** The
   checkout endpoint never touches `plan_id`; only the webhook does, and it
   derives the plan from the price id Stripe reports.
2. **The webhook is idempotent and order-safe.** Every delivery is recorded
   in `processed_stripe_events`; repeats are no-ops and events older than
   the newest one already applied for that customer are skipped, so a late
   retry cannot drag an ACTIVE subscription back to TRIALING. `event.created`
   only has one-second resolution, so `_apply_subscription_status` refuses
   the two backwards transitions Stripe never really makes.
3. **One incident, one dunning sequence.** Stripe emits an
   `invoice.payment_failed` per retry attempt, each with its own event id;
   `send_dunning_email` is the single throttle that keeps the owner from
   being mailed the same warning over and over.
4. **Emails are sent *after* the event is recorded, never inside the
   transaction.** A handler that wants to mail somebody returns a callable;
   the endpoint runs it once `processed_stripe_events` has been committed.
   Past that commit guard 1 drops every retry, so the message cannot go out
   twice — whereas sending mid-transaction meant a slow delivery (two
   15-second HTTP calls) could time Stripe out, earn a concurrent retry and
   mail the customer twice before either delivery recorded the event.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    AuthContext,
    OwnerOnlyDep,
    ensure_agency_access,
)
from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import Agency, Plan, Subscription, SubscriptionStatus, User, UserRole
from app.db.models.billing import ProcessedStripeEvent
from app.db.session import get_db
from app.services.emails import (
    BILLING_URL,
    build_payment_failed_email_html,
    build_payment_welcome_email_html,
    send_email,
)
from app.services.stripe_billing import (
    START_NOW_METADATA_KEY,
    StripeSubscriptionItemError,
    activate_trial_now,
    create_billing_portal_session,
    create_checkout_session,
    create_customer,
    update_subscription_price,
    verify_webhook_signature,
)
from app.services.stripe_billing import (
    _get as stripe_get,
)

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])
log = get_logger(__name__)

# Same length of trial the onboarding hands out (services/onboarding.py) and
# the landing advertises.
TRIAL_DAYS = 14


def remaining_trial_days(sub: Subscription | None) -> int:
    """(183) Days of free trial this agency still has coming.

    Checkout used to hand Stripe `trial_period_days=14` unconditionally, so
    cancelling and buying again bought another fortnight for free, as many
    times as you liked. The deadline the subscription already carries is the
    only source of truth:

    - no subscription row at all -> genuinely new, full trial;
    - a row without `trial_ends_at` -> nothing proves trial time is left
      (the account has been through Stripe already), so no trial;
    - a deadline in the past -> trial consumed, no trial.

    Rounded up, and capped at `TRIAL_DAYS` so a corrupt future date cannot
    hand out months. Stripe counts the trial from the checkout, so rounding
    up gives away less than a day and keeps the customer whole.
    """
    if sub is None:
        return TRIAL_DAYS
    ends_at = getattr(sub, "trial_ends_at", None)
    if ends_at is None:
        return 0
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=UTC)
    left = (ends_at - datetime.now(UTC)).total_seconds()
    if left <= 0:
        return 0
    return min(math.ceil(left / 86400), TRIAL_DAYS)


def checkout_idempotency_key(agency_id: uuid.UUID, sub: Subscription | None) -> str:
    """One Stripe Checkout Session per agency subscription generation.

    The key deliberately does not depend on plan, interval, URLs or
    `start_now`: two concurrent requests with different bodies must conflict
    at Stripe instead of creating two payable subscriptions. Once a real
    subscription is cancelled, its Stripe id becomes the next generation, so
    returning customers do not collide with their original checkout.
    """
    generation = getattr(sub, "stripe_subscription_id", None) or "initial"
    return f"selai-checkout:{agency_id}:{generation}"


class PlanOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str
    monthly_price_eur: int
    features: dict
    max_agents: int
    max_properties: int
    max_voice_minutes: int
    # True once the plan has a Stripe price — the UI shows/hides checkout.
    billable: bool = False
    # True once the annual Stripe price exists too — the UI only offers the
    # annual toggle when we can actually charge it.
    billable_yearly: bool = False


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(db: AsyncSession = Depends(get_db)) -> list[PlanOut]:
    rows = (
        (
            await db.execute(
                select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.monthly_price_eur)
            )
        )
        .scalars()
        .all()
    )
    return [
        PlanOut(
            id=p.id,
            code=p.code,
            name=p.name,
            description=p.description,
            monthly_price_eur=p.monthly_price_eur,
            features=p.features,
            max_agents=p.max_agents,
            max_properties=p.max_properties,
            max_voice_minutes=p.max_voice_minutes,
            billable=bool(p.stripe_price_id),
            billable_yearly=bool(p.stripe_price_id_year),
        )
        for p in rows
    ]


class CheckoutBody(BaseModel):
    agency_id: uuid.UUID
    plan_code: str
    success_url: str
    cancel_url: str
    # "year" bills 12 months up front with -20%; needs the annual price to
    # exist in Stripe (scripts/sync_stripe_plans.py creates it).
    interval: Literal["month", "year"] = "month"
    # Explicit opt-in: forfeit the remaining free trial and charge now.
    start_now: bool = False


async def _resolve_price(db: AsyncSession, plan_code: str, interval: str) -> tuple[Plan, str]:
    """The plan the caller asked for and the Stripe price to charge for it.

    Shared by `/checkout` and `/change-plan` on purpose: both move money, and
    two copies of "which price id is this?" is one copy too many — a drift
    between them would bill the wrong interval.
    """
    plan = (await db.execute(select(Plan).where(Plan.code == plan_code))).scalar_one_or_none()
    if plan is None or not plan.stripe_price_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "plan_not_billable")

    if interval == "year":
        price_id = plan.stripe_price_id_year
        if not price_id:
            # Honest failure: the annual price has not been synced to Stripe
            # yet, so we cannot charge it. The panel turns this into a message.
            raise HTTPException(status.HTTP_409_CONFLICT, "annual_price_not_synced")
        return plan, price_id
    return plan, plan.stripe_price_id


@router.post("/checkout")
async def start_checkout(
    body: CheckoutBody,
    # Owner/admin only: contracting a plan is a recurring charge of up to
    # 3.830 EUR/year on the agency's card. A commercial agent must get a
    # typed 403 (`role_required`) so the panel can hide the button, never a
    # live Stripe checkout.
    auth: AuthContext = OwnerOnlyDep,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    ensure_agency_access(auth, body.agency_id)
    agency = await db.get(Agency, body.agency_id)
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agency_not_found")

    plan, price_id = await _resolve_price(db, body.plan_code, body.interval)

    sub = (
        await db.execute(select(Subscription).where(Subscription.agency_id == agency.id))
    ).scalar_one_or_none()

    # A trial created by Stripe already owns the customer's card and the one
    # subscription we must keep. "Pay today" ends that trial in place; opening
    # another Checkout Session here would create a second recurring charge.
    if (
        body.start_now
        and sub is not None
        and sub.status == SubscriptionStatus.TRIALING
        and sub.stripe_subscription_id
    ):
        try:
            await activate_trial_now(
                subscription_id=sub.stripe_subscription_id,
                price_id=price_id,
            )
        except StripeSubscriptionItemError as exc:
            log.warning(
                "billing.start_now.unexpected_items",
                subscription=str(sub.id),
                stripe_subscription=sub.stripe_subscription_id,
                items=exc.item_count,
            )
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "subscription_items_unexpected",
            ) from exc

        log.info(
            "billing.start_now.requested",
            subscription=str(sub.id),
            plan=plan.code,
            interval=body.interval,
        )
        # PlanCards already treats `checkout_url` as the continuation URL.
        # There is no new Checkout to visit, so continue to its own success
        # URL while the subscription.updated webhook applies local state.
        return {
            "checkout_url": body.success_url,
            "session_id": sub.stripe_subscription_id,
        }

    # A live Stripe subscription must never reach a new checkout: Stripe would
    # happily create a SECOND subscription and the agency would pay twice.
    # The panel hides the button, but the panel is not a boundary — this is.
    #
    # `stripe_subscription_id` empty is the legitimate hole in the guard: a
    # TRIALING row created by an earlier abandoned checkout has a customer and
    # no subscription yet, and retrying checkout is exactly what that agency
    # needs. CANCELED goes through too: the old subscription is dead and a new
    # checkout is the correct way back in.
    if sub is not None and sub.stripe_subscription_id:
        if sub.status in (
            SubscriptionStatus.TRIALING,
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.PAST_DUE,
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "already_subscribed",
                    "message": (
                        "Esta agencia ya tiene una suscripción activa. Para "
                        "cambiar de plan usa el cambio de plan; para el resto, "
                        "el portal de facturación."
                    ),
                },
            )
        if sub.status in (SubscriptionStatus.UNPAID, SubscriptionStatus.PAUSED):
            # There is a live-but-broken subscription behind this state. The
            # way out is fixing the payment method in the portal, not a
            # second subscription alongside the debt.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "portal_required",
                    "message": (
                        "Hay una suscripción con un pago pendiente. "
                        "Resuélvelo desde el portal de facturación."
                    ),
                },
            )

    customer_id = sub.stripe_customer_id if sub else None
    if not customer_id:
        customer_id = await create_customer(
            email=agency.contact_email or f"{agency.slug}@invalid.local",
            agency_id=str(agency.id),
            name=agency.name,
        )

    session = await create_checkout_session(
        customer_id=customer_id,
        price_id=price_id,
        success_url=body.success_url,
        cancel_url=body.cancel_url,
        # Only what is left of *this* agency's trial, never another 14 days.
        trial_days=0 if body.start_now else remaining_trial_days(sub),
        # Discount codes for fairs, partners and cancellation rescue: without
        # this Stripe hides the coupon field and the codes are unusable.
        allow_promotion_codes=True,
        start_now=body.start_now,
        idempotency_key=checkout_idempotency_key(agency.id, sub),
    )

    # NB: `plan_id` is deliberately NOT set from `body.plan_code` here.
    # Opening (and abandoning) a checkout must not raise anyone's limits;
    # only the Stripe webhook applies the plan. A brand-new agency still
    # needs a row to hang `stripe_customer_id` off, so it starts on the
    # cheapest active plan.
    if sub is None:
        starter = (
            await db.execute(
                select(Plan)
                .where(Plan.is_active.is_(True))
                .order_by(Plan.monthly_price_eur)
                .limit(1)
            )
        ).scalars().first() or plan
        sub = Subscription(
            agency_id=agency.id,
            plan_id=starter.id,
            status=SubscriptionStatus.TRIALING,
            stripe_customer_id=customer_id,
            # Without a deadline this row is invisible to
            # `lifecycle.trial_expiry_sweep` (it filters on `trial_ends_at`),
            # so abandoning the checkout would buy a free plan for ever.
            trial_ends_at=datetime.now(UTC) + timedelta(days=TRIAL_DAYS),
        )
        db.add(sub)
    else:
        sub.stripe_customer_id = customer_id

    await db.commit()
    return {"checkout_url": session.get("url", ""), "session_id": session.get("id", "")}


class ChangePlanBody(BaseModel):
    agency_id: uuid.UUID
    plan_code: str
    interval: Literal["month", "year"] = "month"


# Statuses in which Stripe still holds a subscription we can edit in place.
# Deliberately the complement of `plan_limits.BLOCKING_STATUSES`: PAST_DUE is
# the grace period while we chase the card, not a dead account, and a customer
# in trouble must be able to *downgrade* rather than being pushed through a
# checkout that opens a second subscription. CANCELED / UNPAID / PAUSED have
# no live Stripe subscription to modify, so they belong in checkout.
CHANGEABLE_STATUSES = frozenset(
    {
        SubscriptionStatus.TRIALING,
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.PAST_DUE,
    }
)


@router.post("/change-plan")
async def change_plan(
    body: ChangePlanBody,
    # Owner/admin only — same reasoning as /checkout: this moves money.
    auth: AuthContext = OwnerOnlyDep,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Upgrade/downgrade an existing subscription without creating a new one.

    The bug this fixes was expensive: the panel only knew how to open a
    checkout, so an agency on Starter that bought Pro ended up with *two*
    live Stripe subscriptions and paid 99 + 199 EUR/month. Changing the price
    of the subscription it already has is the only correct move.

    Two decisions worth writing down:

    - **`create_prorations`, not `always_invoice`.** Both compute the same
      credit/charge; `always_invoice` bills it on the spot, which means a
      customer clicking "cambiar de plan" gets an unexpected card charge in
      that second. `create_prorations` puts the adjustment on the next
      invoice, which is exactly what the pricing page promises when it says
      the change "se prorratea". Same money, no surprise. One caveat the
      response carries back: it only holds while the billing *period* stays
      the same — see `billing_cycle_reset` below.
    - **`plan_id` is not written here.** Like `/checkout` (see the module
      docstring, invariant 1), limits only move once Stripe confirms the
      money. The `customer.subscription.updated` webhook already maps the new
      price back to a plan via `_apply_plan`, so it lands there, normally
      within seconds. Writing it here would raise limits on an API call that
      Stripe might still reject.
    - **409 means one thing only: `checkout_required`.** The panel reacts to
      a 409 by opening a checkout, and a checkout is what mints a *second*
      subscription — the 99+199 bug itself. So every other refusal here has
      to answer with something else, or the fix reintroduces the bug it was
      written to kill.
    """
    ensure_agency_access(auth, body.agency_id)

    plan, price_id = await _resolve_price(db, body.plan_code, body.interval)

    sub = (
        await db.execute(select(Subscription).where(Subscription.agency_id == body.agency_id))
    ).scalar_one_or_none()
    if sub is None or not sub.stripe_subscription_id or sub.status not in CHANGEABLE_STATUSES:
        # Nothing to modify: the panel sends these customers to /checkout,
        # which is the flow that legitimately creates a subscription.
        raise HTTPException(status.HTTP_409_CONFLICT, "checkout_required")

    try:
        change = await update_subscription_price(
            subscription_id=sub.stripe_subscription_id,
            price_id=price_id,
            proration_behavior="create_prorations",
        )
    except StripeSubscriptionItemError as exc:
        log.warning(
            "billing.change_plan.unexpected_items",
            subscription=str(sub.id),
            stripe_subscription=sub.stripe_subscription_id,
            items=exc.item_count,
        )
        # Refusing to guess is the cheap failure here: rewriting the wrong
        # item would bill the customer for something nobody chose.
        #
        # Deliberately NOT a 409. This subscription already has an item too
        # many, and 409 is the panel's signal to open a checkout — which
        # would add a whole second subscription on top. 422 lands in the
        # panel's generic "no hemos podido completar el cambio" branch, which
        # offers a retry and a route to support: exactly right for a shape
        # only a human can untangle.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "subscription_items_unexpected"
        ) from exc

    # Changing the billing period is not a proration. Stripe: "switching a
    # customer from a monthly subscription to a yearly subscription moves the
    # billing date to the date of the switch" — so the annual amount is
    # billed then, not credited onto a next invoice that no longer exists.
    # The panel must say so instead of promising "aparece en tu próxima
    # factura"; it cannot work this out on its own because /me reports the
    # plan, never the interval.
    billing_cycle_reset = bool(
        change.changed
        and change.previous_interval is not None
        and change.previous_interval != body.interval
    )

    log.info(
        "billing.change_plan.requested",
        subscription=str(sub.id),
        plan=plan.code,
        interval=body.interval,
        changed=change.changed,
        billing_cycle_reset=billing_cycle_reset,
    )
    # `status` is Stripe's, not ours: the panel shows it while it waits for
    # the webhook to move `plan_id`.
    return {
        "ok": True,
        "status": change.status or sub.status.value,
        # False when the subscription already had that exact price: nothing
        # was written and nothing will be billed. The panel can then avoid
        # promising a charge that does not exist.
        "changed": change.changed,
        "billing_cycle_reset": billing_cycle_reset,
    }


class PortalBody(BaseModel):
    agency_id: uuid.UUID
    return_url: str


@router.post("/portal")
async def billing_portal(
    body: PortalBody,
    # Owner/admin only: the portal cancels subscriptions and changes cards.
    auth: AuthContext = OwnerOnlyDep,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Self-serve card/invoice/cancel management via Stripe's hosted portal.

    No promotion-code flag here on purpose, unlike `/checkout`: Stripe's
    billing-portal *session* API has no `allow_promotion_codes` parameter —
    sending one is a 400. Whether the portal lets a customer type a coupon is
    part of the portal **configuration** (Stripe Dashboard → Billing →
    Customer portal), so item 183's "add coupon support" ends at checkout as
    far as this code is concerned.
    """
    ensure_agency_access(auth, body.agency_id)
    sub = (
        await db.execute(select(Subscription).where(Subscription.agency_id == body.agency_id))
    ).scalar_one_or_none()
    if sub is None or not sub.stripe_customer_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no_billing_account")
    session = await create_billing_portal_session(
        customer_id=sub.stripe_customer_id,
        return_url=body.return_url,
    )
    return {"portal_url": session.get("url", "")}


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


class EventNotReadyError(Exception):
    """The delivery is valid but could not be applied YET (n18).

    The dispatcher used to record every delivery whose handler returned
    without raising — including the ones that returned early because the row
    they needed did not exist, or because the price lookup failed. Guard 1
    then turned Stripe's retries into no-ops, and a customer who had paid
    stayed on the old plan for ever, silently.

    Raising this instead leaves `processed_stripe_events` untouched and
    answers 503, which is exactly what Stripe's retry schedule is for. It is
    only for states a retry can fix — never for "this event does not concern
    us", which must be recorded and forgotten.
    """


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    raw = await request.body()
    if not verify_webhook_signature(raw, stripe_signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_signature")

    event = await request.json()
    if settings.app_env == "production" and event.get("livemode") is not True:
        # A valid signature only proves that Stripe sent the event; test and
        # live endpoints have independent secrets but operational mistakes do
        # happen. Refuse every non-live event before dedup rows, subscription
        # state or emails can be touched in production.
        log.warning(
            "stripe.webhook.non_live_event_rejected",
            event_id=event.get("id"),
            event_type=event.get("type"),
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "stripe_test_event_in_production",
        )
    event_type = event.get("type", "")
    data_object = (event.get("data") or {}).get("object") or {}

    handlers = {
        "checkout.session.completed": _on_checkout_completed,
        "customer.subscription.updated": _on_subscription_updated,
        "customer.subscription.deleted": _on_subscription_deleted,
        "invoice.payment_failed": _on_payment_failed,
    }
    handler = handlers.get(event_type)
    if handler is None:
        return {"status": "ignored"}

    event_id = event.get("id")
    event_created = _as_int(event.get("created"))
    object_id = _stream_key(data_object)

    # Guard 1 — same delivery twice.
    if event_id and await db.get(ProcessedStripeEvent, event_id) is not None:
        log.info("stripe.webhook.duplicate", event_type=event_type, event_id=event_id)
        return {"status": "duplicate"}

    # Guard 2 — out-of-order retry. Anything older than the newest event we
    # already applied for this customer would regress the subscription.
    #
    # `checkout.session.completed` is exempt (n18): Stripe does not guarantee
    # ordering, and a `customer.subscription.updated` stamped a second later
    # routinely lands first. Treating the purchase as "stale" recorded it
    # without applying it, and the agency that had just paid stayed on the old
    # plan for ever. The one thing it must not do is resurrect an account
    # already cancelled, so that single case is checked explicitly below.
    is_purchase = event_type == "checkout.session.completed"
    if event_id and object_id and event_created is not None and not is_purchase:
        newest = (
            await db.execute(
                select(func.max(ProcessedStripeEvent.event_created)).where(
                    ProcessedStripeEvent.object_id == object_id
                )
            )
        ).scalar_one_or_none()
        if newest is not None and event_created < newest:
            log.info(
                "stripe.webhook.stale",
                event_type=event_type,
                event_id=event_id,
                created=event_created,
                newest=newest,
            )
            await _record_event(db, event_id, event_type, object_id, event_created)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
            return {"status": "stale"}

    # El precio de eximir la compra del guard de orden: una entrega tardía de
    # `checkout.session.completed` no puede revivir una cuenta que ya se dio
    # de baja después. Se comprueba contra el evento de baja, no contra el
    # más reciente de cualquier tipo.
    if is_purchase and object_id and event_created is not None:
        cancelled_at = (
            await db.execute(
                select(func.max(ProcessedStripeEvent.event_created)).where(
                    ProcessedStripeEvent.object_id == object_id,
                    ProcessedStripeEvent.event_type == "customer.subscription.deleted",
                )
            )
        ).scalar_one_or_none()
        if cancelled_at is not None and event_created < cancelled_at:
            log.warning(
                "stripe.webhook.purchase_after_cancellation",
                event_id=event_id,
                created=event_created,
                cancelled_at=cancelled_at,
            )
            await _record_event(db, event_id, event_type, object_id, event_created)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
            return {"status": "stale"}

    try:
        after_commit = await handler(db, data_object)
        if event_id:
            await _record_event(db, event_id, event_type, object_id, event_created)
        await db.commit()
    except EventNotReadyError as exc:
        # NOT recorded on purpose: recording it would make every Stripe retry
        # a no-op and the purchase would be lost for good.
        await db.rollback()
        log.error(
            "stripe.webhook.not_ready",
            event_type=event_type,
            event_id=event_id,
            reason=str(exc),
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "event_not_ready") from exc
    except IntegrityError:
        # Concurrent delivery of the same event won the insert race. It also
        # already ran (or is running) the side effects, which is exactly why
        # `after_commit` has not fired yet: this delivery mails nobody.
        await db.rollback()
        log.info("stripe.webhook.duplicate_race", event_type=event_type, event_id=event_id)
        return {"status": "duplicate"}
    except Exception:
        await db.rollback()
        log.exception("stripe.webhook.failed", event_type=event_type)
        raise

    # Past this line the event is durably recorded, so guard 1 turns every
    # Stripe retry into a no-op — which is what makes it safe to send mail
    # exactly once. Never raises (the callables swallow their own errors),
    # because Stripe must still get its 200.
    if after_commit is not None:
        await after_commit()

    return {"status": "ok"}


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _stream_key(obj: dict) -> str | None:
    """The ordering "stream" an event belongs to.

    Every object we handle (checkout session, subscription, invoice) carries
    the Stripe customer, which is exactly the granularity we care about: one
    agency, one subscription.
    """
    customer = obj.get("customer")
    if isinstance(customer, str) and customer:
        return customer
    obj_id = obj.get("id")
    return obj_id if isinstance(obj_id, str) and obj_id else None


async def _record_event(
    db: AsyncSession,
    event_id: str,
    event_type: str,
    object_id: str | None,
    event_created: int | None,
) -> None:
    db.add(
        ProcessedStripeEvent(
            event_id=event_id,
            event_type=event_type,
            object_id=object_id,
            event_created=event_created,
        )
    )


# ---------------------------------------------------------------------------
# Price -> Plan mapping (the only way limits ever go up)
# ---------------------------------------------------------------------------


async def _plan_for_price(db: AsyncSession, price_id: str | None) -> Plan | None:
    if not price_id:
        return None
    plan = (
        (
            await db.execute(
                select(Plan).where(
                    or_(Plan.stripe_price_id == price_id, Plan.stripe_price_id_year == price_id)
                )
            )
        )
        .scalars()
        .first()
    )
    if plan is not None:
        return plan

    # Stripe Prices are immutable for tax behaviour, so a fiscal migration
    # legitimately moves Plan.stripe_price_id while existing subscriptions
    # keep renewing on the old Price. Recover that historical association from
    # the metadata written by sync_stripe_plans instead of losing the paid
    # webhook. The Product id and metadata are cross-checked before trusting
    # the plan code; a mislabeled Price from another Product cannot grant a
    # different plan.
    identity = await _historical_price_plan_identity(price_id)
    if identity is None:
        return None
    plan_code, product_id = identity
    plan = (
        (
            await db.execute(
                select(Plan).where(
                    Plan.code == plan_code,
                    Plan.is_active.is_(True),
                )
            )
        )
        .scalars()
        .first()
    )
    if plan is None:
        log.warning(
            "stripe.historical_price_unknown_plan_code",
            price=price_id,
            plan_code=plan_code,
        )
        return None
    if not product_id or not plan.stripe_product_id or product_id != plan.stripe_product_id:
        log.warning(
            "stripe.historical_price_product_mismatch",
            price=price_id,
            plan_code=plan_code,
            stripe_product=product_id,
            configured_product=plan.stripe_product_id,
        )
        return None
    log.info(
        "stripe.historical_price_mapped",
        price=price_id,
        plan=plan_code,
        product=product_id,
    )
    return plan


def _metadata_plan_code(obj: dict) -> str | None:
    metadata = obj.get("metadata")
    value = metadata.get("plan_code") if isinstance(metadata, dict) else None
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 64:
        return None
    if not value[0].isalpha() or not all(
        char.islower() or char.isdigit() or char in "_-" for char in value
    ):
        return None
    return value


async def _historical_price_plan_identity(price_id: str) -> tuple[str, str] | None:
    remote_price = await stripe_get(f"/prices/{price_id}", {"expand[]": "product"})
    if remote_price.get("id") != price_id:
        log.warning("stripe.historical_price_id_mismatch", requested=price_id)
        return None

    price_plan_code = _metadata_plan_code(remote_price)
    product = remote_price.get("product")
    product_id: str | None = None
    product_plan_code: str | None = None
    if isinstance(product, dict):
        raw_product_id = product.get("id")
        product_id = raw_product_id if isinstance(raw_product_id, str) else None
        product_plan_code = _metadata_plan_code(product)
    elif isinstance(product, str):
        product_id = product

    if product_id and product_plan_code is None:
        remote_product = await stripe_get(f"/products/{product_id}")
        if remote_product.get("id") != product_id:
            log.warning(
                "stripe.historical_product_id_mismatch",
                price=price_id,
                requested=product_id,
            )
            return None
        product_plan_code = _metadata_plan_code(remote_product)

    codes = {code for code in (price_plan_code, product_plan_code) if code is not None}
    if len(codes) != 1:
        log.warning(
            "stripe.historical_price_ambiguous_metadata",
            price=price_id,
            price_plan_code=price_plan_code,
            product_plan_code=product_plan_code,
        )
        return None
    return codes.pop(), product_id or ""


def _price_id_from_subscription(obj: dict) -> str | None:
    """Read the price id out of a Stripe Subscription object."""
    items = ((obj.get("items") or {}).get("data")) or []
    for item in items:
        price = item.get("price") or {}
        price_id = price.get("id") or item.get("plan", {}).get("id")
        if price_id:
            return str(price_id)
    return None


async def _price_id_for_checkout(obj: dict, stripe_subscription_id: str) -> str | None:
    """Checkout sessions do not carry line items unless expanded, so fall
    back to fetching the subscription Stripe just created.

    A failure here used to be swallowed into `None`, which the caller read as
    "unknown plan" and the dispatcher recorded as processed: a customer paid
    and stayed on Starter for ever. Now it propagates, so the delivery is not
    recorded and Stripe retries it (see `EventNotReadyError`).
    """
    line_items = ((obj.get("line_items") or {}).get("data")) or []
    for item in line_items:
        price_id = (item.get("price") or {}).get("id")
        if price_id:
            return str(price_id)
    try:
        remote = await stripe_get(f"/subscriptions/{stripe_subscription_id}")
    except Exception as exc:
        log.exception("stripe.subscription_fetch_failed", subscription=stripe_subscription_id)
        raise EventNotReadyError(
            f"no se pudo leer la suscripción {stripe_subscription_id} en Stripe"
        ) from exc
    return _price_id_from_subscription(remote or {})


async def _apply_plan(db: AsyncSession, sub: Subscription, price_id: str | None) -> Plan | None:
    """Move `sub` onto the plan that price belongs to. Returns it, so the
    caller can talk about the plan (the welcome email prints its limits)
    without querying twice."""
    plan = await _plan_for_price(db, price_id)
    if plan is None:
        if price_id:
            log.warning("stripe.unknown_price", price=price_id, subscription=str(sub.id))
        return None
    if sub.plan_id != plan.id:
        log.info("billing.plan_applied", subscription=str(sub.id), plan=plan.code)
    sub.plan_id = plan.id
    return plan


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

# A subscription in any of these is live and past its trial: Stripe never
# sends it back to `trialing`, and neither may we.
LIVE_STATUSES = frozenset(
    {
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.PAST_DUE,
        SubscriptionStatus.UNPAID,
    }
)


def _apply_subscription_status(sub: Subscription, incoming: SubscriptionStatus) -> None:
    """Move `sub` to `incoming`, refusing transitions Stripe never makes.

    The webhook's timestamp guard can only drop *strictly* older deliveries,
    and `event.created` is measured in whole seconds — Stripe routinely emits
    several subscription events inside the same second (trial conversion,
    cancellation), so two of them can still be applied in the wrong order.
    Two transitions therefore have to be refused here, because both hand out
    service nobody paid for:

    - back to `trialing` once the subscription has left the trial;
    - anything at all once it is `canceled`. This handler always matches on
      `stripe_subscription_id`, and a deleted Stripe subscription is never
      revived — a returning customer gets a new one, which arrives through
      `checkout.session.completed` instead.
    """
    current = sub.status
    if incoming == current:
        return
    if current == SubscriptionStatus.CANCELED or (
        incoming == SubscriptionStatus.TRIALING and current in LIVE_STATUSES
    ):
        log.info(
            "billing.status_regression_ignored",
            subscription=str(sub.id),
            current=current.value,
            incoming=incoming.value,
        )
        return
    sub.status = incoming


# ---------------------------------------------------------------------------
# Handlers
#
# A handler may return a coroutine factory for work that must happen *after*
# the transaction commits — today, mail. See invariant 4 in the module
# docstring: sending inside the transaction is what lets a Stripe retry
# deliver the same email twice.
# ---------------------------------------------------------------------------

PostCommit = Callable[[], Awaitable[None]]


def _start_now_requested(obj: dict) -> bool:
    raw = (obj.get("metadata") or {}).get(START_NOW_METADATA_KEY)
    return raw is True or (isinstance(raw, str) and raw.lower() == "true")


async def _on_checkout_completed(db: AsyncSession, obj: dict) -> PostCommit | None:
    sub_id = obj.get("subscription")
    customer_id = obj.get("customer")
    if not (sub_id and customer_id):
        return None
    sub = (
        await db.execute(select(Subscription).where(Subscription.stripe_customer_id == customer_id))
    ).scalar_one_or_none()
    if sub is None:
        # `/checkout` creates the row before redirecting, so a miss here is a
        # race or replication lag — not "this customer is not ours". Retrying
        # is the only outcome that does not lose a paid subscription.
        raise EventNotReadyError(f"sin fila de suscripción para el cliente {customer_id}")
    sub.stripe_subscription_id = sub_id
    start_now = _start_now_requested(obj)
    # A paused or canceled account paying at last is exactly what should
    # revive it (Stripe gives it a brand-new subscription), but an account
    # that is already live — or chasing a failed payment — must never be
    # dropped back into a trial.
    #
    # Which live status it revives into is not a detail. Since (183) the
    # checkout only asks for the trial the agency still has coming, a
    # returning customer usually gets *none*: Stripe charges the card there
    # and then and the subscription starts `active`, not `trialing`. Writing
    # TRIALING anyway would show a paying customer an expired trial for up to
    # a whole billing cycle (Stripe only emits `customer.subscription.updated`
    # when something changes, so nothing would correct it), and the one thing
    # standing between that row and `lifecycle.trial_expiry_sweep` *pausing* a
    # customer who just paid is that sweep's `stripe_subscription_id IS NULL`
    # filter. `remaining_trial_days` is the same source of truth the checkout
    # used, so the two cannot disagree about what was bought.
    if sub.status not in LIVE_STATUSES:
        sub.status = (
            SubscriptionStatus.ACTIVE
            if start_now or remaining_trial_days(sub) <= 0
            else SubscriptionStatus.TRIALING
        )
    if start_now:
        # Checkout creation is not payment. Consume the opted-out trial only
        # here, once Stripe confirms the paid subscription, so an abandoned
        # or failed Checkout Session cannot burn access.
        sub.trial_ends_at = datetime.now(UTC)

    plan = await _apply_plan(db, sub, await _price_id_for_checkout(obj, str(sub_id)))
    if plan is None:
        # El precio no corresponde a ningún plan nuestro. Con el pago ya
        # cobrado, dejarlo pasar deja al cliente en el plan viejo sin que
        # nadie se entere; que Stripe reintente mientras se investiga.
        raise EventNotReadyError(f"precio sin plan conocido para la suscripción {sub_id}")

    async def _welcome() -> None:
        await send_payment_welcome_email(db, sub, plan)

    return _welcome


async def _on_subscription_updated(db: AsyncSession, obj: dict) -> None:
    sub_id = obj.get("id")
    sub = (
        await db.execute(select(Subscription).where(Subscription.stripe_subscription_id == sub_id))
    ).scalar_one_or_none()
    if sub is None:
        # `stripe_subscription_id` lo escribe `checkout.session.completed`, y
        # Stripe no garantiza el orden: este evento puede adelantarlo. Que
        # reintente en vez de perder el cambio de estado (un `past_due` o un
        # `paused` tragado aquí deja a la agencia con acceso que no ha pagado,
        # o al revés).
        raise EventNotReadyError(f"aún no conocemos la suscripción {sub_id}")

    status_str = obj.get("status")
    try:
        _apply_subscription_status(sub, SubscriptionStatus(status_str))
    except ValueError:
        log.warning("stripe.unknown_status", status=status_str)

    if _start_now_requested(obj):
        # The owner explicitly forfeited the remainder. Persist that only on
        # Stripe's webhook, never on the initiating API call: an update that
        # Stripe rejects must leave the trial intact.
        sub.trial_ends_at = datetime.now(UTC)

    period_end = obj.get("current_period_end")
    if period_end:
        sub.current_period_end = datetime.fromtimestamp(int(period_end), tz=UTC)

    # Plan changes (upgrade/downgrade in the portal) land here.
    await _apply_plan(db, sub, _price_id_from_subscription(obj))

    if sub.status == SubscriptionStatus.ACTIVE:
        await _clear_dunning(db, sub.agency_id)


async def _on_subscription_deleted(db: AsyncSession, obj: dict) -> None:
    sub_id = obj.get("id")
    sub = (
        await db.execute(select(Subscription).where(Subscription.stripe_subscription_id == sub_id))
    ).scalar_one_or_none()
    if sub is None:
        return
    sub.status = SubscriptionStatus.CANCELED
    sub.canceled_at = datetime.now(UTC)


async def _on_payment_failed(db: AsyncSession, obj: dict) -> PostCommit | None:
    customer_id = obj.get("customer")
    sub = (
        await db.execute(select(Subscription).where(Subscription.stripe_customer_id == customer_id))
    ).scalar_one_or_none()
    if sub is None:
        return
    if sub.status == SubscriptionStatus.CANCELED:
        # Stripe can still fail to collect a final invoice after the
        # subscription is gone. PAST_DUE is deliberately not a blocking
        # status (it is the grace period while we chase the card), so
        # writing it here would hand a departed customer their service back
        # and start emailing them about it.
        log.info("billing.payment_failed_after_cancel", subscription=str(sub.id))
        return
    sub.status = SubscriptionStatus.PAST_DUE

    async def _dunning() -> None:
        try:
            if await send_dunning_email(db, sub, attempt=1):
                # The webhook transaction has already committed. Persist the
                # dunning throttle separately so the daily sweep cannot send
                # the first attempt again on its next run.
                await db.commit()
        except Exception:
            # The Stripe event is durable by now, so this delivery must still
            # return 200. A failed send is retried by the daily dunning sweep;
            # a failed bookkeeping commit is protected from this event's
            # retry by ProcessedStripeEvent.
            log.exception("billing.payment_failed_email_failed", subscription=str(sub.id))
            try:
                await db.rollback()
            except Exception:
                log.exception("billing.payment_failed_email_rollback_failed")

    return _dunning


# ---------------------------------------------------------------------------
# Dunning (shared with the daily retry task in app/workers/tasks.py)
# ---------------------------------------------------------------------------


async def agency_owner_email(db: AsyncSession, agency: Agency) -> str | None:
    owner = (
        (
            await db.execute(
                select(User)
                .where(User.agency_id == agency.id, User.role == UserRole.OWNER)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    return (owner.email if owner is not None else None) or agency.contact_email


# Steps of the /setup wizard. The checklist is considered finished at this
# many ticks — the same threshold `lifecycle.setup_rescue` uses, so the two
# emails never disagree about whether the agency still has work to do.
SETUP_STEPS_DONE = 5


async def send_payment_welcome_email(
    db: AsyncSession,
    sub: Subscription,
    plan: Plan | None,
) -> bool:
    """(188) One-off confirmation after `checkout.session.completed`.

    Runs *after* the webhook has committed the event (invariant 4), so a
    Stripe retry of the same delivery is already being dropped by the time
    this sends anything. Belt and braces on top: a returning customer
    generates a *second* checkout with a brand-new event id, which the event
    dedup cannot catch, so `settings.payment_welcome_sent` is the durable
    flag — and it gets its own commit, because the caller's transaction is
    closed by now.

    Deliberately transactional — no `category`, so it carries no
    unsubscribe header and ignores `email_prefs`: this is the receipt for
    money the customer just committed, not marketing.

    Never raises: the webhook must ack Stripe even if Resend is down.
    """
    try:
        agency = await db.get(Agency, sub.agency_id)
        if agency is None or plan is None:
            return False

        blob = dict(agency.settings or {})
        if blob.get("payment_welcome_sent"):
            log.info("billing.payment_welcome.already_sent", agency=str(agency.id))
            return False

        to = await agency_owner_email(db, agency)
        if not to:
            log.warning("billing.payment_welcome.no_recipient", agency=str(agency.id))
            return False

        checklist = blob.get("setup_checklist")
        done = sum(1 for v in checklist.values() if v is True) if isinstance(checklist, dict) else 0
        html = build_payment_welcome_email_html(
            agency_name=agency.name,
            plan_name=plan.name,
            max_properties=plan.max_properties,
            max_agents=plan.max_agents,
            max_voice_minutes=plan.max_voice_minutes,
            setup_pending=done < SETUP_STEPS_DONE,
            trial_days_left=remaining_trial_days(sub),
        )
        sent = await send_email(
            to=to,
            subject=f"Tu plan {plan.name} de selai ya está activo",
            html=html,
        )
        if sent:
            agency.settings = {
                **blob,
                "payment_welcome_sent": datetime.now(UTC).isoformat(),
            }
            await db.commit()
        return sent
    except Exception:
        log.exception("billing.payment_welcome.failed", subscription=str(sub.id))
        return False


async def send_dunning_email(db: AsyncSession, sub: Subscription, attempt: int) -> bool:
    """Email the owner that the charge failed, and remember it in
    `agency.settings` so the daily sweep knows when to insist.

    This is the only place a dunning email is sent, and it is the throttle
    for the whole sequence: an `attempt` we have already delivered is
    dropped. Stripe fires one `invoice.payment_failed` per retry attempt on
    the same invoice — each a distinct event id, so the webhook's dedup lets
    them all through — and every one of them asks for attempt 1. Without the
    check the owner got the same warning four or five times over and the
    3/7-day clock restarted on each, multiplying the reminders.

    The CTA points at selai's /billing page rather than a raw Stripe portal
    session: portal sessions expire, and /billing mints a fresh one on click.
    """
    agency = await db.get(Agency, sub.agency_id)
    if agency is None:
        return False

    blob = dict(agency.settings or {})
    already_sent = int(blob.get("dunning_count") or 0)
    if attempt <= already_sent:
        log.info(
            "dunning.already_sent",
            agency=str(agency.id),
            attempt=attempt,
            already_sent=already_sent,
        )
        return False

    to = await agency_owner_email(db, agency)
    if not to:
        log.warning("dunning.no_recipient", agency=str(agency.id))
        return False

    sent = await send_email(
        to=to,
        subject="No hemos podido cobrar tu suscripción de selai",
        html=build_payment_failed_email_html(agency.name, BILLING_URL, attempt),
    )
    if sent:
        now = datetime.now(UTC).isoformat()
        blob["dunning_count"] = attempt
        blob["dunning_last_sent"] = now
        blob.setdefault("dunning_started_at", now)
        agency.settings = blob
    return sent


async def _clear_dunning(db: AsyncSession, agency_id: uuid.UUID) -> None:
    """Payment recovered — forget the dunning state so the next incident
    starts its own 0/3/7-day sequence."""
    agency = await db.get(Agency, agency_id)
    if agency is None:
        return
    blob = dict(agency.settings or {})
    if not any(k in blob for k in ("dunning_count", "dunning_last_sent", "dunning_started_at")):
        return
    for key in ("dunning_count", "dunning_last_sent", "dunning_started_at"):
        blob.pop(key, None)
    agency.settings = blob
