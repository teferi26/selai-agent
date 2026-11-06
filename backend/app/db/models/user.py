"""Human users of the dashboard (agency owners, agents, admins)."""
from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.agency import Agency


class UserRole(str, enum.Enum):
    OWNER = "owner"
    AGENT = "agent"
    ADMIN = "admin"


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )
    # Which branch this person works out of (Agencia plan, "varias oficinas").
    # NULL — the value every existing row keeps — means "the whole agency",
    # so nobody who does not use offices is affected by this column.
    office_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("offices.id", ondelete="SET NULL"), index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    # Clerk identity; nullable until the user's first verified login.
    clerk_user_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.AGENT, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    google_calendar_id: Mapped[str | None] = mapped_column(String(255))
    google_refresh_token: Mapped[str | None] = mapped_column(String(512))
    # Home base for geographic routing (visits assigned to closest agent).
    home_lat: Mapped[float | None] = mapped_column()
    home_lng: Mapped[float | None] = mapped_column()
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    agency: Mapped[Agency] = relationship(back_populates="users")

    def __repr__(self) -> str:
        return f"<User {self.email}>"
