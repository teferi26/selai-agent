"""Billing models — plans + subscriptions backed by Stripe.

Plans live in DB (cached from Stripe Products) so the dashboard doesn't
need to hit Stripe to render the pricing page. Subscriptions are 1:1 per
agency for now (per-seat pricing comes later).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class PlanInterval(str, enum.Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"


class Plan(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)

    interval: Mapped[PlanInterval] = mapped_column(
        SAEnum(PlanInterval), default=PlanInterval.MONTHLY, nullable=False
    )
    monthly_price_eur: Mapped[int] = mapped_column(Integer, nullable=False)

    # Capabilities included in this plan, free-form for the dashboard to render.
    features: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Stripe linkage. `stripe_price_id` is the monthly price; the annual one
    # (12 months with the -20% the pricing page advertises) lives in
    # `stripe_price_id_year` and is filled in by scripts/sync_stripe_plans.py.
    stripe_product_id: Mapped[str | None] = mapped_column(String(120))
    stripe_price_id: Mapped[str | None] = mapped_column(String(120))
    stripe_price_id_year: Mapped[str | None] = mapped_column(String(120))

    # Soft limits — enforced in services (not via DB constraints).
    max_agents: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    max_properties: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    max_voice_minutes: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


class SubscriptionStatus(str, enum.Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    # Trial ran out without a card. Set by `lifecycle.trial_expiry_sweep`;
    # blocks paid resources (see services/plan_limits.BLOCKING_STATUSES).
    # Stripe uses the same string for its own paused subscriptions, so the
    # webhook's `SubscriptionStatus(status_str)` maps cleanly too.
    PAUSED = "paused"


class Subscription(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "subscriptions"

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), unique=True, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False
    )

    status: Mapped[SubscriptionStatus] = mapped_column(
        SAEnum(SubscriptionStatus), default=SubscriptionStatus.TRIALING, nullable=False
    )

    stripe_customer_id: Mapped[str | None] = mapped_column(String(120))
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(120))

    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Usage counters reset by a Celery task at period rollover
    # (`billing.reset_usage_counters`, monthly on day 1 at 02:00 UTC).
    voice_minutes_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    leads_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ProcessedStripeEvent(Base):
    """Idempotency ledger for the Stripe webhook.

    Stripe retries deliveries and does not guarantee ordering, so a naive
    handler can apply the same event twice or regress a subscription that
    already moved on. Two guards, both fed by this table:

    - `event_id` is the primary key: a repeated delivery is a no-op.
    - `event_created` (Stripe's `event.created`, unix seconds) is compared
      against the newest event already applied for the same `object_id`
      (the Stripe customer); anything older is recorded and skipped.
    """

    __tablename__ = "processed_stripe_events"

    event_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    # Stripe customer id (or the object's own id when there is no customer):
    # the ordering "stream" this event belongs to.
    object_id: Mapped[str | None] = mapped_column(String(120), index=True)
    event_created: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
