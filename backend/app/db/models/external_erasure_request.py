"""Durable outbox for GDPR deletion work owned by external providers.

Deleting a local row is not evidence that Meta, Vapi, Google Calendar or an
e-signature provider deleted their copy.  Every provider reference we can
identify is queued here as ``pending``.  The locator is encrypted and its
keyed digest provides idempotency without exposing the identifier.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class ExternalErasureRequest(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "external_erasure_requests"
    __table_args__ = (
        UniqueConstraint(
            "agency_id",
            "provider",
            "resource_type",
            "locator_digest",
            name="target",
        ),
    )

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )
    subject_pseudonym: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(48), index=True, nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    locator_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    locator_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # No code path marks this completed merely because the local erase
    # succeeded. Provider-specific workers/manual operations must do so only
    # after receiving positive confirmation from that provider.
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    confirmation_digest: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
