"""
Response-shape narrowing helpers.

These defend the MCP↔UserApp trust boundary: if the UserApp returns a
payload whose top-level shape differs from what the tool expects (a list
when the tool wanted a dict, or vice versa), the helpers log the drift
and fall back to an empty container of the right type so the tool can
proceed without crashing.

The log line `api_shape_drift` is the chess-problem audit signal. After
30 days of production traffic, if it has never fired, the defensive
narrowing has not been needed and call sites can be inlined or the
helpers removed entirely. See OpenTelemetry.md "guard.fired" pattern.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def as_dict(value: Any, *, where: str) -> dict:
    """Return value if it's a dict; log and return {} otherwise.

    Args:
        value: Object to narrow.
        where: Stable caller identifier (e.g. "profile.dietary_latest").
               Used as the log-line label so drift events aggregate cleanly
               in Loki queries.
    """
    if isinstance(value, dict):
        return value
    logger.warning(
        "api_shape_drift where=%s expected=dict got=%s",
        where,
        type(value).__name__,
    )
    return {}


def as_list(value: Any, *, where: str) -> list:
    """Return value if it's a list; log and return [] otherwise."""
    if isinstance(value, list):
        return value
    logger.warning(
        "api_shape_drift where=%s expected=list got=%s",
        where,
        type(value).__name__,
    )
    return []
