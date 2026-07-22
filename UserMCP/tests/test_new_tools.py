"""Tests for the Phase-2 UserMCP tools.

One envelope-shape test per tool, plus a stress test asserting that
every tool's response stays under the ~8K-token budget (~32KB JSON)
for a realistic 30-day window. These are unit tests; the actual Flask
endpoints are mocked via AsyncMock routers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Shared mock plumbing
# ---------------------------------------------------------------------------

# All tools fetch source-status on every call via fetch_sources(). Providing
# stub responses for those three endpoints from the default router keeps
# individual tests focused on their tool-under-test.
_SOURCE_STUBS = {
    "/diagnostics/table-counts": {"tables": []},
    "/garmin/status": {"connected": False},
    "/healthkit/jobs": {"entries": []},
}


def _make_client(routes: dict):
    """Return an AsyncMock whose call_api looks up `routes` then _SOURCE_STUBS."""
    merged = {**_SOURCE_STUBS, **routes}
    mock = AsyncMock()

    def router(path, **kwargs):
        if path in merged:
            value = merged[path]
            return value(kwargs) if callable(value) else value
        # Startswith match for /documents/<id>, /documents/<id>/annotations, etc.
        for prefix, value in merged.items():
            if prefix.endswith("/*") and path.startswith(prefix[:-1]):
                return value(kwargs) if callable(value) else value
        raise AssertionError(f"Unexpected API call: {path}")

    mock.call_api.side_effect = router
    return mock


def _assert_envelope(env):
    assert isinstance(env, dict)
    for key in ("data", "coverage", "sources", "disclaimer"):
        assert key in env, f"envelope missing {key}"
    assert "counts" in env["coverage"]


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_profile_builds_identity_block():
    from tools.profile import handle

    client = _make_client({
        "/session": {
            "user_id": "u1", "tenant_id": 1, "username": "neal",
            "display_name": "Tester", "home_timezone": "America/Chicago",
            "pronouns": "he/him",
        },
        "/dietary-settings": {"settings": [{"diet": "omnivore", "avoid_list": ["peanuts"]}]},
    })

    env = await handle({}, client)

    _assert_envelope(env)
    assert env["data"]["profile"]["display_name"] == "Tester"
    assert env["data"]["dietary_settings"]["diet"] == "omnivore"
    assert "delegates" not in env["data"]


# ---------------------------------------------------------------------------
# regimen
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regimen_filters_to_active_inputs():
    from tools.regimen import handle

    client = _make_client({
        "/health-inputs": {"inputs": [
            {"id": "i1", "name": "Lisinopril", "is_active": True},
            {"id": "i2", "name": "OldMed", "is_active": False},
        ]},
        "/stacks": {"stacks": [{"id": "s1", "name": "morning"}]},
        "/timeframes": {"timeframes": []},
        "/reminders": {"reminders": []},
    })

    env = await handle({}, client)

    _assert_envelope(env)
    names = [i["name"] for i in env["data"]["inputs"]]
    assert "Lisinopril" in names
    assert "OldMed" not in names


@pytest.mark.asyncio
async def test_regimen_suggests_onboarding_when_empty():
    from tools.regimen import handle

    client = _make_client({
        "/health-inputs": {"inputs": []},
        "/stacks": {"stacks": []},
        "/timeframes": {"timeframes": []},
        "/reminders": {"reminders": []},
    })

    env = await handle({}, client)

    assert env["data"]["inputs"] == []
    assert env["next_actions"]  # at least one onboarding hint


# ---------------------------------------------------------------------------
# clinical_history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clinical_history_flags_med_allergy_overlap():
    from tools.clinical_history import handle

    client = _make_client({
        "/conditions": {"conditions": [{"name": "HTN"}]},
        "/allergies": {"allergies": [{"allergen": "Penicillin", "severity": "severe"}]},
        "/family-history": {"entries": []},
        "/surgical-history": {"entries": []},
        "/vaccinations": {"entries": []},
        "/social-history": {"entries": []},
        "/health-inputs": {"inputs": [
            {"id": "i1", "name": "Amoxicillin-Penicillin Combo",
             "input_type": "medication", "is_active": True},
        ]},
    })

    env = await handle({}, client)

    _assert_envelope(env)
    alerts = env["data"]["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "possible"


# ---------------------------------------------------------------------------
# vitals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vitals_rollup_computes_min_max_avg():
    from tools.vitals import handle

    client = _make_client({
        "/blood-pressure": {"readings": [
            {"systolic": 120, "diastolic": 80, "measured_at": "2026-04-01"},
            {"systolic": 130, "diastolic": 85, "measured_at": "2026-04-02"},
        ]},
        # live vitals API returns the value under a named key, not 'value'
        "/weight": {"entries": [{"weight": 185.0, "unit": "lbs", "timestamp": "2026-04-01"}]},
        "/temperature": {"readings": []},
        "/blood-glucose": {"entries": [
            {"blood_glucose": 99.0, "unit": "mg/dL", "timestamp": "2026-04-01"},
            {"blood_glucose": 110.0, "unit": "mg/dL", "timestamp": "2026-04-02"},
        ]},
    })

    env = await handle({"days": 30}, client)

    _assert_envelope(env)
    bp = env["data"]["blood_pressure"]
    assert bp["rollup_systolic"]["count"] == 2
    assert bp["rollup_systolic"]["min"] == 120
    assert bp["rollup_systolic"]["max"] == 130
    assert env["data"]["weight"]["rollup"]["count"] == 1
    glucose = env["data"]["blood_glucose"]
    assert glucose["rollup"] == {"count": 2, "min": 99.0, "max": 110.0, "avg": 104.5}


# ---------------------------------------------------------------------------
# labs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_labs_groups_results_by_test():
    from tools.labs import handle

    client = _make_client({
        "/lab-results": {"results": [
            {"name": "HbA1c", "loinc_code": "4548-4", "value": 5.7, "date": "2026-04-01"},
            {"name": "LDL", "loinc_code": "13457-7", "value": 110, "date": "2026-04-01"},
        ]},
    })

    env = await handle({}, client)

    _assert_envelope(env)
    groups = env["data"]["groups"]
    assert len(groups) == 2
    hba1c = next(g for g in groups if g["name"] == "HbA1c")
    assert hba1c["latest"]["value"] == 5.7
    assert hba1c["trend_available"] is False


# ---------------------------------------------------------------------------
# garmin_detail (get_garmin_minute_detail)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_garmin_detail_passes_at_and_wraps_samples():
    from tools.garmin_detail import handle

    captured = {}

    def route(kwargs):
        captured["params"] = kwargs.get("params")
        return {
            "target": "2026-07-13T18:00:00+00:00",
            "window": {"from": "2026-07-13T17:00:00+00:00",
                       "to": "2026-07-13T19:00:00+00:00", "minutes": 121},
            "samples": [
                {"minute": "2026-07-13T18:00:00+00:00",
                 "heart_rate": 70, "respiratory_rate": 14.2, "stress": None},
            ],
            "counts": {"heart_rate": 2, "respiratory_rate": 1,
                       "stress": 0, "minutes": 1},
            "truncated_future": False,
        }

    client = _make_client({"/garmin/minute-detail": route})
    env = await handle({"at": "2026-07-13T18:00:00Z"}, client)

    _assert_envelope(env)
    assert captured["params"] == {"at": "2026-07-13T18:00:00Z"}
    assert len(env["data"]["samples"]) == 1
    assert env["coverage"]["window"]["minutes"] == 121
    assert env["coverage"]["counts"]["heart_rate"] == 2
    assert env["coverage"]["gaps"] == []


@pytest.mark.asyncio
async def test_garmin_detail_requires_at():
    from tools.garmin_detail import handle

    client = _make_client({})  # route must never be called
    env = await handle({}, client)

    assert env["data"]["samples"] == []
    assert any("required" in g["reason"] for g in env["coverage"]["gaps"])


@pytest.mark.asyncio
async def test_garmin_detail_empty_window_gaps():
    from tools.garmin_detail import handle

    client = _make_client({"/garmin/minute-detail": {
        "target": "2020-01-01T12:00:00+00:00",
        "window": {"from": "...", "to": "...", "minutes": 121},
        "samples": [], "counts": {"minutes": 0}, "truncated_future": False,
    }})
    env = await handle({"at": "2020-01-01T12:00:00Z"}, client)

    reasons = " ".join(g["reason"] for g in env["coverage"]["gaps"])
    assert "no Garmin per-minute data" in reasons
    assert env["coverage"]["truncated"] is False


@pytest.mark.asyncio
async def test_garmin_detail_recent_target_truncated():
    from tools.garmin_detail import handle

    client = _make_client({"/garmin/minute-detail": {
        "target": "2026-07-13T18:00:00+00:00",
        "window": {"from": "...", "to": "...", "minutes": 121},
        "samples": [{"minute": "2026-07-13T17:30:00+00:00",
                     "heart_rate": 66, "respiratory_rate": None, "stress": 20}],
        "counts": {"heart_rate": 1, "respiratory_rate": 0,
                   "stress": 1, "minutes": 1},
        "truncated_future": True,
    }})
    env = await handle({"at": "2026-07-13T18:00:00Z"}, client)

    assert env["coverage"]["truncated"] is True
    assert any("into the future" in g["reason"] for g in env["coverage"]["gaps"])


# ---------------------------------------------------------------------------
# sleep_events (get_sleep_events_detail)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sleep_events_passes_at_and_wraps_events():
    from tools.sleep_events import handle

    captured = {}

    def route(kwargs):
        captured["params"] = kwargs.get("params")
        return {
            "target": "2026-07-12T17:30:00+00:00",
            "window": {"from": "2026-07-12T16:30:00+00:00",
                       "to": "2026-07-12T18:30:00+00:00", "minutes": 121},
            "events": [
                {"start": "2026-07-12T17:20:00+00:00",
                 "end": "2026-07-12T17:50:00+00:00", "sleep_type": "light",
                 "duration_seconds": 1800, "contains_target": True},
            ],
            "stage_at_target": "light",
            "counts": {"events": 1, "by_type": {"light": 1},
                       "in_window_seconds_by_type": {"light": 1800}},
            "truncated_future": False,
        }

    client = _make_client({"/garmin/sleep-events": route})
    env = await handle({"at": "2026-07-12T17:30:00Z"}, client)

    _assert_envelope(env)
    assert captured["params"] == {"at": "2026-07-12T17:30:00Z"}
    assert env["data"]["stage_at_target"] == "light"
    assert env["data"]["in_window_seconds_by_type"] == {"light": 1800}
    assert env["coverage"]["counts"]["by_type"] == {"light": 1}
    assert env["coverage"]["gaps"] == []


@pytest.mark.asyncio
async def test_sleep_events_requires_at():
    from tools.sleep_events import handle

    client = _make_client({})  # route must never be called
    env = await handle({}, client)

    assert env["data"]["events"] == []
    assert any("required" in g["reason"] for g in env["coverage"]["gaps"])


@pytest.mark.asyncio
async def test_sleep_events_empty_window_gaps():
    from tools.sleep_events import handle

    client = _make_client({"/garmin/sleep-events": {
        "target": "2026-07-12T14:00:00+00:00",
        "window": {"from": "...", "to": "...", "minutes": 121},
        "events": [], "stage_at_target": None,
        "counts": {"events": 0, "by_type": {}, "in_window_seconds_by_type": {}},
        "truncated_future": False,
    }})
    env = await handle({"at": "2026-07-12T14:00:00Z"}, client)

    reasons = " ".join(g["reason"] for g in env["coverage"]["gaps"])
    assert "no sleep events" in reasons
    assert env["coverage"]["truncated"] is False


@pytest.mark.asyncio
async def test_sleep_events_recent_target_truncated():
    from tools.sleep_events import handle

    client = _make_client({"/garmin/sleep-events": {
        "target": "2026-07-13T05:00:00+00:00",
        "window": {"from": "...", "to": "...", "minutes": 121},
        "events": [{"start": "2026-07-13T04:30:00+00:00",
                    "end": "2026-07-13T05:00:00+00:00", "sleep_type": "deep",
                    "duration_seconds": 1800, "contains_target": False}],
        "stage_at_target": None,
        "counts": {"events": 1, "by_type": {"deep": 1},
                   "in_window_seconds_by_type": {"deep": 1800}},
        "truncated_future": True,
    }})
    env = await handle({"at": "2026-07-13T05:00:00Z"}, client)

    assert env["coverage"]["truncated"] is True
    assert any("into the future" in g["reason"] for g in env["coverage"]["gaps"])


# ---------------------------------------------------------------------------
# wearables
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wearables_flags_disconnected_garmin():
    from tools.wearables import handle

    client = _make_client({
        "/dashboard": {
            "window": {"days": 30, "start": "2026-03-19", "end": "2026-04-18"},
            "wearable": {"days_available": 0, "total_steps": 0},
        },
        "/garmin/status": {"connected": False},
        "/healthkit/jobs": {"entries": []},
    })

    env = await handle({"days": 30}, client)

    _assert_envelope(env)
    assert env["data"]["connections"]["garmin"]["connected"] is False
    gap_sources = [g.get("source") for g in env["coverage"]["gaps"]]
    assert "garmin" in gap_sources


# ---------------------------------------------------------------------------
# activity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_activity_detects_truncation_from_has_more():
    from tools.activity import handle

    client = _make_client({
        "/all-logs": {
            "entries": [{"id": i, "logged_at": "2026-04-10"} for i in range(50)],
            "pagination": {"has_more": True, "next_offset": 50},
        },
    })

    env = await handle({"days": 14, "limit": 50}, client)

    _assert_envelope(env)
    assert env["coverage"]["truncated"] is True


@pytest.mark.asyncio
async def test_activity_gap_when_server_omits_applied():
    """Old backend (no `applied` echo) → coverage must flag unconfirmed filters."""
    from tools.activity import handle

    client = _make_client({
        "/all-logs": {"entries": [{"id": 1, "logged_at": "2026-05-10"}]},
    })

    env = await handle({"from": "2026-05-01", "to": "2026-05-15"}, client)

    reasons = " ".join(g["reason"] for g in env["coverage"]["gaps"])
    assert "did not confirm" in reasons


@pytest.mark.asyncio
async def test_activity_no_gaps_when_applied_matches():
    from tools.activity import handle

    client = _make_client({
        "/all-logs": {
            "entries": [{"id": 1, "logged_at": "2026-05-10"}],
            "applied": {
                "start_date": "2026-05-01",
                "end_date": "2026-05-15",
                "kind": "medication",
                "input_id": None,
                "sources_truncated": [],
            },
        },
    })

    env = await handle(
        {"from": "2026-05-01", "to": "2026-05-15", "kind": "medication"}, client)

    assert env["coverage"]["gaps"] == []
    assert env["coverage"]["truncated"] is False


@pytest.mark.asyncio
async def test_activity_gap_when_kind_not_applied():
    from tools.activity import handle

    client = _make_client({
        "/all-logs": {
            "entries": [{"id": 1, "logged_at": "2026-05-10"}],
            "applied": {
                "start_date": "2026-05-01",
                "end_date": "2026-05-15",
                "kind": None,
                "input_id": None,
                "sources_truncated": [],
            },
        },
    })

    env = await handle(
        {"from": "2026-05-01", "to": "2026-05-15", "kind": "observation"}, client)

    reasons = " ".join(g["reason"] for g in env["coverage"]["gaps"])
    assert "kind filter 'observation' not applied" in reasons


@pytest.mark.asyncio
async def test_activity_truncated_sources_set_truncated_and_gap():
    from tools.activity import handle

    client = _make_client({
        "/all-logs": {
            "entries": [{"id": 1, "logged_at": "2026-05-10"}],
            "applied": {
                "start_date": "2026-05-01",
                "end_date": "2026-05-15",
                "kind": None,
                "input_id": None,
                "sources_truncated": ["health_input_log"],
            },
        },
    })

    env = await handle({"from": "2026-05-01", "to": "2026-05-15"}, client)

    assert env["coverage"]["truncated"] is True
    reasons = " ".join(g["reason"] for g in env["coverage"]["gaps"])
    assert "health_input_log" in reasons


# ---------------------------------------------------------------------------
# nutrition
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nutrition_flags_dietary_violations():
    from tools.nutrition import handle

    client = _make_client({
        "/food-log": {"entries": [
            {"id": "f1", "name": "Peanut butter sandwich", "eaten_at": "2026-04-15",
             "calories": 400, "protein_g": 12, "fat_g": 20},
            {"id": "f2", "name": "Banana", "eaten_at": "2026-04-15", "calories": 100},
        ]},
        "/dietary-settings": {"settings": [{"avoid_list": ["peanut"]}]},
    })

    env = await handle({"days": 7}, client)

    _assert_envelope(env)
    violations = env["data"]["violations"]
    assert len(violations) == 1
    assert violations[0]["matched_term"] == "peanut"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_surfaces_mode_in_coverage():
    from tools.search import handle

    client = _make_client({
        "/search": {
            "mode": "keyword",
            "results": [{"table": "observations", "id": "o1", "text": "Elevated BP"}],
        },
    })

    env = await handle({"q": "blood pressure"}, client)

    _assert_envelope(env)
    assert env["coverage"]["mode"] == "keyword"
    assert len(env["data"]) == 1


@pytest.mark.asyncio
async def test_search_keyword_mode_emits_degradation_gap():
    """Keyword-only mode must be an explicit gap, not just a mode field."""
    from tools.search import handle

    client = _make_client({
        "/search": {
            "mode": "keyword",
            "results": [{"table": "observations", "id": "o1", "text": "Elevated BP"}],
        },
    })

    env = await handle({"q": "blood pressure"}, client)

    reasons = " ".join(g["reason"] for g in env["coverage"]["gaps"])
    assert "semantic search unavailable" in reasons


@pytest.mark.asyncio
async def test_search_semantic_mode_has_no_degradation_gap():
    from tools.search import handle

    client = _make_client({
        "/search": {
            "mode": "semantic",
            "results": [{"table": "observations", "id": "o1", "text": "Elevated BP"}],
        },
    })

    env = await handle({"q": "blood pressure"}, client)

    assert env["coverage"]["gaps"] == []


@pytest.mark.asyncio
async def test_search_suggests_document_follow_up():
    from tools.search import handle

    client = _make_client({
        "/search": {
            "mode": "semantic",
            "results": [{"table": "document_annotations", "id": "doc-42", "text": "Lab panel"}],
        },
    })

    env = await handle({"q": "lab panel"}, client)

    assert env["next_actions"]
    action = env["next_actions"][0]
    assert action["tool"] == "get_document"
    assert action["args"]["document_id"] == "doc-42"


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_document_rejects_missing_id():
    from tools.documents import handle

    client = _make_client({})
    env = await handle({}, client)

    assert env["coverage"]["gaps"][0]["reason"].startswith("document_id is required")


@pytest.mark.asyncio
async def test_document_returns_pages_and_annotations():
    from tools.documents import handle

    client = _make_client({
        "/documents/doc-1": {
            "id": "doc-1", "title": "Lab Results", "category": "labs",
            "pages": [{"page": 1, "text": "HbA1c 5.7"}],
        },
        "/documents/doc-1/annotations": [{"text": "flagged"}],
    })

    env = await handle({"document_id": "doc-1"}, client)

    _assert_envelope(env)
    assert env["data"]["document"]["title"] == "Lab Results"
    assert len(env["data"]["pages"]) == 1
    assert len(env["data"]["annotations"]) == 1


# ---------------------------------------------------------------------------
# Context-window stress: every tool's serialized envelope fits in ~8K tokens.
# ---------------------------------------------------------------------------

# 8K tokens ≈ 32KB at ~4 chars/token. Tools with realistic 30-day windows
# should leave plenty of headroom for the LLM's reasoning context.
_SIZE_CEILING_BYTES = 32_000


@pytest.mark.asyncio
async def test_context_window_budget_for_each_tool():
    """Assert each tool's JSON response stays under the 8K-token budget.

    We fake plausibly-sized responses per tool (30-day windows worth) and
    measure the serialized envelope. Regressions here mean one tool has
    started returning too much data for the main-context budget.
    """
    from tools import profile, regimen, clinical_history, vitals, labs
    from tools import wearables, activity, nutrition
    from tools import search as search_mod, documents

    # Thirty days of 3 BP + 1 weight reading per day — well above typical.
    bp_entries = [
        {"systolic": 120 + (i % 10), "diastolic": 80 + (i % 5),
         "measured_at": f"2026-04-{(i % 28) + 1:02d}"}
        for i in range(30 * 3)
    ]
    all_log_entries = [
        {"id": f"e{i}", "kind": "medication", "name": "Lisinopril",
         "logged_at": f"2026-04-{(i % 28) + 1:02d}"}
        for i in range(50)
    ]
    food_entries = [
        {"id": f"f{i}", "name": "meal", "eaten_at": f"2026-04-{(i % 28) + 1:02d}",
         "calories": 500, "protein_g": 20, "carbs_g": 50, "fat_g": 20}
        for i in range(90)
    ]

    tool_cases = [
        ("profile", profile.handle, {}, {
            "/session": {"user_id": "u1", "display_name": "Tester"},
            "/dietary-settings": {"settings": [{"diet": "omnivore"}]},
        }),
        ("regimen", regimen.handle, {}, {
            "/health-inputs": {"inputs": [
                {"id": f"i{i}", "name": f"Med {i}", "is_active": True,
                 "doses_per_day": 2}
                for i in range(20)
            ]},
            "/stacks": {"stacks": []},
            "/timeframes": {"timeframes": []},
            "/reminders": {"reminders": []},
        }),
        ("clinical_history", clinical_history.handle, {}, {
            "/conditions": {"conditions": [{"name": "HTN"}, {"name": "T2DM"}]},
            "/allergies": {"allergies": [{"allergen": "Penicillin"}]},
            "/family-history": {"entries": []},
            "/surgical-history": {"entries": []},
            "/vaccinations": {"entries": []},
            "/social-history": {"entries": []},
            "/health-inputs": {"inputs": []},
        }),
        ("vitals", vitals.handle, {"days": 30}, {
            "/blood-pressure": {"readings": bp_entries},
            "/weight": {"readings": [{"value": 80, "measured_at": "2026-04-15"}]},
            "/temperature": {"readings": []},
        }),
        ("labs", labs.handle, {}, {
            "/lab-results": {"results": [
                {"test": t, "name": t.upper(), "value": 5.7,
                 "observed_at": "2026-04-01"}
                for t in ("hba1c", "ldl", "hdl", "tg", "tsh")
            ]},
        }),
        ("wearables", wearables.handle, {"days": 30}, {
            "/dashboard": {
                "window": {"days": 30},
                "wearable": {"days_available": 28, "total_steps": 220_000},
            },
            "/garmin/status": {"connected": True, "last_sync": "2026-04-18T08:00:00Z"},
            "/healthkit/jobs": {"entries": [{"id": "j1", "status": "complete"}]},
        }),
        ("activity", activity.handle, {"days": 14, "limit": 50}, {
            "/all-logs": {"entries": all_log_entries,
                          "pagination": {"has_more": False}},
        }),
        ("nutrition", nutrition.handle, {"days": 30}, {
            "/food-log": {"entries": food_entries},
            "/dietary-settings": {"settings": [{"avoid_list": []}]},
        }),
        ("search", search_mod.handle, {"q": "blood pressure"}, {
            "/search": {"mode": "semantic", "results": [
                {"table": "observations", "id": f"o{i}",
                 "text": "sample observation text"}
                for i in range(10)
            ]},
        }),
        ("documents", documents.handle, {"document_id": "d1"}, {
            "/documents/d1": {
                "id": "d1", "title": "Panel",
                "pages": [{"page": p, "text": "text " * 40} for p in range(5)],
            },
            "/documents/d1/annotations": [],
        }),
    ]

    for name, handle_fn, args, routes in tool_cases:
        client = _make_client(routes)
        env = await handle_fn(args, client)
        size = len(json.dumps(env, default=str))
        assert size < _SIZE_CEILING_BYTES, (
            f"{name} envelope is {size} bytes, over the {_SIZE_CEILING_BYTES}-byte ceiling"
        )
