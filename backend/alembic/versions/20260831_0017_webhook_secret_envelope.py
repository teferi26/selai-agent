"""Widen outbound webhook signing secrets for encrypted envelopes.

Revision ID: 20260831_0017
Revises: 20260831_0016

The online ``rewrap_tenant_credentials`` command owns the data conversion.
Keeping it out of Alembic preserves offline SQL and compare-and-swap safety.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_0017"
down_revision: str | None = "20260831_0016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.alter_column(
        "webhook_subscriptions",
        "secret",
        existing_type=sa.String(length=120),
        type_=sa.String(length=512),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Encrypted envelopes can exceed 120 characters. Narrowing in place could
    # truncate a signing key, so rollback keeps the backward-compatible width.
    pass
