"""agent_runs PII redaction columns

Revision ID: 20260503_0006
Revises: 20260427_0005
Create Date: 2026-05-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260503_0006"
down_revision: Union[str, None] = "20260427_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("output_redacted", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "agent_runs",
        sa.Column("redaction_version", sa.String(8), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("guardrail_action", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "guardrail_action")
    op.drop_column("agent_runs", "redaction_version")
    op.drop_column("agent_runs", "output_redacted")
