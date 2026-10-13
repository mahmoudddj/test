"""SQLAlchemy base, portable column types and common mixins."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, MetaData, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

JSONType = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator):
    """Timezone-aware timestamp stored in UTC.

    Rejects naive datetimes on write and always returns aware UTC datetimes
    (also on SQLite, which has no native timezone support).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime rejected: all timestamps must be timezone-aware")
        value = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class IdMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class TrackedMixin(IdMixin):
    """Provenance + soft-delete columns shared by all clinical tables.

    * ``recorded_time``  - when the row entered the study database
    * ``source`` / ``source_ref`` - origin (manual, import batch, API client, EHR id)
    * ``record_version`` - incremented on every correction (history in audit_event)
    * ``is_voided`` - rows are never hard-deleted; voiding requires a reason
    """

    recorded_time: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    recorded_by: Mapped[str | None] = mapped_column(String(36))
    updated_time: Mapped[datetime | None] = mapped_column(UTCDateTime(), onupdate=_now)
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(255))
    import_batch_id: Mapped[str | None] = mapped_column(String(36))
    record_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_voided: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    void_reason: Mapped[str | None] = mapped_column(Text)
    dq_flags: Mapped[list | None] = mapped_column(JSONType)
