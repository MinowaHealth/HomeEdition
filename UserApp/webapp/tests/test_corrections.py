"""
Tests for PUT /api/v1/healthkit/correct — data corrections endpoint.
"""
import json
import uuid

import pytest
from unittest.mock import MagicMock, patch


class TestCorrectHealthRecord:
    """Tests for the corrections endpoint in integrations.py."""

    @pytest.fixture(autouse=True)
    def setup_mock_db(self, mock_db):
        """Configure mock_db so cursor works as a context manager (with ... as cur)."""
        conn, cur = mock_db
        # The endpoint uses `with conn.cursor(cursor_factory=...) as cur:`
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        self.conn = conn
        self.cur = cur

    @pytest.fixture(autouse=True)
    def mock_analytics(self):
        with patch('routes.integrations.analytics') as mock:
            self.analytics = mock
            yield

    def test_correct_workout_activity_type(self, client, auth_headers):
        """Happy path: correct an unnamed exercise."""
        sample_id = str(uuid.uuid4())
        self.cur.fetchone.return_value = {'original': ''}
        self.cur.rowcount = 1

        resp = client.put('/api/v1/healthkit/correct', headers=auth_headers,
                          data=json.dumps({
                              'sample_id': sample_id,
                              'field': 'activityType',
                              'new_value': 'Running',
                          }))

        assert resp.status_code == 200
        assert resp.get_json() == {'ok': True}
        self.conn.commit.assert_called()
        self.analytics.capture.assert_called_once_with('data_corrected', {
            'record_type': 'workout',
            'corrected_field': 'activityType',
        })

    def test_correct_food_name(self, client, auth_headers):
        """Happy path: correct an unnamed food."""
        sample_id = str(uuid.uuid4())
        self.cur.fetchone.return_value = {'original': ''}
        self.cur.rowcount = 1

        resp = client.put('/api/v1/healthkit/correct', headers=auth_headers,
                          data=json.dumps({
                              'sample_id': sample_id,
                              'field': 'food_name',
                              'new_value': 'Chicken Salad',
                          }))

        assert resp.status_code == 200
        assert resp.get_json() == {'ok': True}
        self.analytics.capture.assert_called_once_with('data_corrected', {
            'record_type': 'food',
            'corrected_field': 'food_name',
        })

    def test_rejects_unknown_field(self, client, auth_headers):
        """400 for a field not in the allowlist."""
        resp = client.put('/api/v1/healthkit/correct', headers=auth_headers,
                          data=json.dumps({
                              'sample_id': str(uuid.uuid4()),
                              'field': 'calories',
                              'new_value': '500',
                          }))

        assert resp.status_code == 400
        assert 'field must be one of' in resp.get_json()['error']

    def test_rejects_missing_fields(self, client, auth_headers):
        """400 when required fields are missing."""
        resp = client.put('/api/v1/healthkit/correct', headers=auth_headers,
                          data=json.dumps({'sample_id': str(uuid.uuid4())}))

        assert resp.status_code == 400
        assert 'required' in resp.get_json()['error']

    def test_rejects_empty_body(self, client, auth_headers):
        """400 when JSON body is empty/null."""
        resp = client.put('/api/v1/healthkit/correct', headers=auth_headers,
                          data=json.dumps(None))

        assert resp.status_code == 400

    def test_returns_404_for_nonexistent_record(self, client, auth_headers):
        """404 when the sample_id doesn't match any record."""
        self.cur.fetchone.return_value = None
        self.cur.rowcount = 0

        resp = client.put('/api/v1/healthkit/correct', headers=auth_headers,
                          data=json.dumps({
                              'sample_id': str(uuid.uuid4()),
                              'field': 'activityType',
                              'new_value': 'Swimming',
                          }))

        assert resp.status_code == 404
        assert resp.get_json()['error'] == 'Record not found'
        self.conn.rollback.assert_called()
