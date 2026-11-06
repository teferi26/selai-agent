"""users: add clerk_user_id for auth identity mapping

Revision ID: 20260724_0008
Revises: 20260503_0007
Create Date: 2026-07-24

`require_auth` resolves the authenticated Clerk identity to a `users` row
via this column (backfilled from the email claim on first verified login).
It is the basis of the agency-membership check that guards every
agency-scoped endpoint.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_0008"
down_revision: Union[str, None] = "20260503_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("clerk_user_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_users_clerk_user_id", "users", ["clerk_user_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_users_clerk_user_id", table_name="users")
    op.drop_column("users", "clerk_user_id")
