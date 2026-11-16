from __future__ import annotations

from datetime import datetime, timedelta, timezone

from eve_trader import timeutil


def test_utc_today_is_utc_date_near_midnight(monkeypatch):
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            local = timezone(timedelta(hours=2))
            # 23:30 UTC on Oct 1 is 01:30 on Oct 2 at +02:00.
            return datetime(2026, 10, 1, 23, 30, tzinfo=timezone.utc).astimezone(tz or local)

    monkeypatch.setattr(timeutil, "datetime", FakeDatetime)
    assert timeutil.utc_today().isoformat() == "2026-10-01"
    assert timeutil.utc_now().tzinfo is not None


def test_naive_utc_keeps_stored_string_format():
    s = timeutil.utc_now_naive().isoformat(timespec="seconds")
    assert "+" not in s and s.endswith(tuple("0123456789")) and len(s) == 19


def test_as_utc_converts_aware_and_tags_naive():
    pst = timezone(timedelta(hours=-8))
    assert timeutil.as_utc(datetime(2026, 1, 1, 20, tzinfo=pst)).hour == 4
    assert timeutil.as_utc(datetime(2026, 1, 1, 20)).tzinfo == timezone.utc
