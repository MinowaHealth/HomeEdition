"""
search_my_data — semantic-first search over the user's observations, notes,
inputs, conditions, allergies, foods, and documents.

Thin wrapper around `GET /api/v1/search`. Adds the standard envelope,
surfaces the fallback mode (semantic vs. keyword) in coverage, and
recommends next_actions when the top hit is a document or observation
the LLM might want to fetch in full.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope
from tools._shape import as_dict
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)


_ALLOWED_SCOPES = {
    "all", "observations", "inputs",
    "conditions", "allergies", "food", "documents", "notes",
}


def schema() -> Tool:
    return Tool(
        name="search_my_data",
        description=(
            "Semantic-first search across the user's own records: "
            "observations, medications/supplements, "
            "conditions, allergies, food items, and document annotations. "
            "If the embedding service is unavailable the endpoint falls "
            "back to keyword search and `coverage.mode` reports which path "
            "was used."
        ),
        inputSchema={
            "type": "object",
            "required": ["q"],
            "properties": {
                "q": {"type": "string", "description": "Query text (1..500 chars)."},
                "scope": {
                    "type": "string",
                    "enum": sorted(_ALLOWED_SCOPES),
                    "default": "all",
                },
                "k": {"type": "integer", "default": 5, "description": "Top-K (1..25)"},
                "from": {"type": "string", "description": "YYYY-MM-DD lower bound"},
                "to": {"type": "string", "description": "YYYY-MM-DD upper bound"},
            },
        },
    )


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    q = (arguments.get("q") or "").strip()
    if not q:
        return build_envelope(
            [],
            coverage={
                "counts": {"rows": 0, "sources_represented": []},
                "gaps": [{"reason": "q is required"}],
                "truncated": False,
            },
            sources=await fetch_sources(client),
        )

    scope = (arguments.get("scope") or "all").lower()
    if scope not in _ALLOWED_SCOPES:
        scope = "all"

    params: Dict[str, Any] = {"q": q, "scope": scope}
    if arguments.get("k"):
        params["k"] = int(arguments["k"])
    if arguments.get("from"):
        params["from"] = arguments["from"]
    if arguments.get("to"):
        params["to"] = arguments["to"]

    try:
        resp = await client.call_api("/search", method="GET", params=params)
    except Exception as exc:
        logger.error(f"search: {exc}")
        return build_envelope(
            [],
            coverage={
                "counts": {"rows": 0, "sources_represented": []},
                "gaps": [{"reason": str(exc)}],
                "truncated": False,
            },
            sources=await fetch_sources(client),
        )

    resp_d = as_dict(resp, where="search.resp")
    results = resp_d.get("results") or []
    mode = resp_d.get("mode") or "unknown"

    # Represented source buckets: everything in the search endpoint is
    # manually-entered (observations, inputs, notes, allergies) rather
    # than wearable-sourced, so the envelope always reports `manual`.
    coverage = {
        "counts": {
            "rows": len(results),
            "sources_represented": ["manual"] if results else [],
        },
        "gaps": [] if results else [{"reason": f"no matches for '{q}' in scope '{scope}'"}],
        "truncated": False,
        "mode": mode,
    }

    next_actions = []
    # If the top hit is a document annotation, suggest fetching the full doc.
    if results and results[0].get("table") == "document_annotations":
        next_actions.append({
            "tool": "get_document",
            "args": {"document_id": results[0].get("id")},
            "why": "Top match is a document annotation — pull the full document for context.",
        })

    sources = await fetch_sources(client)
    return build_envelope(results, coverage=coverage, sources=sources, next_actions=next_actions)
