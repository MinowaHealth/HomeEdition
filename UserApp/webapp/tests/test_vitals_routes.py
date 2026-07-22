"""
Unit tests for vitals blueprint routes.

Tests blood pressure, temperature, weight, observations, and metric deletion
endpoints with mocked DB connections.
"""
import json
import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest


class TestGetBloodPressure:
    def test_returns_readings(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        # _total simulates count(*) OVER() — every row carries the same total
        cur.fetchall.return_value = [
            {
                '_total': 1,
                'id': uuid.uuid4(),
                'measured_at': datetime(2026, 2, 24, 10, 30),
                'systolic': 120,
                'diastolic': 80,
                'pulse': 72,
            }
        ]

        resp = client.get('/api/v1/blood-pressure', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'entries' in data
        assert 'pagination' in data
        assert len(data['entries']) == 1
        assert data['entries'][0]['systolic'] == 120
        assert data['entries'][0]['diastolic'] == 80
        assert 'timestamp' in data['entries'][0]  # measured_at renamed to timestamp
        assert '_total' not in data['entries'][0]
        assert data['pagination']['total'] == 1
        assert data['pagination']['has_more'] is False
        # X-Truncated only fires when has_more is True
        assert resp.headers.get('X-Truncated') is None

    def test_returns_empty_list(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get('/api/v1/blood-pressure', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['entries'] == []
        assert data['pagination']['total'] == 0
        assert data['pagination']['has_more'] is False

    def test_null_device_maps_to_manual_source(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            {
                '_total': 1,
                'id': uuid.uuid4(),
                'measured_at': datetime(2026, 7, 18, 21, 36),
                'systolic': 132, 'diastolic': 60, 'pulse': 76,
                'device': None, 'position': None, 'arm': None, 'notes': None,
            }
        ]
        entry = client.get('/api/v1/blood-pressure', headers=auth_headers).get_json()['entries'][0]
        assert entry['source'] == 'manual'
        assert 'device' not in entry

    def test_device_becomes_source_with_position_arm_notes(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            {
                '_total': 1,
                'id': uuid.uuid4(),
                'measured_at': datetime(2026, 7, 18, 21, 36),
                'systolic': 132, 'diastolic': 60, 'pulse': 76,
                'device': 'cuff meter', 'position': 'supine',
                'arm': 'left wrist', 'notes': 'untrusted: supine cuff import',
            }
        ]
        entry = client.get('/api/v1/blood-pressure', headers=auth_headers).get_json()['entries'][0]
        assert entry['source'] == 'cuff meter'
        assert entry['position'] == 'supine'
        assert entry['arm'] == 'left wrist'
        assert entry['notes'] == 'untrusted: supine cuff import'

    def test_sources_filter_devices_and_manual(self, client, mock_db, auth_headers):
        """?sources=manual,cuff meter -> (device = ANY(%s) OR device IS NULL)."""
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get(
            '/api/v1/blood-pressure?sources=manual,cuff meter',
            headers=auth_headers,
        )
        assert resp.status_code == 200
        query_text = str(cur.execute.call_args.args[0])
        assert 'device = ANY' in query_text
        assert 'device IS NULL' in query_text
        # params: [tenant_id, user_id, devices_list, limit, offset]
        assert cur.execute.call_args.args[1][2] == ['cuff meter']

    def test_sources_filter_devices_only(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get(
            '/api/v1/blood-pressure?sources=cuff meter',
            headers=auth_headers,
        )
        assert resp.status_code == 200
        query_text = str(cur.execute.call_args.args[0])
        assert 'device = ANY' in query_text
        assert 'device IS NULL' not in query_text

    def test_sources_blank_400(self, client, mock_db, auth_headers):
        resp = client.get(
            '/api/v1/blood-pressure?sources=%2C', headers=auth_headers)
        assert resp.status_code == 400


class TestGetBloodPressureSources:
    def test_lists_sources_with_counts(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            {'source': 'manual', 'readings': 687,
             'first': datetime(2023, 10, 18), 'last': datetime(2026, 7, 19, 1, 4)},
            {'source': 'cuff meter', 'readings': 11,
             'first': datetime(2026, 7, 18, 21, 36), 'last': datetime(2026, 7, 19, 0, 20)},
        ]
        resp = client.get('/api/v1/blood-pressure/sources', headers=auth_headers)
        assert resp.status_code == 200
        sources = resp.get_json()['sources']
        assert len(sources) == 2
        assert sources[0]['readings'] == 687
        assert sources[1]['source'] == 'cuff meter'
        assert sources[1]['first'] == '2026-07-18T21:36:00'

    def test_empty(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        resp = client.get('/api/v1/blood-pressure/sources', headers=auth_headers)
        assert resp.get_json()['sources'] == []


class TestLogBloodPressure:
    def test_creates_reading(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        payload = {
            'timestamp': '2026-02-24T10:30:00',
            'systolic': 118,
            'diastolic': 78,
            'heart_rate': 70,
        }

        resp = client.post(
            '/api/v1/blood-pressure',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 201
        cur.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_optional_heart_rate(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        payload = {
            'timestamp': '2026-02-24T10:30:00',
            'systolic': 120,
            'diastolic': 80,
        }

        resp = client.post(
            '/api/v1/blood-pressure',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 201
        # Verify heart_rate/pulse arg is None when not provided
        call_args = cur.execute.call_args[0][1]
        assert call_args[6] is None  # pulse position in INSERT
        assert call_args[7] is None  # position
        assert call_args[8] is None  # device

    def test_position_and_device_stored(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        payload = {
            'timestamp': '2026-02-24T10:30:00',
            'systolic': 120,
            'diastolic': 80,
            'position': 'supine',
            'device': 'cuff meter',
        }
        resp = client.post(
            '/api/v1/blood-pressure',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 201
        call_args = cur.execute.call_args[0][1]
        assert call_args[7] == 'supine'
        assert call_args[8] == 'cuff meter'

    def test_blank_position_device_stored_as_null(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        payload = {
            'timestamp': '2026-02-24T10:30:00',
            'systolic': 120,
            'diastolic': 80,
            'position': '  ',
            'device': '',
        }
        resp = client.post(
            '/api/v1/blood-pressure',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 201
        call_args = cur.execute.call_args[0][1]
        assert call_args[7] is None
        assert call_args[8] is None


class TestDeleteBloodPressure:
    def test_deletes_reading(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.rowcount = 1
        reading_id = str(uuid.uuid4())

        resp = client.delete(
            f'/api/v1/blood-pressure/{reading_id}',
            headers=auth_headers,
        )
        assert resp.status_code == 200
        conn.commit.assert_called_once()


class TestGetObservations:
    def test_returns_observations(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            {
                '_total': 1,
                'id': uuid.uuid4(),
                'content': 'Feeling good today',
                'observed_at': datetime(2026, 2, 24, 10, 0),
                'category': 'general',
                'mental_health_flag': False,
            }
        ]

        resp = client.get('/api/v1/observations', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'entries' in data
        assert 'pagination' in data
        assert len(data['entries']) == 1
        assert data['entries'][0]['observation'] == 'Feeling good today'  # content renamed
        assert '_total' not in data['entries'][0]
        assert data['pagination']['total'] == 1

    def test_returns_empty_list(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get('/api/v1/observations', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['entries'] == []
        assert data['pagination']['total'] == 0


class TestCreateObservation:
    def test_creates_observation(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        payload = {
            'observation': 'Mild headache after exercise',
            'timestamp': '2026-02-24T14:00:00',
        }

        resp = client.post(
            '/api/v1/observations',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 201
        conn.commit.assert_called_once()


class TestGetTemperature:
    def test_returns_temperature_readings(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            {
                '_total': 1,
                'id': uuid.uuid4(),
                'recorded_at': datetime(2026, 2, 24, 8, 0),
                'value': 98.6,
                'unit': 'F',
            }
        ]

        resp = client.get('/api/v1/temperature', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'entries' in data
        assert data['entries'][0]['temperature'] == 98.6
        assert data['pagination']['total'] == 1


class TestLogTemperature:
    def test_creates_temperature(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        payload = {
            'timestamp': '2026-02-24T08:00:00',
            'temperature': 98.6,
        }

        resp = client.post(
            '/api/v1/temperature',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 201
        conn.commit.assert_called_once()


class TestGetWeight:
    def test_returns_weight_readings(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            {
                '_total': 1,
                'id': uuid.uuid4(),
                'recorded_at': datetime(2026, 2, 24, 7, 0),
                'value': 165.0,
                'unit': 'lbs',
            }
        ]

        resp = client.get('/api/v1/weight', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'entries' in data
        assert data['entries'][0]['weight'] == 165.0
        assert data['pagination']['total'] == 1


class TestLogWeight:
    def test_creates_weight(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        payload = {
            'timestamp': '2026-02-24T07:00:00',
            'weight': 165.0,
        }

        resp = client.post(
            '/api/v1/weight',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 201
        conn.commit.assert_called_once()
