"""
date_math — server-side date arithmetic so the LLM never computes dates.

Operations (all in the user's home timezone when `date` is omitted):
  add      — date ± days/weeks/months (month math clamps the day: Jan 31
             + 1 month = Feb 28/29)
  diff     — whole days between two dates (signed: other_date - date)
  weekday  — weekday name of a date
  window   — closed N-day interval ending at date (from = date - N + 1)
"""
from __future__ import annotations

import calendar
from datetime import date as date_t, datetime, timedelta, timezone
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope
from tools._time import home_tz

_OPS = ("add", "diff", "weekday", "window")


def schema() -> Tool:
    return Tool(
        name="date_math",
        description=(
            "Perform exact date arithmetic server-side — use this instead of "
            "computing dates yourself. Operations: 'add' (date ± days/weeks/"
            "months), 'diff' (days between two dates), 'weekday' (day name), "
            "'window' (from/to pair for an N-day interval ending at date). "
            "Omit 'date' to use today in the user's home timezone. For the "
            "current time and common lookback windows, use get_current_time."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": list(_OPS)},
                "date": {"type": "string",
                         "description": "YYYY-MM-DD; default today (user's timezone)"},
                "days": {"type": "integer", "description": "add: days to add (may be negative)"},
                "weeks": {"type": "integer", "description": "add: weeks to add (may be negative)"},
                "months": {"type": "integer",
                           "description": "add: calendar months to add (may be negative; day clamps)"},
                "other_date": {"type": "string", "description": "diff: the second date (YYYY-MM-DD)"},
                "window_days": {"type": "integer",
                                "description": "window: interval length in days (>=1)"},
            },
            "required": ["operation"],
        },
    )


def _parse(s: str, field: str) -> date_t:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be YYYY-MM-DD, got {s!r}")


def _add_months(d: date_t, months: int) -> date_t:
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date_t(year, month, day)


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    op = arguments.get("operation")
    if op not in _OPS:
        raise ValueError(f"operation must be one of {_OPS}")

    tz, tz_source = await home_tz(client)
    if arguments.get("date"):
        base = _parse(arguments["date"], "date")
        base_source = "argument"
    else:
        base = datetime.now(timezone.utc).astimezone(tz).date()
        base_source = f"today ({tz_source})"

    result: Dict[str, Any] = {
        "operation": op,
        "date": base.isoformat(),
        "date_source": base_source,
        "timezone": str(getattr(tz, "key", tz)),
    }

    if op == "add":
        out = base + timedelta(days=int(arguments.get("days") or 0),
                               weeks=int(arguments.get("weeks") or 0))
        months = int(arguments.get("months") or 0)
        if months:
            out = _add_months(out, months)
        result["result"] = out.isoformat()
        result["result_weekday"] = out.strftime("%A")
    elif op == "diff":
        if not arguments.get("other_date"):
            raise ValueError("diff requires other_date")
        other = _parse(arguments["other_date"], "other_date")
        result["other_date"] = other.isoformat()
        result["days"] = (other - base).days
    elif op == "weekday":
        result["weekday"] = base.strftime("%A")
    elif op == "window":
        n = int(arguments.get("window_days") or 0)
        if n < 1:
            raise ValueError("window requires window_days >= 1")
        result["window"] = {"from": (base - timedelta(days=n - 1)).isoformat(),
                            "to": base.isoformat()}

    coverage = {"counts": {"rows": 1, "sources_represented": []},
                "gaps": [], "truncated": False}
    return build_envelope(result, coverage=coverage, sources=[])
