"""Tests for Stage 1 scaffolding loader.

Uses a synthetic records-shape doc inline rather than reading
TestData/records/*.json — keeps the tests independent of content drift
and focused on the loader's translation + ID-mapping behavior.
"""
from __future__ import annotations
from unittest.mock import MagicMock
import pytest

from TestData.three_month_seed.stage1 import (
    PersonaIds, Stage1Result, _seed_persona, _strip,
    _translate_health_input, run_stage1,
)


@pytest.fixture
def fake_client():
    """ManualClient with each post_* recording call args and returning a
    deterministic `{id: ...}` so we can verify ID capture."""
    c = MagicMock()
    counter = {"n": 0}
    def make_id(prefix: str):
        def _impl(email: str, body: dict) -> dict:
            counter["n"] += 1
            return {"id": f"{prefix}-{counter['n']}", "message": "ok"}
        return _impl
    c.post_timeframe.side_effect = make_id("tf")
    c.post_health_input.side_effect = make_id("hi")
    c.post_condition.side_effect = make_id("cond")
    c.post_allergy.side_effect = make_id("alg")
    c.post_family_history.side_effect = make_id("fam")
    c.post_social_history.side_effect = make_id("soc")
    c.post_vaccination.side_effect = make_id("vacc")
    c.post_stack.side_effect = make_id("stk")
    c.post_food_item.side_effect = make_id("food")
    c.post_meal.side_effect = make_id("meal")
    return c


def _doc():
    """Synthetic records doc spanning every table the loader handles."""
    return {
        "meta": {"email": "x@x", "tenant_id": 1},
        "timeframes": [
            {"id": "TF_REC_1", "user_id": "U1", "name": "Morning", "time_of_day": "08:00:00", "is_active": True},
        ],
        "health_inputs": [
            {"id": "HI_REC_1", "user_id": "U1", "name": "Med A", "input_type": "medication",
             "dosage": "10 mg", "default_unit": "mg", "form": "tablet",
             "take_with_food": True, "active": True, "notes": "AM",
             "route": "oral", "start_date": "2020-01-01",      # records-only, drop
             "prescribing_doctor": "Dr Foo", "frequency": "daily"},
            {"id": "HI_REC_2", "user_id": "U1", "name": "Supp B", "input_type": "supplement",
             "dosage": "500 mg", "active": True},
        ],
        "health_conditions": [
            {"id": "C_REC_1", "user_id": "U1", "name": "Hypertension",
             "icd10_code": "I10", "diagnosed_date": "2020-01-01"},
        ],
        "health_allergies": [
            {"id": "A_REC_1", "user_id": "U1", "allergen": "Aspirin", "severity": "low"},
        ],
        "health_family_history": [
            {"id": "F_REC_1", "user_id": "U1", "relationship": "mother", "condition_name": "Migraine"},
        ],
        "health_social_history": [
            {"id": "S_REC_1", "user_id": "U1", "category": "tobacco_use", "status": "never"},
        ],
        "health_vaccinations": [
            {"id": "V_REC_1", "user_id": "U1", "vaccine_name": "Influenza",
             "administered_date": "2025-09-24"},
        ],
        "stacks": [
            {"id": "STK_REC_1", "user_id": "U1", "name": "Morning Meds",
             "timeframe_id": "TF_REC_1", "is_active": True},
        ],
        "stack_inputs": [
            {"id": "SI_REC_1", "stack_id": "STK_REC_1", "health_input_id": "HI_REC_1",
             "sort_order": 1},
            {"id": "SI_REC_2", "stack_id": "STK_REC_1", "health_input_id": "HI_REC_2",
             "sort_order": 2},
        ],
    }


def test_strip_drops_records_metadata():
    assert _strip({"id": "x", "user_id": "u", "name": "n"}) == {"name": "n"}


def test_strip_drops_none_values():
    """None values are dropped so optional route fields default cleanly."""
    assert _strip({"name": "n", "notes": None}) == {"name": "n"}


def test_translate_health_input_renames_fields():
    """records `dosage` → route `default_dosage`; `active` → `is_active`;
    records-only fields (route, start_date, prescribing_doctor, frequency)
    are dropped."""
    rec = {
        "name": "Lisinopril", "input_type": "medication",
        "dosage": "10 mg", "default_unit": "mg", "form": "tablet",
        "take_with_food": True, "active": True, "notes": "AM",
        "route": "oral", "start_date": "2019-08-07",
        "prescribing_doctor": "Dr Foo", "frequency": "daily",
    }
    out = _translate_health_input(rec)
    assert out["default_dosage"] == "10 mg"
    assert out["is_active"] is True
    assert "dosage" not in out
    assert "active" not in out
    assert "route" not in out
    assert "start_date" not in out
    assert "prescribing_doctor" not in out
    assert "frequency" not in out
    assert out["name"] == "Lisinopril"


def test_seed_persona_posts_each_table(fake_client):
    ids = _seed_persona(fake_client, "x@x", _doc())
    assert fake_client.post_timeframe.call_count == 1
    assert fake_client.post_health_input.call_count == 2
    assert fake_client.post_condition.call_count == 1
    assert fake_client.post_allergy.call_count == 1
    assert fake_client.post_family_history.call_count == 1
    assert fake_client.post_social_history.call_count == 1
    assert fake_client.post_vaccination.call_count == 1
    assert fake_client.post_stack.call_count == 1


def test_seed_persona_captures_ids(fake_client):
    ids = _seed_persona(fake_client, "x@x", _doc())
    # 1 timeframe + 2 inputs + 1 stack
    assert ids.timeframes == {"TF_REC_1": "tf-1"}
    assert ids.health_inputs == {"HI_REC_1": "hi-2", "HI_REC_2": "hi-3"}
    assert "STK_REC_1" in ids.stacks


def test_seed_persona_translates_input_body(fake_client):
    _seed_persona(fake_client, "x@x", _doc())
    # First health_input call: body should have default_dosage, is_active,
    # no records-only fields.
    body = fake_client.post_health_input.call_args_list[0].args[1]
    assert body["default_dosage"] == "10 mg"
    assert body["is_active"] is True
    assert "dosage" not in body
    assert "active" not in body
    assert "route" not in body


def test_seed_persona_forwards_stack_inputs_with_server_ids(fake_client):
    """Stack POST should embed `inputs` array whose input_ids are the
    server-assigned UUIDs (hi-2, hi-3), not the records-side UUIDs."""
    _seed_persona(fake_client, "x@x", _doc())
    stack_body = fake_client.post_stack.call_args.args[1]
    assert stack_body["name"] == "Morning Meds"
    assert stack_body["timeframe_id"] == "tf-1"   # records TF_REC_1 → server tf-1
    assert stack_body["inputs"] == [
        {"input_id": "hi-2"},  # records HI_REC_1 → server hi-2 (counter started at 1 for tf)
        {"input_id": "hi-3"},  # records HI_REC_2 → server hi-3
    ]


def test_stack_ids_for_returns_server_uuids(fake_client):
    ids = _seed_persona(fake_client, "x@x", _doc())
    result = Stage1Result(by_email={"x@x": ids})
    assert result.stack_ids_for("x@x") == list(ids.stacks.values())
    assert result.stack_ids_for("missing@x") == []


def test_seed_persona_seeds_meal_catalog(fake_client):
    """Stage 1.5: each persona gets 8 generic foods + 5 meal templates."""
    ids = _seed_persona(fake_client, "x@x", _doc())
    assert fake_client.post_food_item.call_count == 8
    assert fake_client.post_meal.call_count == 5
    assert len(ids.food_items) == 8
    assert len(ids.meals) == 5


def test_seed_persona_meal_inputs_use_server_food_ids(fake_client):
    """Meal POST body's `items` should reference server-side food UUIDs,
    not the records-side _rec_id keys."""
    _seed_persona(fake_client, "x@x", _doc())
    # First meal call: M_BREAKFAST = eggs + toast + apple.
    meal_body = fake_client.post_meal.call_args_list[0].args[1]
    server_food_ids = {item["food_item_id"] for item in meal_body["items"]}
    # All referenced food_item_ids should be from the food_ids dict we
    # just captured. They look like "food-N", not "F_EGGS" etc.
    for fid in server_food_ids:
        assert fid.startswith("food-")


def test_meal_ids_for_returns_server_uuids(fake_client):
    ids = _seed_persona(fake_client, "x@x", _doc())
    result = Stage1Result(by_email={"x@x": ids})
    assert result.meal_ids_for("x@x") == list(ids.meals.values())
    assert result.meal_ids_for("missing@x") == []


def test_run_stage1_skips_personas_with_no_records_file(fake_client, monkeypatch):
    """If meta.email match fails, skip with warning, don't raise."""
    monkeypatch.setattr(
        "TestData.three_month_seed.stage1._records_path_for",
        lambda email: None,
    )
    result = run_stage1(fake_client, ["never-in-records@x.com"])
    assert result.by_email == {}
    fake_client.post_timeframe.assert_not_called()
