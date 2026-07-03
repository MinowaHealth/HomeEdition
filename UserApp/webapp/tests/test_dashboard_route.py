"""
Unit tests for GET /api/v1/dashboard.

The endpoint issues a fixed sequence of SQL queries whose results are
combined into a single response envelope; we feed the mock cursor that
sequence in order and assert the response shape. Focus: the envelope
structure stays stable and the query-param validation is strict — the
rollup arithmetic itself is covered by integration tests against a real
DB in UserApp/livetest/.
"""
from __future__ import annotations

from datetime import datetime


def _set_empty_dashboard_cursor(cur):
    """Seed a mock cursor with empty-but-well-formed dashboard query results."""
    bp_agg = {
        'count': 0, 'avg_systolic': None, 'avg_diastolic': None,
        'min_systolic': None, 'max_systolic': None,
        'min_diastolic': None, 'max_diastolic': None,
    }
    garmin_agg = {
        'days_available': 0, 'total_steps': None, 'avg_steps': None,
        'avg_resting_hr': None, 'avg_stress': None, 'avg_spo2': None,
        'total_sleep_secs': None,
    }
    # Dashboard issues ≥3 fetchone() calls even when empty: BP rollup,
    # BP latest (None), Garmin rollup. Per-metric "latest" fetchones are
    # skipped when metric_rollup is empty.
    cur.fetchone.side_effect = [bp_agg, None, garmin_agg]
    # fetchall order: metric_rollup, scheduled_inputs, bp_events,
    # metric_events, log_events, obs_events. Empty everywhere.
    cur.fetchall.side_effect = [[], [], [], [], [], []]


class TestDashboardValidation:
    def test_rejects_non_integer_days(self, client, mock_db, auth_headers):
        resp = client.get('/api/v1/dashboard?days=abc', headers=auth_headers)
        assert resp.status_code == 400
        assert 'integer' in resp.get_json()['error']

    def test_rejects_zero_days(self, client, mock_db, auth_headers):
        resp = client.get('/api/v1/dashboard?days=0', headers=auth_headers)
        assert resp.status_code == 400

    def test_rejects_days_over_cap(self, client, mock_db, auth_headers):
        resp = client.get('/api/v1/dashboard?days=365', headers=auth_headers)
        assert resp.status_code == 400
        assert 'between 1 and 90' in resp.get_json()['error']


class TestDashboardShape:
    def test_empty_dashboard_returns_all_sections(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        _set_empty_dashboard_cursor(cur)

        resp = client.get('/api/v1/dashboard?days=7', headers=auth_headers)
        assert resp.status_code == 200, resp.get_json()

        body = resp.get_json()
        for key in ('window', 'vitals', 'wearable', 'adherence', 'recent_events'):
            assert key in body

        assert body['window']['days'] == 7
        assert body['vitals']['blood_pressure']['count'] == 0
        assert body['vitals']['blood_pressure']['latest'] is None
        # Every configured metric type should be present even when empty
        assert 'weight' in body['vitals']['metrics']
        assert body['vitals']['metrics']['weight']['count'] == 0
        assert body['wearable']['days_available'] == 0
        assert body['adherence']['scheduled_input_count'] == 0
        assert body['recent_events'] == []

    def test_bp_latest_is_rendered_when_present(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        bp_agg = {
            'count': 1, 'avg_systolic': 120.0, 'avg_diastolic': 80.0,
            'min_systolic': 120, 'max_systolic': 120,
            'min_diastolic': 80, 'max_diastolic': 80,
        }
        bp_latest = {
            'measured_at': datetime(2026, 4, 15, 8, 30),
            'systolic': 120, 'diastolic': 80, 'pulse': 65,
        }
        garmin_agg = {
            'days_available': 0, 'total_steps': None, 'avg_steps': None,
            'avg_resting_hr': None, 'avg_stress': None, 'avg_spo2': None,
            'total_sleep_secs': None,
        }
        cur.fetchone.side_effect = [bp_agg, bp_latest, garmin_agg]
        cur.fetchall.side_effect = [[], [], [], [], [], []]

        resp = client.get('/api/v1/dashboard?days=30', headers=auth_headers)
        assert resp.status_code == 200

        body = resp.get_json()
        latest = body['vitals']['blood_pressure']['latest']
        assert latest is not None
        assert latest['systolic'] == 120
        assert latest['diastolic'] == 80
        assert body['vitals']['blood_pressure']['avg_systolic'] == 120.0
