"""A conversation thread. One per (lead, channel)."""
from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.agency import Agency
    from app.db.models.lead import Lead
    from app.db.models.message import Message


class ConversationChannel(str, enum.Enum):
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    VOICE = "voice"
    SMS = "sms"
    WEB = "web"


class ConversationStatus(str, enum.Enum):
    ACTIVE = "active"
    SNOOZED = "snoozed"
    HANDED_OFF = "handed_off"       # escalated to human
    CLOSED = "closed"


class Conversation(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "conversations"

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )

    channel: Mapped[ConversationChannel] = mapped_column(
        SAEnum(ConversationChannel), nullable=False, index=True
    )
    status: Mapped[ConversationStatus] = mapped_column(
        SAEnum(ConversationStatus), default=ConversationStatus.ACTIVE, nullable=False, index=True
    )

    # External identifier (WA conversation id, email thread id, voice call id)
    external_thread_id: Mapped[str | None] = mapped_column(String(255), index=True)

    # If handed off, which user took over
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    # Free-form extras (voice call records, per-conversation flags...).
    # Mirrors Lead.extra: reassign the whole dict on write so SQLAlchemy
    # change-tracking notices.
    extra: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    lead: Mapped[Lead] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
    agency: Mapped[Agency] = relationship()

    def __repr__(self) -> str:
        return f"<Conversation {self.channel.value} lead={self.lead_id}>"
