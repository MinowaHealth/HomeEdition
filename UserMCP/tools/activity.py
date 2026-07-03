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

from tools._envelope import build_envelope, window_block
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)

_MAX_DAYS = 90
_DEFAULT_DAYS = 14


def schema() -> Tool:
    return Tool(
        name="get_recent_activity",
        description=(
            "Return the user's recent activity feed: medication/supplement "
            "logs, food logs, and observations, in a single chronological "
            "stream. Filter by kind or by input_id. Default window is 14 days "
            "(max 90)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": _DEFAULT_DAYS},
                "from": {"type": "string"},
                "to": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["all", "medication", "food", "observation"],
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


def _resolve_window(arguments: Dict[str, Any]) -> tuple[date, date]:
    from_str = arguments.get("from")
    to_str = arguments.get("to")
    if from_str and to_str:
        start = datetime.strptime(from_str, "%Y-%m-%d").date()
        end = datetime.strptime(to_str, "%Y-%m-%d").date()
    else:
        days = int(arguments.get("days", _DEFAULT_DAYS) or _DEFAULT_DAYS)
        days = max(1, min(_MAX_DAYS, days))
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days - 1)
    if (end - start).days + 1 > _MAX_DAYS:
        start = end - timedelta(days=_MAX_DAYS - 1)
    return start, end


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    start, end = _resolve_window(arguments)
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

    truncated = bool(pagination and pagination.get("has_more"))

    coverage = {
        "window": window_block(start, end),
        "counts": {
            "rows": len(events),
            "sources_represented": ["manual"] if events else [],
        },
        "gaps": [] if events else [{"reason": "no activity in window"}],
        "truncated": truncated,
    }

    sources = await fetch_sources(client)
    return build_envelope(events, coverage=coverage, sources=sources)
