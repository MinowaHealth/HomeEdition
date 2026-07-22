"""
get_current_time — authoritative clock + user-local date context.

The LLM has no reliable clock: it guesses "today" from its training-time
sense of UTC and infers the user's timezone. This tool answers both from
the server, including copy-paste-ready from/to window pairs so the model
never does its own date arithmetic (MCP/TimeCapabilities-Plan1.md).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope
from tools._time import home_tz


def schema() -> Tool:
    return Tool(
        name="get_current_time",
        description=(
            "Return the authoritative current time: UTC instant, the user's "
            "home timezone, their local date/time, weekday, and ready-made "
            "from/to pairs for the last 7/30/90 days. ALWAYS call this before "
            "any date reasoning ('today', 'yesterday', 'this week', 'last "
            "month') instead of inferring dates yourself. For other date "
            "arithmetic use date_math."
        ),
        inputSchema={"type": "object", "properties": {}},
    )


def _window(today, n: int) -> Dict[str, str]:
    return {"from": (today - timedelta(days=n - 1)).isoformat(),
            "to": today.isoformat()}


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    tz, source = await home_tz(client)
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)
    today = now_local.date()

    offset = now_local.utcoffset()
    total_min = int(offset.total_seconds() // 60)
    sign = "+" if total_min >= 0 else "-"
    offset_str = f"{sign}{abs(total_min) // 60:02d}:{abs(total_min) % 60:02d}"

    data = {
        "utc": now_utc.isoformat(timespec="seconds"),
        "timezone": str(tz.key if hasattr(tz, "key") else tz),
        "timezone_source": source,
        "utc_offset": offset_str,
        "local": now_local.isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "weekday": now_local.strftime("%A"),
        "yesterday": (today - timedelta(days=1)).isoformat(),
        "common_windows": {
            "last_7_days": _window(today, 7),
            "last_30_days": _window(today, 30),
            "last_90_days": _window(today, 90),
        },
    }
    coverage = {
        "counts": {"rows": 1, "sources_represented": []},
        "gaps": ([] if source == "profile" else
                 [{"source": "profile", "reason":
                   "home_timezone not set — times shown in UTC"}]),
        "truncated": False,
    }
    return build_envelope(data, coverage=coverage, sources=[])
