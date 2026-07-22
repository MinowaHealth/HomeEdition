"""
Adherence report tool.

Wraps `GET /api/v1/adherence`, which projects `health_inputs × timeframes ×
doses_per_day` into expected dose windows and left-joins `health_input_log`
for actuals. PRN meds and inputs with unspecified `doses_per_day` are
excluded from the percentage calculation but surfaced separately so the
caller can explain the gap.

Closes PLAN-002 at the tool-surface level.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope, resolve_window, window_block
from tools._time import home_tz
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)


def schema() -> Tool:
    return Tool(
        name="get_adherence_report",
        description=(
            "Compute adherence for active scheduled medications and supplements. "
            "For each input, returns scheduled doses (doses_per_day × days in "
            "window), logged doses, percent adherence, and a list of days where "
            "the user fell short. PRN (as-needed) inputs are excluded from the "
            "percentage and listed separately under `excluded_prn`. "
            "Dates are the user's local days; call get_current_time for today."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": (
                        "Look-back window in days (default 30, max 90). "
                        "Ignored if `from` and `to` are both set."
                    ),
                    "default": 30,
                },
                "from": {
                    "type": "string",
                    "description": "Start date YYYY-MM-DD. Takes priority over `days`.",
                },
                "to": {
                    "type": "string",
                    "description": "End date YYYY-MM-DD. Takes priority over `days`.",
                },
                "input_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of health_input UUIDs to restrict to.",
                },
            },
        },
    )




async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    # days-shorthand only: anchor 'today' in the user's home timezone
    tz = None
    if not (arguments.get("from") and arguments.get("to")):
        tz, _tz_source = await home_tz(client)
    start, end = resolve_window(arguments, tz=tz)

    # The /adherence route reads start_date/end_date (parse_date_range_params);
    # `from`/`to` were silently dropped — minowa-mcp-bug-report.md Bug 3b.
    params: Dict[str, Any] = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    input_ids = arguments.get("input_ids")
    if isinstance(input_ids, list) and input_ids:
        params["input_ids"] = ",".join(str(x) for x in input_ids)

    try:
        response = await client.call_api(
            "/adherence",
            method="GET",
            params=params,
        )
    except Exception as exc:
        logger.error(f"adherence: {exc}")
        return build_envelope(
            {"inputs": [], "excluded_prn": [], "excluded_unspecified": []},
            coverage={
                "window": window_block(start, end),
                "counts": {"rows": 0, "sources_represented": []},
                "gaps": [{"reason": str(exc)}],
                "truncated": False,
            },
            sources=await fetch_sources(client),
        )

    inputs = response.get("inputs") or []
    excluded_prn = response.get("excluded_prn") or []
    excluded_unspecified = response.get("excluded_unspecified") or []

    # Coverage for adherence is based on the number of scheduled-input rows
    # the report covers — not the raw log count, which would double-count
    # multi-dose days. The window comes from the route's response (what was
    # actually applied), falling back to the request only if absent.
    route_window = response.get("window") or {}
    coverage = {
        "window": (
            window_block(route_window["from"], route_window["to"])
            if route_window.get("from") and route_window.get("to")
            else window_block(start, end)
        ),
        "counts": {
            "rows": len(inputs),
            "sources_represented": ["manual"],  # health_input_log is manual-only
            "excluded_prn": len(excluded_prn),
            "excluded_unspecified": len(excluded_unspecified),
        },
        "gaps": [],
        "truncated": False,
    }

    # If any scheduled input sits below 50%, hint at the most under-adherent
    # one via next_actions so the LLM can dig in without being told to.
    next_actions = []
    worst = None
    for row in inputs:
        pct = row.get("pct_adherence")
        if pct is None:
            continue
        if worst is None or pct < worst.get("pct_adherence", 101):
            worst = row
    if worst and worst.get("pct_adherence", 100) < 50:
        next_actions.append({
            "tool": "get_recent_activity",
            "args": {
                "kind": "medication",
                "input_id": worst.get("input_id"),
                "from": start.isoformat(),
                "to": end.isoformat(),
            },
            "why": (
                f"{worst.get('name')} is below 50% adherence "
                f"({worst.get('pct_adherence')}%) — check log entries directly"
            ),
        })

    sources = await fetch_sources(client)
    return build_envelope(
        {
            "inputs": inputs,
            "excluded_prn": excluded_prn,
            "excluded_unspecified": excluded_unspecified,
        },
        coverage=coverage,
        sources=sources,
        next_actions=next_actions,
    )
