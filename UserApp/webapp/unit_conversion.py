"""
Measurement-unit conversion for the per-user imperial/metric display preference.

Covers weight (lbs/kg), temperature (F/C), and blood glucose (mg/dL / mmol/L).
Distinct from units.py, which is the medication dosage-unit vocabulary.

Storage is canonical-per-row: health_metrics keeps the value + unit exactly
as entered. Conversion happens at the API boundary — GETs convert each row
to the user's display unit, aggregation converts to canonical first.

Self-check: python unit_conversion.py
"""

# Canonical unit per metric_type — what aggregation converts to.
# 'body_temperature' is the HealthKit spelling of 'temperature'.
CANONICAL_UNITS = {
    'weight': 'lbs',
    'temperature': 'F',
    'body_temperature': 'F',
    'blood_glucose': 'mg/dL',
}

# What each unit_system displays per metric_type.
DISPLAY_UNITS = {
    'imperial': {'weight': 'lbs', 'temperature': 'F', 'body_temperature': 'F',
                 'blood_glucose': 'mg/dL'},
    'metric': {'weight': 'kg', 'temperature': 'C', 'body_temperature': 'C',
               'blood_glucose': 'mmol/L'},
}

_METRIC_ALIASES = {'body_temperature': 'temperature'}

KG_PER_LB = 0.45359237
MGDL_PER_MMOLL = 18.0182

# Rounding applied to CONVERTED values only; a value already in its display
# unit passes through untouched. mmol/L gets 1 decimal per approved plan.
_DECIMALS = {'mg/dL': 0, 'mmol/L': 1, 'kg': 1, 'lbs': 1, 'F': 1, 'C': 1}


def _convert(metric_type: str, value: float, from_unit: str, to_unit: str) -> float:
    """Convert value between the two known units of a metric_type."""
    if from_unit == to_unit:
        return value
    key = (_METRIC_ALIASES.get(metric_type, metric_type), from_unit, to_unit)
    if key == ('weight', 'lbs', 'kg'):
        converted = value * KG_PER_LB
    elif key == ('weight', 'kg', 'lbs'):
        converted = value / KG_PER_LB
    elif key == ('temperature', 'F', 'C'):
        converted = (value - 32) * 5 / 9
    elif key == ('temperature', 'C', 'F'):
        converted = value * 9 / 5 + 32
    elif key == ('blood_glucose', 'mg/dL', 'mmol/L'):
        converted = value / MGDL_PER_MMOLL
    elif key == ('blood_glucose', 'mmol/L', 'mg/dL'):
        converted = value * MGDL_PER_MMOLL
    else:
        # Unknown unit (legacy/imported row) — pass through rather than crash.
        return value
    return round(converted, _DECIMALS[to_unit])


def to_canonical(metric_type: str, value: float, unit: str) -> float:
    """Convert a stored value to the metric_type's canonical unit."""
    canonical = CANONICAL_UNITS.get(metric_type)
    if canonical is None:
        return value
    return _convert(metric_type, value, unit, canonical)


def to_display(metric_type: str, value: float, unit: str, unit_system: str) -> tuple[float, str]:
    """Convert a stored value to the user's display unit. Returns (value, unit)."""
    display_unit = DISPLAY_UNITS.get(unit_system, DISPLAY_UNITS['imperial']).get(metric_type)
    if display_unit is None:
        return value, unit
    converted = _convert(metric_type, value, unit, display_unit)
    if converted is value and unit != display_unit:
        return value, unit  # unknown unit — report what's stored, don't mislabel
    return converted, display_unit


if __name__ == '__main__':
    assert to_canonical('weight', 100, 'kg') == 220.5
    assert to_canonical('weight', 150, 'lbs') == 150
    assert to_canonical('temperature', 37, 'C') == 98.6
    assert to_canonical('blood_glucose', 5.5, 'mmol/L') == 99
    assert to_display('blood_glucose', 100, 'mg/dL', 'metric') == (5.5, 'mmol/L')
    assert to_display('blood_glucose', 100, 'mg/dL', 'imperial') == (100, 'mg/dL')
    assert to_display('weight', 154.32, 'lbs', 'metric') == (70.0, 'kg')
    assert to_display('temperature', 98.6, 'F', 'metric') == (37.0, 'C')
    assert to_display('temperature', 101.3, 'F', 'imperial') == (101.3, 'F')
    assert to_display('weight', 12, 'stone', 'metric') == (12, 'stone')  # unknown unit passes through
    print('unit_conversion.py self-check OK')
