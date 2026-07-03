"""
Unit tests for health_inputs blueprint routes.

Tests health inputs, stacks, and timeframes CRUD with mocked DB.
"""
import json
import uuid
from datetime import datetime

import pytest


class TestGetHealthInputs:
    def test_returns_inputs(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        # _total simulates count(*) OVER() — every row carries the same total
        cur.fetchall.return_value = [
            {
                '_total': 1,
                'id': uuid.uuid4(),
                'name': 'Vitamin D3',
                'input_type': 'supplement',
                'default_dosage': '5000 IU',
                'route': 'oral',
                'is_active': True,
                'notes': None,
                'instructions': None,
                'custom_fields': None,
                'doses_per_day': 1,
                'frequency': 'daily',
                'category': 'vitamins',
                'form': 'softgel',
                'brand': 'NatureMade',
                'take_with_food': True,
                'default_unit': 'iu',
                'created_at': datetime(2026, 1, 1),
                'updated_at': datetime(2026, 1, 1),
            }
        ]

        resp = client.get('/api/v1/health-inputs', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'entries' in data
        assert 'pagination' in data
        assert len(data['entries']) == 1
        assert '_total' not in data['entries'][0]
        assert data['pagination']['total'] == 1
        assert data['pagination']['has_more'] is False

    def test_returns_doses_per_day(self, client, mock_db, auth_headers):
        """GET returns doses_per_day in response (integer or null)."""
        conn, cur = mock_db
        # _total simulates count(*) OVER() — every row carries the same total
        cur.fetchall.return_value = [
            {
                '_total': 2,
                'id': uuid.uuid4(),
                'name': 'Adderall XR',
                'input_type': 'medication',
                'default_dosage': '30mg',
                'route': 'oral',
                'is_active': True,
                'notes': None,
                'instructions': None,
                'custom_fields': None,
                'doses_per_day': 2,
                'frequent_status': None,
            },
            {
                '_total': 2,
                'id': uuid.uuid4(),
                'name': 'Vitamin C',
                'input_type': 'supplement',
                'default_dosage': '1000mg',
                'route': 'oral',
                'is_active': True,
                'notes': None,
                'instructions': None,
                'custom_fields': None,
                'doses_per_day': None,
                'frequent_status': None,
            },
        ]

        resp = client.get('/api/v1/health-inputs', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['entries'][0]['doses_per_day'] == 2
        assert data['entries'][1]['doses_per_day'] is None
        assert data['pagination']['total'] == 2

    def test_empty_list(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get('/api/v1/health-inputs', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['entries'] == []
        assert data['pagination']['total'] == 0
        assert data['pagination']['has_more'] is False


class TestCreateHealthInput:
    def test_creates_input(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': str(uuid.uuid4())}
        payload = {
            'name': 'Metformin',
            'input_type': 'medication',
            'default_dosage': '500mg',
        }

        resp = client.post(
            '/api/v1/health-inputs',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 201
        conn.commit.assert_called()

    def test_requires_name(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        payload = {'input_type': 'medication'}

        resp = client.post(
            '/api/v1/health-inputs',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 400

    def test_requires_input_type(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        payload = {'name': 'Metformin'}

        resp = client.post(
            '/api/v1/health-inputs',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 400


class TestDosesPerDay:
    """Tests for the doses_per_day field on health inputs."""

    def test_create_with_doses_per_day(self, client, mock_db, auth_headers):
        """POST with doses_per_day stores and returns it."""
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': str(uuid.uuid4())}
        payload = {
            'name': 'Adderall XR',
            'input_type': 'medication',
            'default_dosage': '30mg',
            'doses_per_day': 2,
        }

        resp = client.post('/api/v1/health-inputs', headers=auth_headers,
                           data=json.dumps(payload))
        assert resp.status_code == 201
        conn.commit.assert_called()

    def test_create_without_doses_per_day(self, client, mock_db, auth_headers):
        """POST without doses_per_day defaults to null (no error)."""
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': str(uuid.uuid4())}
        payload = {
            'name': 'Aspirin',
            'input_type': 'medication',
        }

        resp = client.post('/api/v1/health-inputs', headers=auth_headers,
                           data=json.dumps(payload))
        assert resp.status_code == 201

    def test_create_prn(self, client, mock_db, auth_headers):
        """POST with doses_per_day=-1 (as-needed/PRN) succeeds."""
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': str(uuid.uuid4())}
        payload = {
            'name': 'Ibuprofen',
            'input_type': 'medication',
            'doses_per_day': -1,
        }

        resp = client.post('/api/v1/health-inputs', headers=auth_headers,
                           data=json.dumps(payload))
        assert resp.status_code == 201

    def test_update_adds_doses_per_day(self, client, mock_db, auth_headers):
        """PUT can add doses_per_day to an existing input."""
        conn, cur = mock_db
        cur.rowcount = 1
        cur.fetchone.return_value = {
            'name': 'Metformin', 'input_type': 'medication',
            'default_dosage': '500mg', 'default_unit': 'mg',
            'brand': None, 'form': 'tablet',
            'is_active': True, 'take_with_food': False,
            'notes': None, 'instructions': None,
            'custom_fields': None, 'doses_per_day': None,
            'frequent_status': None, 'timeframe_id': None,
        }
        input_id = str(uuid.uuid4())
        payload = {
            'name': 'Metformin',
            'input_type': 'medication',
            'doses_per_day': 2,
        }

        resp = client.put(f'/api/v1/health-inputs/{input_id}',
                          headers=auth_headers, data=json.dumps(payload))
        assert resp.status_code == 200

    def test_update_clears_doses_per_day(self, client, mock_db, auth_headers):
        """PUT with doses_per_day=null clears the field."""
        conn, cur = mock_db
        cur.rowcount = 1
        cur.fetchone.return_value = {
            'name': 'Metformin', 'input_type': 'medication',
            'default_dosage': '500mg', 'default_unit': 'mg',
            'brand': None, 'form': 'tablet',
            'is_active': True, 'take_with_food': False,
            'notes': None, 'instructions': None,
            'custom_fields': None, 'doses_per_day': 2,
            'frequent_status': None, 'timeframe_id': None,
        }
        input_id = str(uuid.uuid4())
        payload = {
            'name': 'Metformin',
            'input_type': 'medication',
            'doses_per_day': None,
        }

        resp = client.put(f'/api/v1/health-inputs/{input_id}',
                          headers=auth_headers, data=json.dumps(payload))
        assert resp.status_code == 200

    def test_rejects_string_doses_per_day(self, client, mock_db, auth_headers):
        """POST with string doses_per_day returns 400."""
        conn, cur = mock_db
        payload = {
            'name': 'Test Med',
            'input_type': 'medication',
            'doses_per_day': 'twice',
        }

        resp = client.post('/api/v1/health-inputs', headers=auth_headers,
                           data=json.dumps(payload))
        assert resp.status_code == 400
        assert 'doses_per_day' in resp.get_json()['error']

    def test_rejects_out_of_range(self, client, mock_db, auth_headers):
        """POST with doses_per_day=10 returns 400."""
        conn, cur = mock_db
        payload = {
            'name': 'Test Med',
            'input_type': 'medication',
            'doses_per_day': 10,
        }

        resp = client.post('/api/v1/health-inputs', headers=auth_headers,
                           data=json.dumps(payload))
        assert resp.status_code == 400

    def test_rejects_negative_other_than_minus_one(self, client, mock_db, auth_headers):
        """POST with doses_per_day=-5 returns 400 (only -1 allowed)."""
        conn, cur = mock_db
        payload = {
            'name': 'Test Med',
            'input_type': 'medication',
            'doses_per_day': -5,
        }

        resp = client.post('/api/v1/health-inputs', headers=auth_headers,
                           data=json.dumps(payload))
        assert resp.status_code == 400

    def test_rejects_zero(self, client, mock_db, auth_headers):
        """POST with doses_per_day=0 returns 400 (use null instead)."""
        conn, cur = mock_db
        payload = {
            'name': 'Test Med',
            'input_type': 'medication',
            'doses_per_day': 0,
        }

        resp = client.post('/api/v1/health-inputs', headers=auth_headers,
                           data=json.dumps(payload))
        assert resp.status_code == 400

    def test_put_rejects_invalid(self, client, mock_db, auth_headers):
        """PUT with invalid doses_per_day returns 400."""
        conn, cur = mock_db
        cur.fetchone.return_value = {
            'name': 'Test Med', 'input_type': 'medication',
            'default_dosage': '500mg', 'default_unit': 'mg',
            'brand': None, 'form': 'tablet',
            'is_active': True, 'take_with_food': False,
            'notes': None, 'instructions': None,
            'custom_fields': None, 'doses_per_day': None,
            'frequent_status': None, 'timeframe_id': None,
        }
        input_id = str(uuid.uuid4())
        payload = {
            'name': 'Test Med',
            'input_type': 'medication',
            'doses_per_day': 'daily',
        }

        resp = client.put(f'/api/v1/health-inputs/{input_id}',
                          headers=auth_headers, data=json.dumps(payload))
        assert resp.status_code == 400


class TestUpdateHealthInput:
    def test_updates_input(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 1
        input_id = str(uuid.uuid4())
        # fetchone returns existing record as a dict-like row keyed by
        # SELECT column name (db_manager connections default to RealDictCursor).
        cur.fetchone.return_value = {
            'name': 'Test Med', 'input_type': 'medication',
            'default_dosage': '500mg', 'default_unit': 'mg',
            'brand': None, 'form': 'tablet',
            'is_active': True, 'take_with_food': False,
            'notes': None, 'instructions': None,
            'custom_fields': None, 'doses_per_day': None,
            'frequent_status': None, 'timeframe_id': None,
        }
        # PUT requires both 'name' and 'input_type' (bare [] access in UPDATE)
        payload = {
            'name': 'Metformin ER',
            'input_type': 'medication',
            'default_dosage': '1000mg',
        }

        resp = client.put(
            f'/api/v1/health-inputs/{input_id}',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 200
        conn.commit.assert_called()


class TestDeleteHealthInput:
    def test_deletes_input(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 1
        input_id = str(uuid.uuid4())

        resp = client.delete(
            f'/api/v1/health-inputs/{input_id}',
            headers=auth_headers,
        )
        assert resp.status_code == 200
        conn.commit.assert_called()


class TestGetStacks:
    def test_returns_stacks(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        # Single query with json_agg — includes 'inputs' field from JOIN.
        # _total simulates count(*) OVER() — fires after GROUP BY in PG eval
        # order, so it counts the number of distinct stacks.
        cur.fetchall.return_value = [
            {
                '_total': 1,
                'id': uuid.uuid4(),
                'name': 'Morning Meds',
                'timeframe_id': None,
                'timeframe_name': None,
                'is_active': True,
                'inputs': None,  # json_agg returns NULL when no inputs
            }
        ]

        resp = client.get('/api/v1/stacks', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'entries' in data
        assert 'pagination' in data
        assert len(data['entries']) == 1
        assert data['pagination']['total'] == 1
        assert data['pagination']['has_more'] is False
        # _total should be stripped from the serialized row
        assert '_total' not in data['entries'][0]
        # json_agg NULL should have been normalized to empty list
        assert data['entries'][0]['inputs'] == []


class TestGetTimeframes:
    def test_returns_timeframes(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            {
                'id': uuid.uuid4(),
                'name': 'Morning',
                'time_of_day': '08:00',
                'sort_order': 1,
                'is_active': True,
            }
        ]

        resp = client.get('/api/v1/timeframes', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 1


class TestCreateStackValidation:
    def test_missing_name_returns_400(self, client, mock_db, auth_headers):
        resp = client.post(
            '/api/v1/stacks',
            data=json.dumps({}),
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.get_json() == {'error': 'Missing required field: name'}

    def test_empty_name_returns_400(self, client, mock_db, auth_headers):
        resp = client.post(
            '/api/v1/stacks',
            data=json.dumps({'name': '   '}),
            headers=auth_headers,
        )
        assert resp.status_code == 400


class TestCreateTimeframeValidation:
    def test_missing_name_returns_400(self, client, mock_db, auth_headers):
        resp = client.post(
            '/api/v1/timeframes',
            data=json.dumps({}),
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.get_json() == {'error': 'Missing required field: name'}


class TestUpdateHealthInputInstructions:
    def test_put_persists_instructions(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        input_id = str(uuid.uuid4())
        cur.fetchone.return_value = {
            'name': 'Aspirin', 'input_type': 'medication',
            'default_dosage': '81mg', 'default_unit': 'mg',
            'brand': None, 'form': 'tablet',
            'is_active': True, 'take_with_food': False,
            'notes': None, 'instructions': 'Take with water',
            'custom_fields': None, 'doses_per_day': 1, 'frequent_status': None,
            'timeframe_id': None,
        }
        cur.rowcount = 1

        resp = client.put(
            f'/api/v1/health-inputs/{input_id}',
            data=json.dumps({'instructions': 'Take after meals'}),
            headers=auth_headers,
        )
        assert resp.status_code == 200

        update_call = next(
            c for c in cur.execute.call_args_list
            if c.args and 'UPDATE health_inputs' in c.args[0]
        )
        sql = update_call.args[0]
        params = update_call.args[1]
        assert 'instructions' in sql
        assert 'Take after meals' in params


class TestTimeframeNotes:
    def test_post_persists_notes(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': uuid.uuid4()}

        resp = client.post(
            '/api/v1/timeframes',
            data=json.dumps({'name': 'Morning', 'notes': 'pre-coffee'}),
            headers=auth_headers,
        )
        assert resp.status_code == 201

        insert_call = next(
            c for c in cur.execute.call_args_list
            if c.args and 'INSERT INTO timeframes' in c.args[0]
        )
        assert 'notes' in insert_call.args[0]
        assert 'pre-coffee' in insert_call.args[1]

    def test_put_persists_notes(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        tf_id = str(uuid.uuid4())
        resp = client.put(
            f'/api/v1/timeframes/{tf_id}',
            data=json.dumps({'name': 'Morning', 'notes': 'updated note'}),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        update_call = next(
            c for c in cur.execute.call_args_list
            if c.args and 'UPDATE timeframes' in c.args[0]
        )
        assert 'notes' in update_call.args[0]
        assert 'updated note' in update_call.args[1]


class TestGetTimeframesFormat:
    def test_time_of_day_is_hhmm(self, client, mock_db, auth_headers):
        from datetime import time as dt_time
        conn, cur = mock_db
        cur.fetchall.return_value = [
            {'id': uuid.uuid4(), 'name': 'Morning',
             'time_of_day': dt_time(8, 0), 'sort_order': 0, 'is_active': True},
        ]
        resp = client.get('/api/v1/timeframes', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()[0]['time_of_day'] == '08:00'


class TestUnitNormalization:
    """default_unit is normalized through units.normalize_unit on write."""

    EXISTING = {
        'name': 'Vitamin D3', 'input_type': 'supplement',
        'default_dosage': '5000', 'default_unit': 'iu',
        'brand': None, 'form': 'softgel',
        'is_active': True, 'take_with_food': False,
        'notes': None, 'instructions': None,
        'custom_fields': None, 'doses_per_day': None,
        'frequent_status': None, 'timeframe_id': None,
    }

    @staticmethod
    def _insert_params(cur):
        call = next(
            c for c in cur.execute.call_args_list
            if c.args and 'INSERT INTO health_inputs' in c.args[0]
        )
        return call.args[1]

    @staticmethod
    def _update_params(cur):
        call = next(
            c for c in cur.execute.call_args_list
            if c.args and 'UPDATE health_inputs' in c.args[0]
        )
        return call.args[1]

    def test_post_normalizes_alias(self, client, mock_db, auth_headers):
        """POST with 'MCG' stores canonical 'ug'."""
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': str(uuid.uuid4())}
        payload = {'name': 'B12', 'input_type': 'supplement',
                   'default_unit': 'MCG'}

        resp = client.post('/api/v1/health-inputs', headers=auth_headers,
                           data=json.dumps(payload))
        assert resp.status_code == 201
        assert 'ug' in self._insert_params(cur)
        assert 'MCG' not in self._insert_params(cur)

    def test_post_rejects_unknown_unit(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        payload = {'name': 'B12', 'input_type': 'supplement',
                   'default_unit': 'furlong'}

        resp = client.post('/api/v1/health-inputs', headers=auth_headers,
                           data=json.dumps(payload))
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['code'] == 'INVALID_UNIT'
        assert 'mg' in body['error']

    def test_post_empty_unit_stores_null(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': str(uuid.uuid4())}
        payload = {'name': 'B12', 'input_type': 'supplement',
                   'default_unit': ''}

        resp = client.post('/api/v1/health-inputs', headers=auth_headers,
                           data=json.dumps(payload))
        assert resp.status_code == 201
        assert None in self._insert_params(cur)
        assert '' not in self._insert_params(cur)

    def test_put_normalizes_alias(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 1
        cur.fetchone.return_value = dict(self.EXISTING)
        payload = {'name': 'Vitamin D3', 'input_type': 'supplement',
                   'default_unit': 'IU'}

        resp = client.put(f'/api/v1/health-inputs/{uuid.uuid4()}',
                          headers=auth_headers, data=json.dumps(payload))
        assert resp.status_code == 200
        assert 'iu' in self._update_params(cur)
        assert 'IU' not in self._update_params(cur)

    def test_put_rejects_unknown_unit(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = dict(self.EXISTING)
        payload = {'name': 'Vitamin D3', 'input_type': 'supplement',
                   'default_unit': 'tbd'}

        resp = client.put(f'/api/v1/health-inputs/{uuid.uuid4()}',
                          headers=auth_headers, data=json.dumps(payload))
        assert resp.status_code == 400
        assert resp.get_json()['code'] == 'INVALID_UNIT'
        conn.close.assert_called()

    def test_put_omitted_unit_keeps_legacy_value(self, client, mock_db, auth_headers):
        """PUT without default_unit passes a legacy stored value through
        unchanged — non-canonical rows stay updatable for other fields."""
        conn, cur = mock_db
        cur.rowcount = 1
        existing = dict(self.EXISTING, default_unit='tbd')
        cur.fetchone.return_value = existing
        payload = {'name': 'Vitamin D3', 'input_type': 'supplement',
                   'notes': 'take in the morning'}

        resp = client.put(f'/api/v1/health-inputs/{uuid.uuid4()}',
                          headers=auth_headers, data=json.dumps(payload))
        assert resp.status_code == 200
        assert 'tbd' in self._update_params(cur)


class TestUnitNormalizationV2:
    """v2 endpoints share the same normalization; embedding is patched out."""

    def test_post_normalizes_alias(self, client, mock_db, auth_headers):
        from unittest.mock import patch as mock_patch
        conn, cur = mock_db
        cur.fetchone.return_value = {'id': str(uuid.uuid4())}
        payload = {'name': 'B12', 'input_type': 'supplement',
                   'default_unit': 'µg'}

        with mock_patch('routes.health_inputs_v2.embed_field',
                        return_value=None) as embed:
            resp = client.post('/api/v2/health-inputs', headers=auth_headers,
                               data=json.dumps(payload))
        assert resp.status_code == 201
        embed.assert_called_once()
        insert_call = next(
            c for c in cur.execute.call_args_list
            if c.args and 'INSERT INTO health_inputs' in c.args[0]
        )
        assert 'ug' in insert_call.args[1]

    def test_post_rejects_unknown_unit_before_embedding(self, client, mock_db,
                                                        auth_headers):
        from unittest.mock import patch as mock_patch
        conn, cur = mock_db
        payload = {'name': 'B12', 'input_type': 'supplement',
                   'default_unit': 'furlong'}

        with mock_patch('routes.health_inputs_v2.embed_field') as embed:
            resp = client.post('/api/v2/health-inputs', headers=auth_headers,
                               data=json.dumps(payload))
        assert resp.status_code == 400
        assert resp.get_json()['code'] == 'INVALID_UNIT'
        embed.assert_not_called()

    def test_put_rejects_unknown_unit(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchone.return_value = dict(TestUnitNormalization.EXISTING)
        payload = {'name': 'Vitamin D3', 'input_type': 'supplement',
                   'default_unit': 'dose'}

        resp = client.put(f'/api/v2/health-inputs/{uuid.uuid4()}',
                          headers=auth_headers, data=json.dumps(payload))
        assert resp.status_code == 400
        assert resp.get_json()['code'] == 'INVALID_UNIT'


class TestDeleteTimeframeOrphans:
    def test_reports_orphaned_count(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        tf_id = str(uuid.uuid4())
        cur.fetchone.return_value = {'count': 3}

        resp = client.delete(
            f'/api/v1/timeframes/{tf_id}',
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get('stacks_orphaned') == 3
