"""billing hardening: paused status, annual prices, Stripe event dedup

Revision ID: 20260725_0011
Revises: 20260725_0010
Create Date: 2026-07-25

Three independent pieces, one migration because they ship together:

- `subscriptionstatus` gains 'paused' — the state a trial lands in when it
  expires without a card (lifecycle.trial_expiry_sweep). It is a blocking
  status in services/plan_limits.py.
- `plans.stripe_price_id_year` — the annual (-20%) Stripe price, created by
  scripts/sync_stripe_plans.py and used by POST /billing/checkout when the
  caller asks for interval="year".
- `processed_stripe_events` — idempotency ledger for the Stripe webhook, so
  a retried delivery is a no-op and an out-of-order one cannot regress a
  subscription's status.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0011"
down_revision: Union[str, None] = "20260725_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres 12+ allows ADD VALUE inside a transaction as long as the new
    # label is not used in that same transaction — it is not.
    op.execute("ALTER TYPE subscriptionstatus ADD VALUE IF NOT EXISTS 'paused'")

    op.add_column("plans", sa.Column("stripe_price_id_year", sa.String(120)))

    op.create_table(
        "processed_stripe_events",
        sa.Column("event_id", sa.String(120), primary_key=True),
        sa.Column("event_type", sa.String(120), nullable=False, server_default=""),
        sa.Column("object_id", sa.String(120)),
        sa.Column("event_created", sa.Integer),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_processed_stripe_events_object_id", "processed_stripe_events", ["object_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_processed_stripe_events_object_id", table_name="processed_stripe_events")
    op.drop_table("processed_stripe_events")
    op.drop_column("plans", "stripe_price_id_year")
    # Postgres cannot drop a single enum label; any row still sitting in
    # 'paused' would break the downgrade, so we move them back to 'trialing'
    # and leave the (harmless) label in place.
    op.execute("UPDATE subscriptions SET status = 'trialing' WHERE status = 'paused'")
