"""conversations.extra: free-form JSON blob (voice call records, flags)

Revision ID: 20260725_0010
Revises: 20260725_0009
Create Date: 2026-07-25

Mirrors `leads.extra`. First consumer: the Vapi end-of-call webhook appends
one record per finished call ({call_id, ended_at, duration_seconds,
recording_url, summary}) under `extra["calls"]`, which the dashboard's
GET /calls endpoint flattens.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0010"
down_revision: Union[str, None] = "20260725_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("extra", sa.JSON, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("conversations", "extra")
