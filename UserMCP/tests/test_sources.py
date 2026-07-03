"""Tests for tools/_sources.py — envelope source-status helper.

Verifies that fetch_sources() makes the three expected parallel calls,
aggregates row counts into manual/healthkit/garmin buckets, caches the
result per request, and degrades gracefully when any sub-call errors.
"""
from __future__ import annotations

import asyncio
import contextvars
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools._sources import (
    GARMIN_RECORD_TABLES,
    HEALTHKIT_RECORD_TABLES,
    MANUAL_RECORD_TABLES,
    fetch_sources,
    reset_cache,
)


def _table_counts(entries):
    """Mimic the /diagnostics/table-counts response shape."""
    return {"tables": [{"table": t, "count": n} for t, n in entries]}


async def _run_isolated(coro_factory):
    """Run coro in a fresh contextvars.Context so cache state is isolated."""
    ctx = contextvars.copy_context()
    return await asyncio.create_task(coro_factory(), context=ctx)


@pytest.fixture(autouse=True)
def clear_cache():
    reset_cache()
    yield
    reset_cache()


@pytest.mark.asyncio
async def test_fetch_sources_hits_three_endpoints():
    mock_api = AsyncMock()
    mock_api.call_api.return_value = {}

    await fetch_sources(mock_api)

    called = [c.args[0] for c in mock_api.call_api.call_args_list]
    assert "/diagnostics/table-counts" in called
    assert "/garmin/status" in called
    assert "/healthkit/jobs" in called


@pytest.mark.asyncio
async def test_fetch_sources_returns_three_named_buckets():
    mock_api = AsyncMock()
    mock_api.call_api.return_value = {}

    entries = await fetch_sources(mock_api)

    names = [e["name"] for e in entries]
    assert names == ["manual", "healthkit", "garmin"]


@pytest.mark.asyncio
async def test_fetch_sources_aggregates_row_counts_by_bucket():
    table_counts = _table_counts([
        ("health_blood_pressure_readings", 50),  # manual
        ("health_metrics", 100),                  # manual
        ("hkit_records", 500),                    # healthkit
        ("hkit_workouts", 20),                    # healthkit
        ("garm_hr", 1000),                        # garmin
        ("garm_sleep", 30),                       # garmin
        ("sessions", 999),                        # ignored — not a source table
    ])
    mock_api = AsyncMock()

    def router(path, **kwargs):
        if path == "/diagnostics/table-counts":
            return table_counts
        if path == "/garmin/status":
            return {"connected": True, "last_sync": "2026-04-17T08:00:00Z"}
        return {"entries": []}

    mock_api.call_api.side_effect = router

    entries = await fetch_sources(mock_api)
    by_name = {e["name"]: e for e in entries}

    assert by_name["manual"]["record_count"] == 150
    assert by_name["healthkit"]["record_count"] == 520
    assert by_name["garmin"]["record_count"] == 1030
    assert by_name["garmin"]["connected"] is True
    assert by_name["garmin"]["last_sync"] == "2026-04-17T08:00:00Z"


@pytest.mark.asyncio
async def test_fetch_sources_garmin_disconnected_shape():
    mock_api = AsyncMock()

    def router(path, **kwargs):
        if path == "/garmin/status":
            return {"connected": False}
        if path == "/diagnostics/table-counts":
            return {"tables": []}
        return {"entries": []}

    mock_api.call_api.side_effect = router

    entries = await fetch_sources(mock_api)
    garmin = next(e for e in entries if e["name"] == "garmin")

    assert garmin["connected"] is False
    assert garmin["last_sync"] is None
    assert garmin["record_count"] == 0


@pytest.mark.asyncio
async def test_fetch_sources_healthkit_last_sync_from_completed_job():
    mock_api = AsyncMock()

    def router(path, **kwargs):
        if path == "/healthkit/jobs":
            return {"entries": [
                {"id": "j1", "completed_at": "2026-04-16T12:00:00Z"},
            ]}
        if path == "/diagnostics/table-counts":
            return {"tables": []}
        return {"connected": False}

    mock_api.call_api.side_effect = router

    entries = await fetch_sources(mock_api)
    hkit = next(e for e in entries if e["name"] == "healthkit")

    assert hkit["last_sync"] == "2026-04-16T12:00:00Z"


@pytest.mark.asyncio
async def test_fetch_sources_tolerates_subcall_errors():
    """If a diagnostic route errors, we still return a well-formed list."""
    mock_api = AsyncMock()

    def router(path, **kwargs):
        if path == "/diagnostics/table-counts":
            raise RuntimeError("boom")
        if path == "/garmin/status":
            return {"connected": False}
        return {"entries": []}

    mock_api.call_api.side_effect = router

    entries = await fetch_sources(mock_api)

    # All three buckets present; record_count zeroed because counts failed
    assert [e["name"] for e in entries] == ["manual", "healthkit", "garmin"]
    assert all(e["record_count"] == 0 for e in entries)


@pytest.mark.asyncio
async def test_fetch_sources_caches_within_request():
    """Second call in the same request context must not re-fetch."""
    async def scenario():
        mock_api = AsyncMock()
        mock_api.call_api.return_value = {}

        first = await fetch_sources(mock_api)
        first_count = mock_api.call_api.call_count
        second = await fetch_sources(mock_api)
        second_count = mock_api.call_api.call_count

        assert first == second
        assert first_count == 3          # one parallel fan-out
        assert second_count == 3          # no additional calls

    await _run_isolated(scenario)


@pytest.mark.asyncio
async def test_reset_cache_forces_refetch():
    mock_api = AsyncMock()
    mock_api.call_api.return_value = {}

    await fetch_sources(mock_api)
    before = mock_api.call_api.call_count
    reset_cache()
    await fetch_sources(mock_api)

    assert mock_api.call_api.call_count == before + 3


def test_bucket_tables_are_disjoint():
    """A table name should belong to at most one source bucket."""
    assert MANUAL_RECORD_TABLES.isdisjoint(HEALTHKIT_RECORD_TABLES)
    assert MANUAL_RECORD_TABLES.isdisjoint(GARMIN_RECORD_TABLES)
    assert HEALTHKIT_RECORD_TABLES.isdisjoint(GARMIN_RECORD_TABLES)
