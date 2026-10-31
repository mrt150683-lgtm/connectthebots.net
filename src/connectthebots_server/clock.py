"""The wall-clock adapter and a single UTC representation for stored dates."""

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def __call__(self) -> datetime: ...


def utc_now() -> datetime:
    """Read real time only at the adapter boundary; rules receive a clock."""
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    """Reject ambiguous local dates and serialize a timezone-aware date as UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware date is required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
