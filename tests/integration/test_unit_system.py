"""End-to-end unit-system tests against a running appliance.

Repeatable version of the manual verification for the imperial/metric
preference work:

- convert-on-read for blood glucose, weight, and temperature
- the mixed-unit dashboard rollup regression (mg/dL + mmol/L rows used to
  average raw values into nonsense, e.g. 100 and 5.5 -> 52.75)
- POST default unit driven by the preference (not hardcoded F/lbs/mg-dL)
- PATCH /settings/unit-system contract, including rejection of bad values

Integration test: needs the stack up (docker compose, CLAUDE.md § Deployment)
with the seeded test login. SKIPS — never fails — when the stack is
unreachable, so a plain ``pytest`` run without the appliance stays green.

Creates its own rows and deletes them; saves and restores the login's
unit_system preference. One login per run (the /login rate limit allows 5/min).

Env overrides:
    UNIT_TEST_API_BASE  default http://localhost:80
    UNIT_TEST_EMAIL     default test@example.com
    UNIT_TEST_PASSWORD  default Password2026
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

httpx = pytest.importorskip("httpx")

API_BASE = os.environ.get("UNIT_TEST_API_BASE", "http://localhost:80").rstrip("/")
EMAIL = os.environ.get("UNIT_TEST_EMAIL", "test@example.com")
PASSWORD = os.environ.get("UNIT_TEST_PASSWORD", "Password2026")


def _stamp(minutes_ago: int) -> str:
    return (datetime.now() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M")


@pytest.fixture(scope="module")
def client():
    c = httpx.Client(base_url=API_BASE, timeout=10)
    try:
        resp = c.post("/login", json={"email": EMAIL, "password": PASSWORD})
    except httpx.TransportError:
        pytest.skip(f"appliance not reachable at {API_BASE}")
    if resp.status_code != 200 or not resp.json().get("success"):
        pytest.skip(f"test login {EMAIL} unavailable (HTTP {resp.status_code})")
    yield c
    c.close()


@pytest.fixture(scope="module")
def restore_preference(client):
    original = client.get("/api/v1/session").json().get("unit_system", "imperial")
    yield
    client.patch("/api/v1/settings/unit-system", json={"unit_system": original})


def _entry_ids(client, path: str) -> set[str]:
    return {e["id"] for e in client.get(path).json()["entries"]}


def _entries_by_id(client, path: str, ids: set[str]) -> list[dict]:
    return [e for e in client.get(path).json()["entries"] if e["id"] in ids]


def _set_system(client, system: str):
    resp = client.patch("/api/v1/settings/unit-system", json={"unit_system": system})
    assert resp.status_code == 200
    assert resp.json() == {"unit_system": system}


def test_session_exposes_unit_system(client):
    assert client.get("/api/v1/session").json().get("unit_system") in ("imperial", "metric")


def test_patch_rejects_invalid_values(client, restore_preference):
    for bad in ("furlongs", "", None, "IMPERIAL"):
        resp = client.patch("/api/v1/settings/unit-system", json={"unit_system": bad})
        assert resp.status_code == 400, f"{bad!r} accepted"
    assert client.patch("/api/v1/settings/unit-system", json={}).status_code == 400


def test_post_rejects_unknown_units(client):
    for path, payload in [
        ("/api/v1/temperature", {"temperature": 98.6, "unit": "banana", "timestamp": _stamp(5)}),
        ("/api/v1/weight", {"weight": 150, "unit": "stone", "timestamp": _stamp(5)}),
        ("/api/v1/blood-glucose", {"blood_glucose": 100, "unit": "g/L", "timestamp": _stamp(5)}),
    ]:
        resp = client.post(path, json=payload)
        assert resp.status_code == 400, f"{path} accepted {payload['unit']!r}"


def test_post_normalizes_alias_units(client, restore_preference):
    # 'degF' (HealthKit spelling) must store as canonical 'F'.
    _set_system(client, "imperial")
    before = _entry_ids(client, "/api/v1/temperature")
    created = []
    try:
        resp = client.post(
            "/api/v1/temperature",
            json={"temperature": 98.6, "unit": "degF", "timestamp": _stamp(5)},
        )
        assert resp.status_code == 201
        new = _entry_ids(client, "/api/v1/temperature") - before
        created += list(new)
        (entry,) = _entries_by_id(client, "/api/v1/temperature", new)
        assert (entry["temperature"], entry["unit"]) == (98.6, "F")
    finally:
        for metric_id in created:
            client.delete(f"/api/v1/health-metrics/{metric_id}")


def test_mixed_unit_flow(client, restore_preference):
    _set_system(client, "imperial")

    baseline = {
        "glucose": _entry_ids(client, "/api/v1/blood-glucose"),
        "weight": _entry_ids(client, "/api/v1/weight"),
        "temperature": _entry_ids(client, "/api/v1/temperature"),
    }
    created: list[str] = []
    try:
        # Same reading logged in both glucose units, plus metric weight/temp.
        for path, payload in [
            ("/api/v1/blood-glucose", {"blood_glucose": 100, "unit": "mg/dL", "timestamp": _stamp(50)}),
            ("/api/v1/blood-glucose", {"blood_glucose": 5.5, "unit": "mmol/L", "timestamp": _stamp(40)}),
            ("/api/v1/weight", {"weight": 70, "unit": "kg", "timestamp": _stamp(30)}),
            ("/api/v1/temperature", {"temperature": 37, "unit": "C", "timestamp": _stamp(20)}),
        ]:
            assert client.post(path, json=payload).status_code == 201

        new_glucose = _entry_ids(client, "/api/v1/blood-glucose") - baseline["glucose"]
        new_weight = _entry_ids(client, "/api/v1/weight") - baseline["weight"]
        new_temp = _entry_ids(client, "/api/v1/temperature") - baseline["temperature"]
        created += list(new_glucose | new_weight | new_temp)
        assert len(new_glucose) == 2 and len(new_weight) == 1 and len(new_temp) == 1

        # --- imperial reads: everything in mg/dL / lbs / F ------------------
        glucose = _entries_by_id(client, "/api/v1/blood-glucose", new_glucose)
        assert {e["unit"] for e in glucose} == {"mg/dL"}
        assert sorted(e["blood_glucose"] for e in glucose) == [99, 100]

        (weight,) = _entries_by_id(client, "/api/v1/weight", new_weight)
        assert (weight["weight"], weight["unit"]) == (154.3, "lbs")

        (temp,) = _entries_by_id(client, "/api/v1/temperature", new_temp)
        assert (temp["temperature"], temp["unit"]) == (98.6, "F")

        # --- dashboard rollup: the mixed-unit regression ---------------------
        # Raw AVG of (100 mg/dL, 5.5 mmol/L) was 52.75; canonicalized it's
        # ~99.55 mg/dL. Only assert exactly when ours are the only rows.
        summary = client.get("/api/v1/dashboard").json()["vitals"]["metrics"]["blood_glucose"]
        assert summary["unit"] == "mg/dL"
        if summary["count"] == 2:
            assert summary["avg"] == pytest.approx(99.55, abs=0.5)
            assert summary["min"] == pytest.approx(99.1, abs=0.5)
            assert summary["max"] == 100
        else:  # pre-existing rows in the window: still must be a sane band
            assert summary["min"] <= summary["avg"] <= summary["max"]
            assert summary["avg"] > 55, "rollup looks unit-blind again"

        # --- metric reads: same rows, converted -----------------------------
        _set_system(client, "metric")
        glucose = _entries_by_id(client, "/api/v1/blood-glucose", new_glucose)
        assert {(e["blood_glucose"], e["unit"]) for e in glucose} == {(5.5, "mmol/L")}

        (weight,) = _entries_by_id(client, "/api/v1/weight", new_weight)
        assert (weight["weight"], weight["unit"]) == (70.0, "kg")

        (temp,) = _entries_by_id(client, "/api/v1/temperature", new_temp)
        assert (temp["temperature"], temp["unit"]) == (37.0, "C")

        # --- POST without a unit inherits the preference ---------------------
        # Under metric, 36.8 must store as C: it reads back as (36.8, C).
        # Had the old hardcoded 'F' default applied, 36.8F would display 2.7C.
        assert client.post(
            "/api/v1/temperature",
            json={"temperature": 36.8, "timestamp": _stamp(10)},
        ).status_code == 201
        defaulted = _entry_ids(client, "/api/v1/temperature") - baseline["temperature"] - new_temp
        created += list(defaulted)
        (entry,) = _entries_by_id(client, "/api/v1/temperature", defaulted)
        assert (entry["temperature"], entry["unit"]) == (36.8, "C")

    finally:
        for metric_id in created:
            client.delete(f"/api/v1/health-metrics/{metric_id}")
