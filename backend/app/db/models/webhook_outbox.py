"""Transactional outbox for CRM webhook deliveries."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class WebhookOutbox(UUIDPrimaryKey, Timestamps, Base):
    """One durable, retryable delivery to one CRM subscription.

    ``id`` is also the public delivery id. It remains stable across HTTP
    retries so receivers can deduplicate the unavoidable crash window between
    their 2xx response and our commit.
    """

    __tablename__ = "webhook_outbox"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "dedup_key",
            name="uq_webhook_outbox_subscription_dedup",
        ),
        Index(
            "ix_webhook_outbox_pending",
            "available_at",
            "created_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agencies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)

    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
