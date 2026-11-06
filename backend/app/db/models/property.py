"""A property listing. Vectorized for semantic search."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.agency import Agency


class PropertyType(str, enum.Enum):
    APARTMENT = "apartment"
    HOUSE = "house"
    DUPLEX = "duplex"
    STUDIO = "studio"
    PENTHOUSE = "penthouse"
    OFFICE = "office"
    COMMERCIAL = "commercial"
    LAND = "land"


class PropertyStatus(str, enum.Enum):
    DRAFT = "draft"
    AVAILABLE = "available"
    RESERVED = "reserved"
    SOLD = "sold"
    RENTED = "rented"
    ARCHIVED = "archived"


class Property(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "properties"

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )
    # Which branch manages this listing (Agencia plan, "varias oficinas").
    # NULL — the value every existing row keeps — means "the whole agency".
    office_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("offices.id", ondelete="SET NULL"), index=True
    )

    # Identity
    reference: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Classification
    property_type: Mapped[PropertyType] = mapped_column(SAEnum(PropertyType), nullable=False)
    status: Mapped[PropertyStatus] = mapped_column(
        SAEnum(PropertyStatus), default=PropertyStatus.AVAILABLE, nullable=False
    )
    operation: Mapped[str] = mapped_column(String(16), default="sale", nullable=False)  # sale | rent

    # Pricing
    price: Mapped[int] = mapped_column(Integer, nullable=False)  # euros
    price_per_sqm: Mapped[int | None] = mapped_column(Integer)

    # Location
    address: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    neighborhood: Mapped[str | None] = mapped_column(String(128))
    city: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    postal_code: Mapped[str | None] = mapped_column(String(16))
    country: Mapped[str] = mapped_column(String(2), default="ES", nullable=False)
    latitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[float | None] = mapped_column(Numeric(10, 7))

    # Specs
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[int | None] = mapped_column(Integer)
    area_sqm: Mapped[int | None] = mapped_column(Integer)
    year_built: Mapped[int | None] = mapped_column(Integer)
    floor: Mapped[str | None] = mapped_column(String(16))
    energy_rating: Mapped[str | None] = mapped_column(String(4))

    has_elevator: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_parking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_terrace: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_pool: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_ac: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_heating: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    furnished: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pet_friendly: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Media
    image_urls: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    video_url: Mapped[str | None] = mapped_column(String(512))
    virtual_tour_url: Mapped[str | None] = mapped_column(String(512))

    # Price tracking — list of {date, price} snapshots. Updated by event listener
    # in db.events. The "last alert" timestamp avoids spamming leads on tiny
    # corrections.
    price_history: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    last_price_alert_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Extra arbitrary data (pulled from portals, custom fields)
    extra: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Vector for semantic search (Voyage embeddings, 1024 dims by default).
    # Model choice stored in `extra["embedding_model"]`.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))

    agency: Mapped[Agency] = relationship(back_populates="properties")

    def __repr__(self) -> str:
        return f"<Property {self.reference} {self.city} €{self.price}>"
