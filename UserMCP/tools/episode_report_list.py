"""
list_episode_reports — list saved Episode Analysis reports (metadata only).

Read tool (GET /api/v1/documents/episode-reports). Returns the report
envelope — title, analyzed window, version chain, links — never the HTML.
Fetch a specific report's narrative with get_document, or open the view
link for the rendered page.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope
from tools._links import absolutize_links

logger = logging.getLogger(__name__)


def schema() -> Tool:
    return Tool(
        name="list_episode_reports",
        description=(
            "List the user's saved Episode Analysis reports — titles, "
            "analyzed time windows, and versions, newest episode first. "
            "Use from/to to find reports whose analyzed window overlaps a "
            "time range. Superseded versions are hidden unless "
            "latest_only=false. Fetch a report's narrative text with "
            "get_document; the links.view URL renders the full HTML report."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "from": {
                    "type": "string",
                    "description": "ISO 8601 — include reports whose episode "
                                   "window overlaps [from, to] (optional)",
                },
                "to": {
                    "type": "string",
                    "description": "ISO 8601 — end of the overlap range "
                                   "(optional)",
                },
                "latest_only": {
                    "type": "boolean",
                    "description": "Hide superseded versions (default true)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max reports to return (default 50)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default 0)",
                },
            },
        },
    )


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    for key in ("from", "to", "limit", "offset"):
        if arguments.get(key) is not None:
            params[key] = arguments[key]
    if arguments.get("latest_only") is False:
        params["latest_only"] = "false"

    try:
        resp = await client.call_api(
            "/documents/episode-reports", method="GET", params=params,
        )
    except Exception as exc:
        logger.error(f"list_episode_reports: {exc}")
        return build_envelope({"success": False, "error": str(exc), "reports": []})

    resp = resp if isinstance(resp, dict) else {}
    reports = resp.get("reports") or []
    for r in reports:
        if isinstance(r, dict) and r.get("links"):
            r["links"] = absolutize_links(r["links"])

    data = {
        "success": True,
        "reports": reports,
        "pagination": resp.get("pagination"),
    }
    next_actions = []
    if reports and isinstance(reports[0], dict) and reports[0].get("id"):
        next_actions.append({
            "tool": "get_document",
            "args": {"document_id": reports[0]["id"]},
            "why": "Read the most recent report's narrative text.",
        })
    return build_envelope(data, next_actions=next_actions)
