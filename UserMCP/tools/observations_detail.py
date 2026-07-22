"""
get_observations_detail — user observations around a point in time.

Wraps `GET /api/v1/observations/detail`, which returns every observation
(free-text note, symptom, logged event) whose observed_at falls in
[at − 60min, at + 60min]. Parallels the Garmin minute-detail and sleep-events
tools: same ±60-minute window, same `coverage.truncated` semantics for a
recent target. Use it to see what the user recorded AROUND a discrete event
(a reaction, a symptom onset, a reading) at their own note resolution.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope
from tools._shape import as_dict
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)


def schema() -> Tool:
    return Tool(
        name="get_observations_detail",
        description=(
            "Return the user's observations (free-text notes, symptoms, logged "
            "events) for the hour before and hour after a specific point in time "
            "(±60 minutes). Each observation carries its timestamp, text, "
            "category, severity, mental-health flag, tags, and signed offset "
            "from the target instant (negative = before). If the point in time "
            "is recent, only elapsed observations are returned. Empty when "
            "nothing was recorded in the window."
        ),
        inputSchema={
            "type": "object",
            "required": ["at"],
            "properties": {
                "at": {
                    "type": "string",
                    "description": (
                        "The point in time, ISO 8601. Offset-aware values "
                        "(...-07:00 or ...Z) are honored; a value with no "
                        "offset is read in the user's home timezone."
                    ),
                },
            },
        },
    )


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    at = (arguments.get("at") or "").strip()
    if not at:
        return build_envelope(
            {"target": None, "observations": []},
            coverage={
                "counts": {"rows": 0, "sources_represented": []},
                "gaps": [{"reason": "at (ISO 8601 timestamp) is required"}],
                "truncated": False,
            },
            sources=await fetch_sources(client),
        )

    try:
        resp = await client.call_api(
            "/observations/detail", method="GET", params={"at": at}
        )
    except Exception as exc:
        logger.error(f"observations_detail: {exc}")
        return build_envelope(
            {"target": None, "observations": []},
            coverage={
                "counts": {"rows": 0, "sources_represented": []},
                "gaps": [{"reason": str(exc)}],
                "truncated": False,
            },
            sources=await fetch_sources(client),
        )

    resp_d = as_dict(resp, where="observations_detail.resp")
    observations = resp_d.get("observations") or []
    counts = resp_d.get("counts") or {}
    truncated_future = bool(resp_d.get("truncated_future"))

    gaps = []
    if not observations:
        gaps.append({"reason": "no observations recorded in this window"})
    if truncated_future:
        gaps.append({"reason": (
            "target is recent — the window extends into the future; only "
            "elapsed observations are returned"
        )})

    coverage = {
        "window": resp_d.get("window") or {},
        "counts": {
            "rows": counts.get("observations", len(observations)),
            "sources_represented": ["observations"] if observations else [],
            "by_category": counts.get("by_category", {}),
        },
        "gaps": gaps,
        "truncated": truncated_future,
    }

    data = {
        "target": resp_d.get("target"),
        "window": resp_d.get("window") or {},
        "observations": observations,
    }
    return build_envelope(data, coverage=coverage, sources=await fetch_sources(client))
