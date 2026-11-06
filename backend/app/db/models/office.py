"""An office (branch) of an agency — the "varias oficinas" of the Agencia plan.

An agency has N offices, or none at all. Every `office_id` in the schema is
NULLABLE and NULL means "the whole agency", which is exactly how every row
behaved before this table existed. That is the rule the rest of the feature
is built on: an agency that never creates an office cannot tell the
difference.

Nothing hangs off an office by cascade. Deleting one detaches its users and
properties (`ondelete="SET NULL"`) instead of taking leads, listings or
conversations with it — an office is an organisational label, not a tenant.
The tenant is still the agency.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.db.models.agency import Agency


class Office(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "offices"

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Free text on purpose: it is printed, never geocoded or matched against.
    address: Mapped[str | None] = mapped_column(String(512))

    agency: Mapped[Agency] = relationship()

    def __repr__(self) -> str:
        return f"<Office {self.name}>"
