"""Unit tests for POST /api/v1/garmin/sync `range` parameter.

Covers the week/month/quarter window widths, the 'all' history floor,
precedence of explicit from_date over range, invalid-range rejection, and
the unchanged no-body self-heal default. DB is mocked; queue_garmin_sync is
patched so no job thread starts.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytz

CREDS = {'encrypted_password': 'b64-garth-blob', 'last_sync': None}
JOB_ROW = {'id': '3f1c2f5e-0000-0000-0000-000000000001'}


def _post_sync(client, mock_db, auth_headers, body):
    conn, cur = mock_db
    cur.fetchone.side_effect = [dict(CREDS), dict(JOB_ROW)]
    with patch('garmin_worker.queue_garmin_sync') as queue:
        resp = client.post('/api/v1/garmin/sync', json=body, headers=auth_headers)
    return resp, queue


def _width_days(body):
    f = datetime.strptime(body['sync_from'], '%Y-%m-%d').date()
    t = datetime.strptime(body['sync_to'], '%Y-%m-%d').date()
    return (t - f).days + 1


def _user_today():
    return datetime.now(pytz.timezone('America/Los_Angeles')).date()


class TestGarminSyncRange:
    def test_range_widths(self, client, mock_db, auth_headers):
        for rng, days in (('week', 7), ('month', 30), ('quarter', 90)):
            resp, queue = _post_sync(client, mock_db, auth_headers, {'range': rng})
            assert resp.status_code == 202, rng
            body = resp.get_json()
            assert _width_days(body) == days, rng
            assert body['sync_to'] == _user_today().isoformat()
            queue.assert_called_once()

    def test_range_all_uses_history_floor(self, client, mock_db, auth_headers):
        resp, _ = _post_sync(client, mock_db, auth_headers, {'range': 'all'})
        assert resp.status_code == 202
        assert resp.get_json()['sync_from'] == '2010-01-01'

    def test_invalid_range_400(self, client, mock_db, auth_headers):
        resp, queue = _post_sync(client, mock_db, auth_headers, {'range': 'fortnight'})
        assert resp.status_code == 400
        assert 'week, month, quarter, all' in resp.get_json()['error']
        queue.assert_not_called()

    def test_explicit_from_date_beats_range(self, client, mock_db, auth_headers):
        resp, _ = _post_sync(
            client, mock_db, auth_headers,
            {'range': 'week', 'from_date': '2026-01-01', 'to_date': '2026-01-05'},
        )
        body = resp.get_json()
        assert body['sync_from'] == '2026-01-01'
        assert body['sync_to'] == '2026-01-05'

    def test_no_body_keeps_self_heal_default(self, client, mock_db, auth_headers):
        """Regression pin: empty body = 90-day-back window (91 days inclusive)."""
        resp, _ = _post_sync(client, mock_db, auth_headers, {})
        body = resp.get_json()
        assert _width_days(body) == 91
        assert body['sync_to'] == _user_today().isoformat()

    def test_no_body_reaches_back_to_older_last_sync(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        old = datetime.now(pytz.utc) - timedelta(days=200)
        cur.fetchone.side_effect = [
            {'encrypted_password': 'b64', 'last_sync': old},
            dict(JOB_ROW),
        ]
        with patch('garmin_worker.queue_garmin_sync'):
            resp = client.post('/api/v1/garmin/sync', json={}, headers=auth_headers)
        assert resp.get_json()['sync_from'] == old.date().isoformat()

    def test_range_skips_self_heal_reach_back(self, client, mock_db, auth_headers):
        """Explicit range means exactly that window even when last_sync is old."""
        conn, cur = mock_db
        old = datetime.now(pytz.utc) - timedelta(days=200)
        cur.fetchone.side_effect = [
            {'encrypted_password': 'b64', 'last_sync': old},
            dict(JOB_ROW),
        ]
        with patch('garmin_worker.queue_garmin_sync'):
            resp = client.post('/api/v1/garmin/sync', json={'range': 'week'},
                               headers=auth_headers)
        assert _width_days(resp.get_json()) == 7
