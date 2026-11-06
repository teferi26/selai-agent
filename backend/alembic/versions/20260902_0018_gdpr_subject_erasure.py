"""Pseudonymized GDPR evidence and external-erasure outbox.

Revision ID: 20260902_0018
Revises: 20260831_0017
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "20260902_0018"
down_revision: str | None = "20260831_0017"
branch_labels: str | None = None
depends_on: str | None = None


def _drop_consent_lead_fk() -> None:
    """Drop the lead FK regardless of the name older Alembic used for it."""
    op.execute(
        """
        DO $$
        DECLARE constraint_name text;
        BEGIN
          SELECT c.conname INTO constraint_name
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
          WHERE t.relname = 'consent_logs'
            AND c.contype = 'f'
            AND a.attname = 'lead_id'
          LIMIT 1;
          IF constraint_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE consent_logs DROP CONSTRAINT %I', constraint_name);
          END IF;
        END $$;
        """
    )


def upgrade() -> None:
    op.add_column(
        "consent_logs",
        sa.Column("subject_pseudonym", sa.String(length=80), nullable=True),
    )
    op.create_index(
        "ix_consent_logs_subject_pseudonym",
        "consent_logs",
        ["subject_pseudonym"],
    )
    op.drop_constraint("ck_consent_logs_lead_or_user", "consent_logs", type_="check")
    _drop_consent_lead_fk()
    op.create_foreign_key(
        "fk_consent_logs_lead_id_leads",
        "consent_logs",
        "leads",
        ["lead_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "lead_or_user_or_pseudonym",
        "consent_logs",
        "lead_id IS NOT NULL OR user_id IS NOT NULL OR subject_pseudonym IS NOT NULL",
    )

    op.create_table(
        "external_erasure_requests",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agency_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_pseudonym", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=48), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("locator_digest", sa.String(length=64), nullable=False),
        sa.Column("locator_encrypted", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("confirmation_digest", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["agency_id"], ["agencies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agency_id",
            "provider",
            "resource_type",
            "locator_digest",
            name="uq_external_erasure_requests_target",
        ),
    )
    op.create_index(
        "ix_external_erasure_requests_agency_id",
        "external_erasure_requests",
        ["agency_id"],
    )
    op.create_index(
        "ix_external_erasure_requests_provider",
        "external_erasure_requests",
        ["provider"],
    )
    op.create_index(
        "ix_external_erasure_requests_subject_pseudonym",
        "external_erasure_requests",
        ["subject_pseudonym"],
    )


def downgrade() -> None:
    op.drop_table("external_erasure_requests")

    # Old schema cannot represent a retained pseudonym without a lead/user.
    # Remove only those new erasure-evidence rows before restoring its check.
    op.execute(
        "DELETE FROM consent_logs WHERE lead_id IS NULL AND user_id IS NULL"
    )
    op.drop_constraint(
        "lead_or_user_or_pseudonym",
        "consent_logs",
        type_="check",
    )
    _drop_consent_lead_fk()
    op.create_foreign_key(
        "fk_consent_logs_lead_id_leads",
        "consent_logs",
        "leads",
        ["lead_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_consent_logs_lead_or_user",
        "consent_logs",
        "lead_id IS NOT NULL OR user_id IS NOT NULL",
    )
    op.drop_index("ix_consent_logs_subject_pseudonym", table_name="consent_logs")
    op.drop_column("consent_logs", "subject_pseudonym")
