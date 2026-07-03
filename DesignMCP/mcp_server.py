#!/usr/bin/env python3
"""DesignMCP — MCP server exposing the UserApp REST contract to Claude Design.

Two tools (userapp_inventory, userapp_request) backed by a single shared
httpx client carrying the fixed rodrigo@borgia.family demo API key. There is
no per-request auth — the audience is the Claude Design tool, which has no
way to inject user-scoped credentials. The whole service is bound to one
synthetic identity on tenant=1 and never touches PHI.

Environment variables:
  USERAPP_BASE_URL   UserApp endpoint (default http://localhost)
  USERAPP_API_KEY    hbk_* token for the demo identity. REQUIRED.
  MCP_PORT           Port to listen on (default 33282)
  MCP_HOST           Bind address (default 127.0.0.1; 0.0.0.0 in container)
  UVICORN_LOG_LEVEL  critical|error|warning|info|debug|trace (default info)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.sse import SseServerTransport
from mcp.types import ServerCapabilities, TextContent, Tool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from client import UserAppClient
from logging_setup import configure_logging
from tools import all_tools, dispatch_map


load_dotenv()
configure_logging("designmcp")
logger = logging.getLogger(__name__)

USERAPP_BASE_URL = os.getenv("USERAPP_BASE_URL", "http://localhost")
USERAPP_API_KEY = os.getenv("USERAPP_API_KEY", "")
MCP_PORT = int(os.getenv("MCP_PORT", "33282"))
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")

if not USERAPP_API_KEY:
    logger.error("USERAPP_API_KEY is not set — DesignMCP cannot authenticate to UserApp.")
    sys.exit(1)
if not USERAPP_API_KEY.startswith("hbk_"):
    logger.error("USERAPP_API_KEY must be an hbk_* token; got prefix %r", USERAPP_API_KEY[:4])
    sys.exit(1)

logger.info("DesignMCP starting")
logger.info("  USERAPP_BASE_URL: %s", USERAPP_BASE_URL)
logger.info("  MCP_PORT: %s", MCP_PORT)
logger.info("  USERAPP_API_KEY: %s...redacted", USERAPP_API_KEY[:8])


server = Server("designmcp")
sse_transport = SseServerTransport("/messages/")

_api_client = UserAppClient(USERAPP_BASE_URL, USERAPP_API_KEY)
_TOOLS = all_tools()
_DISPATCH = dispatch_map()


class _SentResponse(Response):
    async def __call__(self, scope, receive, send):
        pass


_SENT = _SentResponse()


@server.list_tools()
async def list_tools() -> list[Tool]:
    return list(_TOOLS)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handler = _DISPATCH.get(name)
    if handler is None:
        return [TextContent(type="text", text=f"Error: Unknown tool: {name}")]

    try:
        result = await handler(arguments or {}, _api_client)
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
    except Exception as exc:
        logger.exception("Tool %s raised", name)
        return [TextContent(type="text", text=f"Error: {exc}")]


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(
            streams[0],
            streams[1],
            InitializationOptions(
                server_name="designmcp",
                server_version="0.1.0",
                capabilities=ServerCapabilities(tools={}, resources={}, prompts={}, completion=None),
            ),
        )
    return _SENT


async def handle_messages(request: Request):
    await sse_transport.handle_post_message(request.scope, request.receive, request._send)
    return _SENT


async def handle_health(_request: Request):
    return JSONResponse({"status": "ok", "service": "designmcp"})


app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Route("/messages/", endpoint=handle_messages, methods=["POST"]),
        Route("/health", endpoint=handle_health, methods=["GET"]),
    ],
)


def main():
    import uvicorn

    uvicorn.run(
        app,
        host=MCP_HOST,
        port=MCP_PORT,
        log_level=os.getenv("UVICORN_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
