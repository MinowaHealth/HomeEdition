"""
get_sleep_events_detail — Garmin sleep-stage events around a point in time.

Wraps `GET /api/v1/garmin/sleep-events`, which returns every sleep-stage
interval (deep / light / rem / awake) overlapping the window — ±60 minutes
around `at` by default, widenable with `window_minutes` (max ±720) or set
explicitly with `from`/`to` (span capped at 24h). Events keep their true,
un-clipped start/end — a stage that runs past the window edge is shown in
full, since the goal is to characterize what was happening AROUND the target
time. Use it to see the sleep context of a discrete event (a symptom, a
waking, a reaction) at stage resolution.

A recent target returns only elapsed events; `coverage.truncated` reflects
that.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope
from tools._shape import as_dict
from tools._sources import fetch_sources
from tools._window import WINDOW_PROPERTIES, parse_window_args

logger = logging.getLogger(__name__)


def schema() -> Tool:
    return Tool(
        name="get_sleep_events_detail",
        description=(
            "Return Garmin sleep-stage events (deep, light, rem, awake) around "
            "a point in time. Two ways to call it: give `at` (window defaults "
            "to ±60 minutes; widen with `window_minutes`, up to ±720), or give "
            "explicit `from` and `to` bounds (span capped at 24 hours). Each "
            "event has its true start/end and duration; events overrunning "
            "the window are returned in full. Includes the stage the user was in "
            "at the target instant and a per-stage seconds rollup clipped to the "
            "window. If the window extends into the future, only elapsed events "
            "are returned. Empty when the user was awake or the night hasn't "
            "synced."
        ),
        inputSchema={
            "type": "object",
            "properties": WINDOW_PROPERTIES,
        },
    )


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    params, problem = parse_window_args(arguments)
    if problem:
        return build_envelope(
            {"target": None, "events": []},
            coverage={
                "counts": {"rows": 0, "sources_represented": []},
                "gaps": [{"reason": problem}],
                "truncated": False,
            },
            sources=await fetch_sources(client),
        )

    try:
        resp = await client.call_api(
            "/garmin/sleep-events", method="GET", params=params
        )
    except Exception as exc:
        logger.error(f"sleep_events_detail: {exc}")
        return build_envelope(
            {"target": None, "events": []},
            coverage={
                "counts": {"rows": 0, "sources_represented": []},
                "gaps": [{"reason": str(exc)}],
                "truncated": False,
            },
            sources=await fetch_sources(client),
        )

    resp_d = as_dict(resp, where="sleep_events.resp")
    events = resp_d.get("events") or []
    counts = resp_d.get("counts") or {}
    truncated_future = bool(resp_d.get("truncated_future"))

    gaps = []
    if not events:
        gaps.append({"reason": (
            "no sleep events in this window — the user was awake, or the night "
            "hasn't synced yet"
        )})
    if truncated_future:
        gaps.append({"reason": (
            "target is recent — the window extends into the future; only "
            "elapsed sleep events are returned"
        )})

    coverage = {
        "window": resp_d.get("window") or {},
        "counts": {
            "rows": counts.get("events", len(events)),
            "sources_represented": ["garmin"] if events else [],
            "by_type": counts.get("by_type", {}),
        },
        "gaps": gaps,
        "truncated": truncated_future,
    }

    data = {
        "target": resp_d.get("target"),
        "window": resp_d.get("window") or {},
        "events": events,
        "stage_at_target": resp_d.get("stage_at_target"),
        "in_window_seconds_by_type": counts.get("in_window_seconds_by_type", {}),
    }
    return build_envelope(data, coverage=coverage, sources=await fetch_sources(client))
