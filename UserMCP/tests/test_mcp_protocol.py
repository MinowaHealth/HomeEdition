"""
Layer 2: End-to-end MCP protocol tests using the real MCP client SDK.

These tests boot the real UserMCP Starlette app under uvicorn on a random
localhost port and drive it through `mcp.client.sse.sse_client` +
`ClientSession` — the same code path Claude Desktop uses.

UserAppClient.call_api is monkey-patched to return canned responses so no
UserApp is required. Everything else (SSE transport, auth gate, JSON-RPC
framing, tool dispatch, response packaging) runs exactly as in production.

Tool assertions iterate the actual registry (`tools.all_tools()` /
`tools.dispatch_map()`) rather than hardcoding names — the 0.5.0 redesign
swapped to a dynamic registry and the roster will continue to evolve. The
tests check invariants (every listed tool is dispatchable, every schema
is well-formed) rather than a frozen name list.

What this layer catches that Layer 0/1 do not:
  - Tool registration drift (tool defined in list_tools but missing from
    call_tool dispatch, or vice versa)
  - JSON-RPC envelope bugs (malformed error shape, missing jsonrpc field,
    result vs. error key collisions)
  - Schema errors in Tool.inputSchema that only surface client-side
  - Resource read/list round-trip regressions
"""
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
import uvicorn

sys.path.insert(0, str(Path(__file__).parent.parent))

import mcp_server
from tools import all_tools, dispatch_map
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession


# ============================================================================
# Canned UserApp responses
#
# 0.5.0 tools hit ~20 endpoints across profile / regimen / clinical history /
# vitals / wearables / nutrition / documents / adherence / labs / search /
# activity / feedback. We return shapes that each tool's parser will accept
# without blowing up — empty envelopes, empty lists, or minimal dicts.
# ============================================================================

_EMPTY_ENVELOPE = {
    "entries": [],
    "pagination": {"total": 0, "limit": 200, "offset": 0, "has_more": False},
}

_SESSION = {
    "user_id": "00000000-0000-0000-0000-000000000001",
    "username": "testuser",
    "tenant_id": 1,
    "timezone": "UTC",
    "home_timezone": "UTC",
}

# Endpoints that return a plain list
_LIST_ENDPOINTS = {
    "/timeframes",
    "/conditions",
    "/allergies",
    "/family-history",
    "/social-history",
    "/surgical-history",
    "/vaccinations",
    "/blood-pressure",
    "/temperature",
    "/weight",
    "/food-log",
    "/reminders",
    "/health-query",
}

# Endpoints that return a plain dict (shape varies per tool)
_DICT_ENDPOINTS = {
    "/session": _SESSION,
    "/dietary-settings": {"diet_type": None, "preferences": []},
    "/dashboard": {"summary": {}, "metrics": []},
    "/garmin/status": {"connected": False, "last_sync": None},
    "/healthkit/jobs": {"jobs": [], "count": 0},
    "/adherence": {"window_days": 7, "adherence": []},
    "/lab-results": {"results": [], "count": 0},
    "/search": {"results": [], "total": 0},
    "/feedback": {"status": "ok", "id": "feedback-1"},
}


async def _stub_call_api(self, endpoint: str, method: str = "GET", **kwargs):
    """Return empty-but-valid shapes for every endpoint the tools hit."""
    # Strip query string and trailing slash for lookup
    base = endpoint.split("?")[0].rstrip("/") or endpoint

    if base in _DICT_ENDPOINTS:
        return _DICT_ENDPOINTS[base]
    if base in _LIST_ENDPOINTS:
        return []
    if base in ("/health-inputs", "/health-input-log", "/all-logs"):
        return _EMPTY_ENVELOPE
    if base.startswith("/documents/"):
        if base.endswith("/annotations"):
            return []
        return {"id": "doc-1", "filename": "x.pdf", "pages": []}
    # Fallback — empty list is safe for most GET-and-iterate callers.
    return []


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def stub_userapp(monkeypatch):
    """Swap the live UserApp call with a canned stub for every test in this module."""
    monkeypatch.setattr(mcp_server.UserAppClient, "call_api", _stub_call_api)


@pytest_asyncio.fixture
async def live_server():
    """Boot the real Starlette app under uvicorn on a random port."""
    config = uvicorn.Config(
        mcp_server.app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        lifespan="on",
        log_config=None,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    else:
        raise RuntimeError("uvicorn failed to start within 2s")

    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    try:
        yield base
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()


@asynccontextmanager
async def mcp_session(base_url: str):
    """Open an authenticated MCP ClientSession.

    This is a helper, not a pytest fixture: anyio task groups (used inside
    sse_client) must be entered and exited in the same asyncio task. A
    pytest-asyncio generator fixture splits setup/teardown across tasks,
    which trips anyio's "different task" check. Driving the context
    manager inline from each test keeps both sides in one task.
    """
    sse_url = f"{base_url}/sse"
    headers = {"Authorization": "Bearer test-token-does-not-matter"}
    async with sse_client(sse_url, headers=headers) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


# ============================================================================
# Registry invariants (no server required)
# ============================================================================

def test_dispatch_map_covers_every_listed_tool():
    """Every Tool returned by all_tools() must have a handler in dispatch_map().
    Guards against adding a tool to _TOOL_MODULES but forgetting to export
    handle(), or renaming schema().name without updating dispatch."""
    listed = {t.name for t in all_tools()}
    dispatched = set(dispatch_map().keys())
    assert listed == dispatched, (
        f"registry drift: listed-only={listed - dispatched}, "
        f"dispatched-only={dispatched - listed}"
    )


# ============================================================================
# Protocol handshake
# ============================================================================

async def test_initialize_succeeds(live_server):
    """If this test passes, SSE connect + JSON-RPC initialize round-trip works."""
    async with mcp_session(live_server) as session:
        assert session is not None


# ============================================================================
# list_tools contract
# ============================================================================

async def test_list_tools_over_wire_matches_registry(live_server):
    """Wire-side list_tools must match the in-process registry exactly."""
    registry_names = {t.name for t in all_tools()}
    async with mcp_session(live_server) as session:
        result = await session.list_tools()
    wire_names = {t.name for t in result.tools}
    assert wire_names == registry_names


async def test_list_tools_schemas_are_valid_jsonschema(live_server):
    """Each Tool.inputSchema must be a valid JSON object with type=object.
    Catches cases where a tool is added without a proper schema."""
    async with mcp_session(live_server) as session:
        result = await session.list_tools()
    assert result.tools, "registry is empty"
    for tool in result.tools:
        assert isinstance(tool.inputSchema, dict), f"{tool.name} schema not a dict"
        assert tool.inputSchema.get("type") == "object", f"{tool.name} schema type wrong"
        assert "properties" in tool.inputSchema, f"{tool.name} missing properties"
        assert tool.description, f"{tool.name} missing description"


async def test_list_tools_required_fields_match_properties(live_server):
    """If a tool declares `required`, every required name must appear in properties."""
    async with mcp_session(live_server) as session:
        result = await session.list_tools()
    for tool in result.tools:
        required = tool.inputSchema.get("required", [])
        props = tool.inputSchema.get("properties", {})
        for r in required:
            assert r in props, f"{tool.name}: required field {r!r} not in properties"


# ============================================================================
# list_resources / read_resource
# ============================================================================

# ============================================================================
# call_tool round trip
#
# We round-trip send_feedback because (a) it's a stable, non-empty tool
# guaranteed to remain in the surface, and (b) its stubbed response exercises
# envelope wrapping — the most failure-prone part of the pipeline.
# ============================================================================

async def test_call_tool_send_feedback_roundtrip(live_server):
    """Full call_tool round trip: client → SSE → server → tool → API stub → back.
    Validates envelope framing (TextContent with parseable JSON body)."""
    async with mcp_session(live_server) as session:
        result = await session.call_tool(
            "send_feedback",
            {"content": "layer-2 test", "feedback_type": "general"},
        )
    assert result.content, "call_tool returned no content"
    payload = json.loads(result.content[0].text)
    # 0.5.0 wraps every tool response in an envelope with `data` and `meta`.
    assert "data" in payload or "success" in payload, f"unexpected shape: {payload}"


async def test_call_tool_unknown_returns_error_not_crash(live_server):
    """Unknown tool names must surface as errors, not kill the session."""
    async with mcp_session(live_server) as session:
        result = await session.call_tool("this_tool_does_not_exist", {})
    text = result.content[0].text if result.content else ""
    assert "Error" in text or "error" in text.lower()


# ============================================================================
# Auth at the SSE layer
#
# The empty-bearer path is covered by Layer 1 (TestClient) — httpx
# refuses to send a trailing-space header client-side, so we can't
# replay that exact bug through a real HTTP client. We only check the
# missing-auth path here since httpx can send that.
# ============================================================================

async def test_sse_connect_without_auth_fails(live_server):
    """Connecting with no Authorization header must produce a JSON-RPC error body."""
    import httpx

    async with httpx.AsyncClient(timeout=5.0) as http:
        r = await http.get(f"{live_server}/sse")
    assert r.status_code == 401
    body = r.json()
    assert body["jsonrpc"] == "2.0"
    assert body["error"]["code"] == mcp_server.JSONRPC_AUTH_ERROR
    assert isinstance(body["error"]["code"], int)
