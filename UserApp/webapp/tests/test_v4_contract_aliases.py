from pathlib import Path
import sys

import pytest


WEBAPP_ROOT = Path(__file__).resolve().parents[1]
if str(WEBAPP_ROOT) not in sys.path:
    sys.path.insert(0, str(WEBAPP_ROOT))

from routes.analytics import parse_food_notes
from routes.food import normalize_food_payload_v4, normalize_meal_payload_v4
from utils import parse_bool
from routes.logging_routes import (
    normalize_food_log_payload_v4,
    parse_food_notes_payload,
    validate_food_log_payload,
    validate_health_input_log_payload,
)


def test_normalize_food_payload_aliases():
    payload = normalize_food_payload_v4({'carbs_total_g': 10, 'fat_total_g': 4})
    assert payload['carbs_g'] == 10
    assert payload['fat_g'] == 4


def test_normalize_food_payload_canonical_wins():
    payload = normalize_food_payload_v4({'carbs_total_g': 10, 'carbs_g': 8})
    assert payload['carbs_g'] == 8


def test_normalize_meal_payload_aliases():
    payload = normalize_meal_payload_v4({
        'is_template': 1,
        'items': [{'food_item_id': 'x', 'quantity': 2}],
    })
    assert payload['is_favorite'] is True
    assert payload['items'][0]['servings'] == 2


def test_normalize_meal_payload_canonical_wins():
    payload = normalize_meal_payload_v4({
        'is_template': 1,
        'is_favorite': False,
        'items': [{'food_item_id': 'x', 'quantity': 2, 'servings': 3}],
    })
    assert payload['is_favorite'] is False
    assert payload['items'][0]['servings'] == 3


def test_normalize_food_log_payload_aliases():
    payload = normalize_food_log_payload_v4({'quantity': 1.5, 'carbs_total_g': 12, 'fat_total_g': 5})
    assert payload['servings'] == 1.5
    assert payload['carbs_g'] == 12
    assert payload['fat_g'] == 5


def test_parse_food_notes_payload_merges_notes_and_overrides():
    payload = parse_food_notes_payload({'notes': {'meal': 'lunch'}, 'carbs_g': 20})
    assert payload['meal'] == 'lunch'
    assert payload['carbs_g'] == 20


def test_validate_health_input_log_payload_rules():
    assert validate_health_input_log_payload({}) == 'JSON body required'
    assert validate_health_input_log_payload({'timestamp': '2026-01-01T00:00:00Z'}) == 'input_id or free_text is required'
    assert validate_health_input_log_payload({'timestamp': '2026-01-01T00:00:00Z', 'free_text': 'ibuprofen'}) is None


def test_validate_food_log_payload_rules():
    assert validate_food_log_payload({}) == 'JSON body required'
    assert validate_food_log_payload({'timestamp': '2026-01-01T00:00:00Z'}) == 'food_item_id or free_text is required'
    assert validate_food_log_payload({'timestamp': '2026-01-01T00:00:00Z', 'food_item_id': 'abc'}) is None


def test_parse_food_notes_returns_dict_only():
    assert parse_food_notes('{"calories": 100}') == {'calories': 100}
    assert parse_food_notes('[1,2,3]') == {}
    assert parse_food_notes(None) == {}


def test_parse_bool_handles_legacy_inputs():
    assert parse_bool(True) is True
    assert parse_bool('yes') is True
    assert parse_bool('0') is False
    assert parse_bool(None, default=False) is False
