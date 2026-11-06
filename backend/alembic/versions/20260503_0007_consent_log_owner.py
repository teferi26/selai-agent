"""consent_logs: support owner (B2B) consents

Revision ID: 20260503_0007
Revises: 20260503_0006
Create Date: 2026-05-03

Changes:
- `lead_id` becomes nullable (was NOT NULL).
- New `user_id` FK for owner consents.
- New `document_version` column to record which version of TdS/DPA/Privacy
  the owner signed.
- Add three enum values to `consentscope`: terms_of_service, dpa,
  privacy_policy.
- Check constraint: at least one of (lead_id, user_id) must be set.

PG 12+ allows `ALTER TYPE ADD VALUE IF NOT EXISTS` inside a transaction; we
target PG 16 so this is safe.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260503_0007"
down_revision: Union[str, None] = "20260503_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Relax lead_id to nullable.
    op.alter_column("consent_logs", "lead_id", nullable=True)

    # 2. Add user_id column + FK + index.
    op.add_column(
        "consent_logs",
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_consent_logs_user_id_users",
        "consent_logs",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_consent_logs_user_id", "consent_logs", ["user_id"])

    # 3. document_version (which version of the legal doc was signed).
    op.add_column(
        "consent_logs",
        sa.Column("document_version", sa.String(16), nullable=True),
    )

    # 4. New scopes for B2B owner consents.
    op.execute("ALTER TYPE consentscope ADD VALUE IF NOT EXISTS 'terms_of_service'")
    op.execute("ALTER TYPE consentscope ADD VALUE IF NOT EXISTS 'dpa'")
    op.execute("ALTER TYPE consentscope ADD VALUE IF NOT EXISTS 'privacy_policy'")

    # 5. Constraint: a consent record must have either a lead OR a user owner.
    op.create_check_constraint(
        "ck_consent_logs_lead_or_user",
        "consent_logs",
        "lead_id IS NOT NULL OR user_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_consent_logs_lead_or_user", "consent_logs", type_="check")
    op.drop_column("consent_logs", "document_version")
    op.drop_index("ix_consent_logs_user_id", table_name="consent_logs")
    op.drop_constraint("fk_consent_logs_user_id_users", "consent_logs", type_="foreignkey")
    op.drop_column("consent_logs", "user_id")
    op.alter_column("consent_logs", "lead_id", nullable=False)
    # NB: PostgreSQL doesn't support removing values from an enum type. The
    # added scope values stay in the type definition even after downgrade.
