"""
save_episode_report — persist a completed Episode Analysis report (the
single-page HTML artifact plus its narrative text) into the user's document
collection ('Episode Reports' system folder).

Write tool (POST /api/v1/documents/episode-reports). The report becomes an
immutable document: the narrative is full-text and semantically searchable
via search_my_data (scope=documents), the HTML renders via the returned
view link, and re-analysis of the same episode saves a NEW report that
supersedes the old one (supersedes_document_id) rather than editing it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope
from tools._links import absolutize_links

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 200
MAX_REPORT_HTML_CHARS = 2 * 1024 * 1024
MAX_NARRATIVE_CHARS = 256 * 1024


def schema() -> Tool:
    return Tool(
        name="save_episode_report",
        description=(
            "Save a completed Episode Analysis report into the user's "
            "document collection ('Episode Reports' folder). Call this after "
            "the report HTML has been generated and the user has confirmed "
            "they want it kept — never proactively. Pass the full "
            "self-contained HTML plus the narrative as plain text (lead, "
            "narrative paragraphs, verbatim observations, caveats) so the "
            "report is searchable. If this run supersedes an earlier version "
            "of the same episode, pass that report's document id."
        ),
        inputSchema={
            "type": "object",
            "required": ["title", "report_html", "narrative_text",
                         "episode_start", "episode_end"],
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Report title, e.g. 'Overnight — "
                                   "2026-07-19 · 1:21–4:52 AM' (max 200 chars)",
                },
                "report_html": {
                    "type": "string",
                    "description": "The complete self-contained single-page "
                                   "HTML report (max 2 MB)",
                },
                "narrative_text": {
                    "type": "string",
                    "description": "Plain-text narrative: lead, narrative "
                                   "paragraphs, verbatim observations, and "
                                   "caveats (max 256 KB). Drives search.",
                },
                "episode_start": {
                    "type": "string",
                    "description": "ISO 8601 start of the analyzed episode "
                                   "window (unpadded)",
                },
                "episode_end": {
                    "type": "string",
                    "description": "ISO 8601 end of the analyzed episode "
                                   "window (unpadded)",
                },
                "version": {
                    "type": "integer",
                    "description": "Report version number (default 1; bump "
                                   "when superseding)",
                },
                "supersedes_document_id": {
                    "type": "string",
                    "description": "Document id of the earlier version this "
                                   "report replaces (optional)",
                },
                "annotations": {
                    "type": "object",
                    "description": "Structured annotations: spans (e.g. "
                                   "thumpy periods), events, caveats, "
                                   "discarded_readings (optional)",
                },
                "model_id": {
                    "type": "string",
                    "description": "Model that authored the report "
                                   "(provenance, optional)",
                },
                "source_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "MCP tools consulted while building the "
                                   "report (provenance, optional)",
                },
            },
        },
    )


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    title = (arguments.get("title") or "").strip()
    report_html = arguments.get("report_html") or ""
    narrative = arguments.get("narrative_text") or ""

    problem = None
    if not title:
        problem = "title is required"
    elif len(title) > MAX_TITLE_CHARS:
        problem = f"title must be {MAX_TITLE_CHARS} characters or fewer"
    elif not report_html.strip():
        problem = "report_html is required"
    elif len(report_html) > MAX_REPORT_HTML_CHARS:
        problem = f"report_html must be {MAX_REPORT_HTML_CHARS} characters or fewer"
    elif not narrative.strip():
        problem = "narrative_text is required"
    elif len(narrative) > MAX_NARRATIVE_CHARS:
        problem = f"narrative_text must be {MAX_NARRATIVE_CHARS} characters or fewer"
    elif not arguments.get("episode_start") or not arguments.get("episode_end"):
        problem = "episode_start and episode_end are required"
    if problem:
        return build_envelope({"success": False, "error": problem})

    payload: Dict[str, Any] = {
        "title": title,
        "report_html": report_html,
        "narrative_text": narrative,
        "episode_start": arguments["episode_start"],
        "episode_end": arguments["episode_end"],
        "created_via": "usermcp",
    }
    for key in ("version", "supersedes_document_id", "annotations",
                "model_id", "source_tools"):
        if arguments.get(key):
            payload[key] = arguments[key]

    try:
        doc = await client.call_api(
            "/documents/episode-reports", method="POST", json=payload,
        )
    except Exception as exc:
        logger.error(f"save_episode_report: {exc}")
        return build_envelope({"success": False, "error": str(exc)})

    doc = doc if isinstance(doc, dict) else {}
    links = absolutize_links(doc.get("links"))
    data = {
        "success": True,
        "document": {
            "id": doc.get("id"),
            "title": doc.get("title"),
            "created_at": doc.get("created_at"),
            "episode_start": doc.get("episode_start"),
            "episode_end": doc.get("episode_end"),
            "version": doc.get("version"),
            "folder": "Episode Reports",
        },
        "links": links,
    }
    next_actions = []
    if doc.get("id"):
        next_actions.append({
            "tool": "list_episode_reports",
            "args": {},
            "why": "Confirm the report appears in the collection.",
        })
    return build_envelope(data, next_actions=next_actions)
