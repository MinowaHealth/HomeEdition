"""
Unit tests for logging_routes blueprint.

Covers the 7 helpers plus the POST (log_meal, log_stack, log_health_input,
log_food_item) and GET (health-input-log, all-logs) handlers.
"""
import json
import uuid
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ============================================================================
# HELPERS
# ============================================================================


class TestParseUuid:
    """logging_routes.parse_uuid — returns UUID or None, never raises."""

    def test_valid_uuid_string(self):
        from routes.logging_routes import parse_uuid
        u = uuid.uuid4()
        assert parse_uuid(str(u)) == u

    def test_valid_uuid_object(self):
        from routes.logging_routes import parse_uuid
        u = uuid.uuid4()
        assert parse_uuid(u) == u

    def test_invalid_string_returns_none(self):
        from routes.logging_routes import parse_uuid
        assert parse_uuid('not-a-uuid') is None

    def test_none_returns_none(self):
        from routes.logging_routes import parse_uuid
        assert parse_uuid(None) is None

    def test_empty_string_returns_none(self):
        from routes.logging_routes import parse_uuid
        assert parse_uuid('') is None

    def test_integer_returns_none(self):
        from routes.logging_routes import parse_uuid
        assert parse_uuid(12345) is None


class TestNormalizeFoodLogPayloadV4:
    """logging_routes.normalize_food_log_payload_v4 — alias coalescing."""

    def test_none_input_returns_dict(self):
        from routes.logging_routes import normalize_food_log_payload_v4
        assert normalize_food_log_payload_v4(None) == {}

    def test_empty_dict(self):
        from routes.logging_routes import normalize_food_log_payload_v4
        assert normalize_food_log_payload_v4({}) == {}

    def test_quantity_aliases_to_servings(self):
        from routes.logging_routes import normalize_food_log_payload_v4
        out = normalize_food_log_payload_v4({'quantity': 2.5})
        assert out['servings'] == 2.5

    def test_canonical_servings_wins_over_quantity(self):
        from routes.logging_routes import normalize_food_log_payload_v4
        out = normalize_food_log_payload_v4({'quantity': 2.5, 'servings': 3.0})
        assert out['servings'] == 3.0

    def test_carbs_total_g_aliases_to_carbs_g(self):
        from routes.logging_routes import normalize_food_log_payload_v4
        out = normalize_food_log_payload_v4({'carbs_total_g': 12})
        assert out['carbs_g'] == 12

    def test_fat_total_g_aliases_to_fat_g(self):
        from routes.logging_routes import normalize_food_log_payload_v4
        out = normalize_food_log_payload_v4({'fat_total_g': 8})
        assert out['fat_g'] == 8

    def test_does_not_mutate_input(self):
        from routes.logging_routes import normalize_food_log_payload_v4
        src = {'quantity': 1}
        out = normalize_food_log_payload_v4(src)
        assert 'servings' not in src
        assert out['servings'] == 1


class TestParseFoodNotesPayload:
    """logging_routes.parse_food_notes_payload — merge notes + nutrition overrides."""

    def test_empty(self):
        from routes.logging_routes import parse_food_notes_payload
        assert parse_food_notes_payload({}) == {}

    def test_dict_notes(self):
        from routes.logging_routes import parse_food_notes_payload
        out = parse_food_notes_payload({'notes': {'meal': 'dinner'}})
        assert out == {'meal': 'dinner'}

    def test_string_notes_json(self):
        from routes.logging_routes import parse_food_notes_payload
        out = parse_food_notes_payload({'notes': '{"calories": 250}'})
        assert out['calories'] == 250

    def test_string_notes_non_json_falls_back_to_text(self):
        from routes.logging_routes import parse_food_notes_payload
        out = parse_food_notes_payload({'notes': 'tasted great'})
        assert out['notes_text'] == 'tasted great'

    def test_string_notes_json_array_falls_back_to_text(self):
        """JSON-parses to non-dict (list) — stored as raw text."""
        from routes.logging_routes import parse_food_notes_payload
        out = parse_food_notes_payload({'notes': '[1, 2]'})
        assert out['notes_text'] == '[1, 2]'

    def test_top_level_keys_merge_in(self):
        from routes.logging_routes import parse_food_notes_payload
        out = parse_food_notes_payload({
            'notes': {'meal': 'lunch'},
            'calories': 400,
            'protein_g': 25,
            'fiber_g': 5,
        })
        assert out['meal'] == 'lunch'
        assert out['calories'] == 400
        assert out['protein_g'] == 25
        assert out['fiber_g'] == 5

    def test_top_level_overrides_notes(self):
        from routes.logging_routes import parse_food_notes_payload
        out = parse_food_notes_payload({
            'notes': {'calories': 100},
            'calories': 250,
        })
        assert out['calories'] == 250

    def test_none_input(self):
        from routes.logging_routes import parse_food_notes_payload
        assert parse_food_notes_payload(None) == {}


class TestValidateHealthInputLogPayload:
    def test_empty_returns_error(self):
        from routes.logging_routes import validate_health_input_log_payload
        assert validate_health_input_log_payload({}) == 'JSON body required'

    def test_none_returns_error(self):
        from routes.logging_routes import validate_health_input_log_payload
        assert validate_health_input_log_payload(None) == 'JSON body required'

    def test_missing_timestamp(self):
        from routes.logging_routes import validate_health_input_log_payload
        assert validate_health_input_log_payload({'free_text': 'x'}) == 'timestamp is required'

    def test_missing_input_id_and_free_text(self):
        from routes.logging_routes import validate_health_input_log_payload
        msg = validate_health_input_log_payload({'timestamp': '2026-01-01T00:00:00Z'})
        assert msg == 'input_id or free_text is required'

    def test_with_input_id_ok(self):
        from routes.logging_routes import validate_health_input_log_payload
        assert validate_health_input_log_payload({
            'timestamp': '2026-01-01T00:00:00Z', 'input_id': 'abc',
        }) is None

    def test_with_free_text_ok(self):
        from routes.logging_routes import validate_health_input_log_payload
        assert validate_health_input_log_payload({
            'timestamp': '2026-01-01T00:00:00Z', 'free_text': 'aspirin',
        }) is None


class TestValidateFoodLogPayload:
    def test_empty_returns_error(self):
        from routes.logging_routes import validate_food_log_payload
        assert validate_food_log_payload({}) == 'JSON body required'

    def test_none_returns_error(self):
        from routes.logging_routes import validate_food_log_payload
        assert validate_food_log_payload(None) == 'JSON body required'

    def test_missing_timestamp(self):
        from routes.logging_routes import validate_food_log_payload
        assert validate_food_log_payload({'food_item_id': 'abc'}) == 'timestamp is required'

    def test_missing_both_food_item_and_free_text(self):
        from routes.logging_routes import validate_food_log_payload
        msg = validate_food_log_payload({'timestamp': '2026-01-01T00:00:00Z'})
        assert msg == 'food_item_id or free_text is required'

    def test_with_food_item_id_ok(self):
        from routes.logging_routes import validate_food_log_payload
        assert validate_food_log_payload({
            'timestamp': '2026-01-01T00:00:00Z', 'food_item_id': 'abc',
        }) is None

    def test_with_free_text_ok(self):
        from routes.logging_routes import validate_food_log_payload
        assert validate_food_log_payload({
            'timestamp': '2026-01-01T00:00:00Z', 'free_text': 'pizza',
        }) is None


class TestResolveTimeColumn:
    """Pick logged_at when present, else fall back to legacy timestamp column."""

    def test_returns_preferred_when_column_present(self):
        from routes.logging_routes import resolve_time_column
        with patch('routes.logging_routes.table_has_column', return_value=True):
            assert resolve_time_column(MagicMock(), 'health_food_logv2') == 'logged_at'

    def test_returns_legacy_when_preferred_missing(self):
        from routes.logging_routes import resolve_time_column
        with patch('routes.logging_routes.table_has_column', return_value=False):
            assert resolve_time_column(MagicMock(), 'health_food_logv2') == 'timestamp'

    def test_custom_preferred_and_legacy(self):
        from routes.logging_routes import resolve_time_column
        with patch('routes.logging_routes.table_has_column', return_value=False):
            assert resolve_time_column(
                MagicMock(), 'health_metrics',
                preferred='recorded_at', legacy='ts',
            ) == 'ts'
# ============================================================================
# Shared fixtures for HTTP route tests
# ============================================================================


@pytest.fixture(autouse=True)
def _patch_schema_introspection():
    """All routes call table_has_column — return True so feature columns exist."""
    with patch('routes.logging_routes.table_has_column', return_value=True):
        yield


@pytest.fixture(autouse=True)
def _silence_analytics():
    """analytics.capture only logs locally; patch it to avoid needing
    g.user / distinct_id setup in every test."""
    with patch('routes.logging_routes.analytics') as mock:
        yield mock


# ============================================================================
# POST /log-meal
# ============================================================================


class TestLogMeal:
    def test_missing_payload_400(self, client, auth_headers):
        resp = client.post('/api/v1/log-meal', headers=auth_headers,
                           data=json.dumps({}))
        assert resp.status_code == 400
        assert 'meal_id' in resp.get_json()['error']

    def test_missing_timestamp_400(self, client, auth_headers):
        resp = client.post('/api/v1/log-meal', headers=auth_headers,
                           data=json.dumps({'meal_id': str(uuid.uuid4())}))
        assert resp.status_code == 400

    def test_invalid_meal_id_400(self, client, mock_db, auth_headers):
        resp = client.post('/api/v1/log-meal', headers=auth_headers,
                           data=json.dumps({
                               'meal_id': 'not-a-uuid',
                               'timestamp': '2026-04-19T10:00:00',
                           }))
        assert resp.status_code == 400
        assert 'Invalid meal_id' in resp.get_json()['error']

    def test_meal_not_found_404(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = None  # SELECT id FROM meals returns nothing

        resp = client.post('/api/v1/log-meal', headers=auth_headers,
                           data=json.dumps({
                               'meal_id': str(uuid.uuid4()),
                               'timestamp': '2026-04-19T10:00:00',
                           }))
        assert resp.status_code == 404
        assert resp.get_json()['error'] == 'Meal not found'

    def test_meal_no_items_404(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': uuid.uuid4()}
        cur.fetchall.return_value = []  # no meal_items rows

        resp = client.post('/api/v1/log-meal', headers=auth_headers,
                           data=json.dumps({
                               'meal_id': str(uuid.uuid4()),
                               'timestamp': '2026-04-19T10:00:00',
                           }))
        assert resp.status_code == 404
        body = resp.get_json()
        assert body['error'] == 'Meal has no items'
        assert body['items_logged'] == 0

    def test_happy_path_201(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': uuid.uuid4()}
        cur.fetchall.return_value = [
            {'food_item_id': uuid.uuid4(), 'servings': 1.0},
            {'food_item_id': uuid.uuid4(), 'servings': 0.5},
        ]

        resp = client.post('/api/v1/log-meal', headers=auth_headers,
                           data=json.dumps({
                               'meal_id': str(uuid.uuid4()),
                               'timestamp': '2026-04-19T10:00:00',
                           }))
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['items_found'] == 2
        assert body['items_logged'] == 2
        conn.commit.assert_called()


# ============================================================================
# POST /log-stack
# ============================================================================


class TestLogStack:
    def test_missing_payload_400(self, client, auth_headers):
        resp = client.post('/api/v1/log-stack', headers=auth_headers,
                           data=json.dumps({}))
        assert resp.status_code == 400

    def test_invalid_stack_id_400(self, client, mock_db, auth_headers):
        resp = client.post('/api/v1/log-stack', headers=auth_headers,
                           data=json.dumps({
                               'stack_id': 'not-a-uuid',
                               'timestamp': '2026-04-19T10:00:00',
                           }))
        assert resp.status_code == 400
        assert 'Invalid stack_id' in resp.get_json()['error']

    def test_stack_not_found_404(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = None

        resp = client.post('/api/v1/log-stack', headers=auth_headers,
                           data=json.dumps({
                               'stack_id': str(uuid.uuid4()),
                               'timestamp': '2026-04-19T10:00:00',
                           }))
        assert resp.status_code == 404

    def test_stack_no_inputs_400(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': uuid.uuid4()}
        cur.fetchall.return_value = []  # no stack_inputs

        resp = client.post('/api/v1/log-stack', headers=auth_headers,
                           data=json.dumps({
                               'stack_id': str(uuid.uuid4()),
                               'timestamp': '2026-04-19T10:00:00',
                           }))
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['inputs_logged'] == 0

    def test_happy_path_201(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': uuid.uuid4()}
        cur.fetchall.return_value = [
            {'health_input_id': uuid.uuid4(), 'dosage_override': '500mg'},
        ]

        resp = client.post('/api/v1/log-stack', headers=auth_headers,
                           data=json.dumps({
                               'stack_id': str(uuid.uuid4()),
                               'timestamp': '2026-04-19T10:00:00',
                           }))
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['inputs_found'] == 1
        assert body['inputs_logged'] == 1


# ============================================================================
# POST /log-health-input
# ============================================================================


class TestLogHealthInput:
    def test_empty_body_400(self, client, mock_db, auth_headers):
        resp = client.post('/api/v1/log-health-input', headers=auth_headers,
                           data=json.dumps({}))
        assert resp.status_code == 400

    def test_missing_timestamp_400(self, client, mock_db, auth_headers):
        resp = client.post('/api/v1/log-health-input', headers=auth_headers,
                           data=json.dumps({'free_text': 'aspirin'}))
        assert resp.status_code == 400

    def test_invalid_input_id_400(self, client, mock_db, auth_headers):
        resp = client.post('/api/v1/log-health-input', headers=auth_headers,
                           data=json.dumps({
                               'timestamp': '2026-04-19T10:00:00',
                               'input_id': 'not-a-uuid',
                           }))
        assert resp.status_code == 400

    def test_happy_path_with_input_id(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        resp = client.post('/api/v1/log-health-input', headers=auth_headers,
                           data=json.dumps({
                               'timestamp': '2026-04-19T10:00:00',
                               'input_id': str(uuid.uuid4()),
                               'dosage': '500mg',
                           }))
        assert resp.status_code == 201
        assert 'id' in resp.get_json()
        conn.commit.assert_called()

    def test_happy_path_with_free_text(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        resp = client.post('/api/v1/log-health-input', headers=auth_headers,
                           data=json.dumps({
                               'timestamp': '2026-04-19T10:00:00',
                               'free_text': 'random vitamin',
                               'free_dosage': '1 capsule',
                           }))
        assert resp.status_code == 201

    def test_freeform_unsupported_schema_400(self, client, mock_db, auth_headers):
        """When the schema lacks free_text/free_dosage, freeform requests are rejected."""
        with patch('routes.logging_routes.table_has_column', return_value=False):
            resp = client.post('/api/v1/log-health-input', headers=auth_headers,
                               data=json.dumps({
                                   'timestamp': '2026-04-19T10:00:00',
                                   'free_text': 'aspirin',
                               }))
            assert resp.status_code == 400

    def test_db_error_returns_400(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.execute.side_effect = Exception('db blew up')

        resp = client.post('/api/v1/log-health-input', headers=auth_headers,
                           data=json.dumps({
                               'timestamp': '2026-04-19T10:00:00',
                               'free_text': 'aspirin',
                           }))
        assert resp.status_code == 400


# ============================================================================
# POST /log-food-item
# ============================================================================


class TestLogFoodItem:
    def test_empty_body_400(self, client, mock_db, auth_headers):
        resp = client.post('/api/v1/log-food-item', headers=auth_headers,
                           data=json.dumps({}))
        assert resp.status_code == 400

    def test_invalid_food_item_id_400(self, client, mock_db, auth_headers):
        resp = client.post('/api/v1/log-food-item', headers=auth_headers,
                           data=json.dumps({
                               'timestamp': '2026-04-19T12:00:00',
                               'food_item_id': 'not-a-uuid',
                           }))
        assert resp.status_code == 400

    def test_happy_path_catalog(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        resp = client.post('/api/v1/log-food-item', headers=auth_headers,
                           data=json.dumps({
                               'timestamp': '2026-04-19T12:00:00',
                               'food_item_id': str(uuid.uuid4()),
                               'servings': 2,
                               'unit': 'cup',
                               'notes': {'meal': 'lunch'},
                               'calories': 350,
                           }))
        assert resp.status_code == 201
        assert 'id' in resp.get_json()
        conn.commit.assert_called()

    def test_happy_path_freeform(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        resp = client.post('/api/v1/log-food-item', headers=auth_headers,
                           data=json.dumps({
                               'timestamp': '2026-04-19T12:00:00',
                               'free_text': 'leftover pizza',
                               'photo_url': 'https://example.com/pic.jpg',
                           }))
        assert resp.status_code == 201

    def test_db_error_returns_400(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.execute.side_effect = Exception('insert failed')

        resp = client.post('/api/v1/log-food-item', headers=auth_headers,
                           data=json.dumps({
                               'timestamp': '2026-04-19T12:00:00',
                               'food_item_id': str(uuid.uuid4()),
                           }))
        assert resp.status_code == 400


# ============================================================================
# GET /health-input-log
# ============================================================================


class TestGetHealthInputLog:
    def test_returns_logs(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [{
            '_total': 1,
            'id': uuid.uuid4(),
            'logged_at': datetime(2026, 4, 19, 10, 0),
            'dosage_taken': '500mg',
            'free_text': None,
            'free_dosage': None,
            'promoted_at': None,
            'input_name': 'Metformin',
            'default_unit': 'mg',
            'input_type': 'medication',
            'stack_name': 'Morning Meds',
        }]

        resp = client.get('/api/v1/health-input-log', headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert 'entries' in body
        assert 'pagination' in body
        assert body['entries'][0]['is_freeform'] is False
        assert 'timestamp' in body['entries'][0]

    def test_returns_freeform_logs(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [{
            '_total': 1,
            'id': uuid.uuid4(),
            'logged_at': datetime(2026, 4, 19, 10, 0),
            'dosage_taken': None,
            'free_text': 'random pill',
            'free_dosage': '1 tab',
            'promoted_at': None,
            'input_name': None,
            'default_unit': None,
            'input_type': None,
            'stack_name': None,
        }]

        resp = client.get('/api/v1/health-input-log', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['entries'][0]['is_freeform'] is True

    def test_empty(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get('/api/v1/health-input-log', headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['entries'] == []
        assert body['pagination']['total'] == 0

    def test_invalid_input_type_400(self, client, mock_db, auth_headers):
        resp = client.get(
            '/api/v1/health-input-log?input_type=garbage',
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert 'input_type' in resp.get_json()['error']

    def test_valid_input_type_filter(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get(
            '/api/v1/health-input-log?input_type=medication',
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_date_range_filter(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get(
            '/api/v1/health-input-log?start_date=2026-04-01&end_date=2026-04-19',
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_invalid_date_format_400(self, client, mock_db, auth_headers):
        resp = client.get(
            '/api/v1/health-input-log?start_date=not-a-date',
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_input_id_filter(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get(
            f'/api/v1/health-input-log?input_id={uuid.uuid4()}',
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_pagination_truncated_header(self, client, mock_db, auth_headers):
        """When offset+returned < total, X-Truncated header is set."""
        conn, cur = mock_db
        # Return one row, but _total reports many — has_more is True.
        cur.fetchall.return_value = [{
            '_total': 99,
            'id': uuid.uuid4(),
            'logged_at': datetime(2026, 4, 19, 10, 0),
            'dosage_taken': None,
            'free_text': None,
            'free_dosage': None,
            'promoted_at': None,
            'input_name': 'X',
            'default_unit': None,
            'input_type': 'medication',
            'stack_name': None,
        }]

        resp = client.get(
            '/api/v1/health-input-log?limit=1',
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers.get('X-Truncated') == 'true'


# ============================================================================
# GET /all-logs — the chunky one (six per-source SELECTs merged)
# ============================================================================


class TestGetAllLogs:
    def test_merges_all_sources(self, client, mock_db, auth_headers):
        """Each fetchall returns a list — the route runs 6 SELECTs in order:
        health_input_log, BP, temperature, weight, sleep/nutrition metrics,
        medication metrics, food."""
        conn, cur = mock_db
        cur.fetchall.side_effect = [
            # health_input_log
            [{
                'id': uuid.uuid4(),
                'logged_at': datetime(2026, 4, 19, 9, 0),
                'dosage_taken': '500mg',
                'free_text': None,
                'free_dosage': None,
                'input_name': 'Metformin',
                'default_unit': 'mg',
                'stack_name': 'AM',
            }],
            # blood_pressure
            [{
                'id': uuid.uuid4(),
                'measured_at': datetime(2026, 4, 19, 8, 0),
                'systolic': 120,
                'diastolic': 80,
                'pulse': 70,
            }],
            # temperature
            [{
                'id': uuid.uuid4(),
                'recorded_at': datetime(2026, 4, 19, 7, 0),
                'value': 98.6,
                'unit': 'F',
            }],
            # weight
            [{
                'id': uuid.uuid4(),
                'recorded_at': datetime(2026, 4, 19, 6, 0),
                'value': 165.0,
                'unit': 'lbs',
            }],
            # sleep/nutrition metrics
            [
                {
                    'id': uuid.uuid4(),
                    'recorded_at': datetime(2026, 4, 19, 5, 0),
                    'metric_type': 'sleep',
                    'value': 7.5,
                    'unit': 'hours',
                    'notes': None,
                    'source': 'garmin',
                },
                {
                    'id': uuid.uuid4(),
                    'recorded_at': datetime(2026, 4, 18, 22, 0),
                    'metric_type': 'nutrition',
                    'value': 1800,
                    'unit': 'kcal',
                    'notes': None,
                    'source': 'manual',
                },
            ],
            # medication metrics — exercise the JSON-notes parser branch
            [{
                'id': uuid.uuid4(),
                'recorded_at': datetime(2026, 4, 19, 4, 0),
                'metric_type': 'medication',
                'value': 1,
                'unit': 'dose',
                'notes': json.dumps({
                    'metadata': {
                        'medication': {
                            'name': 'Lisinopril',
                            'dosage': '10mg',
                            'status': 'taken',
                        }
                    }
                }),
                'source': 'healthkit',
            }],
            # food
            [{
                'id': uuid.uuid4(),
                'logged_at': datetime(2026, 4, 19, 12, 0),
                'servings': 1.5,
                'unit': 'cup',
                'notes': json.dumps({'meal': 'lunch', 'calories': 300}),
                'food_item_id': uuid.uuid4(),
                'free_text': None,
                'food_name': 'Salad',
            }],
        ]

        resp = client.get('/api/v1/all-logs', headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert 'entries' in body
        # All sources represented
        types = {e['type'] for e in body['entries']}
        assert {'health_input', 'blood_pressure', 'temperature', 'weight',
                'sleep', 'nutrition', 'medication', 'food'} <= types
        # Sorted DESC by timestamp
        ts = [e['timestamp'] for e in body['entries']]
        assert ts == sorted(ts, reverse=True)

    def test_empty_all_sources(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.side_effect = [[], [], [], [], [], [], []]

        resp = client.get('/api/v1/all-logs', headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['entries'] == []
        assert body['pagination']['total'] == 0

    def test_handles_freeform_health_input(self, client, mock_db, auth_headers):
        """Freeform health input log (no input_name) shows free_text in description."""
        conn, cur = mock_db
        cur.fetchall.side_effect = [
            [{
                'id': uuid.uuid4(),
                'logged_at': datetime(2026, 4, 19, 9, 0),
                'dosage_taken': None,
                'free_text': 'random pill',
                'free_dosage': '1 tab',
                'input_name': None,
                'default_unit': None,
                'stack_name': None,
            }],
            [], [], [], [], [], [],
        ]

        resp = client.get('/api/v1/all-logs', headers=auth_headers)
        assert resp.status_code == 200
        entries = resp.get_json()['entries']
        assert any('random pill' in e['description'] for e in entries)
        assert entries[0]['is_freeform'] is True

    def test_medication_metrics_with_string_notes(self, client, mock_db, auth_headers):
        """notes as a plain (non-JSON) string — falls back gracefully."""
        conn, cur = mock_db
        cur.fetchall.side_effect = [
            [], [], [], [],
            [],
            [{
                'id': uuid.uuid4(),
                'recorded_at': datetime(2026, 4, 19, 4, 0),
                'metric_type': 'medication',
                'value': 'taken',
                'unit': None,
                'notes': 'not-json-but-still-text',
                'source': None,
            }],
            [],
        ]

        resp = client.get('/api/v1/all-logs', headers=auth_headers)
        assert resp.status_code == 200


# ============================================================================
# PUT /health-input-log/<id>
# ============================================================================


class TestUpdateHealthInputLog:
    def test_invalid_log_id_400(self, client, mock_db, auth_headers):
        resp = client.put('/api/v1/health-input-log/not-a-uuid',
                          headers=auth_headers,
                          data=json.dumps({}))
        assert resp.status_code == 400

    def test_invalid_input_id_400(self, client, mock_db, auth_headers):
        log_id = str(uuid.uuid4())
        resp = client.put(f'/api/v1/health-input-log/{log_id}',
                          headers=auth_headers,
                          data=json.dumps({'input_id': 'not-a-uuid'}))
        assert resp.status_code == 400

    def test_not_found_404(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 0
        log_id = str(uuid.uuid4())

        resp = client.put(f'/api/v1/health-input-log/{log_id}',
                          headers=auth_headers,
                          data=json.dumps({'dosage': '250mg'}))
        assert resp.status_code == 404

    def test_happy_path(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 1
        log_id = str(uuid.uuid4())

        resp = client.put(f'/api/v1/health-input-log/{log_id}',
                          headers=auth_headers,
                          data=json.dumps({
                              'timestamp': '2026-04-19T10:00:00',
                              'dosage': '250mg',
                              'free_text': 'note',
                              'free_dosage': 'one',
                          }))
        assert resp.status_code == 200
        conn.commit.assert_called()

    def test_promotion_path(self, client, mock_db, auth_headers):
        """Supplying input_id sets promoted_at via COALESCE."""
        conn, cur = mock_db
        cur.rowcount = 1
        log_id = str(uuid.uuid4())

        resp = client.put(f'/api/v1/health-input-log/{log_id}',
                          headers=auth_headers,
                          data=json.dumps({
                              'input_id': str(uuid.uuid4()),
                          }))
        assert resp.status_code == 200

    def test_db_error_returns_400(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.execute.side_effect = Exception('boom')
        log_id = str(uuid.uuid4())

        resp = client.put(f'/api/v1/health-input-log/{log_id}',
                          headers=auth_headers,
                          data=json.dumps({'dosage': 'x'}))
        assert resp.status_code == 400


# ============================================================================
# DELETE /health-input-log/<id>
# ============================================================================


class TestDeleteHealthInputLog:
    def test_invalid_id_400(self, client, mock_db, auth_headers):
        resp = client.delete('/api/v1/health-input-log/bogus',
                             headers=auth_headers)
        assert resp.status_code == 400

    def test_not_found_404(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 0
        log_id = str(uuid.uuid4())

        resp = client.delete(f'/api/v1/health-input-log/{log_id}',
                             headers=auth_headers)
        assert resp.status_code == 404

    def test_happy_path(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 1
        log_id = str(uuid.uuid4())

        resp = client.delete(f'/api/v1/health-input-log/{log_id}',
                             headers=auth_headers)
        assert resp.status_code == 200
        conn.commit.assert_called()


# ============================================================================
# GET /food-log
# ============================================================================


class TestGetFoodLog:
    def test_returns_logs(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [{
            '_total': 1,
            'id': uuid.uuid4(),
            'logged_at': datetime(2026, 4, 19, 12, 0),
            'servings': 1.5,
            'food_item_id': uuid.uuid4(),
            'free_text': None,
            'promoted_at': None,
            'food_name': 'Salad',
        }]

        resp = client.get('/api/v1/food-log', headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body['entries']) == 1
        assert body['entries'][0]['is_freeform'] is False

    def test_freeform_marker(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [{
            '_total': 1,
            'id': uuid.uuid4(),
            'logged_at': datetime(2026, 4, 19, 12, 0),
            'servings': 1,
            'food_item_id': None,
            'free_text': 'mystery dish',
            'promoted_at': None,
            'food_name': None,
        }]

        resp = client.get('/api/v1/food-log', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['entries'][0]['is_freeform'] is True

    def test_empty(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get('/api/v1/food-log', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['entries'] == []

    def test_date_range(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get(
            '/api/v1/food-log?start_date=2026-04-01&end_date=2026-04-19',
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_invalid_date_400(self, client, mock_db, auth_headers):
        resp = client.get(
            '/api/v1/food-log?start_date=junk',
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_truncated_header(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [{
            '_total': 99,
            'id': uuid.uuid4(),
            'logged_at': datetime(2026, 4, 19, 12, 0),
            'servings': 1,
            'food_item_id': None,
            'free_text': None,
            'promoted_at': None,
            'food_name': 'X',
        }]

        resp = client.get('/api/v1/food-log?limit=1', headers=auth_headers)
        assert resp.headers.get('X-Truncated') == 'true'


# ============================================================================
# PUT /food-log/<id>
# ============================================================================


class TestUpdateFoodLog:
    def test_invalid_id_400(self, client, mock_db, auth_headers):
        resp = client.put('/api/v1/food-log/bogus',
                          headers=auth_headers,
                          data=json.dumps({}))
        assert resp.status_code == 400

    def test_invalid_food_item_id_400(self, client, mock_db, auth_headers):
        log_id = str(uuid.uuid4())
        resp = client.put(f'/api/v1/food-log/{log_id}',
                          headers=auth_headers,
                          data=json.dumps({'food_item_id': 'bad'}))
        assert resp.status_code == 400

    def test_not_found_404(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 0
        log_id = str(uuid.uuid4())

        resp = client.put(f'/api/v1/food-log/{log_id}',
                          headers=auth_headers,
                          data=json.dumps({'servings': 2}))
        assert resp.status_code == 404

    def test_happy_path(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 1
        log_id = str(uuid.uuid4())

        resp = client.put(f'/api/v1/food-log/{log_id}',
                          headers=auth_headers,
                          data=json.dumps({
                              'timestamp': '2026-04-19T12:00:00',
                              'servings': 2,
                              'unit': 'cup',
                              'free_text': 'updated freeform',
                              'photo_url': 'http://x/y.jpg',
                              'notes': {'meal': 'dinner'},
                          }))
        assert resp.status_code == 200
        conn.commit.assert_called()

    def test_promotion_path(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 1
        log_id = str(uuid.uuid4())

        resp = client.put(f'/api/v1/food-log/{log_id}',
                          headers=auth_headers,
                          data=json.dumps({
                              'food_item_id': str(uuid.uuid4()),
                          }))
        assert resp.status_code == 200

    def test_db_error_returns_400(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.execute.side_effect = Exception('boom')
        log_id = str(uuid.uuid4())

        resp = client.put(f'/api/v1/food-log/{log_id}',
                          headers=auth_headers,
                          data=json.dumps({'servings': 1}))
        assert resp.status_code == 400


# ============================================================================
# DELETE /food-log/<id>
# ============================================================================


class TestDeleteFoodLog:
    def test_invalid_id_400(self, client, mock_db, auth_headers):
        resp = client.delete('/api/v1/food-log/bogus', headers=auth_headers)
        assert resp.status_code == 400

    def test_not_found_404(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 0
        log_id = str(uuid.uuid4())

        resp = client.delete(f'/api/v1/food-log/{log_id}', headers=auth_headers)
        assert resp.status_code == 404

    def test_happy_path(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 1
        log_id = str(uuid.uuid4())

        resp = client.delete(f'/api/v1/food-log/{log_id}', headers=auth_headers)
        assert resp.status_code == 200
        conn.commit.assert_called()


# ============================================================================
# GET /log-promotions
# ============================================================================


class TestGetLogPromotions:
    def test_returns_promotions(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [{
            '_total': 1,
            'id': uuid.uuid4(),
            'source_table': 'health_input_log',
            'source_log_id': uuid.uuid4(),
            'suggested_catalog_table': 'health_inputs',
            'suggested_catalog_id': uuid.uuid4(),
            'free_text_original': 'aspirin',
            'match_confidence': 0.9,
            'match_method': 'fuzzy',
            'status': 'pending',
            'resolved_at': None,
            'created_at': datetime(2026, 4, 19, 10, 0),
        }]

        resp = client.get('/api/v1/log-promotions', headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert 'entries' in body
        assert body['entries'][0]['status'] == 'pending'

    def test_status_filter(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get(
            '/api/v1/log-promotions?status=accepted',
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_table_missing_returns_empty(self, client, mock_db, auth_headers):
        """When log_promotions table doesn't exist, return empty page (no 500)."""
        conn, cur = mock_db
        with patch('routes.logging_routes.table_has_column', return_value=False):
            resp = client.get('/api/v1/log-promotions', headers=auth_headers)
            assert resp.status_code == 200
            assert resp.get_json()['entries'] == []

    def test_promotion_with_null_catalog_id(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [{
            '_total': 1,
            'id': uuid.uuid4(),
            'source_table': 'health_input_log',
            'source_log_id': uuid.uuid4(),
            'suggested_catalog_table': None,
            'suggested_catalog_id': None,
            'free_text_original': 'mystery',
            'match_confidence': 0.0,
            'match_method': None,
            'status': 'pending',
            'resolved_at': None,
            'created_at': datetime(2026, 4, 19, 10, 0),
        }]

        resp = client.get('/api/v1/log-promotions', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['entries'][0]['suggested_catalog_id'] is None


# ============================================================================
# POST /log-promotions
# ============================================================================


class TestCreateLogPromotion:
    def test_invalid_source_table_400(self, client, mock_db, auth_headers):
        resp = client.post('/api/v1/log-promotions',
                           headers=auth_headers,
                           data=json.dumps({'source_table': 'wrong_table'}))
        assert resp.status_code == 400
        assert 'source_table' in resp.get_json()['error']

    def test_missing_source_log_id_400(self, client, mock_db, auth_headers):
        resp = client.post('/api/v1/log-promotions',
                           headers=auth_headers,
                           data=json.dumps({
                               'source_table': 'health_input_log',
                           }))
        assert resp.status_code == 400
        assert 'source_log_id' in resp.get_json()['error']

    def test_invalid_source_log_id_400(self, client, mock_db, auth_headers):
        resp = client.post('/api/v1/log-promotions',
                           headers=auth_headers,
                           data=json.dumps({
                               'source_table': 'health_input_log',
                               'source_log_id': 'not-a-uuid',
                           }))
        assert resp.status_code == 400

    def test_table_missing_404(self, client, mock_db, auth_headers):
        with patch('routes.logging_routes.table_has_column', return_value=False):
            resp = client.post('/api/v1/log-promotions',
                               headers=auth_headers,
                               data=json.dumps({
                                   'source_table': 'health_input_log',
                                   'source_log_id': str(uuid.uuid4()),
                               }))
            assert resp.status_code == 404

    def test_happy_path(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        resp = client.post('/api/v1/log-promotions',
                           headers=auth_headers,
                           data=json.dumps({
                               'source_table': 'health_input_log',
                               'source_log_id': str(uuid.uuid4()),
                               'suggested_catalog_table': 'health_inputs',
                               'suggested_catalog_id': str(uuid.uuid4()),
                               'free_text_original': 'aspirin',
                               'match_confidence': 0.92,
                               'match_method': 'fuzzy',
                           }))
        assert resp.status_code == 201
        assert 'id' in resp.get_json()

    def test_db_error_returns_400(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.execute.side_effect = Exception('insert blew up')

        resp = client.post('/api/v1/log-promotions',
                           headers=auth_headers,
                           data=json.dumps({
                               'source_table': 'health_input_log',
                               'source_log_id': str(uuid.uuid4()),
                               'match_confidence': 0.5,
                           }))
        assert resp.status_code == 400


# ============================================================================
# PUT /log-promotions/<id>
# ============================================================================


class TestUpdateLogPromotion:
    def test_invalid_id_400(self, client, mock_db, auth_headers):
        resp = client.put('/api/v1/log-promotions/bogus',
                          headers=auth_headers,
                          data=json.dumps({'status': 'accepted'}))
        assert resp.status_code == 400

    def test_table_missing_404(self, client, mock_db, auth_headers):
        promo_id = str(uuid.uuid4())
        with patch('routes.logging_routes.table_has_column', return_value=False):
            resp = client.put(f'/api/v1/log-promotions/{promo_id}',
                              headers=auth_headers,
                              data=json.dumps({'status': 'dismissed'}))
            assert resp.status_code == 404

    def test_promotion_not_found_404(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = None
        promo_id = str(uuid.uuid4())

        resp = client.put(f'/api/v1/log-promotions/{promo_id}',
                          headers=auth_headers,
                          data=json.dumps({'status': 'accepted'}))
        assert resp.status_code == 404

    def test_dismiss_path(self, client, mock_db, auth_headers):
        """status='dismissed' updates the promotion row but not the source log."""
        conn, cur = mock_db
        cur.fetchone.return_value = {
            'source_table': 'health_input_log',
            'source_log_id': uuid.uuid4(),
            'suggested_catalog_id': uuid.uuid4(),
        }
        promo_id = str(uuid.uuid4())

        resp = client.put(f'/api/v1/log-promotions/{promo_id}',
                          headers=auth_headers,
                          data=json.dumps({'status': 'dismissed'}))
        assert resp.status_code == 200
        conn.commit.assert_called()

    def test_accept_health_input_log(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = {
            'source_table': 'health_input_log',
            'source_log_id': uuid.uuid4(),
            'suggested_catalog_id': uuid.uuid4(),
        }
        promo_id = str(uuid.uuid4())

        resp = client.put(f'/api/v1/log-promotions/{promo_id}',
                          headers=auth_headers,
                          data=json.dumps({'status': 'accepted'}))
        assert resp.status_code == 200

    def test_accept_food_log(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = {
            'source_table': 'health_food_logv2',
            'source_log_id': uuid.uuid4(),
            'suggested_catalog_id': uuid.uuid4(),
        }
        promo_id = str(uuid.uuid4())

        resp = client.put(f'/api/v1/log-promotions/{promo_id}',
                          headers=auth_headers,
                          data=json.dumps({'status': 'accepted'}))
        assert resp.status_code == 200

    def test_accept_invalid_source_table_400(self, client, mock_db, auth_headers):
        """If a stored promotion has an unknown source_table, accept returns 400."""
        conn, cur = mock_db
        cur.fetchone.return_value = {
            'source_table': 'invalid_table',
            'source_log_id': uuid.uuid4(),
            'suggested_catalog_id': uuid.uuid4(),
        }
        promo_id = str(uuid.uuid4())

        resp = client.put(f'/api/v1/log-promotions/{promo_id}',
                          headers=auth_headers,
                          data=json.dumps({'status': 'accepted'}))
        assert resp.status_code == 400


# ============================================================================
# DELETE /log-promotions/<id>
# ============================================================================


class TestDeleteLogPromotion:
    def test_invalid_id_400(self, client, mock_db, auth_headers):
        resp = client.delete('/api/v1/log-promotions/bogus',
                             headers=auth_headers)
        assert resp.status_code == 400

    def test_table_missing_404(self, client, mock_db, auth_headers):
        promo_id = str(uuid.uuid4())
        with patch('routes.logging_routes.table_has_column', return_value=False):
            resp = client.delete(f'/api/v1/log-promotions/{promo_id}',
                                 headers=auth_headers)
            assert resp.status_code == 404

    def test_not_found_404(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 0
        promo_id = str(uuid.uuid4())

        resp = client.delete(f'/api/v1/log-promotions/{promo_id}',
                             headers=auth_headers)
        assert resp.status_code == 404

    def test_happy_path(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 1
        promo_id = str(uuid.uuid4())

        resp = client.delete(f'/api/v1/log-promotions/{promo_id}',
                             headers=auth_headers)
        assert resp.status_code == 200
        conn.commit.assert_called()

    def test_db_error_returns_400(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.execute.side_effect = Exception('boom')
        promo_id = str(uuid.uuid4())

        resp = client.delete(f'/api/v1/log-promotions/{promo_id}',
                             headers=auth_headers)
        assert resp.status_code == 400


# ============================================================================
# GET /adherence
# ============================================================================


class TestGetAdherence:
    def test_default_window(self, client, mock_db, auth_headers):
        """No params — default 30-day window, no inputs returned."""
        conn, cur = mock_db
        cur.fetchall.return_value = []  # no health_inputs rows

        resp = client.get('/api/v1/adherence', headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert 'window' in body
        assert body['window']['days'] == 30
        assert body['inputs'] == []
        assert body['excluded_prn'] == []
        assert body['excluded_unspecified'] == []

    def test_with_explicit_window(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get(
            '/api/v1/adherence?start_date=2026-04-01&end_date=2026-04-07',
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.get_json()['window']['days'] == 7

    def test_invalid_input_ids_400(self, client, mock_db, auth_headers):
        resp = client.get(
            '/api/v1/adherence?input_ids=not-a-uuid',
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_valid_input_ids_filter(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get(
            f'/api/v1/adherence?input_ids={uuid.uuid4()},{uuid.uuid4()}',
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_scheduled_input_with_logs(self, client, mock_db, auth_headers):
        """A scheduled input with full adherence returns pct_adherence=100."""
        conn, cur = mock_db
        input_id = uuid.uuid4()
        # First fetchall: list of health_inputs
        # Second fetchall: log rows by (input_id, log_date)
        today = datetime.now().date()
        cur.fetchall.side_effect = [
            [{
                'id': input_id,
                'name': 'Aspirin',
                'input_type': 'medication',
                'default_unit': 'mg',
                'doses_per_day': 1,
                'direct_timeframe_id': None,
                'timeframes': [],
            }],
            [{
                'input_id': input_id,
                'log_date': today - timedelta(days=i),
                'log_count': 1,
            } for i in range(30)],
        ]

        resp = client.get('/api/v1/adherence', headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body['inputs']) == 1
        entry = body['inputs'][0]
        assert entry['name'] == 'Aspirin'
        assert entry['scheduled_doses'] == 30
        # Logged at most 30 (for the days that fell inside the window)
        assert entry['logged_doses'] >= 1

    def test_prn_input_excluded(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [{
            'id': uuid.uuid4(),
            'name': 'Ibuprofen',
            'input_type': 'medication',
            'default_unit': 'mg',
            'doses_per_day': -1,
            'direct_timeframe_id': None,
            'timeframes': [],
        }]

        resp = client.get('/api/v1/adherence', headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['inputs'] == []
        assert len(body['excluded_prn']) == 1
        assert body['excluded_prn'][0]['name'] == 'Ibuprofen'

    def test_unspecified_input_excluded(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [{
            'id': uuid.uuid4(),
            'name': 'VitaminD',
            'input_type': 'supplement',
            'default_unit': 'iu',
            'doses_per_day': None,
            'direct_timeframe_id': None,
            'timeframes': [],
        }]

        resp = client.get('/api/v1/adherence', headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['inputs'] == []
        assert len(body['excluded_unspecified']) == 1

    def test_db_error_returns_500(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.execute.side_effect = Exception('boom')

        resp = client.get('/api/v1/adherence', headers=auth_headers)
        assert resp.status_code == 500


# ============================================================================
# AUTH-required smoke check (one route is enough — same decorator on all)
# ============================================================================


class TestAuthRequired:
    def test_log_meal_rejects_unauthenticated(self, client):
        with patch('utils.auth.get_session', return_value=None):
            resp = client.post(
                '/api/v1/log-meal',
                data=json.dumps({}),
                headers={'Authorization': 'Bearer bad', 'Content-Type': 'application/json'},
            )
            assert resp.status_code == 401

    def test_get_all_logs_rejects_unauthenticated(self, client):
        with patch('utils.auth.get_session', return_value=None):
            resp = client.get(
                '/api/v1/all-logs',
                headers={'Authorization': 'Bearer bad'},
            )
            assert resp.status_code == 401
