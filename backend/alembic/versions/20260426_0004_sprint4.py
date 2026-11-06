"""sprint 4: user home coordinates for geographic routing

Revision ID: 20260426_0004
Revises: 20260425_0003
Create Date: 2026-04-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260426_0004"
down_revision: Union[str, None] = "20260425_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("home_lat", sa.Float()))
    op.add_column("users", sa.Column("home_lng", sa.Float()))


def downgrade() -> None:
    op.drop_column("users", "home_lng")
    op.drop_column("users", "home_lat")
