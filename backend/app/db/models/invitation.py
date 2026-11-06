"""Team invitations — how anyone other than the signup owner gets in.

Until this table existed, the only `User` the product ever created was the
owner of the signup (`services/onboarding.py`), so the "hasta 2/5/15 personas
de tu equipo" the pricing page sells was impossible: a commercial agent who
registered in Clerk and landed on /onboarding created a *second, empty*
agency instead of joining their own.

An invitation is a bearer credential sent by email: whoever holds `token`
can claim the seat, so it is generated with `secrets.token_urlsafe` (never
derived from the email or the id), it expires, and it can be revoked. The
acceptance endpoint additionally demands a Clerk session whose email matches
`email`, so a leaked link alone does not hand over access to the agency.

Lifecycle is expressed as three nullable timestamps rather than a status
column: they are facts (when it was accepted / revoked) instead of a state
someone has to keep in sync, and `status` derives from them plus the clock.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.db.models.user import UserRole

# A week is long enough for someone on holiday and short enough that a link
# forwarded or left in an old inbox stops working before it is forgotten.
INVITATION_TTL_DAYS = 7


def generate_token() -> str:
    """256 bits of URL-safe randomness. Not guessable, not enumerable."""
    return secrets.token_urlsafe(32)


def default_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=INVITATION_TTL_DAYS)


class Invitation(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "invitations"

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )
    # Always stored lower-cased (`normalize_email`): the whole flow compares it
    # against the email claim of a Clerk session, and "Maria@x.es" must not
    # become a seat nobody can claim.
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole), default=UserRole.AGENT, nullable=False
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # SET NULL, not CASCADE: if the owner who invited leaves, the invitation
    # they sent is still a valid seat in the agency.
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The list screen asks "what is pending for this agency?" and the invite
    # form asks "does this person already have one?" — both are (agency, email).
    __table_args__ = (Index("ix_invitations_agency_id_email", "agency_id", "email"),)

    def is_expired(self, now: datetime | None = None) -> bool:
        expires = self.expires_at
        if expires is None:
            return False
        if expires.tzinfo is None:
            # Naive timestamps come back from SQLite and hand-written rows.
            expires = expires.replace(tzinfo=UTC)
        return (now or datetime.now(UTC)) >= expires

    @property
    def status(self) -> str:
        """`pending` | `accepted` | `revoked` | `expired` — derived, never stored."""
        if self.accepted_at is not None:
            return "accepted"
        if self.revoked_at is not None:
            return "revoked"
        if self.is_expired():
            return "expired"
        return "pending"

    @property
    def is_pending(self) -> bool:
        """Pending seats count against the plan's `max_agents`: five invitations
        out at 0 members would otherwise become six people on a plan of five."""
        return self.status == "pending"

    def __repr__(self) -> str:
        return f"<Invitation {self.email} {self.status}>"


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()
