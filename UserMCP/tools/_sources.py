"""
Source-status helper.

Fills the `sources` block of the envelope. Each invocation runs up to three
UserApp calls — table counts, Garmin status, HealthKit job list — and caches
the result in a per-request context variable so multiple tools in one MCP
session don't re-fetch. The cache is scoped per invocation, not per process:
once the ASGI request ends, the context is gone.

The point of this block is to let the LLM distinguish "empty because the
user has no data" from "empty because Garmin hasn't synced in 3 days". The
former is a real answer; the latter is a recoverable setup issue the user
should know about.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Any, Dict, List, Optional

from tools._shape import as_dict

logger = logging.getLogger(__name__)


# Per-request cache. Set to a dict on first fetch, reused by later tool
# calls in the same request. Reset by the SSE handler between sessions.
_sources_cache: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "sources_cache",
    default=None,
)


def reset_cache() -> None:
    """Drop the cached source status. Call on SSE connection teardown."""
    _sources_cache.set(None)


# Tables used to compute `record_count` for the "manual" bucket — anything
# the user types directly into the web UI lands here.
MANUAL_RECORD_TABLES = {
    "health_blood_pressure_readings",
    "health_metrics",
    "health_input_log",
    "health_food_logv2",
    "health_observations",
}

# Tables that back the HealthKit-origin row count.
HEALTHKIT_RECORD_TABLES = {
    "hkit_records",
    "hkit_activity_summaries",
    "hkit_workouts",
    "hkit_clinical_records",
    "hkit_lab_observations",
}

# Tables that back the Garmin-origin row count.
GARMIN_RECORD_TABLES = {
    "garm_hr",
    "garm_sleep",
    "garm_sleep_events",
    "garm_stress",
    "garm_daily_summ",
    "garm_rr",
}


async def _safe_call(api_client: Any, path: str, **kwargs) -> Any:
    """Call `api_client.call_api` but swallow errors to a None result.

    Source status is an advisory block — if the underlying diagnostic route
    errors (e.g. webapp restart mid-request), we still want the tool to
    return a useful response with a partial `sources` list rather than
    failing the whole call.
    """
    try:
        return await api_client.call_api(path, **kwargs)
    except Exception as exc:
        logger.warning(f"sources: {path} failed: {exc}")
        return None


def _count_for_tables(table_counts: Any, table_set: set) -> int:
    """Sum row counts across a set of table names. Treats errors as zero."""
    counts = as_dict(table_counts, where="_sources._count_for_tables")
    if not counts:
        return 0
    total = 0
    for entry in counts.get("tables") or []:
        if entry.get("table") in table_set:
            count = entry.get("count") or 0
            if isinstance(count, int) and count > 0:
                total += count
    return total


async def fetch_sources(api_client: Any) -> List[Dict[str, Any]]:
    """Return the envelope's `sources` list for this request.

    First call in a request fetches; later calls in the same request return
    the cached result. Always returns a list — never raises — so the caller
    can inline it into the envelope without guarding.

    Shape per entry:
        {"name": "manual",   "last_sync": None,  "record_count": 142}
        {"name": "healthkit","last_sync": "...", "record_count": 94}
        {"name": "garmin",   "last_sync": None,  "record_count": 0}
    """
    cached = _sources_cache.get()
    if cached is not None:
        return cached.get("entries", [])

    counts_task = _safe_call(api_client, "/diagnostics/table-counts", method="GET")
    garmin_task = _safe_call(api_client, "/garmin/status", method="GET")
    hkit_task = _safe_call(
        api_client, "/healthkit/jobs", method="GET",
        params={"limit": 1, "offset": 0},
    )
    table_counts, garmin_status, hkit_jobs = await asyncio.gather(
        counts_task, garmin_task, hkit_task
    )

    # HealthKit: last job that moved rows is a proxy for last sync.
    hkit_last_sync = None
    hkit_d = as_dict(hkit_jobs, where="_sources.hkit_jobs")
    for job in hkit_d.get("entries") or []:
        if job.get("completed_at"):
            hkit_last_sync = job["completed_at"]
            break

    # Garmin: the status endpoint returns `{connected, last_sync, ...}` when
    # credentials exist, `{connected: false}` otherwise.
    garmin_d = as_dict(garmin_status, where="_sources.garmin_status")
    garmin_connected = bool(garmin_d.get("connected"))
    garmin_last_sync = garmin_d.get("last_sync")

    entries: List[Dict[str, Any]] = [
        {
            "name": "manual",
            "last_sync": None,  # manual entry has no "sync" concept
            "record_count": _count_for_tables(table_counts, MANUAL_RECORD_TABLES),
        },
        {
            "name": "healthkit",
            "last_sync": hkit_last_sync,
            "record_count": _count_for_tables(table_counts, HEALTHKIT_RECORD_TABLES),
        },
        {
            "name": "garmin",
            "last_sync": garmin_last_sync,
            "record_count": _count_for_tables(table_counts, GARMIN_RECORD_TABLES),
            "connected": garmin_connected,
        },
    ]

    _sources_cache.set({"entries": entries})
    return entries
