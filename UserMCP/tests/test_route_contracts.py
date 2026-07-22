"""Cross-layer param-contract tests — the Bug-3b class anti-regression.

minowa-mcp-bug-report.md Bug 3b: the adherence tool sent `from`/`to` while
the route read `start_date`/`end_date`, so the window was silently dropped
and a test that mocked below the boundary enshrined the bug. These tests
pin the HTTP contract from the MCP side: exact outgoing param names,
YYYY-MM-DD date formats, and the kind/scope enums.

TWIN FILE: `UserApp/webapp/tests/test_route_contracts.py` pins the same
contract from the route side. The two suites cannot import each other
(separate services), so CONTRACT is duplicated literally in both and
guarded by a shared content hash — editing one side fails that side's
hash test until BOTH files (and the pinned hash) are updated together.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import activity, adherence, chat_summary, labs, search

# ---------------------------------------------------------------------------
# The contract table — keep byte-identical with the twin file.
# ---------------------------------------------------------------------------

CONTRACT = {
    "tools": {
        "get_recent_activity": {
            "path": "/all-logs",
            "sends": {"from": "start_date", "to": "end_date", "kind": "kind",
                      "input_id": "input_id", "limit": "limit"},
            "route_reads_dates_via": "parse_date_range_params",
            "route_must_not_read": ["from", "to"],
        },
        "search_my_data": {
            "path": "/search",
            "sends": {"q": "q", "scope": "scope", "k": "k", "mode": "mode",
                      "from": "from", "to": "to"},
            "route_reads_dates_via": "request.args",
            "route_must_not_read": ["start_date", "end_date"],
        },
        "get_adherence_report": {
            "path": "/adherence",
            "sends": {"from": "start_date", "to": "end_date",
                      "input_ids": "input_ids"},
            "route_reads_dates_via": "parse_date_range_params",
            "route_must_not_read": ["from", "to"],
        },
        "get_lab_history": {
            "path": "/lab-results",
            "sends": {},
            "route_reads_dates_via": None,
            "route_must_not_read": [],
        },
        "save_chat_summary": {
            "path": "/documents/chat-summaries",
            "sends_json": ["title", "summary_markdown", "created_via",
                           "model_id", "source_tools", "session_started_at"],
            "route_reads_dates_via": None,
            "route_must_not_read": [],
        },
    },
    "kind": {
        "mcp_enum": ["all", "medication", "food", "observation", "sync"],
        "route_applies": ["food", "medication", "observation", "sync"],
    },
    "scopes": ["all", "allergies", "conditions",
               "documents", "food", "inputs", "notes", "observations"],
    "modes": ["auto", "semantic", "keyword"],
}

CONTRACT_SHA256 = "60a063bec93b376132527b114e2a80bd4a2ab7c5002c17300f4603d92560d43b"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def test_contract_hash_pinned():
    """Fails when CONTRACT changes — update the twin file and the pinned
    hash in BOTH files in the same commit."""
    digest = hashlib.sha256(
        json.dumps(CONTRACT, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert digest == CONTRACT_SHA256, (
        "CONTRACT changed. Update UserApp/webapp/tests/test_route_contracts.py "
        f"to match and pin CONTRACT_SHA256 = \"{digest}\" in both files."
    )


# ---------------------------------------------------------------------------
# Capturing client
# ---------------------------------------------------------------------------

_SOURCE_STUBS = {
    "/diagnostics/table-counts": {"tables": []},
    "/garmin/status": {"connected": False},
    "/healthkit/jobs": {"entries": []},
}


def _capturing_client(path: str, response):
    """AsyncMock client that records the params sent to `path`."""
    captured: dict = {"called": False}
    mock = AsyncMock()

    def router(p, **kwargs):
        if p == path:
            captured["called"] = True
            captured["params"] = kwargs.get("params")
            captured["json"] = kwargs.get("json")
            return response
        if p in _SOURCE_STUBS:
            return _SOURCE_STUBS[p]
        raise AssertionError(f"Unexpected API call: {p}")

    mock.call_api.side_effect = router
    return mock, captured


# ---------------------------------------------------------------------------
# get_recent_activity → /all-logs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activity_sends_contract_params():
    spec = CONTRACT["tools"]["get_recent_activity"]
    client, captured = _capturing_client(
        spec["path"], {"entries": [], "applied": {}})

    await activity.handle({
        "from": "2026-05-01", "to": "2026-05-15",
        "kind": "medication", "input_id": "uuid-1", "limit": 25,
    }, client)

    assert captured["called"]
    params = captured["params"]
    assert set(params) == set(spec["sends"].values())
    assert params["kind"] == "medication"
    assert params["input_id"] == "uuid-1"
    assert params["limit"] == 25
    assert DATE_RE.match(params["start_date"])
    assert DATE_RE.match(params["end_date"])
    assert params["start_date"] == "2026-05-01"
    assert params["end_date"] == "2026-05-15"
    # The MCP arg names must never leak across the boundary.
    for mcp_name in spec["route_must_not_read"]:
        assert mcp_name not in params


@pytest.mark.asyncio
async def test_activity_default_args_send_only_window_and_limit():
    """kind=all and no input_id must not be sent at all — the route treats
    an unknown kind as not-applied and we don't want noise params."""
    spec = CONTRACT["tools"]["get_recent_activity"]
    client, captured = _capturing_client(
        spec["path"], {"entries": [], "applied": {}})

    await activity.handle({"days": 7}, client)

    assert set(captured["params"]) == {"start_date", "end_date", "limit"}


# ---------------------------------------------------------------------------
# search_my_data → /search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_sends_contract_params():
    spec = CONTRACT["tools"]["search_my_data"]
    client, captured = _capturing_client(
        spec["path"], {"results": [], "mode": "semantic"})

    await search.handle({
        "q": "Allegra", "scope": "observations", "k": 10, "mode": "keyword",
        "from": "2026-05-01", "to": "2026-05-31",
    }, client)

    params = captured["params"]
    assert set(params) == set(spec["sends"].values())
    assert params["q"] == "Allegra"
    assert params["scope"] == "observations"
    assert params["k"] == 10
    assert params["mode"] == "keyword"
    assert params["from"] == "2026-05-01"
    assert params["to"] == "2026-05-31"


@pytest.mark.asyncio
async def test_search_auto_mode_not_sent():
    """mode=auto is the route default — sending it is noise; omitting it
    keeps old-route compatibility during rollout."""
    spec = CONTRACT["tools"]["search_my_data"]
    client, captured = _capturing_client(
        spec["path"], {"results": [], "mode": "semantic"})

    await search.handle({"q": "Allegra"}, client)

    assert "mode" not in captured["params"]


# ---------------------------------------------------------------------------
# get_adherence_report → /adherence  (the original Bug 3b)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_adherence_sends_contract_params():
    spec = CONTRACT["tools"]["get_adherence_report"]
    client, captured = _capturing_client(
        spec["path"],
        {"inputs": [], "excluded_prn": [], "excluded_unspecified": []})

    await adherence.handle({
        "from": "2026-06-01", "to": "2026-06-30",
        "input_ids": ["u1", "u2"],
    }, client)

    params = captured["params"]
    assert set(params) == set(spec["sends"].values())
    assert params["start_date"] == "2026-06-01"
    assert params["end_date"] == "2026-06-30"
    assert params["input_ids"] == "u1,u2"
    for mcp_name in spec["route_must_not_read"]:
        assert mcp_name not in params, (
            f"'{mcp_name}' sent to /adherence — the route reads "
            "start_date/end_date and silently drops it (Bug 3b)"
        )


# ---------------------------------------------------------------------------
# get_lab_history → /lab-results
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_labs_sends_no_params():
    spec = CONTRACT["tools"]["get_lab_history"]
    client, captured = _capturing_client(spec["path"], {"results": []})

    await labs.handle({}, client)

    assert captured["called"]
    assert not captured["params"]


# ---------------------------------------------------------------------------
# save_chat_summary → POST /documents/chat-summaries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_summary_sends_contract_body():
    spec = CONTRACT["tools"]["save_chat_summary"]
    client, captured = _capturing_client(
        spec["path"], {"id": "d1", "title": "T", "created_at": "2026-07-15",
                       "links": {"web": "/x", "download": "/y"}})

    await chat_summary.handle({
        "title": "Lab review", "summary_markdown": "# Notes",
        "model_id": "claude-x", "source_tools": ["search_my_data"],
        "session_started_at": "2026-07-15T10:00:00Z",
    }, client)

    assert captured["called"]
    body = captured["json"]
    assert set(body) == set(spec["sends_json"])
    assert body["title"] == "Lab review"
    assert body["summary_markdown"] == "# Notes"
    assert body["created_via"] == "usermcp"


# ---------------------------------------------------------------------------
# Enum drift guards
# ---------------------------------------------------------------------------

def test_search_mode_enum_matches_contract():
    props = search.schema().inputSchema["properties"]
    assert props["mode"]["enum"] == CONTRACT["modes"]


def test_activity_kind_enum_matches_contract():
    props = activity.schema().inputSchema["properties"]
    assert props["kind"]["enum"] == CONTRACT["kind"]["mcp_enum"]


def test_search_scope_enum_matches_contract():
    assert search._ALLOWED_SCOPES == set(CONTRACT["scopes"])
    props = search.schema().inputSchema["properties"]
    assert props["scope"]["enum"] == sorted(CONTRACT["scopes"])


# ---------------------------------------------------------------------------
# Coverage-honesty invariants (requested vs applied)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activity_gap_when_window_not_applied():
    client, _ = _capturing_client("/all-logs", {
        "entries": [{"id": 1}],
        "applied": {"start_date": "2026-06-15", "end_date": "2026-07-15"},
    })

    env = await activity.handle(
        {"from": "2026-05-01", "to": "2026-05-15"}, client)

    reasons = [g["reason"] for g in env["coverage"]["gaps"]]
    assert any("date window not applied" in r for r in reasons)


@pytest.mark.asyncio
async def test_activity_gap_when_input_id_not_applied():
    client, _ = _capturing_client("/all-logs", {
        "entries": [{"id": 1}],
        "applied": {"start_date": "2026-05-01", "end_date": "2026-05-15",
                    "input_id": None},
    })

    env = await activity.handle(
        {"from": "2026-05-01", "to": "2026-05-15", "input_id": "uuid-1"},
        client)

    reasons = [g["reason"] for g in env["coverage"]["gaps"]]
    assert any("input_id filter not applied" in r for r in reasons)
