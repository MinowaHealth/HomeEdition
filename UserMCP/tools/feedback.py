"""
Feedback tool for UserMCP

Allows MCP consumers (e.g. Claude Desktop) to submit feedback about
the MCP server's behavior. Routes to POST /api/v1/feedback which
stores to the database and posts to Slack.

Retained from the pre-0.5 tool surface. Now registered through the
tools registry and returns an envelope-wrapped response.
"""

import logging
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope

logger = logging.getLogger(__name__)


async def send_feedback(
    api_client: Any,
    content: str,
    feedback_type: str = "general",
) -> Dict[str, Any]:
    """Submit feedback. Returns an unwrapped result dict.

    Preserved for callers that import this function directly (tests,
    scripts). The MCP-facing wrapper is `handle()` below.
    """
    valid_types = ("bug", "feature", "general", "praise")
    if feedback_type not in valid_types:
        feedback_type = "general"

    try:
        response = await api_client.call_api(
            "/feedback",
            method="POST",
            json={
                "content": content,
                "feedback_type": feedback_type,
                "page_context": "mcp",
                "app_version": "usermcp-0.5.0",
                "source_app": "UserMCP",
            },
        )
        return {
            "success": True,
            "message": "Feedback submitted. Thank you!",
            "id": response.get("id"),
        }
    except Exception as e:
        logger.error(f"Error submitting feedback: {e}")
        return {
            "success": False,
            "error": str(e),
        }


def schema() -> Tool:
    return Tool(
        name="send_feedback",
        description=(
            "Submit feedback about the MCP server's behavior to the SeenWhole team. "
            "Use this when the user reports a problem with data quality, missing "
            "information, incorrect results, or has a feature request."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Feedback text describing the issue or suggestion",
                },
                "feedback_type": {
                    "type": "string",
                    "enum": ["bug", "feature", "general", "praise"],
                    "description": "Category: bug (broken), feature (request), general, praise",
                },
            },
            "required": ["content", "feedback_type"],
        },
    )


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    content = arguments.get("content")
    if not content:
        return build_envelope({"success": False, "error": "content is required"})

    feedback_type = arguments.get("feedback_type", "general")
    result = await send_feedback(client, content, feedback_type)
    return build_envelope(result)
