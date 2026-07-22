"""
save_chat_summary — persist a user-confirmed summary of this chat session
into the user's document collection ('AI Sessions' system folder).

Write tool (POST /api/v1/documents/chat-summaries). The summary becomes an
ordinary markdown document: full-text and semantic searchable via
search_my_data (scope=documents), viewable via the returned links, and
delegate-visible like any other document.

Behavioral gate: the tool description instructs the model to save only
after the user explicitly asks for it and has confirmed the text —
enforced at the prompt level, matching the send_feedback write pattern.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope
from tools._links import absolutize_links

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 200
MAX_SUMMARY_CHARS = 256 * 1024


def schema() -> Tool:
    return Tool(
        name="save_chat_summary",
        description=(
            "Save a markdown summary of this chat session into the user's "
            "document collection ('AI Sessions' folder) for safekeeping. "
            "ONLY call this after the user has explicitly asked to save a "
            "summary of the session AND has reviewed and confirmed the "
            "summary text — never call it proactively. The saved summary "
            "becomes a searchable, viewable document in their health record."
        ),
        inputSchema={
            "type": "object",
            "required": ["title", "summary_markdown"],
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short descriptive title, e.g. "
                                   "'Lab review — July 2026' (max 200 chars)",
                },
                "summary_markdown": {
                    "type": "string",
                    "description": "The user-confirmed session summary as "
                                   "markdown (max 256 KB)",
                },
                "model_id": {
                    "type": "string",
                    "description": "Model that authored the summary "
                                   "(provenance, optional)",
                },
                "source_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "MCP tools consulted during the session "
                                   "(provenance, optional)",
                },
                "session_started_at": {
                    "type": "string",
                    "description": "ISO 8601 session start time (optional)",
                },
            },
        },
    )


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    title = (arguments.get("title") or "").strip()
    summary = arguments.get("summary_markdown") or ""

    problem = None
    if not title:
        problem = "title is required"
    elif len(title) > MAX_TITLE_CHARS:
        problem = f"title must be {MAX_TITLE_CHARS} characters or fewer"
    elif not summary.strip():
        problem = "summary_markdown is required"
    elif len(summary) > MAX_SUMMARY_CHARS:
        problem = f"summary_markdown must be {MAX_SUMMARY_CHARS} characters or fewer"
    if problem:
        return build_envelope({"success": False, "error": problem})

    payload: Dict[str, Any] = {
        "title": title,
        "summary_markdown": summary,
        "created_via": "usermcp",
    }
    for key in ("model_id", "source_tools", "session_started_at"):
        if arguments.get(key):
            payload[key] = arguments[key]

    try:
        doc = await client.call_api(
            "/documents/chat-summaries", method="POST", json=payload,
        )
    except Exception as exc:
        logger.error(f"save_chat_summary: {exc}")
        return build_envelope({"success": False, "error": str(exc)})

    doc = doc if isinstance(doc, dict) else {}
    links = absolutize_links(doc.get("links"))
    data = {
        "success": True,
        "document": {
            "id": doc.get("id"),
            "title": doc.get("title"),
            "created_at": doc.get("created_at"),
            "folder": "AI Sessions",
        },
        "links": links,
    }
    next_actions = []
    if doc.get("id"):
        next_actions.append({
            "tool": "get_document",
            "args": {"document_id": doc["id"]},
            "why": "Verify the saved summary content.",
        })
    return build_envelope(data, next_actions=next_actions)
