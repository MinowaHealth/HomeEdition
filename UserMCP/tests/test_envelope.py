"""Tests for tools/_envelope.py — the standard MCP response wrapper.

The envelope is the promise made to every MCP caller: regardless of which
tool runs, the response has the same top-level shape and the coverage
block never lies about what was fetched. Tests here pin that promise.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools._envelope import (
    DEFAULT_DISCLAIMER,
    build_envelope,
    empty_envelope,
    window_block,
)


def test_build_envelope_has_required_top_level_keys():
    env = build_envelope(
        {"items": [1, 2, 3]},
        coverage={"counts": {"rows": 3, "sources_represented": ["manual"]}, "gaps": [], "truncated": False},
        sources=[{"name": "manual", "last_sync": None, "record_count": 3}],
    )
    for key in ("data", "coverage", "sources", "disclaimer", "next_actions"):
        assert key in env, f"missing envelope key: {key}"


def test_build_envelope_uses_default_disclaimer():
    env = build_envelope(
        {},
        coverage={"counts": {"rows": 0, "sources_represented": []}, "gaps": [], "truncated": False},
        sources=[],
    )
    assert env["disclaimer"] == DEFAULT_DISCLAIMER


def test_build_envelope_custom_disclaimer():
    env = build_envelope(
        {},
        coverage={"counts": {"rows": 0, "sources_represented": []}, "gaps": [], "truncated": False},
        sources=[],
        disclaimer="custom note",
    )
    assert env["disclaimer"] == "custom note"


def test_build_envelope_next_actions_defaults_to_empty_list():
    env = build_envelope(
        {"x": 1},
        coverage={"counts": {"rows": 1, "sources_represented": []}, "gaps": [], "truncated": False},
        sources=[],
    )
    assert env["next_actions"] == []


def test_build_envelope_preserves_next_actions():
    actions = [{"tool": "get_other", "args": {}, "why": "test"}]
    env = build_envelope(
        {},
        coverage={"counts": {"rows": 0, "sources_represented": []}, "gaps": [], "truncated": False},
        sources=[],
        next_actions=actions,
    )
    assert env["next_actions"] == actions


def test_window_block_closed_interval():
    w = window_block("2026-04-01", "2026-04-10")
    assert w["from"] == "2026-04-01"
    assert w["to"] == "2026-04-10"


def test_window_block_accepts_date_objects():
    from datetime import date

    w = window_block(date(2026, 4, 1), date(2026, 4, 10))
    assert w["from"] == "2026-04-01"
    assert w["to"] == "2026-04-10"


def test_empty_envelope_flags_reason_as_gap():
    env = empty_envelope(
        reason="no garmin credentials configured",
        window=window_block("2026-04-01", "2026-04-10"),
        sources=[],
    )
    # empty_envelope uses data=[] (empty list) by design
    assert env["data"] == []
    assert env["coverage"]["counts"]["rows"] == 0
    gaps = env["coverage"]["gaps"]
    assert any("garmin" in g.get("reason", "").lower() for g in gaps)
    # Reason-bearing gap carries the window endpoints too
    assert any(
        g.get("from") == "2026-04-01" and g.get("to") == "2026-04-10"
        for g in gaps
    )
