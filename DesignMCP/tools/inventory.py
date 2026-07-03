"""userapp_inventory tool — returns the full UserApp route map.

Calls the introspection endpoint at /api/v1/_design/inventory and forwards
the result unmodified. Lets the Claude Design tool discover every endpoint
without us hand-curating MCP tool metadata.
"""
from __future__ import annotations

from typing import Any

from mcp.types import Tool


NAME = "userapp_inventory"


def schema() -> Tool:
    return Tool(
        name=NAME,
        description=(
            "List every UserApp REST endpoint with its HTTP methods, blueprint, "
            "and one-line summary. Call this first to discover the API surface, "
            "then use userapp_request to invoke specific routes. Returns a "
            "structured inventory grouped by blueprint."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )


async def handle(arguments: dict, client) -> dict[str, Any]:
    """Forward the introspection call and return the raw response."""
    return await client.request("GET", "/api/v1/_design/inventory")
