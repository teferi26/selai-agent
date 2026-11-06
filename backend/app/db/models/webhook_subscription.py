"""Outgoing webhooks — agencies subscribe to events to forward into their CRM."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class WebhookSubscription(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "webhook_subscriptions"

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )

    target_url: Mapped[str] = mapped_column(String(512), nullable=False)
    # Comma-separated list would be simpler, but Postgres ARRAY is cleaner
    # and easier to query.
    events: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    # Authenticated-encryption envelope for the HMAC-SHA256 signing key. The
    # plaintext is shown exactly once when the subscription is created.
    secret: Mapped[str] = mapped_column(String(512), nullable=False)

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
