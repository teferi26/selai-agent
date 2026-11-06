"""consent logs

Revision ID: 20260424_0002
Revises: 20260423_0001
Create Date: 2026-04-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260424_0002"
down_revision: Union[str, None] = "20260423_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    consent_scope = postgresql.ENUM(
        "data_processing", "marketing", "ai_interaction",
        name="consentscope", create_type=False,
    )
    consent_action = postgresql.ENUM(
        "granted", "revoked", name="consentaction", create_type=False,
    )
    op.execute("CREATE TYPE consentscope AS ENUM ('data_processing', 'marketing', 'ai_interaction')")
    op.execute("CREATE TYPE consentaction AS ENUM ('granted', 'revoked')")

    op.create_table(
        "consent_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agency_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agencies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", consent_scope, nullable=False),
        sa.Column("action", consent_action, nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("evidence", sa.Text, nullable=False, server_default=""),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("user_agent", sa.String(255)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_consent_logs_agency_id", "consent_logs", ["agency_id"])
    op.create_index("ix_consent_logs_lead_id", "consent_logs", ["lead_id"])


def downgrade() -> None:
    op.drop_table("consent_logs")
    op.execute("DROP TYPE IF EXISTS consentaction")
    op.execute("DROP TYPE IF EXISTS consentscope")
