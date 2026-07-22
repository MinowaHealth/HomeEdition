"""
get_garmin_minute_detail — per-minute Garmin HR / respiration / stress around
a single point in time.

Wraps `GET /api/v1/garmin/minute-detail`, which returns one row per minute in
the window — ±60 minutes around `at` by default, widenable with
`window_minutes` (max ±720) or set explicitly with `from`/`to` (span capped
at 24h) — with heart_rate, respiratory_rate, and stress (null where a series
had no sample that minute). Use it to see what the wearable
recorded around a specific event — a symptom onset, a medication dose, a
reaction — at minute resolution rather than the daily rollups the other
wearable tools return.

A recent target returns only elapsed minutes (the forward half of the window
is still in the future); `coverage.truncated` reflects that.
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
        name="get_garmin_minute_detail",
        description=(
            "Return per-minute Garmin heart rate, respiratory rate, and stress "
            "around a point in time. Two ways to call it: give `at` (window "
            "defaults to ±60 minutes; widen with `window_minutes`, up to ±720), "
            "or give explicit `from` and `to` bounds (span capped at 24 hours). "
            "Use this to correlate the wearable's readings with a discrete "
            "event (symptom onset, a dose, a reaction). Each minute carries "
            "the three values, null where that series had no sample. If the "
            "window extends into the future, only elapsed minutes are returned."
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
            {"target": None, "samples": []},
            coverage={
                "counts": {"rows": 0, "sources_represented": []},
                "gaps": [{"reason": problem}],
                "truncated": False,
            },
            sources=await fetch_sources(client),
        )

    try:
        resp = await client.call_api(
            "/garmin/minute-detail", method="GET", params=params
        )
    except Exception as exc:
        logger.error(f"garmin_minute_detail: {exc}")
        return build_envelope(
            {"target": None, "samples": []},
            coverage={
                "counts": {"rows": 0, "sources_represented": []},
                "gaps": [{"reason": str(exc)}],
                "truncated": False,
            },
            sources=await fetch_sources(client),
        )

    resp_d = as_dict(resp, where="garmin_detail.resp")
    samples = resp_d.get("samples") or []
    counts = resp_d.get("counts") or {}
    truncated_future = bool(resp_d.get("truncated_future"))

    gaps = []
    if not samples:
        gaps.append({"reason": (
            "no Garmin per-minute data in this window — the watch may not have "
            "been worn, or the window hasn't synced yet"
        )})
    if truncated_future:
        gaps.append({"reason": (
            "target is recent — the window extends into the future; only "
            "elapsed minutes are returned"
        )})

    coverage = {
        # Minute-granular window straight from the route (authoritative).
        "window": resp_d.get("window") or {},
        "counts": {
            "rows": counts.get("minutes", len(samples)),
            "sources_represented": ["garmin"] if samples else [],
            "heart_rate": counts.get("heart_rate", 0),
            "respiratory_rate": counts.get("respiratory_rate", 0),
            "stress": counts.get("stress", 0),
        },
        "gaps": gaps,
        "truncated": truncated_future,
    }

    data = {
        "target": resp_d.get("target"),
        "window": resp_d.get("window") or {},
        "samples": samples,
    }
    return build_envelope(data, coverage=coverage, sources=await fetch_sources(client))
