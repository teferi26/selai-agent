"""SQLAlchemy declarative base + common mixins."""
import enum as _enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy import Enum as _SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Default SAEnum to write .value (lowercase) instead of .name (uppercase) so
# Python enums serialize to the lowercase strings the migrations created.
_orig_saenum_init = _SAEnum.__init__


def _saenum_init(self, *enums, **kw):
    if "values_callable" not in kw and enums and isinstance(enums[0], type) and issubclass(enums[0], _enum.Enum):
        kw["values_callable"] = lambda e: [m.value for m in e]
    _orig_saenum_init(self, *enums, **kw)


_SAEnum.__init__ = _saenum_init

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
