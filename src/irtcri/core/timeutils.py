"""Timezone-safe time handling.

Policy (see docs/data_model.md#time):
* Every stored timestamp is timezone-aware and normalised to UTC.
* Naive timestamps are *never* silently accepted by the API. Bulk imports may
  supply naive local times only together with an explicit IANA timezone (by
  default the site's registered timezone), which is recorded in provenance.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

UTC = timezone.utc


class NaiveDatetimeError(ValueError):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_aware(dt: datetime | None, field: str = "timestamp") -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise NaiveDatetimeError(f"{field} must include timezone information (got naive {dt.isoformat()})")
    return dt.astimezone(UTC)


def localize(dt: datetime, tz_name: str) -> datetime:
    """Attach ``tz_name`` to a naive datetime (used by importers) and convert to UTC."""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC)
    return dt.replace(tzinfo=ZoneInfo(tz_name)).astimezone(UTC)


def parse_timestamp(value, tz_name: str | None = None, field: str = "timestamp") -> datetime | None:
    """Parse strings / pandas timestamps into aware UTC datetimes."""
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NaT:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    dt = ts.to_pydatetime()
    if dt.tzinfo is None:
        if tz_name is None:
            raise NaiveDatetimeError(f"{field}: naive timestamp '{value}' and no timezone supplied")
        return localize(dt, tz_name)
    return dt.astimezone(UTC)


def to_utc_series(s: pd.Series) -> pd.Series:
    out = pd.to_datetime(s, utc=True)
    return out
