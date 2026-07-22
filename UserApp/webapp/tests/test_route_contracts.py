"""Cross-layer param-contract tests — route side (Bug-3b class anti-regression).

minowa-mcp-bug-report.md Bug 3b: the adherence MCP tool sent `from`/`to`
while the route read `start_date`/`end_date`, so the window was silently
dropped. These tests pin the contract from the route side: which query
params each route reads (asserted against the route source, so a param
rename fails immediately), the kind mapping, and the scope keys.

TWIN FILE: `UserMCP/tests/test_route_contracts.py` pins the same contract
from the MCP-tool side. The two suites cannot import each other (separate
services), so CONTRACT is duplicated literally in both and guarded by a
shared content hash — editing one side fails that side's hash test until
BOTH files (and the pinned hash) are updated together.
"""
from __future__ import annotations

import hashlib
import inspect
import json

import pytest

from routes import logging_routes
from routes import search as search_routes

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
            "sends": {"q": "q", "scope": "scope", "k": "k",
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
    },
    "kind": {
        "mcp_enum": ["all", "medication", "food", "observation"],
        "route_applies": ["food", "medication"],
    },
    "scopes": ["all", "allergies", "conditions",
               "documents", "food", "inputs", "notes", "observations"],
}

CONTRACT_SHA256 = "4454f05b910ef8c08dae6cad0d40c55a5ba8481e19081f9b55db4c708c0c2a90"


def test_contract_hash_pinned():
    """Fails when CONTRACT changes — update the twin file and the pinned
    hash in BOTH files in the same commit."""
    digest = hashlib.sha256(
        json.dumps(CONTRACT, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert digest == CONTRACT_SHA256, (
        "CONTRACT changed. Update UserMCP/tests/test_route_contracts.py "
        f"to match and pin CONTRACT_SHA256 = \"{digest}\" in both files."
    )


# ---------------------------------------------------------------------------
# Which params each route reads — source-level pins. A rename of any
# contracted query param in the route breaks these before it can silently
# strand the MCP tool's request (the Bug-3b failure shape).
# ---------------------------------------------------------------------------

class TestRouteReadsContractParams:
    def test_all_logs_reads_contract_params(self):
        src = inspect.getsource(logging_routes.get_all_logs)
        assert "parse_date_range_params" in src
        assert "request.args.get('kind')" in src
        assert "request.args.get('input_id')" in src
        for bad in CONTRACT["tools"]["get_recent_activity"]["route_must_not_read"]:
            assert f"request.args.get('{bad}')" not in src

    def test_adherence_reads_contract_params(self):
        src = inspect.getsource(logging_routes.get_adherence)
        assert "parse_date_range_params" in src, (
            "/adherence stopped reading start_date/end_date — the MCP tool "
            "sends exactly those names (Bug 3b)"
        )
        assert "request.args.get('input_ids')" in src
        for bad in CONTRACT["tools"]["get_adherence_report"]["route_must_not_read"]:
            assert f"request.args.get('{bad}')" not in src

    def test_search_reads_contract_params(self):
        src = inspect.getsource(search_routes.search_user_data)
        for param in ("q", "scope", "from", "to"):
            assert f"request.args.get('{param}'" in src
        for bad in CONTRACT["tools"]["search_my_data"]["route_must_not_read"]:
            assert f"request.args.get('{bad}')" not in src


# ---------------------------------------------------------------------------
# Enum drift guards
# ---------------------------------------------------------------------------

def test_search_scopes_match_contract():
    assert set(search_routes._SCOPES.keys()) == set(CONTRACT["scopes"])


class TestKindContractBehavior:
    """Every MCP kind enum value, driven from the contract table: values the
    route maps must be echoed as applied; the rest run unfiltered and are
    reported not-applied (honesty over rejection)."""

    @pytest.mark.parametrize("kind", CONTRACT["kind"]["mcp_enum"])
    def test_kind_applied_iff_route_maps_it(self, client, mock_db, auth_headers, kind):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get(f'/api/v1/all-logs?kind={kind}', headers=auth_headers)
        assert resp.status_code == 200
        applied_kind = resp.get_json()['applied']['kind']
        if kind in CONTRACT["kind"]["route_applies"]:
            assert applied_kind == kind
        else:
            assert applied_kind is None


# ---------------------------------------------------------------------------
# Adherence SQL pins — the join filters and dedup the schedule math relies
# on. These are SQL-level behaviors a mocked cursor can't exercise, so pin
# them at the source level instead of silently losing them in a refactor.
# ---------------------------------------------------------------------------

def test_adherence_sql_filters_pinned():
    src = inspect.getsource(logging_routes.get_adherence)
    assert "hi.is_active = true" in src
    assert "s.is_active = true" in src, "inactive stacks must not schedule doses"
    assert "tf.is_active = true" in src, "inactive timeframes must not schedule doses"
    assert "DISTINCT jsonb_build_object" in src, (
        "timeframe dedup lost — two stacks sharing a timeframe would "
        "double-count expected doses"
    )
    assert "'custom_days', tf.custom_days" in src
    assert "'start_date', tf.start_date" in src


def test_lab_results_latest_ordering_pinned():
    """Postgres DESC sorts NULLs first, so without NULLS LAST a NULL-dated
    duplicate shadows the dated draw in every latest-per-test group
    (found during the Bug-4 backfill: 37 dated codes, only 3 surfaced)."""
    from routes import analytics
    src = inspect.getsource(analytics.get_lab_results)
    assert src.count("effective_date DESC NULLS LAST") == 2
