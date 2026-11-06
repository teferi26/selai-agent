"""device_tokens: Expo push tokens for the mobile app

Revision ID: 20260725_0009
Revises: 20260724_0008
Create Date: 2026-07-25

One row per device install. `expo_push_token` is globally unique — the
registration endpoint upserts by token so a device that switches agency
(or reinstalls) never duplicates rows.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260725_0009"
down_revision: Union[str, None] = "20260724_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agency_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agencies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expo_push_token", sa.String(255), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_device_tokens_agency_id", "device_tokens", ["agency_id"])
    op.create_index(
        "ix_device_tokens_expo_push_token", "device_tokens", ["expo_push_token"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_device_tokens_expo_push_token", table_name="device_tokens")
    op.drop_index("ix_device_tokens_agency_id", table_name="device_tokens")
    op.drop_table("device_tokens")
