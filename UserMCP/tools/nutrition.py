"""
get_nutrition_report — food log summary vs. dietary settings.

Combines `/food-log` (what was eaten) with `/dietary-settings` (the
diet/allergen rules the user is following) and produces:
  - day-by-day calorie/macro rollup
  - flagged entries that appear to violate the user's current diet
    (best-effort substring match on dietary-setting avoid_list)

Writing this check in the tool keeps it in the same layer as the LLM
prompt, so mismatches get called out before the user sees them.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

from mcp.types import Tool

from tools._envelope import build_envelope, extract_list as _extract_list, resolve_window, window_block
from tools._time import home_tz
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)

_MAX_DAYS = 90
_DEFAULT_DAYS = 14


def schema() -> Tool:
    return Tool(
        name="get_nutrition_report",
        description=(
            "Return a nutrition report for a window: daily calorie/macro "
            "rollups, meal count, and entries that appear to violate the "
            "user's current dietary settings. Default 14 days, max 90. "
            "Dates are the user's local days; call get_current_time for today."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": _DEFAULT_DAYS},
                "from": {"type": "string"},
                "to": {"type": "string"},
            },
        },
    )


async def _safe(client: Any, path: str, **kwargs) -> Any:
    try:
        return await client.call_api(path, **kwargs)
    except Exception as exc:
        logger.warning(f"nutrition: {path} failed: {exc}")
        return {"_error": str(exc)}


def _day_of(entry: Dict[str, Any]) -> str:
    for k in ("eaten_at", "logged_at", "created_at", "timestamp"):
        v = entry.get(k)
        if not v:
            continue
        try:
            return str(v)[:10]
        except Exception:
            continue
    return "unknown"


def _sum_macro(entries: List[Dict[str, Any]], field: str) -> float:
    total = 0.0
    for e in entries:
        v = e.get(field)
        if isinstance(v, (int, float)):
            total += float(v)
    return round(total, 1)


def _avoid_list(dietary_latest: Dict[str, Any]) -> List[str]:
    avoid = dietary_latest.get("avoid_list") or dietary_latest.get("excluded_foods") or []
    if isinstance(avoid, list):
        return [str(x).strip().lower() for x in avoid if x]
    if isinstance(avoid, str):
        return [s.strip().lower() for s in avoid.split(",") if s.strip()]
    return []


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    # days-shorthand only: anchor 'today' in the user's home timezone
    tz = None
    if not (arguments.get("from") and arguments.get("to")):
        tz, _tz_source = await home_tz(client)
    start, end = resolve_window(arguments, tz=tz, default_days=_DEFAULT_DAYS, max_days=_MAX_DAYS)
    params = {"start_date": start.isoformat(), "end_date": end.isoformat()}

    food_r, diet_r, sources = await asyncio.gather(
        _safe(client, "/food-log", method="GET", params=params),
        _safe(client, "/dietary-settings", method="GET"),
        fetch_sources(client),
    )

    gaps = []
    if isinstance(food_r, dict) and food_r.get("_error"):
        gaps.append({"source": "food_log", "reason": food_r["_error"]})
    if isinstance(diet_r, dict) and diet_r.get("_error"):
        gaps.append({"source": "dietary_settings", "reason": diet_r["_error"]})

    entries = _extract_list(food_r, "entries", "results", "food_log")
    dietary_list = _extract_list(diet_r, "settings", "results")
    dietary_latest = dietary_list[0] if dietary_list else {}

    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for e in entries:
        by_day.setdefault(_day_of(e), []).append(e)

    daily_rollups = []
    for day, day_entries in sorted(by_day.items()):
        daily_rollups.append({
            "date": day,
            "meal_count": len(day_entries),
            "calories": _sum_macro(day_entries, "calories"),
            "protein_g": _sum_macro(day_entries, "protein_g"),
            "carbs_g": _sum_macro(day_entries, "carbs_g"),
            "fat_g": _sum_macro(day_entries, "fat_g"),
            "fiber_g": _sum_macro(day_entries, "fiber_g"),
            "sodium_mg": _sum_macro(day_entries, "sodium_mg"),
        })

    avoid = _avoid_list(dietary_latest) if dietary_latest else []
    violations = []
    if avoid:
        for e in entries:
            name = (e.get("name") or e.get("food_name") or e.get("free_text") or "").lower()
            for term in avoid:
                if term and term in name:
                    violations.append({
                        "entry_id": e.get("id"),
                        "name": e.get("name") or e.get("food_name") or e.get("free_text"),
                        "date": _day_of(e),
                        "matched_term": term,
                    })
                    break

    data = {
        "daily": daily_rollups,
        "dietary_settings": dietary_latest,
        "violations": violations,
    }

    coverage = {
        "window": window_block(start, end),
        "counts": {
            "rows": len(entries),
            "days": len(daily_rollups),
            "violations": len(violations),
            "sources_represented": ["manual"] if entries else [],
        },
        "gaps": gaps,
        "truncated": False,
    }

    next_actions = []
    if violations:
        next_actions.append({
            "tool": "get_recent_activity",
            "args": {"kind": "food", "days": (end - start).days + 1},
            "why": (
                f"{len(violations)} food entries appeared to violate the user's "
                "dietary settings — review the raw food log to confirm."
            ),
        })

    return build_envelope(data, coverage=coverage, sources=sources, next_actions=next_actions)
