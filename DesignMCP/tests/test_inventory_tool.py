"""Inventory tool tests — confirms the tool delegates to the right endpoint."""
from __future__ import annotations

import pytest

from tools import inventory as inventory_tool


class _StubClient:
    def __init__(self, response):
        self._response = response
        self.last_call = None

    async def request(self, method, path, *, params=None, json=None, headers=None):
        self.last_call = (method, path)
        return self._response


@pytest.mark.asyncio
async def test_inventory_calls_design_endpoint():
    fake_response = {"status_code": 200, "json": {"count": 42, "routes": []}, "headers": {}}
    client = _StubClient(fake_response)
    result = await inventory_tool.handle({}, client)
    assert client.last_call == ("GET", "/api/v1/_design/inventory")
    assert result is fake_response
