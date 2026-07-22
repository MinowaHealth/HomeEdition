"""
get_vitals_timeline — BP, weight, temperature, blood glucose over a window.

Wraps /blood-pressure, /temperature, /weight, /blood-glucose. Returns raw rows for each
category plus a per-category rollup (count/avg/min/max) so an LLM can
answer "how's my BP trending" in one call without scanning every reading.

Blood pressure is source-aware: rows carry their collection source (the
device column, 'manual' when none), available_sources inventories every
source the user has (via /blood-pressure/sources), and the bp_sources
argument filters to any subset — for users with multiple collection methods.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

from mcp.types import Tool

from tools._envelope import build_envelope, resolve_window, window_block
from tools._time import home_tz
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)

_MAX_DAYS = 90
_DEFAULT_DAYS = 30


def schema() -> Tool:
    return Tool(
        name="get_vitals_timeline",
        description=(
            "Return blood pressure, weight, temperature, and blood glucose "
            "readings over a window (default last 30 days, max 90). Values come "
            "back in the user's display unit preference. Each category includes raw "
            "rows and a rollup (count, min, max, avg) so trends are visible "
            "at a glance. Blood pressure rows carry their collection source "
            "(device, or 'manual'), position, and arm; "
            "blood_pressure.available_sources lists every source the user has "
            "with counts, and bp_sources filters to any subset of them — "
            "useful when the user records BP with more than one method. "
            "Dates are the user's local days; call get_current_time for today."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": _DEFAULT_DAYS},
                "from": {"type": "string", "description": "YYYY-MM-DD"},
                "to": {"type": "string", "description": "YYYY-MM-DD"},
                "include": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["bp", "weight", "temperature", "glucose"]},
                    "description": "Subset to fetch. Omit for all four.",
                },
                "bp_sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Blood pressure only: restrict to these collection "
                        "sources (names as listed in "
                        "blood_pressure.available_sources, e.g. ['manual', "
                        "'cuff meter']). Omit for all sources."
                    ),
                },
            },
        },
    )


async def _safe_call(client: Any, path: str, **kwargs) -> Any:
    try:
        return await client.call_api(path, **kwargs)
    except Exception as exc:
        logger.warning(f"vitals: {path} failed: {exc}")
        return {"_error": str(exc)}


def _rollup(rows: List[Dict[str, Any]], *fields: str) -> Dict[str, Any]:
    """Aggregate the first present field per row — the vitals API returns the
    value under a named key ('weight', 'temperature', 'blood_glucose'); older
    shapes used 'value'."""
    def _pick(r: Dict[str, Any]) -> Any:
        for f in fields:
            if r.get(f) is not None:
                return r[f]
        return None
    values = [_pick(r) for r in rows if _pick(r) is not None]
    values = [float(v) for v in values if isinstance(v, (int, float))]
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "avg": round(sum(values) / len(values), 2),
    }


def _bp_rows(resp: Any) -> List[Dict[str, Any]]:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for k in ("readings", "entries", "results"):
            v = resp.get(k)
            if isinstance(v, list):
                return v
    return []


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    # days-shorthand only: anchor 'today' in the user's home timezone
    tz = None
    if not (arguments.get("from") and arguments.get("to")):
        tz, _tz_source = await home_tz(client)
    start, end = resolve_window(arguments, tz=tz, default_days=_DEFAULT_DAYS, max_days=_MAX_DAYS)
    params = {"start_date": start.isoformat(), "end_date": end.isoformat()}

    include = set(arguments.get("include") or ["bp", "weight", "temperature", "glucose"])

    fetches = {}
    if "bp" in include:
        bp_params = dict(params)
        bp_sources = [s for s in (arguments.get("bp_sources") or []) if s]
        if bp_sources:
            bp_params["sources"] = ",".join(bp_sources)
        fetches["bp"] = _safe_call(client, "/blood-pressure", method="GET", params=bp_params)
        fetches["bp_available"] = _safe_call(client, "/blood-pressure/sources", method="GET")
    if "weight" in include:
        fetches["weight"] = _safe_call(client, "/weight", method="GET", params=params)
    if "temperature" in include:
        fetches["temperature"] = _safe_call(client, "/temperature", method="GET", params=params)
    if "glucose" in include:
        fetches["glucose"] = _safe_call(client, "/blood-glucose", method="GET", params=params)
    fetches["sources"] = fetch_sources(client)

    results = await asyncio.gather(*fetches.values())
    by_key = dict(zip(fetches.keys(), results))
    sources = by_key.pop("sources")

    data = {}
    gaps = []
    total_rows = 0

    if "bp" in include:
        r = by_key["bp"]
        if isinstance(r, dict) and r.get("_error"):
            gaps.append({"source": "blood_pressure", "reason": r["_error"]})
            data["blood_pressure"] = {"rows": [], "rollup_systolic": {}, "rollup_diastolic": {}}
        else:
            rows = _bp_rows(r)
            total_rows += len(rows)
            data["blood_pressure"] = {
                "rows": rows,
                "rollup_systolic": _rollup(rows, "systolic"),
                "rollup_diastolic": _rollup(rows, "diastolic"),
            }
        # All-time source inventory (device or 'manual', with counts) so the
        # model can see what collection methods exist and re-query filtered.
        avail = by_key.get("bp_available")
        if isinstance(avail, dict) and not avail.get("_error"):
            data["blood_pressure"]["available_sources"] = avail.get("sources") or []
        else:
            data["blood_pressure"]["available_sources"] = []

    if "weight" in include:
        r = by_key["weight"]
        if isinstance(r, dict) and r.get("_error"):
            gaps.append({"source": "weight", "reason": r["_error"]})
            data["weight"] = {"rows": [], "rollup": {}}
        else:
            rows = _bp_rows(r)
            total_rows += len(rows)
            data["weight"] = {"rows": rows, "rollup": _rollup(rows, "weight", "value")}

    if "temperature" in include:
        r = by_key["temperature"]
        if isinstance(r, dict) and r.get("_error"):
            gaps.append({"source": "temperature", "reason": r["_error"]})
            data["temperature"] = {"rows": [], "rollup": {}}
        else:
            rows = _bp_rows(r)
            total_rows += len(rows)
            data["temperature"] = {"rows": rows, "rollup": _rollup(rows, "temperature", "value")}

    if "glucose" in include:
        r = by_key["glucose"]
        if isinstance(r, dict) and r.get("_error"):
            gaps.append({"source": "blood_glucose", "reason": r["_error"]})
            data["blood_glucose"] = {"rows": [], "rollup": {}}
        else:
            rows = _bp_rows(r)
            total_rows += len(rows)
            data["blood_glucose"] = {"rows": rows, "rollup": _rollup(rows, "blood_glucose", "value")}

    coverage = {
        "window": window_block(start, end),
        "counts": {
            "rows": total_rows,
            "sources_represented": ["manual", "healthkit"] if total_rows else [],
        },
        "gaps": gaps,
        "truncated": False,
    }

    return build_envelope(data, coverage=coverage, sources=sources)
