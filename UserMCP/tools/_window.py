"""Shared at / window_minutes / from-to handling for the point-in-time
detail tools (garmin minute-detail, sleep-events, observations).

All three wrap routes that accept the same window params; keeping the schema
fragment and the argument parsing here guarantees the tools stay in the same
shape.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

WINDOW_PROPERTIES: Dict[str, Any] = {
    "at": {
        "type": "string",
        "description": (
            "The point in time, ISO 8601. Offset-aware values "
            "(2026-07-13T14:32:00-07:00 or ...Z) are honored; a value with "
            "no offset is read in the user's home timezone. Required unless "
            "from/to are given."
        ),
    },
    "window_minutes": {
        "type": "integer",
        "description": (
            "Half-width of the window around `at`, in minutes "
            "(default 60, max 720). Ignored when from/to are given."
        ),
    },
    "from": {
        "type": "string",
        "description": (
            "Window start, ISO 8601 (same timezone rules as `at`). "
            "Requires `to`; overrides `at`/`window_minutes`."
        ),
    },
    "to": {
        "type": "string",
        "description": "Window end, ISO 8601. Requires `from`.",
    },
}


def parse_window_args(
    arguments: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Turn tool arguments into query params for the wrapped route.

    Returns (params, problem); exactly one is None. Bounds validation
    (window_minutes range, 24h span cap) is left to the route — it is the
    authority and its 400 text flows back through the error envelope.
    """
    at = (arguments.get("at") or "").strip()
    from_ = (arguments.get("from") or "").strip()
    to = (arguments.get("to") or "").strip()
    window_minutes = arguments.get("window_minutes")

    if (from_ or to) and not (from_ and to):
        return None, "from and to must be provided together"
    if not (at or from_):
        return None, "at (ISO 8601 timestamp) or from/to is required"

    if from_:
        return {"from": from_, "to": to}, None
    params: Dict[str, Any] = {"at": at}
    if window_minutes is not None:
        params["window_minutes"] = window_minutes
    return params, None
