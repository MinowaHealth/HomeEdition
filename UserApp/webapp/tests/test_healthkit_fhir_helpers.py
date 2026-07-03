import json
import os
from pathlib import Path
import sys


WEBAPP_ROOT = Path(__file__).resolve().parents[1]
if str(WEBAPP_ROOT) not in sys.path:
    sys.path.insert(0, str(WEBAPP_ROOT))

# Required by db_manager import path during app module load.
os.environ.setdefault('APP_DB_USER', 'healthv10_app')
os.environ.setdefault('APP_DB_PASSWORD', 'test')

from app import (  # noqa: E402
    _extract_blood_pressure,
    _extract_readings_from_record,
    _normalize_unit,
    _resolve_type_from_observation,
)


def test_normalize_unit_maps_common_variants():
    assert _normalize_unit('kg') == 'kg'
    assert _normalize_unit('pounds') == 'lb'
    assert _normalize_unit('Celsius') == 'degC'
    assert _normalize_unit('degF') == 'degF'
    assert _normalize_unit('mmHg') == 'mmHg'
    assert _normalize_unit(None) is None


def test_resolve_type_from_observation_by_loinc_and_label():
    loinc_glucose = {'code': {'coding': [{'code': '2339-0', 'display': 'Glucose'}]}}
    assert _resolve_type_from_observation(loinc_glucose, '') == 'blood_glucose'

    label_only = {'code': {'coding': []}}
    assert _resolve_type_from_observation(label_only, 'Body weight') == 'weight'


def test_extract_blood_pressure_from_components():
    fhir = {
        'component': [
            {
                'code': {'coding': [{'code': '8480-6'}]},
                'valueQuantity': {'value': 121, 'unit': 'mmHg'},
            },
            {
                'code': {'coding': [{'code': '8462-4'}]},
                'valueQuantity': {'value': 79, 'unit': 'mmHg'},
            },
        ]
    }
    bp = _extract_blood_pressure(fhir)
    assert bp == {'systolic': 121.0, 'diastolic': 79.0, 'unit': 'mmHg'}


def test_extract_readings_from_record_bp_and_temperature():
    bp_fhir = {
        'resourceType': 'Observation',
        'effectiveDateTime': '2026-01-01T12:00:00Z',
        'component': [
            {
                'code': {'coding': [{'code': '8480-6'}]},
                'valueQuantity': {'value': 118, 'unit': 'mmHg'},
            },
            {
                'code': {'coding': [{'code': '8462-4'}]},
                'valueQuantity': {'value': 76, 'unit': 'mmHg'},
            },
        ],
    }
    bp_readings = _extract_readings_from_record(
        {'displayName': 'BP', 'fhir': json.dumps(bp_fhir)}
    )
    assert len(bp_readings) == 1
    assert bp_readings[0]['type'] == 'blood_pressure'
    assert bp_readings[0]['systolic'] == 118.0
    assert bp_readings[0]['diastolic'] == 76.0

    temp_fhir = {
        'resourceType': 'Observation',
        'effectiveDateTime': '2026-01-02T08:00:00Z',
        'code': {'coding': [{'code': '8310-5'}], 'text': 'Body temperature'},
        'valueQuantity': {'value': 36.6, 'unit': 'Celsius'},
    }
    temp_readings = _extract_readings_from_record(
        {'displayName': 'Temp', 'fhir': json.dumps(temp_fhir)}
    )
    assert len(temp_readings) == 1
    assert temp_readings[0]['type'] == 'body_temperature'
    assert temp_readings[0]['unit'] == 'degC'


def test_extract_readings_from_record_invalid_payload():
    assert _extract_readings_from_record(None) == []
    assert _extract_readings_from_record({'fhir': '{not-json'}) == []
    assert _extract_readings_from_record({'fhir': json.dumps({'resourceType': 'Medication'})}) == []

