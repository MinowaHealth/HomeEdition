#!/usr/bin/env python3
"""
UserMCP - MCP Server for Minowa.ai Home Edition Health Data

Streamable HTTP transport (stateless, /mcp) with per-request bearer token
authentication; the legacy HTTP/SSE transport (/sse + /messages/) is kept
for older client configs. Stateless means no server-side session to strand
when a laptop sleeps mid-session — see MCP/ClaudeDesktopDisconnects.md.
Proxies all requests through the Flask API (UserApp webapp), which
enforces per-user privacy with explicit app-level user_id scoping
(household trust model — no RLS on this box).

Environment variables:
  API_BASE_URL: Flask webapp API endpoint (default: http://localhost)
  MCP_PORT: Port to listen on (default: 13282)
  MCP_HOST: Host to bind to (default: 127.0.0.1; set to 0.0.0.0 in containers)
  UVICORN_LOG_LEVEL: Logging level for uvicorn + Python stdlib logging
                     (critical, error, warning, info, debug, trace; default: info)
"""

import json
import sys
import os
import logging
import asyncio
import time
import contextvars
from typing import Any, Dict, Optional
from contextlib import asynccontextmanager
from pathlib import Path

# Add parent directory to path for imports when run as script
sys.path.insert(0, str(Path(__file__).parent))

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse, Response
from starlette.requests import Request
from mcp.server.models import InitializationOptions
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import GetPromptResult, Prompt, Resource, Tool, TextContent, ServerCapabilities
from dotenv import load_dotenv
import httpx
from tools import all_tools, dispatch_map
from tools._shape import as_dict
from tools._sources import reset_cache as reset_sources_cache
import resources as resources_mod
import prompts as prompts_mod
from logging_setup import configure_logging, request_id, new_request_id


class _SentResponse(Response):
    """No-op ASGI response — the real response was already sent by the MCP SDK."""
    async def __call__(self, scope, receive, send):
        pass  # MCP SDK already handled the ASGI send

_SENT = _SentResponse()


# JSON-RPC 2.0 implementation-defined error codes (spec reserves -32000..-32099)
JSONRPC_AUTH_ERROR = -32001


def _jsonrpc_error(
    message: str,
    code: int = JSONRPC_AUTH_ERROR,
    status_code: int = 401,
    request_id: Any = None,
) -> JSONResponse:
    """Return a JSON-RPC 2.0 compliant error response.

    The MCP client parses the HTTP body as a JSON-RPC envelope regardless of
    status code, so auth failures at the HTTP layer must still emit the
    `{jsonrpc, id, error: {code, message}}` shape or the client's Zod
    validator rejects the response.
    """
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        },
        status_code=status_code,
    )

# Load environment
load_dotenv()

# Configure structured logging (JSON in Docker, human-readable locally)
configure_logging("usermcp")
logger = logging.getLogger(__name__)

# Configuration
API_BASE_URL = os.getenv('API_BASE_URL', 'http://localhost')
MCP_PORT = int(os.getenv('MCP_PORT', '13282'))
MCP_HOST = os.getenv('MCP_HOST', '127.0.0.1')
MCP_TRACE = os.getenv('MCP_TRACE', '').lower() in ('1', 'true', 'yes')

logger.info("UserMCP Configuration:")
logger.info(f"  API_BASE_URL: {API_BASE_URL}")
logger.info(f"  MCP_PORT: {MCP_PORT}")
if MCP_TRACE:
    logger.info("  MCP_TRACE: ON — raw JSON-RPC messages will be printed to stdout")


# ============================================================================
# Context Variables for Per-Request State
# ============================================================================

_api_client_context: contextvars.ContextVar[Optional['UserAppClient']] = contextvars.ContextVar(
    'api_client',
    default=None
)


# ============================================================================
# MCP Server Setup
# ============================================================================

server = Server("usermcp")

sse_transport = SseServerTransport("/messages/")

# Stateless: a fresh transport per request, no initialization handshake to
# validate, so a client reconnecting after laptop sleep can never strand
# itself on a dead session. json_response: plain JSON replies (no SSE
# streaming) — our tools are single request/response.
def _new_session_manager() -> StreamableHTTPSessionManager:
    return StreamableHTTPSessionManager(
        app=server,
        event_store=None,
        json_response=True,
        stateless=True,
    )


# Rebuilt on every app startup: .run() is once-per-instance and test
# clients (TestClient) cycle the lifespan repeatedly in one process.
session_manager = _new_session_manager()


class UserAppClient:
    """Async API client for proxying requests through the Flask webapp."""

    def __init__(self, api_base_url: str, user_token: str):
        self.api_base_url = api_base_url.rstrip('/')
        self.client = httpx.AsyncClient(
            headers={
                'Authorization': f'Bearer {user_token}',
                'Content-Type': 'application/json',
            },
            timeout=30.0,
        )
        logger.debug(f"Initialized UserAppClient with base_url: {self.api_base_url}")

    async def call_api(self, endpoint: str, method: str = 'GET', **kwargs) -> Dict[str, Any]:
        """Call Flask API with error handling. Non-blocking async."""
        url = f"{self.api_base_url}/api/v1{endpoint}"
        try:
            response = await self.client.request(method, url, **kwargs)
            response.raise_for_status()
            if not response.text:
                return {}
            try:
                return response.json()
            except Exception:
                logger.error(f"Non-JSON response from API: {method} {endpoint} ({len(response.text)} bytes)")
                raise ValueError("The server returned an unexpected response format. Please try again.")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 401:
                logger.warning(f"API auth failed: {method} {endpoint} → 401")
                raise ValueError(
                    "Authentication failed — your session token or API key may have "
                    "expired. Please refresh your token or generate a new API key."
                )
            if status == 503:
                logger.warning(f"API query timeout: {method} {endpoint} → 503")
                raise ValueError(
                    "The query took too long and was cancelled by the server. "
                    "Try a shorter date range or fewer data types."
                )
            logger.error(f"API call failed: {method} {endpoint} → {status}")
            raise ValueError(f"The server returned an error (HTTP {status}). If this persists, try a shorter date range.")
        except httpx.HTTPError as e:
            logger.error(f"API connection error: {method} {endpoint}: {e}")
            raise ValueError("Connection error: could not reach the health data API")

    async def close(self):
        """Close the underlying HTTP client."""
        await self.client.aclose()


# ============================================================================
# MCP Tools
# ============================================================================

# Tool registry resolved once at import time. Each entry exposes schema()
# and async handle(args, client). See tools/__init__.py for registration.
_TOOLS = all_tools()
_DISPATCH = dispatch_map()


def _resolve_api_client() -> tuple[Optional['UserAppClient'], bool]:
    """Return (client, owned_by_caller).

    SSE path: the connection handler put a session-long client in the
    contextvar — reuse it, caller must not close it. Streamable HTTP path:
    the server loop runs in the session manager's task group where that
    contextvar is unset, so build a client from the Authorization header of
    the HTTP request the SDK attaches to the request context; caller closes it.
    """
    client = _api_client_context.get()
    if client:
        return client, False
    try:
        http_request = server.request_context.request
    except LookupError:
        return None, False
    if http_request is None:
        return None, False
    auth_header = http_request.headers.get("Authorization", "")
    user_token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    if not user_token.strip():
        return None, False
    request_id.set(new_request_id())  # task-local; ties this call's log lines together
    return UserAppClient(API_BASE_URL, user_token), True


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return list(_TOOLS)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute MCP tool."""
    t0 = time.perf_counter()
    logger.info(f"Tool called: {name}", extra={'tool': name})

    api_client, owned = _resolve_api_client()
    if not api_client:
        logger.error(f"Tool {name} called without API client context")
        return [TextContent(type="text", text="Error: No API client available - not authenticated")]

    handler = _DISPATCH.get(name)
    if handler is None:
        if owned:
            await api_client.close()
        logger.warning(f"Unknown tool: {name}")
        return [TextContent(type="text", text=f"Error: Unknown tool: {name}")]

    try:
        result = await handler(arguments or {}, api_client)

        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        # Envelope summary — one structured line per tool call so regressions
        # show up in `docker compose logs` (rows dropping to 0, truncation
        # spiking, sources vanishing) rather than in prose.
        result_d = as_dict(result or {}, where="mcp_server.envelope.result")
        coverage = result_d.get("coverage")
        sources = result_d.get("sources")
        rows = None
        truncated = None
        if isinstance(coverage, dict):
            counts = as_dict(coverage.get("counts"), where="mcp_server.envelope.counts")
            rows = counts.get("rows")
            truncated = bool(coverage.get("truncated")) if coverage.get("truncated") is not None else None
        sources_present = None
        if isinstance(sources, list):
            sources_present = sorted({
                s.get("source") for s in sources
                if isinstance(s, dict) and s.get("source") and (s.get("connected") or s.get("last_sync"))
            })
        logger.info(
            f"envelope_summary tool={name} rows={rows} truncated={truncated} duration_ms={duration_ms}",
            extra={
                'event': 'envelope_summary',
                'tool': name,
                'duration_ms': duration_ms,
                'envelope_rows': rows,
                'envelope_truncated': truncated,
                'envelope_sources_present': sources_present,
            },
        )
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    except Exception as e:
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.error(
            f"Tool error ({name}): {e} ({duration_ms}ms)",
            extra={'tool': name, 'duration_ms': duration_ms},
            exc_info=True
        )
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    finally:
        if owned:
            await api_client.close()


@server.list_resources()
async def list_resources() -> list[Resource]:
    """List available resources. See resources.py for the full set."""
    return resources_mod.all_resources()


@server.read_resource()
async def read_resource(uri: str) -> str:
    """Read a resource by URI. Requires per-request API client context."""
    api_client, owned = _resolve_api_client()
    try:
        return await resources_mod.read(str(uri), api_client)
    finally:
        if owned:
            await api_client.close()


@server.list_prompts()
async def list_prompts() -> list[Prompt]:
    """List available MCP prompts (slash-command templates)."""
    return prompts_mod.all_prompts()


@server.get_prompt()
async def get_prompt(name: str, arguments: dict | None = None) -> GetPromptResult:
    """Return a prompt template by name."""
    return await prompts_mod.get(name, arguments)


# ============================================================================
# Starlette HTTP/SSE Setup
# ============================================================================

@asynccontextmanager
async def lifespan(app: Starlette):
    """Startup/shutdown lifecycle. Owns the streamable-HTTP task group."""
    global session_manager
    session_manager = _new_session_manager()
    async with session_manager.run():
        logger.info("UserMCP server starting...")
        yield
        logger.info("UserMCP server shutting down...")


async def handle_streamable_http(scope, receive, send):
    """ASGI endpoint for the stateless streamable HTTP transport (/mcp).

    Gates on a bearer token being present (parity with the SSE path);
    the token itself is validated by UserApp on the first proxied call.
    """
    headers = {k: v for k, v in scope.get("headers") or []}
    auth_header = headers.get(b"authorization", b"").decode("latin-1")
    if not auth_header.startswith("Bearer ") or not auth_header[7:].strip():
        logger.warning("Streamable HTTP request without valid Authorization header")
        await _jsonrpc_error("Missing or invalid Authorization header")(scope, receive, send)
        return
    await session_manager.handle_request(scope, receive, send)


async def handle_mcp_route(request: Request):
    """Route wrapper — exact /mcp path (Mount would 307-redirect to /mcp/)."""
    await handle_streamable_http(request.scope, request.receive, request._send)
    return _SENT


async def handle_sse(request: Request):
    """
    Handle SSE connection for MCP protocol.

    Validates bearer token from Authorization header and creates
    a per-request API client that proxies to the Flask webapp.
    The client is shared across SSE and POST handlers for this session.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        logger.warning("SSE connection without valid Authorization header")
        return _jsonrpc_error("Missing or invalid Authorization header")

    user_token = auth_header[7:]  # Strip "Bearer "
    if not user_token.strip():
        logger.warning("SSE connection with empty bearer token")
        return _jsonrpc_error("Empty bearer token")

    rid = new_request_id()
    rid_token = request_id.set(rid)
    logger.info("New SSE connection with user token")

    api_client = UserAppClient(API_BASE_URL, user_token)
    ctx_token = _api_client_context.set(api_client)

    # Wrap send to trace outgoing SSE messages
    raw_send = request._send
    if MCP_TRACE:
        async def tracing_send(message):
            if message.get('type') == 'http.response.body':
                body = message.get('body', b'')
                if body:
                    for line in body.decode('utf-8', errors='replace').splitlines():
                        if line.startswith('data: '):
                            print(f"\033[36m← SSE\033[0m {line[6:]}", flush=True)
            await raw_send(message)
        send_fn = tracing_send
    else:
        send_fn = raw_send

    try:
        async with sse_transport.connect_sse(
            request.scope,
            request.receive,
            send_fn
        ) as streams:
            await server.run(
                streams[0],
                streams[1],
                InitializationOptions(
                    server_name="usermcp",
                    server_version="0.5.0",
                    capabilities=ServerCapabilities(
                        tools={}, resources={}, prompts={}, completion=None
                    ),
                ),
            )
    finally:
        reset_sources_cache()
        await api_client.close()
        _api_client_context.reset(ctx_token)
        request_id.reset(rid_token)

    return _SENT  # MCP SDK already sent the ASGI response


async def handle_messages(request: Request):
    """
    Handle POST messages from MCP client.

    Reuses the API client from the SSE session context rather than
    creating a new one per POST (fixes P1 connection reuse issue).
    Falls back to creating a new client if context is missing.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        logger.warning("Messages POST without valid Authorization header")
        return _jsonrpc_error("Missing or invalid Authorization header")

    rid = new_request_id()
    rid_token = request_id.set(rid)

    # Try to reuse the client from SSE context; create new one only if needed
    existing_client = _api_client_context.get()
    ctx_token = None
    new_client = None

    if not existing_client:
        user_token = auth_header[7:]
        if not user_token.strip():
            logger.warning("Messages POST with empty bearer token")
            request_id.reset(rid_token)
            return _jsonrpc_error("Empty bearer token")
        new_client = UserAppClient(API_BASE_URL, user_token)
        ctx_token = _api_client_context.set(new_client)

    # Trace incoming POST body (MCP JSON-RPC requests)
    if MCP_TRACE:
        body = await request.body()
        print(f"\033[33m→ MSG\033[0m {body.decode('utf-8', errors='replace')}", flush=True)

        # Reconstruct receive so the transport can still read the body
        body_sent = False
        async def replay_receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return {'type': 'http.disconnect'}
        receive_fn = replay_receive
    else:
        receive_fn = request.receive

    try:
        await sse_transport.handle_post_message(
            request.scope,
            receive_fn,
            request._send
        )
    finally:
        if new_client:
            reset_sources_cache()
            await new_client.close()
        if ctx_token:
            _api_client_context.reset(ctx_token)
        request_id.reset(rid_token)

    return _SENT  # MCP SDK already sent the 202


async def health_check(request: Request):
    """Health check endpoint."""
    return JSONResponse({
        "status": "ok",
        "service": "usermcp",
    })


# Starlette app
routes = [
    Route("/health", health_check, methods=["GET"]),
    Route("/mcp", handle_mcp_route, methods=["GET", "POST", "DELETE"]),
    # Legacy HTTP/SSE transport — strands sessions on laptop sleep
    # (MCP/ClaudeDesktopDisconnects.md). Kept for older client configs.
    Route("/sse", handle_sse, methods=["GET"]),
    Route("/messages/", handle_messages, methods=["POST"]),
]

app = Starlette(
    routes=routes,
    lifespan=lifespan
)


# ============================================================================
# Main
# ============================================================================

async def main():
    """Start MCP server."""
    import uvicorn

    logger.info(f"Starting UserMCP on {MCP_HOST}:{MCP_PORT}")
    config = uvicorn.Config(
        app,
        host=MCP_HOST,
        port=MCP_PORT,
        log_level=os.getenv('UVICORN_LOG_LEVEL', 'info').lower(),
        log_config=None,  # Preserve our LokiHandler; uvicorn's default wipes root handlers
    )
    uv_server = uvicorn.Server(config)
    await uv_server.serve()


if __name__ == '__main__':
    asyncio.run(main())
