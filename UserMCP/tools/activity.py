"""
get_recent_activity — unified event stream of med logs, food logs, observations.

Wraps `/all-logs` which already returns a merged feed. Supports `kind`
narrowing (medication | food | observation | all) and an optional
`input_id` filter so the adherence tool can deep-link to the timeline
of a specific under-adhered input.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope, resolve_window, window_block
from tools._time import home_tz
from tools._links import absolutize_links
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)

_MAX_DAYS = 90
_DEFAULT_DAYS = 14


def schema() -> Tool:
    return Tool(
        name="get_recent_activity",
        description=(
            "Return the user's recent activity feed: medication/supplement "
            "logs, food logs, observations, data-source sync events "
            "(Garmin, HealthKit), document arrivals (uploads, faxes, "
            "saved AI session summaries), and supply acquisitions (new "
            "med/supplement arrivals), in a single chronological stream. "
            "Filter by kind or by input_id. Default window is 14 days (max 90). "
            "Dates are the user's local days; call get_current_time for today."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": _DEFAULT_DAYS},
                "from": {"type": "string"},
                "to": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["all", "medication", "food", "observation", "sync",
                             "document", "acquisition"],
                    "default": "all",
                },
                "input_id": {
                    "type": "string",
                    "description": "Optional — narrow to a specific health_input UUID.",
                },
                "limit": {"type": "integer", "default": 50},
            },
        },
    )


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    # days-shorthand only: anchor 'today' in the user's home timezone
    tz = None
    if not (arguments.get("from") and arguments.get("to")):
        tz, _tz_source = await home_tz(client)
    start, end = resolve_window(arguments, tz=tz, default_days=_DEFAULT_DAYS, max_days=_MAX_DAYS)
    kind = (arguments.get("kind") or "all").lower()
    input_id = arguments.get("input_id")
    limit = max(1, min(200, int(arguments.get("limit", 50) or 50)))

    params: Dict[str, Any] = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "limit": limit,
    }
    if kind != "all":
        params["kind"] = kind
    if input_id:
        params["input_id"] = input_id

    try:
        resp = await client.call_api("/all-logs", method="GET", params=params)
    except Exception as exc:
        logger.error(f"activity: {exc}")
        return build_envelope(
            [],
            coverage={
                "window": window_block(start, end),
                "counts": {"rows": 0, "sources_represented": []},
                "gaps": [{"reason": str(exc)}],
                "truncated": False,
            },
            sources=await fetch_sources(client),
        )

    # /all-logs may return a plain list or a pagination envelope
    if isinstance(resp, list):
        events = resp
        pagination = None
    elif isinstance(resp, dict):
        events = resp.get("entries") or resp.get("events") or resp.get("results") or []
        pagination = resp.get("pagination")
    else:
        events = []
        pagination = None

    # Stack-invisibility rule (CLAUDE.md): /all-logs stamps a `stack` label on
    # med events ('PRN' when NULL, which mislabels individually-logged scheduled
    # meds). MCP treats log items as first-order objects — drop the field.
    events = [
        {k: v for k, v in e.items() if k != "stack"} if isinstance(e, dict) else e
        for e in events
    ]
    for e in events:
        if isinstance(e, dict) and e.get("links"):
            e["links"] = absolutize_links(e["links"])

    truncated = bool(pagination and pagination.get("has_more"))

    # Coverage honesty: trust the server's `applied` echo, not our request.
    # A filter we asked for but the server didn't apply becomes a gap.
    applied = resp.get("applied") if isinstance(resp, dict) else None
    gaps = []
    if not events:
        gaps.append({"reason": "no activity in window"})
    if applied is None:
        gaps.append({"reason": "server did not confirm which filters were applied"})
    else:
        if applied.get("start_date") != start.isoformat() or applied.get("end_date") != end.isoformat():
            gaps.append({"reason": "date window not applied by server"})
        if kind != "all" and applied.get("kind") != kind:
            gaps.append({"reason": f"kind filter '{kind}' not applied — results include all types"})
        if input_id and applied.get("input_id") != input_id:
            gaps.append({"reason": "input_id filter not applied by server"})
        truncated_sources = applied.get("sources_truncated") or []
        if truncated_sources:
            truncated = True
            gaps.append({
                "reason": "window may be incomplete for "
                + ", ".join(truncated_sources)
                + " — narrow the date range"
            })

    coverage = {
        "window": window_block(start, end),
        "counts": {
            "rows": len(events),
            "sources_represented": ["manual"] if events else [],
        },
        "gaps": gaps,
        "truncated": truncated,
    }

    sources = await fetch_sources(client)
    return build_envelope(events, coverage=coverage, sources=sources)
