"""Tests for tools/adherence.py — the adherence report tool.

Covers the envelope wrapping, parameter passthrough to /adherence, the
90-day cap, and the next_actions hint triggered by worst-case adherence
below 50%.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.adherence import handle, schema


def _adherence_response(inputs=None, excluded_prn=None, excluded_unspecified=None):
    return {
        "inputs": inputs or [],
        "excluded_prn": excluded_prn or [],
        "excluded_unspecified": excluded_unspecified or [],
    }


def test_schema_exposes_expected_arguments():
    tool = schema()
    assert tool.name == "get_adherence_report"
    props = tool.inputSchema["properties"]
    for key in ("days", "from", "to", "input_ids"):
        assert key in props


@pytest.mark.asyncio
async def test_handle_wraps_result_in_envelope():
    mock_api = AsyncMock()
    # fetch_sources calls /diagnostics/table-counts, /garmin/status, /healthkit/jobs.
    # Provide a single dict that works for all three (empty tables, no Garmin,
    # no HealthKit jobs) via side_effect.
    def router(path, **kwargs):
        if path == "/adherence":
            return _adherence_response(inputs=[
                {"input_id": "a", "name": "Lisinopril", "pct_adherence": 90},
            ])
        if path == "/diagnostics/table-counts":
            return {"tables": []}
        if path == "/garmin/status":
            return {"connected": False}
        return {"entries": []}
    mock_api.call_api.side_effect = router

    env = await handle({"days": 7}, mock_api)

    assert "data" in env
    assert "coverage" in env
    assert "sources" in env
    assert "disclaimer" in env
    assert "next_actions" in env
    assert env["data"]["inputs"][0]["name"] == "Lisinopril"
    assert env["coverage"]["counts"]["rows"] == 1


@pytest.mark.asyncio
async def test_handle_passes_params_to_adherence_endpoint():
    mock_api = AsyncMock()
    captured = {}

    def router(path, **kwargs):
        if path == "/adherence":
            captured["params"] = kwargs.get("params") or {}
            return _adherence_response()
        return {"tables": []} if path == "/diagnostics/table-counts" else (
            {"connected": False} if path == "/garmin/status" else {"entries": []}
        )
    mock_api.call_api.side_effect = router

    await handle({"from": "2026-04-01", "to": "2026-04-07"}, mock_api)

    # The route reads start_date/end_date — sending from/to silently fell
    # back to a 30-day default window (Bug 3b). Pin the correct names.
    assert captured["params"]["start_date"] == "2026-04-01"
    assert captured["params"]["end_date"] == "2026-04-07"
    assert "from" not in captured["params"]
    assert "to" not in captured["params"]


@pytest.mark.asyncio
async def test_handle_input_ids_joined_as_csv():
    mock_api = AsyncMock()
    captured = {}

    def router(path, **kwargs):
        if path == "/adherence":
            captured["params"] = kwargs.get("params") or {}
            return _adherence_response()
        return {"tables": []} if path == "/diagnostics/table-counts" else (
            {"connected": False} if path == "/garmin/status" else {"entries": []}
        )
    mock_api.call_api.side_effect = router

    await handle({"days": 30, "input_ids": ["uuid-1", "uuid-2", "uuid-3"]}, mock_api)

    assert captured["params"]["input_ids"] == "uuid-1,uuid-2,uuid-3"


@pytest.mark.asyncio
async def test_handle_caps_days_at_90():
    """Request > 90 days must not send > 90-day window to backend.

    The 90-day cap matches the UserApp parse_date_range_params ceiling,
    so we never build a request the endpoint will reject.
    """
    mock_api = AsyncMock()
    captured = {}

    def router(path, **kwargs):
        if path == "/adherence":
            captured["params"] = kwargs.get("params") or {}
            return _adherence_response()
        return {"tables": []} if path == "/diagnostics/table-counts" else (
            {"connected": False} if path == "/garmin/status" else {"entries": []}
        )
    mock_api.call_api.side_effect = router

    await handle({"days": 365}, mock_api)

    start = date.fromisoformat(captured["params"]["start_date"])
    end = date.fromisoformat(captured["params"]["end_date"])
    assert (end - start).days + 1 <= 90


@pytest.mark.asyncio
async def test_coverage_window_comes_from_route_response():
    """coverage.window must reflect the window the route actually applied
    (e.g. after clamping), not echo the request."""
    mock_api = AsyncMock()

    def router(path, **kwargs):
        if path == "/adherence":
            resp = _adherence_response()
            resp["window"] = {"from": "2026-06-01", "to": "2026-06-15", "days": 15}
            return resp
        return {"tables": []} if path == "/diagnostics/table-counts" else (
            {"connected": False} if path == "/garmin/status" else {"entries": []}
        )
    mock_api.call_api.side_effect = router

    env = await handle({"from": "2026-06-01", "to": "2026-06-30"}, mock_api)

    assert env["coverage"]["window"]["to"] == "2026-06-15"


@pytest.mark.asyncio
async def test_handle_adds_next_action_for_poor_adherence():
    """When the worst input is below 50%, the envelope should hint at a
    follow-up tool call so the LLM can dig in without being asked."""
    mock_api = AsyncMock()

    def router(path, **kwargs):
        if path == "/adherence":
            return _adherence_response(inputs=[
                {"input_id": "a", "name": "Lisinopril", "pct_adherence": 90},
                {"input_id": "b", "name": "Metformin", "pct_adherence": 30},
            ])
        return {"tables": []} if path == "/diagnostics/table-counts" else (
            {"connected": False} if path == "/garmin/status" else {"entries": []}
        )
    mock_api.call_api.side_effect = router

    env = await handle({"days": 30}, mock_api)

    assert len(env["next_actions"]) >= 1
    action = env["next_actions"][0]
    assert "Metformin" in action.get("why", "")
    assert action.get("args", {}).get("input_id") == "b"


@pytest.mark.asyncio
async def test_handle_no_next_action_when_all_adherence_ok():
    mock_api = AsyncMock()

    def router(path, **kwargs):
        if path == "/adherence":
            return _adherence_response(inputs=[
                {"input_id": "a", "name": "Lisinopril", "pct_adherence": 90},
                {"input_id": "b", "name": "Metformin", "pct_adherence": 85},
            ])
        return {"tables": []} if path == "/diagnostics/table-counts" else (
            {"connected": False} if path == "/garmin/status" else {"entries": []}
        )
    mock_api.call_api.side_effect = router

    env = await handle({"days": 30}, mock_api)

    assert env["next_actions"] == []


@pytest.mark.asyncio
async def test_handle_degrades_gracefully_on_api_error():
    """A failing /adherence call should still produce a well-formed envelope
    with the error surfaced in coverage.gaps rather than raising."""
    mock_api = AsyncMock()

    def router(path, **kwargs):
        if path == "/adherence":
            raise RuntimeError("backend down")
        return {"tables": []} if path == "/diagnostics/table-counts" else (
            {"connected": False} if path == "/garmin/status" else {"entries": []}
        )
    mock_api.call_api.side_effect = router

    env = await handle({"days": 7}, mock_api)

    assert env["data"]["inputs"] == []
    gaps = env["coverage"]["gaps"]
    assert any("backend down" in g.get("reason", "") for g in gaps)


@pytest.mark.asyncio
async def test_handle_excluded_prn_reported_separately():
    mock_api = AsyncMock()

    def router(path, **kwargs):
        if path == "/adherence":
            return _adherence_response(
                inputs=[],
                excluded_prn=[{"input_id": "p", "name": "PRN Med"}],
                excluded_unspecified=[{"input_id": "u", "name": "Unspecified Med"}],
            )
        return {"tables": []} if path == "/diagnostics/table-counts" else (
            {"connected": False} if path == "/garmin/status" else {"entries": []}
        )
    mock_api.call_api.side_effect = router

    env = await handle({"days": 30}, mock_api)

    assert len(env["data"]["excluded_prn"]) == 1
    assert len(env["data"]["excluded_unspecified"]) == 1
    assert env["coverage"]["counts"]["excluded_prn"] == 1
    assert env["coverage"]["counts"]["excluded_unspecified"] == 1
