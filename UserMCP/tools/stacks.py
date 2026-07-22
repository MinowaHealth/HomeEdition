"""
get_stacks — stack composition for stack analysis.

The ONE deliberate exception to the stack-invisibility rule (CLAUDE.md
2026-07-13): stacks are invisible to MCP unless the tool's name explicitly
contains "stack". This tool is that named exception — it exists for stack
analysis (composition review, overlap/interaction surface, schedule
sanity), not for logging. Its responses are NOT stack-stripped.
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
        name="get_stacks",
        description=(
            "Return the user's stacks (named bundles of medications/supplements "
            "taken together) with each stack's contents: item name, type, "
            "effective dose (dosage_override falling back to default_dosage/"
            "default_unit), and the timeframe (name, time of day, frequency) "
            "the stack follows. Use this for stack analysis — composition "
            "review, overlap or interaction surface, schedule sanity — not "
            "for logging intake."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max stacks to return (default 50)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default 0)",
                },
            },
        },
    )


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    params = {}
    if arguments.get("limit") is not None:
        params["limit"] = arguments["limit"]
    if arguments.get("offset") is not None:
        params["offset"] = arguments["offset"]

    gaps = []
    try:
        resp = await client.call_api("/stacks", method="GET", params=params)
    except Exception as exc:
        logger.warning(f"stacks: /stacks failed: {exc}")
        resp = {}
        gaps.append({"source": "stacks", "reason": str(exc)})

    stacks = _extract_list(resp, "stacks", "entries", "results")
    pagination = resp.get("pagination") if isinstance(resp, dict) else None

    sources = await fetch_sources(client)

    coverage = {
        "counts": {
            "rows": len(stacks),
            "sources_represented": ["manual"],
        },
        "gaps": gaps,
        "truncated": bool(
            isinstance(pagination, dict)
            and pagination.get("total", 0) > len(stacks) + int(params.get("offset", 0) or 0)
        ),
    }

    next_actions = []
    if coverage["truncated"]:
        next_actions.append({
            "tool": "get_stacks",
            "args": {"offset": len(stacks) + int(params.get("offset", 0) or 0)},
            "why": "More stacks exist beyond this page.",
        })

    return build_envelope(
        {"stacks": stacks, "pagination": pagination},
        coverage=coverage,
        sources=sources,
        next_actions=next_actions,
    )
