"""Tests for save_episode_report / list_episode_reports (2026-07-20 feature).

Validation happens tool-side (required fields, size caps) so a bad call
never reaches UserApp; success passes the route's response through the
envelope with absolutized links. List tool forwards window/pagination
params and only sends latest_only when disabling it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import episode_report, episode_report_list
from tools._links import APP_BASE_URL

SAVE_RESPONSE = {
    "id": "doc-9",
    "title": "Overnight — 2026-07-19",
    "created_at": "2026-07-20T12:00:00+00:00",
    "episode_start": "2026-07-19T08:21:00+00:00",
    "episode_end": "2026-07-19T11:52:00+00:00",
    "version": 1,
    "links": {
        "web": "/?activity=documents&doc=doc-9",
        "view": "/api/v1/documents/doc-9/view",
        "download": "/api/v1/documents/doc-9/download",
    },
}

VALID_SAVE_ARGS = {
    "title": "Overnight — 2026-07-19",
    "report_html": "<h1>report</h1>",
    "narrative_text": "Quiet night overall.",
    "episode_start": "2026-07-19T01:21:00",
    "episode_end": "2026-07-19T04:52:00",
}


def _client(response):
    mock = AsyncMock()
    mock.call_api.return_value = response
    return mock


# ---------------------------------------------------------------------------
# save_episode_report — tool-side validation, no API call on bad input
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("missing,expected", [
    ("title", "title is required"),
    ("report_html", "report_html is required"),
    ("narrative_text", "narrative_text is required"),
    ("episode_start", "episode_start and episode_end are required"),
    ("episode_end", "episode_start and episode_end are required"),
])
async def test_save_validation_rejects_without_api_call(missing, expected):
    args = {k: v for k, v in VALID_SAVE_ARGS.items() if k != missing}
    client = _client(SAVE_RESPONSE)
    env = await episode_report.handle(args, client)
    assert env["data"]["success"] is False
    assert expected in env["data"]["error"]
    client.call_api.assert_not_called()


@pytest.mark.asyncio
async def test_save_size_caps():
    client = _client(SAVE_RESPONSE)
    env = await episode_report.handle(
        {**VALID_SAVE_ARGS, "report_html": "x" * (2 * 1024 * 1024 + 1)}, client)
    assert "2097152" in env["data"]["error"]

    env = await episode_report.handle(
        {**VALID_SAVE_ARGS, "narrative_text": "x" * (256 * 1024 + 1)}, client)
    assert "262144" in env["data"]["error"]
    client.call_api.assert_not_called()


@pytest.mark.asyncio
async def test_save_success_envelope_and_links():
    client = _client(SAVE_RESPONSE)
    env = await episode_report.handle(VALID_SAVE_ARGS, client)

    data = env["data"]
    assert data["success"] is True
    assert data["document"]["id"] == "doc-9"
    assert data["document"]["folder"] == "Episode Reports"
    assert data["document"]["episode_start"] == "2026-07-19T08:21:00+00:00"
    assert data["links"]["view"] == f"{APP_BASE_URL}/api/v1/documents/doc-9/view"

    args, kwargs = client.call_api.call_args
    assert args[0] == "/documents/episode-reports"
    body = kwargs["json"]
    assert body["created_via"] == "usermcp"
    assert "version" not in body           # defaults omitted
    assert "supersedes_document_id" not in body


@pytest.mark.asyncio
async def test_save_forwards_supersession_and_annotations():
    client = _client(SAVE_RESPONSE)
    await episode_report.handle({
        **VALID_SAVE_ARGS,
        "version": 2,
        "supersedes_document_id": "doc-8",
        "annotations": {"spans": [{"label": "thumpy"}]},
    }, client)
    body = client.call_api.call_args.kwargs["json"]
    assert body["version"] == 2
    assert body["supersedes_document_id"] == "doc-8"
    assert body["annotations"]["spans"][0]["label"] == "thumpy"


@pytest.mark.asyncio
async def test_save_api_error_returns_failure_envelope():
    client = AsyncMock()
    client.call_api.side_effect = Exception("503 SCHEMA_NOT_READY")
    env = await episode_report.handle(VALID_SAVE_ARGS, client)
    assert env["data"]["success"] is False
    assert "503" in env["data"]["error"]


def test_save_description_carries_behavioral_gate():
    desc = episode_report.schema().description
    assert "user has confirmed" in desc
    assert "never proactively" in desc


# ---------------------------------------------------------------------------
# list_episode_reports
# ---------------------------------------------------------------------------

LIST_RESPONSE = {
    "reports": [{
        "id": "doc-9",
        "title": "Overnight — 2026-07-19",
        "episode_start": "2026-07-19T08:21:00+00:00",
        "episode_end": "2026-07-19T11:52:00+00:00",
        "version": 1,
        "supersedes_document_id": None,
        "created_at": "2026-07-20T12:00:00+00:00",
        "links": {"view": "/api/v1/documents/doc-9/view"},
    }],
    "pagination": {"total": 1, "limit": 50, "offset": 0, "has_more": False},
}


@pytest.mark.asyncio
async def test_list_forwards_params_and_absolutizes_links():
    client = _client(LIST_RESPONSE)
    env = await episode_report_list.handle(
        {"from": "2026-07-19", "to": "2026-07-20", "limit": 10}, client)

    args, kwargs = client.call_api.call_args
    assert args[0] == "/documents/episode-reports"
    assert kwargs["params"] == {"from": "2026-07-19", "to": "2026-07-20", "limit": 10}

    data = env["data"]
    assert data["success"] is True
    assert data["reports"][0]["links"]["view"].startswith(APP_BASE_URL)
    assert data["pagination"]["total"] == 1
    assert env["next_actions"][0]["tool"] == "get_document"


@pytest.mark.asyncio
async def test_list_latest_only_sent_only_when_disabled():
    client = _client(LIST_RESPONSE)
    await episode_report_list.handle({}, client)
    assert client.call_api.call_args.kwargs["params"] == {}

    await episode_report_list.handle({"latest_only": False}, client)
    assert client.call_api.call_args.kwargs["params"] == {"latest_only": "false"}


@pytest.mark.asyncio
async def test_list_api_error_returns_failure_envelope():
    client = AsyncMock()
    client.call_api.side_effect = Exception("boom")
    env = await episode_report_list.handle({}, client)
    assert env["data"]["success"] is False
    assert env["data"]["reports"] == []
