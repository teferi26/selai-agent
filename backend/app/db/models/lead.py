"""A person the agency is trying to convert into a customer."""
from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.agency import Agency
    from app.db.models.conversation import Conversation
    from app.db.models.visit import Visit


class LeadStatus(str, enum.Enum):
    NEW = "new"
    ENGAGED = "engaged"          # has replied at least once
    QUALIFIED = "qualified"      # we know budget + preferences
    VISIT_SCHEDULED = "visit_scheduled"
    VISITED = "visited"
    NEGOTIATING = "negotiating"
    WON = "won"
    LOST = "lost"
    DORMANT = "dormant"          # no activity in N days


class LeadTemperature(str, enum.Enum):
    """Heuristic used to prioritize human agent attention."""
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class Lead(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "leads"

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )

    # Identity — any of these may be null; matching logic in services/lead_resolver.py
    full_name: Mapped[str | None] = mapped_column(String(255))
    phone_e164: Mapped[str | None] = mapped_column(String(32), index=True)
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    whatsapp_wa_id: Mapped[str | None] = mapped_column(String(64), index=True)

    # Sales intelligence — the setter fills this in as it learns.
    status: Mapped[LeadStatus] = mapped_column(
        SAEnum(LeadStatus), default=LeadStatus.NEW, nullable=False, index=True
    )
    temperature: Mapped[LeadTemperature] = mapped_column(
        SAEnum(LeadTemperature), default=LeadTemperature.COLD, nullable=False, index=True
    )
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0-100

    # Qualification fields — filled progressively by the agent
    budget_min: Mapped[int | None] = mapped_column(Integer)
    budget_max: Mapped[int | None] = mapped_column(Integer)
    preferred_cities: Mapped[list[str] | None] = mapped_column(JSON)
    preferred_neighborhoods: Mapped[list[str] | None] = mapped_column(JSON)
    min_bedrooms: Mapped[int | None] = mapped_column(Integer)
    min_bathrooms: Mapped[int | None] = mapped_column(Integer)
    min_area_sqm: Mapped[int | None] = mapped_column(Integer)
    operation: Mapped[str | None] = mapped_column(String(16))  # sale|rent
    must_haves: Mapped[list[str] | None] = mapped_column(JSON)     # ["parking", "elevator"]
    deal_breakers: Mapped[list[str] | None] = mapped_column(JSON)

    # Soft facts the agent picks up (financing ready?, moving timeline, family size...)
    qualification_notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    detected_objections: Mapped[list[str] | None] = mapped_column(JSON)
    interested_property_ids: Mapped[list[str] | None] = mapped_column(JSON)

    # Source tracking
    source_channel: Mapped[str | None] = mapped_column(String(32))
    source_campaign: Mapped[str | None] = mapped_column(String(128))

    # Consent / compliance (GDPR).
    consent_marketing: Mapped[bool] = mapped_column(default=False, nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="es", nullable=False)

    # Free-form extras
    extra: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Relationships
    agency: Mapped[Agency] = relationship(back_populates="leads")
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    visits: Mapped[list[Visit]] = relationship(back_populates="lead", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Lead {self.full_name or self.phone_e164 or self.email}>"
