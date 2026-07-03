"""DesignMCP tools registry.

Two tools total: an inventory introspector and a generic request proxy.
Each module exports schema() -> Tool and async handle(args, client) -> dict.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import inventory, request

_MODULES = (inventory, request)


def all_tools() -> list[Any]:
    """Return Tool schema for each registered tool."""
    return [m.schema() for m in _MODULES]


def dispatch_map() -> dict[str, Callable[..., Awaitable[dict[str, Any]]]]:
    """Return {tool_name: async handle(args, client)}."""
    return {m.schema().name: m.handle for m in _MODULES}
