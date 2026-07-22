"""Tests for tools/time_context.py, tools/date_math.py, and the
tz-anchored resolve_window fix (MCP/TimeCapabilities-Plan1.md)."""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import date_math, time_context
from tools._envelope import resolve_window
from tools._time import home_tz

pytestmark = pytest.mark.asyncio


def _client(session=None, fail=False):
    client = AsyncMock()

    async def call_api(path, method="GET", **kwargs):
        if fail:
            raise ValueError("boom")
        if path == "/session":
            return session or {}
        return {}

    client.call_api = AsyncMock(side_effect=call_api)
    return client


# ---------------------------------------------------------------- home_tz

async def test_home_tz_profile():
    tz, source = await home_tz(_client({"home_timezone": "America/Chicago"}))
    assert source == "profile"
    assert str(tz) == "America/Chicago"


async def test_home_tz_fallbacks():
    for c in (_client({}), _client(fail=True),
              _client({"home_timezone": "Not/AZone"})):
        tz, source = await home_tz(c)
        assert source == "utc_fallback"
        assert str(tz) == "UTC"


# ---------------------------------------------------------- get_current_time

async def test_current_time_shape_and_windows():
    env = await time_context.handle({}, _client({"home_timezone": "America/Chicago"}))
    d = env["data"]
    assert d["timezone"] == "America/Chicago"
    assert d["timezone_source"] == "profile"
    # windows are closed intervals: from == to - N + 1
    for name, n in (("last_7_days", 7), ("last_30_days", 30), ("last_90_days", 90)):
        w = d["common_windows"][name]
        f = date.fromisoformat(w["from"])
        t = date.fromisoformat(w["to"])
        assert (t - f).days + 1 == n
        assert w["to"] == d["today"]
    assert d["local"][-6] in "+-"  # ISO offset present


async def test_current_time_nonhour_offset():
    env = await time_context.handle({}, _client({"home_timezone": "Asia/Kolkata"}))
    assert env["data"]["utc_offset"] == "+05:30"


async def test_current_time_utc_fallback_reports_gap():
    env = await time_context.handle({}, _client({}))
    assert env["data"]["timezone_source"] == "utc_fallback"
    assert env["coverage"]["gaps"]


# ------------------------------------------------------------- resolve_window

def test_resolve_window_tz_crosses_utc_midnight():
    """At 02:00 UTC it is still 'yesterday' in Chicago — the shorthand
    window must end on the Chicago date, not the UTC date."""
    frozen = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)

    with patch("tools._envelope.datetime", _FrozenDatetime):
        start_utc, end_utc = resolve_window({"days": 7})
        start_chi, end_chi = resolve_window({"days": 7}, tz=ZoneInfo("America/Chicago"))
    assert end_utc == date(2026, 7, 15)
    assert end_chi == date(2026, 7, 14)
    assert (end_chi - start_chi).days + 1 == 7


def test_resolve_window_explicit_dates_ignore_tz():
    start, end = resolve_window({"from": "2026-06-01", "to": "2026-06-30"},
                                tz=ZoneInfo("America/Chicago"))
    assert (start.isoformat(), end.isoformat()) == ("2026-06-01", "2026-06-30")


# ------------------------------------------------------------------ date_math

async def test_date_math_add_days_and_months_clamp():
    env = await date_math.handle(
        {"operation": "add", "date": "2026-01-31", "months": 1}, _client({}))
    assert env["data"]["result"] == "2026-02-28"
    env = await date_math.handle(
        {"operation": "add", "date": "2026-07-16", "days": -30}, _client({}))
    assert env["data"]["result"] == "2026-06-16"


async def test_date_math_diff_signed():
    env = await date_math.handle(
        {"operation": "diff", "date": "2026-07-16", "other_date": "2026-06-16"},
        _client({}))
    assert env["data"]["days"] == -30


async def test_date_math_weekday_and_window():
    env = await date_math.handle(
        {"operation": "weekday", "date": "2026-07-16"}, _client({}))
    assert env["data"]["weekday"] == "Thursday"
    env = await date_math.handle(
        {"operation": "window", "date": "2026-07-16", "window_days": 7}, _client({}))
    assert env["data"]["window"] == {"from": "2026-07-10", "to": "2026-07-16"}


async def test_date_math_defaults_to_user_local_today():
    env = await date_math.handle(
        {"operation": "weekday"}, _client({"home_timezone": "America/Chicago"}))
    assert "today" in env["data"]["date_source"]
    assert env["data"]["timezone"] == "America/Chicago"


async def test_date_math_rejects_bad_input():
    with pytest.raises(ValueError):
        await date_math.handle({"operation": "diff", "date": "2026-07-16"}, _client({}))
    with pytest.raises(ValueError):
        await date_math.handle({"operation": "nope"}, _client({}))
