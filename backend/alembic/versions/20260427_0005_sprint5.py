"""sprint 5: billing, marketplace, webhook subscriptions

Revision ID: 20260427_0005
Revises: 20260426_0004
Create Date: 2026-04-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260427_0005"
down_revision: Union[str, None] = "20260426_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    plan_interval = postgresql.ENUM("monthly", "yearly", name="planinterval", create_type=False)
    sub_status = postgresql.ENUM(
        "trialing", "active", "past_due", "canceled", "unpaid",
        name="subscriptionstatus", create_type=False,
    )
    offer_status = postgresql.ENUM(
        "open", "claimed", "expired", "withdrawn",
        name="leadofferstatus", create_type=False,
    )
    op.execute("CREATE TYPE planinterval AS ENUM ('monthly', 'yearly')")
    op.execute("CREATE TYPE subscriptionstatus AS ENUM ('trialing', 'active', 'past_due', 'canceled', 'unpaid')")
    op.execute("CREATE TYPE leadofferstatus AS ENUM ('open', 'claimed', 'expired', 'withdrawn')")

    op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("interval", plan_interval, nullable=False, server_default="monthly"),
        sa.Column("monthly_price_eur", sa.Integer, nullable=False),
        sa.Column("features", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("stripe_product_id", sa.String(120)),
        sa.Column("stripe_price_id", sa.String(120)),
        sa.Column("max_agents", sa.Integer, nullable=False, server_default="5"),
        sa.Column("max_properties", sa.Integer, nullable=False, server_default="200"),
        sa.Column("max_voice_minutes", sa.Integer, nullable=False, server_default="300"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agencies.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sub_status, nullable=False, server_default="trialing"),
        sa.Column("stripe_customer_id", sa.String(120)),
        sa.Column("stripe_subscription_id", sa.String(120)),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True)),
        sa.Column("current_period_end", sa.DateTime(timezone=True)),
        sa.Column("canceled_at", sa.DateTime(timezone=True)),
        sa.Column("voice_minutes_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("leads_processed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "lead_offers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_agency_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("preview", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("asking_fee_eur", sa.Integer, nullable=False),
        sa.Column("commission_pct", sa.Integer, nullable=False, server_default="10"),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", offer_status, nullable=False, server_default="open"),
        sa.Column("buyer_agency_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agencies.id", ondelete="SET NULL")),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("transferred_lead_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("leads.id", ondelete="SET NULL")),
        sa.Column("redemption_token", sa.String(64), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_lead_offers_seller_agency_id", "lead_offers", ["seller_agency_id"])
    op.create_index("ix_lead_offers_lead_id", "lead_offers", ["lead_id"])
    op.create_index("ix_lead_offers_status", "lead_offers", ["status"])

    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agency_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_url", sa.String(512), nullable=False),
        sa.Column("events", postgresql.ARRAY(sa.String), nullable=False),
        sa.Column("secret", sa.String(120), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_webhook_subscriptions_agency_id", "webhook_subscriptions", ["agency_id"])


def downgrade() -> None:
    op.drop_table("webhook_subscriptions")
    op.drop_table("lead_offers")
    op.drop_table("subscriptions")
    op.drop_table("plans")
    op.execute("DROP TYPE IF EXISTS leadofferstatus")
    op.execute("DROP TYPE IF EXISTS subscriptionstatus")
    op.execute("DROP TYPE IF EXISTS planinterval")
