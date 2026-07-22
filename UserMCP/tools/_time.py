"""
Shared timezone helper: the user's home timezone, resolved server-side.

The LLM has no authoritative clock and cannot be trusted with date
arithmetic (MCP/TimeCapabilities-Plan1.md). Everything time-related answers
from the server clock + the profile's home_timezone via this helper.
"""
from __future__ import annotations

import logging
from typing import Any, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_UTC = ZoneInfo("UTC")


async def home_tz(client: Any) -> Tuple[ZoneInfo, str]:
    """Return (tz, source). source: 'profile' | 'utc_fallback'."""
    try:
        resp = await client.call_api("/session", method="GET")
        name = (resp or {}).get("home_timezone")
        if name:
            return ZoneInfo(name), "profile"
    except Exception as exc:
        logger.warning(f"home_tz: /session failed, falling back to UTC: {exc}")
    return _UTC, "utc_fallback"
