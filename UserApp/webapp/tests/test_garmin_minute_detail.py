"""Unit tests for GET /api/v1/garmin/minute-detail.

Covers the ± hour window bounds, per-minute merge across the three series,
the recent-target `truncated_future` flag, and input validation. The DB is
mocked (mock_db); the three garm_* SELECTs are fed via fetchall.side_effect.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _dt(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


class TestGarminMinuteDetail:
    def test_requires_at(self, client, mock_db, auth_headers):
        resp = client.get('/api/v1/garmin/minute-detail', headers=auth_headers)
        assert resp.status_code == 400
        assert 'at' in resp.get_json()['error']

    def test_invalid_at_400(self, client, mock_db, auth_headers):
        resp = client.get(
            '/api/v1/garmin/minute-detail?at=not-a-timestamp',
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_window_is_plus_minus_60_minutes(self, client, mock_db, auth_headers):
        """Every garm_* SELECT must be bound to [at-60min, at+60min]."""
        conn, cur = mock_db
        cur.fetchall.side_effect = [[], [], []]

        resp = client.get(
            '/api/v1/garmin/minute-detail?at=2026-07-13T18:00:00Z',
            headers=auth_headers,
        )
        assert resp.status_code == 200
        at = _dt(2026, 7, 13, 18, 0)
        expected = (at - timedelta(minutes=60), at + timedelta(minutes=60))
        bound = [c for c in cur.execute.call_args_list if len(c.args) == 2]
        assert len(bound) == 3  # hr, rr, stress
        for c in bound:
            assert tuple(c.args[1]) == expected
        body = resp.get_json()
        assert body['window']['minutes'] == 121
        assert body['target'] == at.isoformat()

    def test_merges_series_by_minute(self, client, mock_db, auth_headers):
        """A minute with samples in more than one series collapses to one row;
        multiple samples in the same minute average."""
        conn, cur = mock_db
        m = _dt(2026, 7, 13, 18, 0)
        cur.fetchall.side_effect = [
            # hr: two samples in the 18:00 minute -> average 70
            [{'timestamp': m, 'heart_rate': 68},
             {'timestamp': m.replace(second=30), 'heart_rate': 72}],
            # rr: one sample in the same minute
            [{'timestamp': m.replace(second=10), 'respiratory_rate': 14.2}],
            # stress: a different minute
            [{'timestamp': m + timedelta(minutes=1), 'garm_stress': 30}],
        ]

        resp = client.get(
            '/api/v1/garmin/minute-detail?at=2026-07-13T18:00:00Z',
            headers=auth_headers,
        )
        body = resp.get_json()
        samples = {s['minute']: s for s in body['samples']}
        assert len(samples) == 2
        first = samples[m.isoformat()]
        assert first['heart_rate'] == 70          # (68+72)/2
        assert first['respiratory_rate'] == 14.2
        assert first['stress'] is None
        second = samples[(m + timedelta(minutes=1)).isoformat()]
        assert second['stress'] == 30
        assert second['heart_rate'] is None
        assert body['counts'] == {
            'heart_rate': 2, 'respiratory_rate': 1, 'stress': 1, 'minutes': 2,
        }

    def test_recent_target_sets_truncated_future(self, client, mock_db, auth_headers):
        """A target within the last hour has a window reaching into the future;
        the flag must be set so the caller knows the data is partial."""
        conn, cur = mock_db
        cur.fetchall.side_effect = [[], [], []]
        now = datetime.now(timezone.utc)
        at = (now - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')

        resp = client.get(
            f'/api/v1/garmin/minute-detail?at={at}', headers=auth_headers)
        assert resp.get_json()['truncated_future'] is True

    def test_past_target_not_truncated(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        cur.fetchall.side_effect = [[], [], []]

        resp = client.get(
            '/api/v1/garmin/minute-detail?at=2020-01-01T12:00:00Z',
            headers=auth_headers,
        )
        assert resp.get_json()['truncated_future'] is False
