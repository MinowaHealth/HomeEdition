"""
Adherence report tool.

Wraps `GET /api/v1/adherence`, which projects `health_inputs × timeframes ×
doses_per_day` into expected dose windows and left-joins `health_input_log`
for actuals. PRN meds and inputs with unspecified `doses_per_day` are
excluded from the percentage calculation but surfaced separately so the
caller can explain the gap.

Closes PLAN-002 at the tool-surface level.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope, window_block
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)


def schema() -> Tool:
    return Tool(
        name="get_adherence_report",
        description=(
            "Compute adherence for active scheduled medications and supplements. "
            "For each input, returns scheduled doses (doses_per_day × days in "
            "window), logged doses, percent adherence, and a list of days where "
            "the user fell short. PRN (as-needed) inputs are excluded from the "
            "percentage and listed separately under `excluded_prn`."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": (
                        "Look-back window in days (default 30, max 90). "
                        "Ignored if `from` and `to` are both set."
                    ),
                    "default": 30,
                },
                "from": {
                    "type": "string",
                    "description": "Start date YYYY-MM-DD. Takes priority over `days`.",
                },
                "to": {
                    "type": "string",
                    "description": "End date YYYY-MM-DD. Takes priority over `days`.",
                },
                "input_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of health_input UUIDs to restrict to.",
                },
            },
        },
    )


def _resolve_window(
    arguments: Dict[str, Any],
) -> tuple[date, date]:
    """Resolve the adherence window. Accepts `from`/`to` or `days` lookback.

    The 90-day cap matches the UserApp `parse_date_range_params` ceiling so
    the endpoint never rejects a request we just built.
    """
    MAX_DAYS = 90
    from_str = arguments.get("from")
    to_str = arguments.get("to")

    if from_str and to_str:
        start = _parse_iso_date(from_str)
        end = _parse_iso_date(to_str)
    else:
        days = int(arguments.get("days", 30) or 30)
        days = max(1, min(MAX_DAYS, days))
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days - 1)

    if (end - start).days + 1 > MAX_DAYS:
        start = end - timedelta(days=MAX_DAYS - 1)
    return start, end


def _parse_iso_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    start, end = _resolve_window(arguments)

    params: Dict[str, Any] = {
        "from": start.isoformat(),
        "to": end.isoformat(),
    }
    input_ids = arguments.get("input_ids")
    if isinstance(input_ids, list) and input_ids:
        params["input_ids"] = ",".join(str(x) for x in input_ids)

    try:
        response = await client.call_api(
            "/adherence",
            method="GET",
            params=params,
        )
    except Exception as exc:
        logger.error(f"adherence: {exc}")
        return build_envelope(
            {"inputs": [], "excluded_prn": [], "excluded_unspecified": []},
            coverage={
                "window": window_block(start, end),
                "counts": {"rows": 0, "sources_represented": []},
                "gaps": [{"reason": str(exc)}],
                "truncated": False,
            },
            sources=await fetch_sources(client),
        )

    inputs = response.get("inputs") or []
    excluded_prn = response.get("excluded_prn") or []
    excluded_unspecified = response.get("excluded_unspecified") or []

    # Coverage for adherence is based on the number of scheduled-input rows
    # the report covers — not the raw log count, which would double-count
    # multi-dose days.
    coverage = {
        "window": window_block(start, end),
        "counts": {
            "rows": len(inputs),
            "sources_represented": ["manual"],  # health_input_log is manual-only
            "excluded_prn": len(excluded_prn),
            "excluded_unspecified": len(excluded_unspecified),
        },
        "gaps": [],
        "truncated": False,
    }

    # If any scheduled input sits below 50%, hint at the most under-adherent
    # one via next_actions so the LLM can dig in without being told to.
    next_actions = []
    worst = None
    for row in inputs:
        pct = row.get("pct_adherence")
        if pct is None:
            continue
        if worst is None or pct < worst.get("pct_adherence", 101):
            worst = row
    if worst and worst.get("pct_adherence", 100) < 50:
        next_actions.append({
            "tool": "get_recent_activity",
            "args": {
                "kind": "medication",
                "input_id": worst.get("input_id"),
                "from": start.isoformat(),
                "to": end.isoformat(),
            },
            "why": (
                f"{worst.get('name')} is below 50% adherence "
                f"({worst.get('pct_adherence')}%) — check log entries directly"
            ),
        })

    sources = await fetch_sources(client)
    return build_envelope(
        {
            "inputs": inputs,
            "excluded_prn": excluded_prn,
            "excluded_unspecified": excluded_unspecified,
        },
        coverage=coverage,
        sources=sources,
        next_actions=next_actions,
    )
