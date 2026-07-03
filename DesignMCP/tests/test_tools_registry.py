"""Tool-registry tests — assert the two tools are registered and discoverable."""
from __future__ import annotations

from tools import all_tools, dispatch_map


def test_two_tools_registered():
    tools = all_tools()
    names = sorted(t.name for t in tools)
    assert names == ["userapp_inventory", "userapp_request"]


def test_dispatch_map_has_both_handlers():
    mapping = dispatch_map()
    assert set(mapping.keys()) == {"userapp_inventory", "userapp_request"}
    for handler in mapping.values():
        assert callable(handler)


def test_inventory_schema_has_no_required_args():
    tools = {t.name: t for t in all_tools()}
    inventory = tools["userapp_inventory"]
    assert inventory.inputSchema["properties"] == {}


def test_request_schema_requires_method_and_path():
    tools = {t.name: t for t in all_tools()}
    request = tools["userapp_request"]
    assert set(request.inputSchema["required"]) == {"method", "path"}
    assert sorted(request.inputSchema["properties"]["method"]["enum"]) == [
        "DELETE", "GET", "PATCH", "POST", "PUT",
    ]
