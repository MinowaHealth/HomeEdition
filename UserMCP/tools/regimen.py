"""
get_my_active_regimen — active meds, supplements, timeframes, reminders.

Replaces the old `get_health_config` which dumped raw table contents. This
tool presents the *active* prescription regimen as the user experiences it:
scheduled meds first, then the timeframes they follow, then upcoming
reminders. Inactive entries are filtered out — callers asking "what am I
taking?" shouldn't see archived rows.

Stacks are deliberately absent: they are a logging convenience, not an
analytical object. Per the stack-invisibility rule (CLAUDE.md), no MCP tool
touches stacks unless "stack" is in its name.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope, extract_list as _extract_list
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)


def schema() -> Tool:
    return Tool(
        name="get_my_active_regimen",
        description=(
            "Return the user's current active medications and supplements, "
            "and the timeframes/reminders governing when they take them. "
            "Inactive entries are excluded. Use this to answer 'what am I taking' "
            "or 'what's my schedule' questions."
        ),
        inputSchema={"type": "object", "properties": {}},
    )


async def _safe_call(client: Any, path: str, **kwargs) -> Any:
    try:
        return await client.call_api(path, **kwargs)
    except Exception as exc:
        logger.warning(f"regimen: {path} failed: {exc}")
        return {"_error": str(exc)}


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    inputs_r, timeframes_r, reminders_r, sources = await asyncio.gather(
        _safe_call(client, "/health-inputs", method="GET", params={"is_active": "true"}),
        _safe_call(client, "/timeframes", method="GET"),
        _safe_call(client, "/reminders", method="GET"),
        fetch_sources(client),
    )

    gaps = []
    for label, r in (
        ("health_inputs", inputs_r),
        ("timeframes", timeframes_r),
        ("reminders", reminders_r),
    ):
        if isinstance(r, dict) and r.get("_error"):
            gaps.append({"source": label, "reason": r["_error"]})

    inputs = _extract_list(inputs_r, "inputs", "entries", "results")
    # Defensive filter — server should already return is_active only
    inputs = [i for i in inputs if i.get("is_active") is not False]

    timeframes = _extract_list(timeframes_r, "timeframes", "entries", "results")
    timeframes = [t for t in timeframes if t.get("is_active") is not False]

    reminders = _extract_list(reminders_r, "reminders", "entries", "results")

    data = {
        "inputs": inputs,
        "timeframes": timeframes,
        "reminders": reminders,
    }
    coverage = {
        "counts": {
            "rows": len(inputs) + len(timeframes) + len(reminders),
            "inputs": len(inputs),
            "timeframes": len(timeframes),
            "reminders": len(reminders),
            "sources_represented": ["manual"],
        },
        "gaps": gaps,
        "truncated": False,
    }

    next_actions = []
    if not inputs and not gaps:
        next_actions.append({
            "tool": "get_my_active_regimen",
            "args": {},
            "why": (
                "No active inputs — the user has not added any medications or "
                "supplements yet. Consider prompting to onboard their regimen."
            ),
        })

    return build_envelope(data, coverage=coverage, sources=sources, next_actions=next_actions)
