"""Path-safety + method-validation tests for the userapp_request tool.

These tests assert the safety rails in tools/request.py without touching
the real UserApp. The client is stubbed; only the validation layer matters.
"""
from __future__ import annotations

import pytest

from tools import request as request_tool


class _StubClient:
    """Records the last call so the test can assert forwarding."""

    def __init__(self):
        self.calls: list[dict] = []

    async def request(self, method, path, *, params=None, json=None, headers=None):
        self.calls.append({
            "method": method,
            "path": path,
            "params": params,
            "json": json,
            "headers": headers,
        })
        return {"status_code": 200, "json": {"ok": True}, "headers": {}}


@pytest.mark.asyncio
async def test_get_v1_path_forwarded():
    client = _StubClient()
    result = await request_tool.handle(
        {"method": "GET", "path": "/api/v1/blood-pressure"},
        client,
    )
    assert result["status_code"] == 200
    assert client.calls[0]["method"] == "GET"
    assert client.calls[0]["path"] == "/api/v1/blood-pressure"


@pytest.mark.asyncio
async def test_post_v2_path_with_body_forwarded():
    client = _StubClient()
    body = {"systolic": 120, "diastolic": 80}
    await request_tool.handle(
        {"method": "POST", "path": "/api/v2/observations", "body": body},
        client,
    )
    assert client.calls[0]["json"] == body


@pytest.mark.asyncio
async def test_query_params_forwarded():
    client = _StubClient()
    await request_tool.handle(
        {"method": "GET", "path": "/api/v1/meals", "query": {"limit": 5}},
        client,
    )
    assert client.calls[0]["params"] == {"limit": 5}


@pytest.mark.asyncio
async def test_rejects_non_api_path():
    client = _StubClient()
    result = await request_tool.handle({"method": "GET", "path": "/login"}, client)
    assert result["status_code"] == 0
    assert "not under /api/v1/ or /api/v2/" in result["error"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_rejects_root_path():
    client = _StubClient()
    result = await request_tool.handle({"method": "GET", "path": "/"}, client)
    assert result["status_code"] == 0
    assert client.calls == []


@pytest.mark.asyncio
async def test_rejects_metrics_path():
    client = _StubClient()
    result = await request_tool.handle({"method": "GET", "path": "/metrics"}, client)
    assert result["status_code"] == 0
    assert client.calls == []


@pytest.mark.asyncio
async def test_rejects_admin_path():
    client = _StubClient()
    result = await request_tool.handle(
        {"method": "GET", "path": "/admin/users"}, client,
    )
    assert result["status_code"] == 0
    assert client.calls == []


@pytest.mark.asyncio
async def test_rejects_unsafe_method():
    client = _StubClient()
    result = await request_tool.handle(
        {"method": "TRACE", "path": "/api/v1/blood-pressure"}, client,
    )
    assert result["status_code"] == 0
    assert "Method" in result["error"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_v3_path_is_blocked():
    """Future-proof: if /api/v3/ is ever added, this guard ensures the MCP
    is updated deliberately rather than silently extending its surface."""
    client = _StubClient()
    result = await request_tool.handle(
        {"method": "GET", "path": "/api/v3/whatever"}, client,
    )
    assert result["status_code"] == 0
    assert client.calls == []
