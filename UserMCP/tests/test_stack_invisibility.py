"""
Executable form of the stack-invisibility rule (CLAUDE.md, 2026-07-13):

    Stacks are a logging convenience, not an analytical object. No MCP tool
    may query, return, or mention stacks unless the tool's name explicitly
    contains "stack". Time-based/diagnostic tools treat health inputs alone
    as first-order objects.

These tests sweep every registered tool schema and every piece of static
prompt/resource text, so a future tool (or a reworded description) that
reintroduces stacks fails CI instead of surfacing in Claude Desktop.
Response-content stripping is covered separately in test_new_tools.py.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import prompts as prompts_mod
import resources as resources_mod
from tools import all_tools


def _stack_free(text: str, where: str) -> None:
    assert "stack" not in text.lower(), f"stack-invisibility rule violated in {where}"


def test_no_tool_schema_mentions_stacks_unless_named_stack():
    """Name, description, and inputSchema of every registered tool."""
    tools = all_tools()
    assert tools, "registry is empty"
    for tool in tools:
        if "stack" in tool.name.lower():
            continue  # explicitly stack-named tools are the one allowed exception
        _stack_free(json.dumps(tool.model_dump(), default=str), f"tool {tool.name}")


@pytest.mark.asyncio
async def test_no_prompt_mentions_stacks():
    """Prompt metadata and the full rendered template text of every prompt."""
    for prompt in prompts_mod.all_prompts():
        _stack_free(prompt.description or "", f"prompt {prompt.name} description")
        result = await prompts_mod.get(prompt.name)
        for message in result.messages:
            _stack_free(message.content.text, f"prompt {prompt.name} template")


def test_no_static_resource_mentions_stacks():
    """Resource metadata and the static markdown payloads.

    The usermcp://profile resource is live user data, not authored text,
    so only the static markdown constants are swept here.
    """
    for resource in resources_mod.all_resources():
        _stack_free(
            f"{resource.name} {resource.description or ''}",
            f"resource {resource.uri}",
        )
    _stack_free(resources_mod.DISCLAIMERS_MARKDOWN, "usermcp://disclaimers")
    _stack_free(resources_mod.DATA_SOURCES_MARKDOWN, "usermcp://data-sources")
