"""sprint 2 schema: price history, templates, offers

Revision ID: 20260425_0003
Revises: 20260424_0002
Create Date: 2026-04-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260425_0003"
down_revision: Union[str, None] = "20260424_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Property: price history + last alert timestamp ---
    op.add_column(
        "properties",
        sa.Column("price_history", sa.JSON, nullable=False, server_default="[]"),
    )
    op.add_column(
        "properties",
        sa.Column("last_price_alert_at", sa.DateTime(timezone=True)),
    )

    # --- Message templates ---
    op.create_table(
        "message_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agency_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agencies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False, server_default="whatsapp"),
        sa.Column("language", sa.String(8), nullable=False, server_default="es"),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_message_templates_agency_id", "message_templates", ["agency_id"])

    # --- Offers ---
    offer_status = postgresql.ENUM(
        "draft", "sent", "opened", "signed", "rejected", "expired",
        name="offerstatus", create_type=False,
    )
    op.execute("CREATE TYPE offerstatus AS ENUM ('draft', 'sent', 'opened', 'signed', 'rejected', 'expired')")

    op.create_table(
        "offers",
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
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("amount_eur", sa.Integer, nullable=False),
        sa.Column("operation", sa.String(16), nullable=False, server_default="sale"),
        sa.Column("conditions", sa.Text, nullable=False, server_default=""),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("status", offer_status, nullable=False, server_default="draft"),
        sa.Column("pdf_url", sa.String(512)),
        sa.Column("sign_url", sa.String(512)),
        sa.Column("sign_provider", sa.String(32)),
        sa.Column("sign_envelope_id", sa.String(255)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("opened_at", sa.DateTime(timezone=True)),
        sa.Column("signed_at", sa.DateTime(timezone=True)),
        sa.Column("extra", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_offers_agency_id", "offers", ["agency_id"])
    op.create_index("ix_offers_lead_id", "offers", ["lead_id"])
    op.create_index("ix_offers_property_id", "offers", ["property_id"])
    op.create_index("ix_offers_status", "offers", ["status"])


def downgrade() -> None:
    op.drop_table("offers")
    op.execute("DROP TYPE IF EXISTS offerstatus")
    op.drop_table("message_templates")
    op.drop_column("properties", "last_price_alert_at")
    op.drop_column("properties", "price_history")
