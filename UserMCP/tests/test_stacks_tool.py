"""Tests for tools/stacks.py — the named exception to stack invisibility."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import stacks

pytestmark = pytest.mark.asyncio

_STACKS_RESPONSE = {
    "stacks": [
        {
            "id": "s1",
            "name": "Morning",
            "is_active": True,
            "timeframe_name": "Morning",
            "timeframe_time_of_day": "08:00:00",
            "timeframe_frequency": "daily",
            "inputs": [
                {"input_id": "i1", "input_name": "Metformin",
                 "input_type": "medication", "default_dosage": "500",
                 "default_unit": "mg", "dosage_override": None},
            ],
        },
    ],
    "pagination": {"total": 1, "limit": 50, "offset": 0},
}


def _client(response=_STACKS_RESPONSE):
    client = AsyncMock()

    async def call_api(path, method="GET", **kwargs):
        if path == "/stacks":
            return response
        return {}  # sources probes

    client.call_api = AsyncMock(side_effect=call_api)
    return client


def test_tool_name_contains_stack():
    """Rename guard: without 'stack' in the name the invisibility sweep
    would ban this tool's own subject matter."""
    assert "stack" in stacks.schema().name.lower()


async def test_returns_stack_composition():
    env = await stacks.handle({}, _client())
    got = env["data"]["stacks"]
    assert got[0]["name"] == "Morning"
    assert got[0]["inputs"][0]["default_dosage"] == "500"
    assert env["coverage"]["counts"]["rows"] == 1
    assert env["coverage"]["truncated"] is False


async def test_pagination_passthrough_and_truncation():
    resp = dict(_STACKS_RESPONSE, pagination={"total": 9, "limit": 1, "offset": 0})
    client = _client(resp)
    env = await stacks.handle({"limit": 1, "offset": 0}, client)
    call = [c for c in client.call_api.call_args_list if c.args[0] == "/stacks"][0]
    assert call.kwargs["params"] == {"limit": 1, "offset": 0}
    assert env["coverage"]["truncated"] is True
    assert env["next_actions"][0]["tool"] == "get_stacks"


async def test_api_failure_reports_gap():
    client = AsyncMock()

    async def call_api(path, method="GET", **kwargs):
        if path == "/stacks":
            raise ValueError("boom")
        return {}

    client.call_api = AsyncMock(side_effect=call_api)
    env = await stacks.handle({}, client)
    assert env["data"]["stacks"] == []
    assert env["coverage"]["gaps"][0]["source"] == "stacks"
