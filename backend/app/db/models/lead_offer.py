"""Inter-agency lead marketplace.

When an agency can't / won't pursue a lead (wrong zone, full inventory,
out of office for weeks), it can publish the lead anonymously to the
marketplace. Other agencies see a sanitized preview and can claim it
in exchange for a referral fee. On claim, the lead's PII is transferred
and the originating agency gets a commission.

Publishing requires the lead's GDPR consent for inter-agency transfer
(`consent_log.scope = data_processing` evidence).
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


class LeadOfferStatus(str, enum.Enum):
    OPEN = "open"
    CLAIMED = "claimed"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


class LeadOffer(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "lead_offers"

    seller_agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )

    # Sanitized preview the marketplace shows. Never include name/phone/email.
    preview: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    asking_fee_eur: Mapped[int] = mapped_column(Integer, nullable=False)
    commission_pct: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[LeadOfferStatus] = mapped_column(
        SAEnum(LeadOfferStatus), default=LeadOfferStatus.OPEN, nullable=False, index=True
    )

    buyer_agency_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="SET NULL")
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # If claimed, the buyer's NEW lead row (we copy, not transfer, to keep
    # the seller's history intact for analytics).
    transferred_lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL")
    )

    # Token used to fetch the actual contact info on claim — single use.
    redemption_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
