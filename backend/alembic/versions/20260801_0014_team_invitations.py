"""invitations: team seats — how anyone but the signup owner gets in

Revision ID: 20260801_0014
Revises: 20260726_0013
Create Date: 2026-08-01

The pricing page sells "hasta 2/5/15 personas de tu equipo" but the only
`User` the product ever created was the owner of the signup, and a commercial
agent who registered ended up creating a second, empty agency. This table is
the missing link: an emailed bearer token, scoped to an agency, that a Clerk
session with the *same* email can exchange for a `users` row in that agency.

`token` is unique so the acceptance endpoint is a single indexed lookup, and
(agency_id, email) is indexed because both the team screen ("what is pending
here?") and the invite form ("does this person already have one?") ask by it.

`userrole` already exists (initial migration); reused with create_type=False.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_0014"
down_revision: Union[str, None] = "20260726_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    user_role = postgresql.ENUM(
        "owner", "agent", "admin", name="userrole", create_type=False
    )
    op.create_table(
        "invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agency_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agencies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", user_role, nullable=False, server_default="agent"),
        sa.Column("token", sa.String(64), nullable=False),
        # SET NULL: if the owner who invited leaves, the seat they offered is
        # still valid.
        sa.Column(
            "invited_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_invitations_agency_id", "invitations", ["agency_id"])
    op.create_index("ix_invitations_token", "invitations", ["token"], unique=True)
    op.create_index("ix_invitations_agency_id_email", "invitations", ["agency_id", "email"])


def downgrade() -> None:
    op.drop_index("ix_invitations_agency_id_email", table_name="invitations")
    op.drop_index("ix_invitations_token", table_name="invitations")
    op.drop_index("ix_invitations_agency_id", table_name="invitations")
    op.drop_table("invitations")
