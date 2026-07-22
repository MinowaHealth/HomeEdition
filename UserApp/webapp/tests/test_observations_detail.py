"""Unit tests for GET /api/v1/observations/detail.

Covers the ±hour window bounds, the per-observation signed offset from the
target, category rollup, the recent-target truncated_future flag, and input
validation. DB mocked via mock_db.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


def _dt(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _row(ts, content, category='text', severity=None, mhf=False, tags=None):
    return {
        'id': uuid.uuid4(),
        'observed_at': ts,
        'content': content,
        'category': category,
        'severity': severity,
        'mental_health_flag': mhf,
        'tags': tags,
    }


class TestObservationsDetail:
    def test_requires_at(self, client, mock_db, auth_headers):
        resp = client.get('/api/v1/observations/detail', headers=auth_headers)
        assert resp.status_code == 400
        assert 'at' in resp.get_json()['error']

    def test_invalid_at_400(self, client, mock_db, auth_headers):
        resp = client.get(
            '/api/v1/observations/detail?at=nope', headers=auth_headers)
        assert resp.status_code == 400

    def test_window_bounds(self, client, mock_db, auth_headers):
        """The query selects observed_at in [at-60, at+60]."""
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get(
            '/api/v1/observations/detail?at=2026-07-12T17:30:00Z',
            headers=auth_headers,
        )
        assert resp.status_code == 200
        at = _dt(2026, 7, 12, 17, 30)
        win_start, win_end = at - timedelta(minutes=60), at + timedelta(minutes=60)
        call = [c for c in cur.execute.call_args_list if len(c.args) == 2][-1]
        from conftest import TEST_USER_ID
        assert tuple(call.args[1]) == (1, TEST_USER_ID, win_start, win_end)
        assert resp.get_json()['window']['minutes'] == 121

    def test_signed_offset_and_fields(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            _row(_dt(2026, 7, 12, 17, 15), 'itchy throat',
                 category='symptom', severity=3, tags=['allergy']),
            _row(_dt(2026, 7, 12, 17, 45), 'took allegra', category='med'),
        ]
        resp = client.get(
            '/api/v1/observations/detail?at=2026-07-12T17:30:00Z',
            headers=auth_headers,
        )
        body = resp.get_json()
        before, after = body['observations']
        assert before['seconds_from_target'] == -900   # 15 min before
        assert before['observation'] == 'itchy throat'
        assert before['source_type'] == 'symptom'
        assert before['severity'] == 3
        assert before['tags'] == ['allergy']
        assert after['seconds_from_target'] == 900     # 15 min after
        assert body['counts']['observations'] == 2
        assert body['counts']['by_category'] == {'symptom': 1, 'med': 1}

    def test_null_category_and_tags_defaulted(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            _row(_dt(2026, 7, 12, 17, 30), 'note', category=None, tags=None),
        ]
        resp = client.get(
            '/api/v1/observations/detail?at=2026-07-12T17:30:00Z',
            headers=auth_headers,
        )
        obs = resp.get_json()['observations'][0]
        assert obs['source_type'] == 'text'
        assert obs['tags'] == []

    def test_recent_target_truncated_future(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        at = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
        resp = client.get(
            f'/api/v1/observations/detail?at={at}', headers=auth_headers)
        assert resp.get_json()['truncated_future'] is True

    def test_past_target_not_truncated(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        resp = client.get(
            '/api/v1/observations/detail?at=2020-01-01T03:00:00Z',
            headers=auth_headers,
        )
        assert resp.get_json()['truncated_future'] is False
