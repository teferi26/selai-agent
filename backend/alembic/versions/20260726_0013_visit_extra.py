"""visits.extra: free-form JSON blob (reminder de-duplication flags)

Revision ID: 20260726_0013
Revises: 20260725_0012
Create Date: 2026-07-26

Mirrors `leads.extra` and `conversations.extra`. First consumer:
`visits.send_reminders` writes `reminder_24h_sent` / `reminder_1h_sent`
*before* sending, so the 60-minute window scanned by Beat every 15 minutes
can no longer send the same lead four identical reminders (item 225).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260726_0013"
down_revision: Union[str, None] = "20260725_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "visits",
        sa.Column("extra", sa.JSON, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("visits", "extra")
