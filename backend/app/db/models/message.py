"""One message in a conversation."""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class MessageDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageRole(str, enum.Enum):
    """Who authored this message — matters for LLM context replay."""
    LEAD = "lead"
    AGENT_AI = "agent_ai"
    AGENT_HUMAN = "agent_human"
    SYSTEM = "system"


class Message(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "messages"

    # (173) One provider message id can only be stored once per agency, for
    # *inbound* rows. Meta/Resend retry a webhook they think we failed to
    # answer, and without this the retry produces a second agent run and a
    # second reply to the lead. The route short-circuits on the duplicate,
    # but only the index makes it safe under concurrency (several workers,
    # several replicas).
    #
    # Inbound only, and that restriction is load-bearing: outbound rows store
    # whatever the provider client returned, and an unconfigured client
    # returns the literal "dev-stub" (see app/channels/*/client.py). A
    # direction-blind unique index turns the second follow-up ever sent on
    # such a deployment into an IntegrityError — a dedup rule breaking the
    # sending path it was never meant to touch.
    __table_args__ = (
        Index(
            "uq_messages_agency_external_id",
            "agency_id",
            "external_message_id",
            unique=True,
            postgresql_where=sa_text(
                "external_message_id IS NOT NULL AND direction = 'inbound'"
            ),
        ),
    )

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    )

    direction: Mapped[MessageDirection] = mapped_column(
        SAEnum(MessageDirection), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(SAEnum(MessageRole), nullable=False)

    # Text body after normalization (for voice, this is the transcript).
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Raw payload from/to the channel (useful for debug/replay).
    raw: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # For outbound-via-external-API, track delivery.
    external_message_id: Mapped[str | None] = mapped_column(String(255), index=True)

    # Media attachments (image/audio/doc URLs) — channel-normalized.
    media: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)

    conversation = relationship("Conversation", back_populates="messages")

    def __repr__(self) -> str:
        return f"<Message {self.direction.value}/{self.role.value}: {self.content[:40]!r}>"
