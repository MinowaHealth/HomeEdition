"""Tests for UserAppClient — the per-request HTTP client in mcp_server.py.

Verifies that the bearer token the MCP client presents on the SSE
connection is threaded into the Authorization header of every
downstream UserApp call, and that known auth failures map to readable
user-facing errors rather than raw HTTP exceptions.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server import UserAppClient


def _make_response(status_code=200, json_data=None, text=""):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text or ("" if json_data is None else "{}")
    resp.json = MagicMock(return_value=json_data or {})
    if status_code >= 400:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                f"HTTP {status_code}", request=MagicMock(), response=resp,
            )
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_bearer_token_set_on_client_headers():
    client = UserAppClient("http://localhost", "secret-token-abc")
    try:
        assert client.client.headers["Authorization"] == "Bearer secret-token-abc"
        assert client.client.headers["Content-Type"] == "application/json"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_call_api_threads_method_and_endpoint_to_httpx():
    client = UserAppClient("http://localhost", "tok")
    fake_resp = _make_response(200, json_data={"ok": True}, text='{"ok":true}')
    client.client.request = AsyncMock(return_value=fake_resp)

    try:
        out = await client.call_api("/health-inputs", method="GET", params={"x": 1})
    finally:
        await client.close()

    assert out == {"ok": True}
    client.client.request.assert_called_once()
    call = client.client.request.call_args
    # positional args: (method, url)
    assert call.args[0] == "GET"
    assert call.args[1] == "http://localhost/api/v1/health-inputs"
    assert call.kwargs["params"] == {"x": 1}


@pytest.mark.asyncio
async def test_call_api_strips_trailing_slash_from_base_url():
    client = UserAppClient("http://localhost/", "tok")
    try:
        assert client.api_base_url == "http://localhost"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_401_maps_to_friendly_auth_error():
    client = UserAppClient("http://localhost", "bad-token")
    client.client.request = AsyncMock(return_value=_make_response(401, text="Unauthorized"))

    try:
        with pytest.raises(ValueError, match="Authentication failed"):
            await client.call_api("/health-inputs", method="GET")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_503_maps_to_timeout_hint():
    client = UserAppClient("http://localhost", "tok")
    client.client.request = AsyncMock(return_value=_make_response(503))

    try:
        with pytest.raises(ValueError, match="too long|shorter date range"):
            await client.call_api("/health-query", method="POST", json={})
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_generic_http_error_maps_to_generic_message():
    client = UserAppClient("http://localhost", "tok")
    client.client.request = AsyncMock(return_value=_make_response(500))

    try:
        with pytest.raises(ValueError, match="HTTP 500"):
            await client.call_api("/some-path", method="GET")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_connection_error_maps_to_connection_message():
    client = UserAppClient("http://localhost", "tok")
    client.client.request = AsyncMock(
        side_effect=httpx.ConnectError("dns failure")
    )

    try:
        with pytest.raises(ValueError, match="Connection error"):
            await client.call_api("/anything", method="GET")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_empty_body_returns_empty_dict():
    client = UserAppClient("http://localhost", "tok")
    client.client.request = AsyncMock(return_value=_make_response(200, text=""))

    try:
        out = await client.call_api("/api/v1/something", method="DELETE")
    finally:
        await client.close()

    assert out == {}
