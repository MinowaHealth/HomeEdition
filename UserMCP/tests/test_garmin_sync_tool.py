"""Tests for the sync_garmin_data tool.

The tool posts range=week + source=mcp (quiet sync, no /all-logs entry) and
must alert when the watch's last upload to Garmin Connect is >15 min old or
unknown.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import garmin_sync


def _client(device_last_sync):
    mock = AsyncMock()
    mock.call_api.return_value = {
        "job_id": "job-1",
        "status": "pending",
        "sync_from": "2026-07-11",
        "sync_to": "2026-07-17",
        "device_last_sync": device_last_sync,
    }
    return mock


def _iso_minutes_ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


@pytest.mark.asyncio
async def test_posts_week_range_with_mcp_source():
    client = _client(_iso_minutes_ago(1))
    await garmin_sync.handle({}, client)
    args, kwargs = client.call_api.call_args
    assert args[0] == "/garmin/sync"
    assert kwargs["method"] == "POST"
    assert kwargs["json"] == {"range": "week", "source": "mcp"}


@pytest.mark.asyncio
async def test_fresh_device_no_alert():
    env = await garmin_sync.handle({}, _client(_iso_minutes_ago(5)))
    data = env["data"]
    assert data["queued"] is True
    assert data["job_id"] == "job-1"
    assert data["device_alert"] is None


@pytest.mark.asyncio
async def test_stale_device_alerts():
    env = await garmin_sync.handle({}, _client(_iso_minutes_ago(45)))
    alert = env["data"]["device_alert"]
    assert alert is not None
    assert "Garmin Connect" in alert
    assert "45 minutes ago" in alert


@pytest.mark.asyncio
async def test_unknown_device_upload_alerts():
    env = await garmin_sync.handle({}, _client(None))
    alert = env["data"]["device_alert"]
    assert alert is not None
    assert "Could not determine" in alert


@pytest.mark.asyncio
async def test_api_error_returns_failure_envelope():
    client = AsyncMock()
    client.call_api.side_effect = Exception("Not connected to Garmin")
    env = await garmin_sync.handle({}, client)
    assert env["data"]["queued"] is False
    assert "Not connected" in env["data"]["error"]
