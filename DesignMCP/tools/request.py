"""userapp_request tool — generic proxy to any UserApp /api/v[12]/ endpoint.

Lets the Claude Design tool make arbitrary REST calls against UserApp using
the fixed rodrigo demo identity. Safety rails:

  * Path must match ^/api/v[12]/ — internal routes (/login, /metrics,
    /admin) are not reachable through this tool.
  * Method must be one of GET, POST, PUT, PATCH, DELETE.
  * No transformation of the response — Design sees the raw API contract
    (status code, headers, JSON body).
"""
from __future__ import annotations

import re
from typing import Any

from mcp.types import Tool


NAME = "userapp_request"

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_PATH_PATTERN = re.compile(r"^/api/v[12]/")


def schema() -> Tool:
    return Tool(
        name=NAME,
        description=(
            "Make a REST request to any UserApp /api/v1/* or /api/v2/* endpoint "
            "as the rodrigo@borgia.family demo user. Use userapp_inventory first "
            "to discover paths and methods. Returns the raw HTTP response: "
            "status_code, headers, and parsed JSON body."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": sorted(_ALLOWED_METHODS),
                    "description": "HTTP method.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Request path beginning with /api/v1/ or /api/v2/. "
                        "Example: /api/v1/blood-pressure"
                    ),
                },
                "query": {
                    "type": "object",
                    "description": "Optional query-string parameters.",
                    "additionalProperties": True,
                },
                "body": {
                    "type": "object",
                    "description": "Optional JSON request body (POST/PUT/PATCH).",
                    "additionalProperties": True,
                },
                "headers": {
                    "type": "object",
                    "description": "Optional extra request headers.",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["method", "path"],
            "additionalProperties": False,
        },
    )


def _validate(method: str, path: str) -> str | None:
    """Return an error message if the request is unsafe, else None."""
    if method.upper() not in _ALLOWED_METHODS:
        return f"Method {method!r} not allowed. Use one of: {sorted(_ALLOWED_METHODS)}."
    if not _PATH_PATTERN.match(path):
        return (
            f"Path {path!r} is not under /api/v1/ or /api/v2/. "
            "DesignMCP only exposes versioned API routes; internal endpoints "
            "(/login, /metrics, /admin) are not reachable."
        )
    return None


async def handle(arguments: dict, client) -> dict[str, Any]:
    method = arguments.get("method", "")
    path = arguments.get("path", "")

    error = _validate(method, path)
    if error:
        return {"status_code": 0, "error": error, "json": None, "headers": {}}

    return await client.request(
        method=method,
        path=path,
        params=arguments.get("query"),
        json=arguments.get("body"),
        headers=arguments.get("headers"),
    )
