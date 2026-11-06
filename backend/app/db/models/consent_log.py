"""Immutable consent audit log (GDPR art. 7).

We never UPDATE rows here — every consent change is a new row, so we can
prove what the lead consented to and when.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class ConsentScope(str, enum.Enum):
    DATA_PROCESSING = "data_processing"
    MARKETING = "marketing"
    AI_INTERACTION = "ai_interaction"
    # B2B owner consents (Parte B — onboarding GDPR).
    TERMS_OF_SERVICE = "terms_of_service"
    DPA = "dpa"
    PRIVACY_POLICY = "privacy_policy"


class ConsentAction(str, enum.Enum):
    GRANTED = "granted"
    REVOKED = "revoked"


class ConsentLog(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "consent_logs"
    __table_args__ = (
        CheckConstraint(
            "lead_id IS NOT NULL OR user_id IS NOT NULL OR subject_pseudonym IS NOT NULL",
            name="lead_or_user_or_pseudonym",
        ),
    )

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )
    # A live record has either `lead_id` (B2C) or `user_id` (B2B). After an
    # art. 17 erasure, only `subject_pseudonym` remains. The DB check enforces
    # that at least one of those three anchors exists.
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), index=True, nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )

    scope: Mapped[ConsentScope] = mapped_column(SAEnum(ConsentScope), nullable=False)
    action: Mapped[ConsentAction] = mapped_column(SAEnum(ConsentAction), nullable=False)

    # How the lead expressed it (channel, exact words, etc.)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))

    # For owner/B2B consents: which version of the document was signed.
    # Lets us prove "you accepted v1, the law changed to v2, you must re-sign".
    document_version: Mapped[str | None] = mapped_column(String(16))

    # After an art. 17 erasure, the lead FK and direct evidence disappear.
    # This keyed pseudonym is the only link retained between the minimal legal
    # audit rows; it cannot be reversed into a lead id without the service
    # secret and deliberately has no FK that could keep the lead alive.
    subject_pseudonym: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
