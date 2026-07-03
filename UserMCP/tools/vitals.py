"""
get_vitals_timeline — BP, weight, temperature, and related metrics over a window.

Wraps /blood-pressure, /temperature, /weight. Returns raw rows for each
category plus a per-category rollup (count/avg/min/max) so an LLM can
answer "how's my BP trending" in one call without scanning every reading.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

from mcp.types import Tool

from tools._envelope import build_envelope, window_block
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)

_MAX_DAYS = 90
_DEFAULT_DAYS = 30


def schema() -> Tool:
    return Tool(
        name="get_vitals_timeline",
        description=(
            "Return blood pressure, weight, and temperature readings over a "
            "window (default last 30 days, max 90). Each category includes raw "
            "rows and a rollup (count, min, max, avg) so trends are visible "
            "at a glance."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": _DEFAULT_DAYS},
                "from": {"type": "string", "description": "YYYY-MM-DD"},
                "to": {"type": "string", "description": "YYYY-MM-DD"},
                "include": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["bp", "weight", "temperature"]},
                    "description": "Subset to fetch. Omit for all three.",
                },
            },
        },
    )


def _resolve_window(arguments: Dict[str, Any]) -> tuple[date, date]:
    from_str = arguments.get("from")
    to_str = arguments.get("to")
    if from_str and to_str:
        start = datetime.strptime(from_str, "%Y-%m-%d").date()
        end = datetime.strptime(to_str, "%Y-%m-%d").date()
    else:
        days = int(arguments.get("days", _DEFAULT_DAYS) or _DEFAULT_DAYS)
        days = max(1, min(_MAX_DAYS, days))
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days - 1)
    if (end - start).days + 1 > _MAX_DAYS:
        start = end - timedelta(days=_MAX_DAYS - 1)
    return start, end


async def _safe_call(client: Any, path: str, **kwargs) -> Any:
    try:
        return await client.call_api(path, **kwargs)
    except Exception as exc:
        logger.warning(f"vitals: {path} failed: {exc}")
        return {"_error": str(exc)}


def _rollup(rows: List[Dict[str, Any]], field: str) -> Dict[str, Any]:
    values = [r.get(field) for r in rows if r.get(field) is not None]
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
    start, end = _resolve_window(arguments)
    params = {"start_date": start.isoformat(), "end_date": end.isoformat()}

    include = set(arguments.get("include") or ["bp", "weight", "temperature"])

    fetches = {}
    if "bp" in include:
        fetches["bp"] = _safe_call(client, "/blood-pressure", method="GET", params=params)
    if "weight" in include:
        fetches["weight"] = _safe_call(client, "/weight", method="GET", params=params)
    if "temperature" in include:
        fetches["temperature"] = _safe_call(client, "/temperature", method="GET", params=params)
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

    if "weight" in include:
        r = by_key["weight"]
        if isinstance(r, dict) and r.get("_error"):
            gaps.append({"source": "weight", "reason": r["_error"]})
            data["weight"] = {"rows": [], "rollup": {}}
        else:
            rows = _bp_rows(r)
            total_rows += len(rows)
            data["weight"] = {"rows": rows, "rollup": _rollup(rows, "value")}

    if "temperature" in include:
        r = by_key["temperature"]
        if isinstance(r, dict) and r.get("_error"):
            gaps.append({"source": "temperature", "reason": r["_error"]})
            data["temperature"] = {"rows": [], "rollup": {}}
        else:
            rows = _bp_rows(r)
            total_rows += len(rows)
            data["temperature"] = {"rows": rows, "rollup": _rollup(rows, "value")}

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
