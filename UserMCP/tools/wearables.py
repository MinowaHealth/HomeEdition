"""
get_wearable_summary — Garmin + HealthKit aggregate over a window.

Closes the wearable-data P0 from qualitative testing: the old
`snapshot.wearable_data` returned empty rows with no explanation when
Garmin wasn't connected or synced.
This tool surfaces:
  - whether each source is connected + when it last synced
  - daily aggregates (steps, sleep, stress, resting HR, SpO2)
  - explicit gaps when Garmin is disconnected or HealthKit hasn't imported yet

Uses the `/dashboard` endpoint's wearable block for the daily rollup, which
is pre-aggregated server-side so even a 90-day window stays small.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope, window_block
from tools._shape import as_dict, as_list
from tools._sources import fetch_sources
from tools.adherence import _resolve_window as _reuse_window  # same 1..90 window rules

logger = logging.getLogger(__name__)


def schema() -> Tool:
    return Tool(
        name="get_wearable_summary",
        description=(
            "Return a summary of wearable (Garmin) and HealthKit data over a "
            "window. Includes daily aggregates (steps, sleep, stress, resting "
            "heart rate, SpO2), connection status for each source, and "
            "explicit gaps when a source isn't connected or hasn't synced "
            "yet. Use this for wearable-related questions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 30},
                "from": {"type": "string"},
                "to": {"type": "string"},
            },
        },
    )


async def _safe(client: Any, path: str, **kwargs) -> Any:
    try:
        return await client.call_api(path, **kwargs)
    except Exception as exc:
        logger.warning(f"wearables: {path} failed: {exc}")
        return {"_error": str(exc)}


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    start, end = _reuse_window(arguments)
    days = (end - start).days + 1

    dashboard_r, garmin_r, hkit_r, sources = await asyncio.gather(
        _safe(client, "/dashboard", method="GET", params={"days": days}),
        _safe(client, "/garmin/status", method="GET"),
        _safe(client, "/healthkit/jobs", method="GET"),
        fetch_sources(client),
    )

    gaps = []

    dashboard_d = as_dict(dashboard_r, where="wearables.dashboard")
    if dashboard_d.get("_error"):
        wearable_block = {}
        gaps.append({"source": "dashboard", "reason": dashboard_d["_error"]})
    else:
        wearable_block = as_dict(dashboard_d.get("wearable", {}), where="wearables.dashboard.wearable")

    garmin_d = as_dict(garmin_r, where="wearables.garmin")
    garmin_connected = bool(garmin_d.get("connected"))
    garmin_last_sync = garmin_d.get("last_sync") or garmin_d.get("last_synced_at")
    if not garmin_connected:
        gaps.append({
            "source": "garmin",
            "reason": "Garmin not connected — wearable rollups reflect HealthKit only.",
        })

    hkit_d = as_dict(hkit_r, where="wearables.hkit")
    hkit_jobs = as_list(
        hkit_d.get("jobs") or hkit_d.get("entries") or [],
        where="wearables.hkit.jobs",
    )
    hkit_latest = None
    if hkit_jobs:
        # Prefer completed_at of first row
        hkit_latest = hkit_jobs[0].get("completed_at") or hkit_jobs[0].get("created_at")
    if not hkit_jobs:
        gaps.append({
            "source": "healthkit",
            "reason": "No HealthKit import jobs on record — mobile sync may not be active.",
        })

    data = {
        "window": window_block(start, end),
        "rollup": wearable_block,
        "connections": {
            "garmin": {
                "connected": garmin_connected,
                "last_sync": garmin_last_sync,
            },
            "healthkit": {
                "last_import": hkit_latest,
                "job_count": len(hkit_jobs),
            },
        },
    }

    counts = {
        "rows": int(wearable_block.get("days_available") or 0),
        "sources_represented": [],
    }
    if garmin_connected and wearable_block.get("days_available"):
        counts["sources_represented"].append("garmin")
    if hkit_latest:
        counts["sources_represented"].append("healthkit")

    next_actions = []
    if not garmin_connected:
        next_actions.append({
            "tool": "get_my_profile",
            "args": {},
            "why": (
                "Garmin isn't connected — if the user mentions their watch, "
                "suggest completing the Garmin OAuth flow in settings."
            ),
        })

    coverage = {
        "window": window_block(start, end),
        "counts": counts,
        "gaps": gaps,
        "truncated": False,
    }

    return build_envelope(data, coverage=coverage, sources=sources, next_actions=next_actions)
