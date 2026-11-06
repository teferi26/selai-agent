"""A scheduled property visit. The setter's main conversion goal."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class VisitStatus(str, enum.Enum):
    REQUESTED = "requested"       # proposed by agent, not confirmed
    CONFIRMED = "confirmed"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    COMPLETED = "completed"


class Visit(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "visits"

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), index=True
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(default=30, nullable=False)

    status: Mapped[VisitStatus] = mapped_column(
        SAEnum(VisitStatus), default=VisitStatus.REQUESTED, nullable=False, index=True
    )

    # External calendar event id (Google)
    calendar_event_id: Mapped[str | None] = mapped_column(String(255))

    # AI-generated briefing for the human agent before the visit.
    # Populated by a background job shortly before scheduled_at.
    pre_visit_briefing: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Post-visit notes (filled in by the human agent or by the AI after follow-up).
    post_visit_notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Free-form extras. Mirrors Lead.extra / Conversation.extra: reassign the
    # whole dict on write so SQLAlchemy change-tracking notices. First
    # consumer: `reminder_24h_sent` / `reminder_1h_sent`, written by
    # visits.send_reminders *before* sending so a lead never gets the same
    # reminder four times (item 225).
    extra: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    lead = relationship("Lead", back_populates="visits")
    property_ = relationship("Property")

    def __repr__(self) -> str:
        return f"<Visit {self.scheduled_at} lead={self.lead_id} property={self.property_id}>"
