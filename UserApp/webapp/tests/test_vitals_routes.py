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
