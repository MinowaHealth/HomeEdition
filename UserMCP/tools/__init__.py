"""
UserMCP tools registry.

Each tool module exports two functions:

    schema() -> mcp.types.Tool
    async handle(arguments: dict, client) -> dict  # envelope-shaped

The dispatcher (mcp_server.py) iterates over `all_tools()` rather than
maintaining an if/elif chain. Adding a new tool means: create the module,
import it below, append to `_TOOL_MODULES`. Nothing else.
"""

from __future__ import annotations

import importlib
from typing import Any, Awaitable, Callable, Dict, List, Tuple

# Tool module names, in the order they should appear in list_tools().
# Modules listed here MUST export `schema()` and `async handle(args, client)`.
#
# Phase 1 roster: the existing 6 tools + adherence, all envelope-wrapped.
# Phase 2 (pending) will swap this list for the task-oriented surface
# described in UserMCPRedesign-Plan.md: profile, regimen, clinical_history,
# vitals, labs, wearables, activity, adherence, nutrition, search,
# documents, feedback.
_TOOL_MODULES: Tuple[str, ...] = (
    # Time context (authoritative clock + date arithmetic)
    "tools.time_context",
    "tools.date_math",
    # Identity & config
    "tools.profile",
    "tools.regimen",
    "tools.stacks",  # the named exception to the stack-invisibility rule
    "tools.clinical_history",
    # Observation & trends
    "tools.vitals",
    "tools.labs",
    "tools.wearables",
    "tools.garmin_detail",
    "tools.garmin_sync",
    "tools.sleep_events",
    "tools.observations_detail",
    # Activity & adherence
    "tools.activity",
    "tools.adherence",
    "tools.acquisitions",
    # Food & nutrition
    "tools.nutrition",
    # Search & documents
    "tools.search",
    "tools.documents",
    "tools.chat_summary",
    "tools.episode_report",
    "tools.episode_report_list",
    # Engagement
    "tools.feedback",
)


def _load_tool_module(dotted: str):
    """Import a tool module; raise at import time if schema/handle missing."""
    mod = importlib.import_module(dotted)
    if not hasattr(mod, "schema") or not callable(mod.schema):
        raise AttributeError(f"{dotted} must export schema()")
    if not hasattr(mod, "handle") or not callable(mod.handle):
        raise AttributeError(f"{dotted} must export async handle(args, client)")
    return mod


def all_tools() -> List[Any]:
    """Return list of mcp.types.Tool definitions in registration order.

    Tolerant of missing modules during phased rollout: a tool whose file
    doesn't exist yet is skipped with a warning, so early-phase deployments
    don't crash just because later-phase files aren't landed.
    """
    import logging
    log = logging.getLogger(__name__)
    tools: List[Any] = []
    for dotted in _TOOL_MODULES:
        try:
            mod = _load_tool_module(dotted)
        except ImportError as exc:
            log.warning(f"tools registry: {dotted} not available yet: {exc}")
            continue
        tools.append(mod.schema())
    return tools


def dispatch_map() -> Dict[str, Callable[..., Awaitable[Dict[str, Any]]]]:
    """Return {tool_name: async handle(args, client)} for every loaded tool."""
    import logging
    log = logging.getLogger(__name__)
    mapping: Dict[str, Callable[..., Awaitable[Dict[str, Any]]]] = {}
    for dotted in _TOOL_MODULES:
        try:
            mod = _load_tool_module(dotted)
        except ImportError as exc:
            log.warning(f"tools registry: {dotted} not available yet: {exc}")
            continue
        tool = mod.schema()
        mapping[tool.name] = mod.handle
    return mapping
