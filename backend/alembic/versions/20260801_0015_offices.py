"""offices: sucursales de una agencia (plan Agencia, "varias oficinas")

Revision ID: 20260801_0015
Revises: 20260801_0014
Create Date: 2026-08-01

Strictly additive: one new table plus two NULLABLE columns. Nothing is
backfilled, nothing is rewritten, no default changes — an agency that never
creates an office ends this migration with exactly the behaviour it had
before it, because NULL office_id is what every existing row keeps and NULL
means "the whole agency" everywhere it is read.

`ondelete="SET NULL"` on both columns is deliberate: closing an office must
detach its people and its listings, never delete them.

`downgrade()` drops exactly what `upgrade()` created, in reverse order, so
the step is reversible. The only thing lost on the way down is the
assignment itself, which is the definition of undoing the feature.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_0015"
down_revision: Union[str, None] = "20260801_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "offices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agency_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agencies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("address", sa.String(512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_offices_agency_id", "offices", ["agency_id"])

    # Nullable, no server_default, no backfill: existing rows stay NULL.
    op.add_column("users", sa.Column("office_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_users_office_id_offices",
        "users",
        "offices",
        ["office_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_users_office_id", "users", ["office_id"])

    op.add_column(
        "properties", sa.Column("office_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_properties_office_id_offices",
        "properties",
        "offices",
        ["office_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_properties_office_id", "properties", ["office_id"])


def downgrade() -> None:
    op.drop_index("ix_properties_office_id", table_name="properties")
    op.drop_constraint("fk_properties_office_id_offices", "properties", type_="foreignkey")
    op.drop_column("properties", "office_id")

    op.drop_index("ix_users_office_id", table_name="users")
    op.drop_constraint("fk_users_office_id_offices", "users", type_="foreignkey")
    op.drop_column("users", "office_id")

    op.drop_index("ix_offices_agency_id", table_name="offices")
    op.drop_table("offices")
