"""Expo push tokens for the mobile app.

One row per device. A token is globally unique (Expo issues it per app
install); if a user logs into another agency with the same device we
re-point the row instead of duplicating it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class DeviceToken(UUIDPrimaryKey, Base):
    __tablename__ = "device_tokens"

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expo_push_token: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<DeviceToken {self.platform} {self.expo_push_token[:16]}...>"
