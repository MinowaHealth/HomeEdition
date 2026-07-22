"""
get_acquisitions — supply arrival history for meds/supplements.

Exists for supply-vs-choice analysis: line acquisition events (arrival date,
amount, cost, brand, vendor) up against usage logs to tell "ran out /
couldn't restock" apart from "chose to stop taking it". The activity feed
caps at 100 merged rows; this tool carries months of per-item history.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope, extract_list as _extract_list
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)


def schema() -> Tool:
    return Tool(
        name="get_acquisitions",
        description=(
            "Return the user's medication/supplement acquisition history — "
            "dated arrival events with quantity, cost, brand, and vendor. "
            "Use this with usage logs (get_recent_activity, "
            "get_adherence_report) to distinguish supply gaps (no arrivals, "
            "usage stops) from deliberate choices (supply on hand, usage "
            "stops anyway). Filter by health_input_id and/or date range."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "health_input_id": {
                    "type": "string",
                    "description": "Optional — narrow to one catalog item (UUID).",
                },
                "from": {"type": "string", "description": "YYYY-MM-DD inclusive"},
                "to": {"type": "string", "description": "YYYY-MM-DD inclusive"},
                "limit": {"type": "integer", "description": "Max rows (default 50)"},
                "offset": {"type": "integer", "description": "Pagination offset"},
            },
        },
    )


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    params = {}
    if arguments.get("health_input_id"):
        params["health_input_id"] = arguments["health_input_id"]
    if arguments.get("from"):
        params["start_date"] = arguments["from"]
    if arguments.get("to"):
        params["end_date"] = arguments["to"]
    for key in ("limit", "offset"):
        if arguments.get(key) is not None:
            params[key] = arguments[key]

    gaps = []
    try:
        resp = await client.call_api("/acquisitions", method="GET", params=params)
    except Exception as exc:
        logger.warning(f"acquisitions: /acquisitions failed: {exc}")
        resp = {}
        gaps.append({"source": "acquisitions", "reason": str(exc)})

    entries = _extract_list(resp, "entries", "results")
    pagination = resp.get("pagination") if isinstance(resp, dict) else None

    sources = await fetch_sources(client)

    coverage = {
        "counts": {
            "rows": len(entries),
            "sources_represented": ["manual"],
        },
        "gaps": gaps,
        "truncated": bool(
            isinstance(pagination, dict)
            and pagination.get("total", 0) > len(entries) + int(params.get("offset", 0) or 0)
        ),
    }

    next_actions = []
    if coverage["truncated"]:
        next_actions.append({
            "tool": "get_acquisitions",
            "args": {**arguments, "offset": len(entries) + int(params.get("offset", 0) or 0)},
            "why": "More acquisition history exists beyond this page.",
        })

    return build_envelope(
        {"acquisitions": entries, "pagination": pagination},
        coverage=coverage,
        sources=sources,
        next_actions=next_actions,
    )
