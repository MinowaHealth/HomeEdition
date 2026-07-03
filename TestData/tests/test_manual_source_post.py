from unittest.mock import MagicMock
import pytest
from TestData.three_month_seed.sources.manual import ManualClient

# Non-translating helpers — body passes through unchanged. The three
# translating helpers (bp, weight, observation) have their own tests below
# because they rewrite the seeder-internal vocabulary into route-expected
# field names at the adapter boundary.
PASSTHROUGH_ROUTES = [
    ("post_condition", "/api/v1/conditions"),
    ("post_allergy", "/api/v1/allergies"),
    ("post_family_history", "/api/v1/family-history"),
    ("post_social_history", "/api/v1/social-history"),
    ("post_surgical_history", "/api/v1/surgical-history"),
    ("post_health_input", "/api/v1/health-inputs"),
    ("post_timeframe", "/api/v1/timeframes"),
    ("post_stack", "/api/v1/stacks"),
    ("post_vaccination", "/api/v1/vaccinations"),
    ("post_log_stack", "/api/v1/log-stack"),
    ("post_log_meal", "/api/v1/log-meal"),
]


def _make_client_with_capture(monkeypatch, response_body=None):
    client = ManualClient(base_url="http://localhost")
    client._token_cache["x@x"] = "T1"
    captured = {}
    body = response_body if response_body is not None else {"id": "stub-id"}
    def fake_post(path, json, headers):
        captured["path"] = path
        captured["json"] = json
        captured["headers"] = headers
        return MagicMock(
            status_code=201, json=lambda: body,
            raise_for_status=lambda: None,
        )
    monkeypatch.setattr(client._http, "post", fake_post)
    return client, captured


@pytest.mark.parametrize("method_name,expected_path", PASSTHROUGH_ROUTES)
def test_passthrough_helper_uses_correct_route(method_name, expected_path, monkeypatch):
    client, captured = _make_client_with_capture(monkeypatch)
    method = getattr(client, method_name)
    result = method("x@x", {"some": "body"})
    assert result == {"id": "stub-id"}
    assert captured["path"] == expected_path
    assert captured["headers"]["Authorization"] == "Bearer T1"
    assert captured["json"] == {"some": "body"}


def test_blood_pressure_translates_seeder_vocabulary(monkeypatch):
    """measured_at → timestamp; pass-through systolic/diastolic; optional heart_rate."""
    client, captured = _make_client_with_capture(
        monkeypatch, response_body={"message": "Blood pressure logged successfully"})
    result = client.post_blood_pressure("x@x", {
        "measured_at": "2026-03-15T07:00:00Z",
        "systolic": 124,
        "diastolic": 78,
        "heart_rate": 62,
    })
    assert captured["path"] == "/api/v1/blood-pressure"
    assert captured["json"] == {
        "timestamp": "2026-03-15T07:00:00Z",
        "systolic": 124,
        "diastolic": 78,
        "heart_rate": 62,
    }
    # Route returns {message: ...} not {id: ...} — caller should not break.
    assert result == {"message": "Blood pressure logged successfully"}


def test_blood_pressure_omits_heart_rate_when_not_provided(monkeypatch):
    client, captured = _make_client_with_capture(monkeypatch)
    client.post_blood_pressure("x@x", {
        "measured_at": "2026-03-15T07:00:00Z",
        "systolic": 124, "diastolic": 78,
    })
    assert "heart_rate" not in captured["json"]


def test_weight_translates_seeder_vocabulary(monkeypatch):
    """measured_at → timestamp; value → weight; default unit lbs."""
    client, captured = _make_client_with_capture(
        monkeypatch, response_body={"message": "Weight logged successfully"})
    result = client.post_weight("x@x", {
        "measured_at": "2026-03-15T07:30:00Z",
        "value": 165.4,
    })
    assert captured["path"] == "/api/v1/weight"
    assert captured["json"] == {
        "timestamp": "2026-03-15T07:30:00Z",
        "weight": 165.4,
        "unit": "lbs",
    }
    assert result == {"message": "Weight logged successfully"}


def test_observation_translates_seeder_vocabulary(monkeypatch):
    """text → observation; observed_at → timestamp; kind → source_type."""
    client, captured = _make_client_with_capture(
        monkeypatch, response_body={"id": "obs-id", "message": "Observation created"})
    result = client.post_observation("x@x", {
        "kind": "migraine_episode",
        "text": "aura sumatriptan_50mg",
        "observed_at": "2026-03-09T20:00:00Z",
    })
    assert captured["path"] == "/api/v1/observations"
    assert captured["json"] == {
        "observation": "aura sumatriptan_50mg",
        "timestamp": "2026-03-09T20:00:00Z",
        "source_type": "migraine_episode",
    }
    assert result["id"] == "obs-id"


def test_post_missing_token_raises_keyerror(monkeypatch):
    """If token_for() hasn't been called yet, _post_json should KeyError."""
    client = ManualClient(base_url="http://localhost")
    monkeypatch.setattr(client._http, "post", lambda *a, **kw: MagicMock(
        status_code=201, json=lambda: {"id": "x"}, raise_for_status=lambda: None,
    ))
    with pytest.raises(KeyError):
        client.post_condition("never-logged-in@example.com", {"name": "X"})


def test_post_http_error_propagates(monkeypatch):
    client = ManualClient(base_url="http://localhost")
    client._token_cache["x@x"] = "T1"
    monkeypatch.setattr(client._http, "post", lambda *a, **kw: MagicMock(
        status_code=500,
        raise_for_status=MagicMock(side_effect=Exception("HTTP 500")),
    ))
    with pytest.raises(Exception, match="HTTP 500"):
        client.post_condition("x@x", {"name": "X"})
