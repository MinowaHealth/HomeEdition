"""Tests for the save_chat_summary tool (2026-07-15 documents feature).

Validation happens tool-side (title/summary required, size caps) so a bad
call never reaches UserApp; success passes the route's response through the
envelope with absolutized links.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import chat_summary
from tools._links import APP_BASE_URL

ROUTE_RESPONSE = {
    "id": "doc-1",
    "title": "Lab review",
    "created_at": "2026-07-15T12:00:00+00:00",
    "links": {
        "web": "/?activity=documents&doc=doc-1",
        "download": "/api/v1/documents/doc-1/download",
    },
}


def _client(response=ROUTE_RESPONSE):
    mock = AsyncMock()
    mock.call_api.return_value = response
    return mock


# ---------------------------------------------------------------------------
# Tool-side validation — no API call on bad input
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("args,expected", [
    ({"summary_markdown": "x"}, "title is required"),
    ({"title": "  ", "summary_markdown": "x"}, "title is required"),
    ({"title": "T" * 201, "summary_markdown": "x"}, "200 characters"),
    ({"title": "T"}, "summary_markdown is required"),
    ({"title": "T", "summary_markdown": "   "}, "summary_markdown is required"),
    ({"title": "T", "summary_markdown": "x" * (256 * 1024 + 1)}, "262144"),
])
async def test_validation_rejects_without_api_call(args, expected):
    client = _client()
    env = await chat_summary.handle(args, client)
    assert env["data"]["success"] is False
    assert expected in env["data"]["error"]
    client.call_api.assert_not_called()


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_success_envelope_and_absolutized_links():
    client = _client()
    env = await chat_summary.handle(
        {"title": "Lab review", "summary_markdown": "# Notes"}, client)

    data = env["data"]
    assert data["success"] is True
    assert data["document"] == {
        "id": "doc-1", "title": "Lab review",
        "created_at": "2026-07-15T12:00:00+00:00", "folder": "AI Sessions",
    }
    assert data["links"]["web"] == f"{APP_BASE_URL}/?activity=documents&doc=doc-1"
    assert data["links"]["download"].startswith(APP_BASE_URL)

    actions = env["next_actions"]
    assert actions[0]["tool"] == "get_document"
    assert actions[0]["args"] == {"document_id": "doc-1"}


@pytest.mark.asyncio
async def test_optional_provenance_forwarded_defaults_omitted():
    client = _client()
    await chat_summary.handle({
        "title": "T", "summary_markdown": "x",
        "model_id": "claude-x",
    }, client)

    _, kwargs = client.call_api.call_args
    body = kwargs["json"]
    assert body["model_id"] == "claude-x"
    assert "source_tools" not in body
    assert "session_started_at" not in body
    assert body["created_via"] == "usermcp"


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_error_returns_failure_envelope():
    client = AsyncMock()
    client.call_api.side_effect = Exception("503 SCHEMA_NOT_READY")
    env = await chat_summary.handle(
        {"title": "T", "summary_markdown": "x"}, client)
    assert env["data"]["success"] is False
    assert "503" in env["data"]["error"]


def test_description_carries_behavioral_gate():
    desc = chat_summary.schema().description or ""
    assert "ONLY call this after the user has explicitly asked" in desc
    assert "never call it proactively" in desc
