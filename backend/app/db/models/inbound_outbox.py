"""Durable receipts for inbound channel webhooks."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class InboundOutbox(UUIDPrimaryKey, Timestamps, Base):
    """An authenticated inbound message waiting for durable processing.

    The tombstone remains after processing so a late provider retry is still
    idempotent. PII-bearing ``payload`` and ``context`` are cleared as soon as
    the receipt is complete.
    """

    __tablename__ = "inbound_outbox"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('whatsapp', 'email')",
            name="channel",
        ),
        UniqueConstraint(
            "agency_id",
            "channel",
            "idempotency_key",
            name="uq_inbound_outbox_agency_channel_key",
        ),
        Index(
            "ix_inbound_outbox_pending",
            "available_at",
            "created_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
    )

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agencies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
