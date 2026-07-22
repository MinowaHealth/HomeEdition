"""Unit tests for GET /api/v1/garmin/sleep-events.

Covers the ± hour overlap window, un-clipped event bounds, the clipped
per-stage rollup, stage_at_target, the recent-target truncated_future flag,
and input validation. DB mocked via mock_db.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _dt(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


class TestGarminSleepEvents:
    def test_requires_at(self, client, mock_db, auth_headers):
        resp = client.get('/api/v1/garmin/sleep-events', headers=auth_headers)
        assert resp.status_code == 400
        assert 'at' in resp.get_json()['error']

    def test_invalid_at_400(self, client, mock_db, auth_headers):
        resp = client.get(
            '/api/v1/garmin/sleep-events?at=nope', headers=auth_headers)
        assert resp.status_code == 400

    def test_overlap_window_bounds(self, client, mock_db, auth_headers):
        """The query must select events overlapping [at-60, at+60]:
        start_time < window_end AND end_time > window_start."""
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.get(
            '/api/v1/garmin/sleep-events?at=2026-07-12T17:30:00Z',
            headers=auth_headers,
        )
        assert resp.status_code == 200
        at = _dt(2026, 7, 12, 17, 30)
        win_start, win_end = at - timedelta(minutes=60), at + timedelta(minutes=60)
        call = [c for c in cur.execute.call_args_list if len(c.args) == 2][-1]
        # params are (window_end, window_start) to match the < / > predicates
        assert tuple(call.args[1]) == (win_end, win_start)
        assert resp.get_json()['window']['minutes'] == 121

    def test_events_unclipped_rollup_clipped_and_stage_at_target(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        at = _dt(2026, 7, 12, 17, 30)
        # deep event straddles the window start (starts 20min before window,
        # i.e. 16:10, ends 16:40 — 10 min of it is inside the 16:30 edge).
        # light event contains the target instant.
        cur.fetchall.return_value = [
            {'start_time': _dt(2026, 7, 12, 16, 10),
             'end_time': _dt(2026, 7, 12, 16, 40), 'sleep_type': 'deep'},
            {'start_time': _dt(2026, 7, 12, 17, 20),
             'end_time': _dt(2026, 7, 12, 17, 50), 'sleep_type': 'light'},
        ]

        resp = client.get(
            '/api/v1/garmin/sleep-events?at=2026-07-12T17:30:00Z',
            headers=auth_headers,
        )
        body = resp.get_json()
        # Event bounds are the true, un-clipped values.
        deep = body['events'][0]
        assert deep['start'] == _dt(2026, 7, 12, 16, 10).isoformat()
        assert deep['duration_seconds'] == 1800          # full 30 min
        assert deep['contains_target'] is False
        light = body['events'][1]
        assert light['contains_target'] is True
        assert body['stage_at_target'] == 'light'
        # Rollup clips to the window: deep contributes only 16:30..16:40 = 600s.
        secs = body['counts']['in_window_seconds_by_type']
        assert secs['deep'] == 600
        assert secs['light'] == 1800
        assert body['counts']['by_type'] == {'deep': 1, 'light': 1}
        assert body['counts']['events'] == 2

    def test_target_in_gap_has_null_stage(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        at = _dt(2026, 7, 12, 17, 30)
        # Two events, neither containing 17:30.
        cur.fetchall.return_value = [
            {'start_time': _dt(2026, 7, 12, 17, 0),
             'end_time': _dt(2026, 7, 12, 17, 20), 'sleep_type': 'rem'},
            {'start_time': _dt(2026, 7, 12, 17, 40),
             'end_time': _dt(2026, 7, 12, 18, 0), 'sleep_type': 'light'},
        ]
        resp = client.get(
            '/api/v1/garmin/sleep-events?at=2026-07-12T17:30:00Z',
            headers=auth_headers,
        )
        assert resp.get_json()['stage_at_target'] is None

    def test_recent_target_truncated_future(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        at = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
        resp = client.get(
            f'/api/v1/garmin/sleep-events?at={at}', headers=auth_headers)
        assert resp.get_json()['truncated_future'] is True

    def test_past_target_not_truncated(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        resp = client.get(
            '/api/v1/garmin/sleep-events?at=2020-01-01T03:00:00Z',
            headers=auth_headers,
        )
        assert resp.get_json()['truncated_future'] is False
