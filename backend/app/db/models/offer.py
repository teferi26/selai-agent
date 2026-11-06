"""Formal purchase / rent offer made by a lead on a property.

Once accepted by the seller, this is the document that becomes the contract
basis. Status transitions:

  DRAFT → SENT → (OPENED → SIGNED | REJECTED | EXPIRED)
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class OfferStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    OPENED = "opened"
    SIGNED = "signed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Offer(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "offers"

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    amount_eur: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(String(16), default="sale", nullable=False)
    conditions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[OfferStatus] = mapped_column(
        SAEnum(OfferStatus), default=OfferStatus.DRAFT, nullable=False, index=True
    )

    pdf_url: Mapped[str | None] = mapped_column(String(512))
    sign_url: Mapped[str | None] = mapped_column(String(512))
    sign_provider: Mapped[str | None] = mapped_column(String(32))   # signaturit, firmafy...
    sign_envelope_id: Mapped[str | None] = mapped_column(String(255))

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    extra: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
