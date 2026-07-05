"""Aggressive unit tests for UserApp/webapp/unit_conversion.py.

Pure-function coverage: known conversion values, round-trip stability,
precision rules, alias handling, passthrough behavior, and a drift tripwire
asserting the SQL CASE in routes/analytics.py still uses the same constants
as this module (the rollup canonicalizes in SQL, so the two must agree).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "UserApp" / "webapp"))

import unit_conversion as uc  # noqa: E402


# ---------------------------------------------------------------- known values

@pytest.mark.parametrize("metric,value,unit,expected", [
    # temperature: exact anchor points
    ("temperature", 0, "C", 32.0),
    ("temperature", 100, "C", 212.0),
    ("temperature", -40, "C", -40.0),
    ("temperature", 37, "C", 98.6),
    ("temperature", 98.6, "F", 98.6),      # already canonical: untouched
    # weight
    ("weight", 100, "kg", 220.5),
    ("weight", 1, "kg", 2.2),
    ("weight", 150, "lbs", 150),
    ("weight", 0, "kg", 0.0),
    # blood glucose
    ("blood_glucose", 5.5, "mmol/L", 99),
    ("blood_glucose", 1.0, "mmol/L", 18),
    ("blood_glucose", 100, "mg/dL", 100),
    # HealthKit spelling behaves identically to 'temperature'
    ("body_temperature", 37, "C", 98.6),
])
def test_to_canonical_known_values(metric, value, unit, expected):
    assert uc.to_canonical(metric, value, unit) == expected


@pytest.mark.parametrize("metric,value,unit,system,expected", [
    ("blood_glucose", 100, "mg/dL", "metric", (5.5, "mmol/L")),
    ("blood_glucose", 180, "mg/dL", "metric", (10.0, "mmol/L")),
    ("blood_glucose", 5.5, "mmol/L", "imperial", (99, "mg/dL")),
    ("blood_glucose", 5.5, "mmol/L", "metric", (5.5, "mmol/L")),
    ("weight", 154.32, "lbs", "metric", (70.0, "kg")),
    ("weight", 70, "kg", "imperial", (154.3, "lbs")),
    ("temperature", 98.6, "F", "metric", (37.0, "C")),
    ("temperature", 36.8, "C", "imperial", (98.2, "F")),
    ("body_temperature", 36.8, "C", "imperial", (98.2, "F")),
])
def test_to_display_known_values(metric, value, unit, system, expected):
    assert uc.to_display(metric, value, unit, system) == expected


# ---------------------------------------------------------------- round trips

_ROUND_TRIP_TOLERANCE = {
    "mg/dL": 2.0,   # integer rounding both ways: worst case ~1.4
    "mmol/L": 0.06,
    "kg": 0.15,
    "lbs": 0.15,
    "C": 0.11,
    "F": 0.15,
}

@pytest.mark.parametrize("metric,pairs", [
    ("weight", ("lbs", "kg")),
    ("temperature", ("F", "C")),
    ("blood_glucose", ("mg/dL", "mmol/L")),
])
@pytest.mark.parametrize("value", [0.1, 1, 5.5, 36.6, 98.6, 100, 250, 999.9])
def test_round_trip_within_rounding(metric, pairs, value):
    a, b = pairs
    there = uc._convert(metric, value, a, b)
    back = uc._convert(metric, there, b, a)
    assert back == pytest.approx(value, abs=_ROUND_TRIP_TOLERANCE[a]), \
        f"{value} {a} -> {there} {b} -> {back} {a}"


# ---------------------------------------------------------------- precision

def test_mmol_gets_one_decimal():
    val, unit = uc.to_display("blood_glucose", 123, "mg/dL", "metric")
    assert unit == "mmol/L"
    assert val == round(val, 1)

def test_mgdl_gets_integer():
    val, unit = uc.to_display("blood_glucose", 6.7, "mmol/L", "imperial")
    assert unit == "mg/dL"
    assert val == int(val)

def test_same_unit_passthrough_never_rounds():
    # A value already in its display unit must come back bit-identical —
    # "keep current formatting" for unconverted values.
    assert uc.to_display("weight", 154.3267, "lbs", "imperial") == (154.3267, "lbs")
    assert uc.to_display("temperature", 98.642, "F", "imperial") == (98.642, "F")
    assert uc.to_canonical("blood_glucose", 99.987, "mg/dL") == 99.987


# ---------------------------------------------------------------- fallbacks

def test_unknown_unit_system_falls_back_to_imperial():
    assert uc.to_display("weight", 70, "kg", "cubits") == (154.3, "lbs")
    assert uc.to_display("weight", 70, "kg", None) == (154.3, "lbs")

def test_unknown_unit_passes_through_with_its_own_label():
    # Legacy/imported row with an unconvertible unit: report what's stored,
    # never mislabel it as the display unit.
    assert uc.to_display("weight", 12, "stone", "metric") == (12, "stone")
    assert uc.to_display("temperature", 310, "K", "imperial") == (310, "K")
    assert uc.to_canonical("weight", 12, "stone") == 12

def test_unlisted_metric_type_is_untouched():
    assert uc.to_display("heart_rate", 62, "bpm", "metric") == (62, "bpm")
    assert uc.to_canonical("heart_rate", 62, "bpm") == 62
    assert uc.to_canonical("steps", 10000, "count") == 10000

def test_zero_and_negative_values_convert():
    assert uc.to_canonical("temperature", -10, "C") == 14.0
    assert uc.to_display("temperature", 0, "F", "metric") == (-17.8, "C")


# ---------------------------------------------------------------- coherence

def test_display_units_cover_every_canonical_metric():
    for system in ("imperial", "metric"):
        assert set(uc.DISPLAY_UNITS[system]) == set(uc.CANONICAL_UNITS)

def test_imperial_display_units_are_the_canonical_units():
    # Aggregation converts to canonical; imperial display must be a no-op.
    assert uc.DISPLAY_UNITS["imperial"] == uc.CANONICAL_UNITS


# ------------------------------------------------- border normalization

@pytest.mark.parametrize("metric,raw,expected", [
    # Apple HealthKit quantity-unit spellings
    ("weight", "lb", "lbs"),
    ("temperature", "degF", "F"),
    ("blood_glucose", "mg/dL", "mg/dL"),
    # app.py FHIR/UCUM normalizer output ('lb'/'kg'/'degC'/'degF')
    ("weight", "kg", "kg"),
    ("temperature", "degC", "C"),
    # casing / spelling variants
    ("weight", "Pounds", "lbs"),
    ("weight", "KILOGRAM", "kg"),
    ("temperature", "°C", "C"),
    ("temperature", "celsius", "C"),
    ("temperature", " F ", "F"),
    ("blood_glucose", "MG/DL", "mg/dL"),
    ("blood_glucose", "mmol/l", "mmol/L"),
    # already canonical: identity
    ("weight", "lbs", "lbs"),
    ("temperature", "C", "C"),
    ("blood_glucose", "mmol/L", "mmol/L"),
    # HealthKit metric-type spelling
    ("body_temperature", "degF", "F"),
])
def test_normalize_metric_unit_known_spellings(metric, raw, expected):
    assert uc.normalize_metric_unit(metric, raw) == expected


@pytest.mark.parametrize("metric,raw", [
    ("weight", "stone"),
    ("temperature", "K"),
    ("temperature", "banana"),
    ("blood_glucose", "g/L"),
    ("weight", ""),
    ("weight", None),
])
def test_normalize_metric_unit_rejects_unknown(metric, raw):
    assert uc.normalize_metric_unit(metric, raw) is None


def test_normalize_metric_unit_ignores_unlisted_metric_types():
    # No vocabulary for these — nothing to normalize against, pass through.
    assert uc.normalize_metric_unit("heart_rate", "bpm") == "bpm"
    assert uc.normalize_metric_unit("medication", "2 tablets daily") == "2 tablets daily"
    assert uc.normalize_metric_unit("steps", None) is None


def test_normalized_units_are_always_convertible():
    # Everything the normalizer emits must be a unit to_display can convert —
    # otherwise the leash lets a spelling through that the rollup mishandles.
    for metric, aliases in uc._UNIT_ALIASES.items():
        for canonical in set(aliases.values()):
            for system in ("imperial", "metric"):
                _, unit = uc.to_display(metric, 50, canonical, system)
                assert unit == uc.DISPLAY_UNITS[system][metric], \
                    f"{metric} {canonical} not convertible under {system}"


# ------------------------------------------------- SQL drift tripwire

def test_analytics_sql_case_uses_same_constants():
    """The dashboard rollup canonicalizes in SQL (routes/analytics.py); its
    CASE expression duplicates this module's constants by necessity. If either
    side changes without the other, mixed-unit aggregates silently skew."""
    source = (REPO_ROOT / "UserApp" / "webapp" / "routes" / "analytics.py").read_text()
    assert str(uc.KG_PER_LB) in source, "kg/lb factor missing or changed in analytics SQL"
    assert str(uc.MGDL_PER_MMOLL) in source, "glucose factor missing or changed in analytics SQL"
    assert "value * 9.0 / 5.0 + 32" in source, "C->F expression missing or changed in analytics SQL"
    # Both temperature spellings must be canonicalized by the CASE.
    assert "'temperature', 'body_temperature'" in source
