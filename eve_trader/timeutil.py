"""One definition of "now" and "today": UTC (= EVE time).

`date.today()` follows the server's local time zone and naive
`datetime.utcnow()` is deprecated and ambiguous; use these instead.
"""
from __future__ import annotations

from datetime import date, datetime, timezone


def utc_now() -> datetime:
    """Timezone-aware current time in UTC."""
    return datetime.now(timezone.utc)


def utc_today() -> date:
    """Current UTC calendar day."""
    return utc_now().date()


def utc_now_naive() -> datetime:
    """Naive UTC time, for strings stored and compared as text (no offset)."""
    return utc_now().replace(tzinfo=None)


def as_utc(value: datetime) -> datetime:
    """Aware values are converted to UTC, naive ones are taken as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
