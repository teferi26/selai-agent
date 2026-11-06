"""Agency = tenant. Every other row joins to an agency."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.lead import Lead
    from app.db.models.property import Property
    from app.db.models.user import User


class Agency(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "agencies"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)

    # Contact
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Madrid", nullable=False)
    default_language: Mapped[str] = mapped_column(String(8), default="es", nullable=False)

    # Branding / prompt customization. Stored as JSON so we can add fields
    # (tone, signature, disclaimer, opening line...) without migrations.
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Relationships
    users: Mapped[list[User]] = relationship(back_populates="agency", cascade="all, delete-orphan")
    properties: Mapped[list[Property]] = relationship(
        back_populates="agency", cascade="all, delete-orphan"
    )
    leads: Mapped[list[Lead]] = relationship(back_populates="agency", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Agency {self.slug}>"
