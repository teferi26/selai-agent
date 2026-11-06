"""Plan limit enforcement.

`Plan` rows declare soft limits (max_agents, max_properties,
max_voice_minutes); this module is the single place where they are read
and enforced. Endpoints that consume a limited resource call the
`ensure_*` helpers, which raise 402 with a machine-readable code the
dashboard turns into an upgrade prompt.

An agency with no subscription row is allowed through (dev environments
and seeded data); a canceled/unpaid/paused subscription blocks paid
resources. An agency inside its free trial is enforced against the plan
the trial advertises, not the row it is parked on (see `TRIAL_PLAN_CODE`).
"""
from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Plan, Property, Subscription, SubscriptionStatus, User

log = get_logger(__name__)

# PAUSED is what an expired trial without a card becomes
# (lifecycle.trial_expiry_sweep): the landing promises "al día 14 eliges plan
# o tu cuenta se pausa", so it has to block like canceled/unpaid.
BLOCKING_STATUSES = frozenset(
    {
        SubscriptionStatus.CANCELED,
        SubscriptionStatus.UNPAID,
        SubscriptionStatus.PAUSED,
    }
)

# Every signup lands on the cheapest active plan (services/onboarding.py) with
# status TRIALING for 14 days, but the site sells that window as "14 días con
# todas las funciones del plan Pro [...] para hacer llamadas de prueba"
# (dashboard/src/app/(marketing)/ayuda). Enforcing the *parked* plan during the
# trial answered 403 to the activation wizard's own voice and phone steps for
# every new customer, on day one, with a "inténtalo de nuevo en unos minutos"
# that would never come true.
#
# So while the trial is live, enforcement reads the plan the trial advertises.
# Nothing is invented here: the flags and the ceilings are the seeded `pro`
# row, so the trial gets a real, bounded allowance (600 voice minutes) instead
# of the unmetered voice it had before any of this existed.
TRIAL_PLAN_CODE = "pro"


@dataclass
class LimitStatus:
    resource: str
    used: int
    limit: int

    @property
    def exceeded(self) -> bool:
        return self.limit >= 0 and self.used >= self.limit

    @property
    def remaining(self) -> int | None:
        """How many more may be created; None means "no ceiling".

        Callers that add rows one by one (the CSV import) need the headroom,
        not a yes/no: a file of 5.000 rows against 1 free slot must import
        that one row, not all 5.000 nor zero.
        """
        if self.limit < 0:
            return None
        return max(0, self.limit - self.used)


def check_limit(resource: str, used: int, limit: int) -> LimitStatus:
    """Pure core so the policy is unit-testable without a DB."""
    return LimitStatus(resource=resource, used=used, limit=limit)


# `informes` is modelled in seed_plans.py as a *tier string* ("básicos" for
# Starter, "completos" for Pro and Agencia), not as a boolean flag. That is
# why `ensure_feature` cannot police it: every non-empty string is truthy, so
# Starter's "básicos" would open every report. Ordered cheapest first.
REPORT_TIERS: tuple[str, ...] = ("basicos", "completos")


def _fold(value: object) -> str:
    """Lower-case and strip accents.

    The seeded values carry accents ("básicos") and plan rows are edited by
    hand in production, so "Basicos" must compare equal to "básicos".
    """
    text = str(value or "").strip().casefold()
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def report_tier_meets(current: object, minimum: str) -> bool:
    """Pure core: does the plan's `informes` tier reach `minimum`?

    A tier we do not recognise counts as *below* everything: an unreadable
    value must not hand out the most expensive report by accident.
    """
    current_tier = _fold(current)
    minimum_tier = _fold(minimum)
    if current_tier not in REPORT_TIERS or minimum_tier not in REPORT_TIERS:
        return False
    return REPORT_TIERS.index(current_tier) >= REPORT_TIERS.index(minimum_tier)


def _limit_error(status_detail: LimitStatus) -> HTTPException:
    return HTTPException(
        status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "code": "plan_limit_reached",
            "resource": status_detail.resource,
            "used": status_detail.used,
            "limit": status_detail.limit,
        },
    )


async def get_plan_state(
    db: AsyncSession, agency_id: uuid.UUID
) -> tuple[Subscription | None, Plan | None]:
    sub = (
        await db.execute(select(Subscription).where(Subscription.agency_id == agency_id))
    ).scalar_one_or_none()
    if sub is None:
        return None, None
    plan = await db.get(Plan, sub.plan_id)
    return sub, plan


def trial_is_live(sub: Subscription | None, now: datetime | None = None) -> bool:
    """Pure check: is this subscription inside its free trial right now?

    The deadline matters as much as the status. `lifecycle.trial_expiry_sweep`
    flips an expired trial to PAUSED on a schedule, so between the deadline and
    the next sweep the row still reads TRIALING — and must not keep handing out
    the trial's rights.
    """
    if sub is None or sub.status != SubscriptionStatus.TRIALING:
        return False
    ends = getattr(sub, "trial_ends_at", None)
    if ends is None:
        # TRIALING with no deadline recorded: nothing says it is over.
        return True
    if ends.tzinfo is None:
        # Naive timestamps come back from SQLite and from hand-written rows.
        ends = ends.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) < ends


async def enforced_plan_state(
    db: AsyncSession, agency_id: uuid.UUID
) -> tuple[Subscription | None, Plan | None]:
    """`get_plan_state` with the trial's rights applied — what gates must read.

    Deliberately *not* folded into `get_plan_state`: /me and the billing page
    have to keep naming the plan the customer is actually subscribed to (the
    panel decides which upgrade buttons to paint by comparing plan codes, and
    the checkout writes against that row), while everything that *enforces*
    has to honour what the trial promised.
    """
    sub, plan = await get_plan_state(db, agency_id)
    if plan is None or not trial_is_live(sub):
        return sub, plan
    trial_plan = (
        await db.execute(
            select(Plan).where(Plan.code == TRIAL_PLAN_CODE, Plan.is_active.is_(True))
        )
    ).scalars().first()
    if trial_plan is None:
        # The advertised plan is not seeded here (tests, a fresh DB): fall back
        # to the parked plan rather than guessing at rights.
        return sub, plan
    # Never a downgrade: an agency trialing Agencia keeps Agencia.
    if (plan.monthly_price_eur or 0) >= (trial_plan.monthly_price_eur or 0):
        return sub, plan
    return sub, trial_plan


async def get_usage(db: AsyncSession, agency_id: uuid.UUID) -> list[LimitStatus]:
    """Current usage vs the limits in force — rendered by /me and the billing
    page, and the source the CSV import counts against.

    Enforced plan, not parked plan: the usage bar has to agree with what the
    endpoints actually allow, or a trial reads "100/100 propiedades" while the
    import happily takes 500.
    """
    sub, plan = await enforced_plan_state(db, agency_id)
    if plan is None:
        return []

    agents_used = (
        await db.execute(
            select(func.count()).select_from(User).where(User.agency_id == agency_id)
        )
    ).scalar_one()
    properties_used = (
        await db.execute(
            select(func.count()).select_from(Property).where(Property.agency_id == agency_id)
        )
    ).scalar_one()
    return [
        check_limit("agents", agents_used, plan.max_agents),
        check_limit("properties", properties_used, plan.max_properties),
        check_limit("voice_minutes", sub.voice_minutes_used if sub else 0, plan.max_voice_minutes),
    ]


async def usage_for(
    db: AsyncSession, agency_id: uuid.UUID, resource: str
) -> LimitStatus | None:
    """One resource's usage vs its limit, or None when it is not metered.

    None means "this agency has no plan row" (dev environments and seeded
    data) — the same escape hatch the `ensure_*` helpers honour.
    """
    for limit_status in await get_usage(db, agency_id):
        if limit_status.resource == resource:
            return limit_status
    return None


async def voice_minutes_status(
    db: AsyncSession, agency_id: uuid.UUID
) -> LimitStatus | None:
    """Voice minutes used vs the plan's cap, or None when not metered.

    The non-raising sibling of `ensure_can_use_voice`, for the one caller
    that cannot answer with an HTTP error: a live phone call. It reads the
    plan directly instead of going through `get_usage` because the other two
    counters (agents, properties) are two extra COUNT queries on a request
    that runs once per *turn* of every call.

    Fails OPEN — returns None — when the state cannot be read or the cap is
    not a number. Hanging up on a real person because a billing lookup
    misbehaved is worse than a handful of unmetered minutes, and it is the
    same rule `is_subscription_blocked` already follows.
    """
    try:
        sub, plan = await enforced_plan_state(db, agency_id)
        if sub is None or plan is None:
            return None
        limit = getattr(plan, "max_voice_minutes", None)
        if not isinstance(limit, int) or isinstance(limit, bool):
            return None
        return check_limit("voice_minutes", int(sub.voice_minutes_used or 0), limit)
    except Exception:
        log.exception("plan_limits.voice_minutes_lookup_failed", agency=str(agency_id))
        return None


async def is_subscription_blocked(db: AsyncSession, agency_id: uuid.UUID) -> bool:
    """True when the agency's subscription is canceled/unpaid/paused.

    For inbound webhooks, which must not raise 4xx back at the provider:
    they check this and silently drop instead of serving a paused account.
    Fails OPEN: an internal error reading billing state must never cost a
    customer a real lead."""
    try:
        sub, _plan = await get_plan_state(db, agency_id)
    except Exception:
        log.exception("plan_limits.blocked_lookup_failed", agency=str(agency_id))
        return False
    return sub is not None and sub.status in BLOCKING_STATUSES


async def ensure_feature(db: AsyncSession, agency_id: uuid.UUID, feature: str) -> None:
    """403 when the agency's plan lacks a boolean feature flag (eg.
    `voice_cloning`, Agencia-only). Agencies without a plan (dev/seeds)
    are not blocked, and a live trial is judged by the plan it advertises,
    so `plan` in the error names the plan that was evaluated."""
    _sub, plan = await enforced_plan_state(db, agency_id)
    if plan is None:
        return
    if not (plan.features or {}).get(feature):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "feature_not_in_plan", "feature": feature, "plan": plan.code},
        )


async def ensure_report_tier(
    db: AsyncSession, agency_id: uuid.UUID, minimum: str = "completos"
) -> None:
    """403 when the plan's `informes` tier is below `minimum`.

    Same error code as `ensure_feature` so the dashboard renders the same
    upgrade prompt; `required`/`current` let it name the tier. Agencies
    without a plan (dev/seeds) are not blocked.
    """
    _sub, plan = await enforced_plan_state(db, agency_id)
    if plan is None:
        return
    current = (plan.features or {}).get("informes")
    if not report_tier_meets(current, minimum):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "feature_not_in_plan",
                "feature": "informes",
                "required": minimum,
                "current": current,
                "plan": plan.code,
            },
        )


async def ensure_subscription_usable(db: AsyncSession, agency_id: uuid.UUID) -> None:
    """402 when the subscription exists but is canceled/unpaid/paused."""
    sub, _plan = await get_plan_state(db, agency_id)
    if sub is not None and sub.status in BLOCKING_STATUSES:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "subscription_inactive", "status": sub.status.value},
        )


async def ensure_can_use_voice(db: AsyncSession, agency_id: uuid.UUID) -> None:
    """Gate for anything that spends voice minutes (calls in/out)."""
    sub, plan = await enforced_plan_state(db, agency_id)
    if sub is None or plan is None:
        return
    if sub.status in BLOCKING_STATUSES:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "subscription_inactive", "status": sub.status.value},
        )
    voice = check_limit("voice_minutes", sub.voice_minutes_used, plan.max_voice_minutes)
    if voice.exceeded:
        raise _limit_error(voice)


async def ensure_can_add(db: AsyncSession, agency_id: uuid.UUID, resource: str) -> None:
    """Gate for creating **one** limited resource ("agents" | "properties").

    No caller today: the CSV import used to be one, but a bulk endpoint has
    to count per row (`usage_for(...).remaining`) — asking "are you already
    at the cap?" once let a 5.000-row file through at 99/100. This stays as
    the right gate for a single create (inviting an agent, adding a listing
    by hand), which is why it is not deleted.
    """
    await ensure_subscription_usable(db, agency_id)
    for limit_status in await get_usage(db, agency_id):
        if limit_status.resource == resource and limit_status.exceeded:
            raise _limit_error(limit_status)
