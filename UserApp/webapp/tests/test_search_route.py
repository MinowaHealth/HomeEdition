"""
Unit tests for GET /api/v1/search.

Focus: argument validation and the keyword-fallback path when Ollama is
unreachable. Semantic-path SQL is exercised in integration tests against
a real pgvector database.
"""
from __future__ import annotations

from unittest.mock import patch


class TestSearchValidation:
    def test_rejects_missing_q(self, client, mock_db, auth_headers):
        resp = client.get('/api/v1/search', headers=auth_headers)
        assert resp.status_code == 400
        assert 'q is required' in resp.get_json()['error']

    def test_rejects_empty_q(self, client, mock_db, auth_headers):
        resp = client.get('/api/v1/search?q=%20%20', headers=auth_headers)
        assert resp.status_code == 400

    def test_rejects_overlong_q(self, client, mock_db, auth_headers):
        q = 'x' * 501
        resp = client.get(f'/api/v1/search?q={q}', headers=auth_headers)
        assert resp.status_code == 400
        assert '500' in resp.get_json()['error']

    def test_rejects_unknown_scope(self, client, mock_db, auth_headers):
        resp = client.get('/api/v1/search?q=bp&scope=bogus', headers=auth_headers)
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'unknown scope' in body['error']
        assert 'all' in body['supported']

    def test_rejects_non_integer_k(self, client, mock_db, auth_headers):
        resp = client.get('/api/v1/search?q=bp&k=abc', headers=auth_headers)
        assert resp.status_code == 400

    def test_rejects_k_over_cap(self, client, mock_db, auth_headers):
        resp = client.get('/api/v1/search?q=bp&k=100', headers=auth_headers)
        assert resp.status_code == 400


class TestSearchKeywordFallback:
    def test_keyword_fallback_when_ollama_unreachable(self, client, mock_db, auth_headers):
        """If get_embedding returns None (Ollama down), /search must still
        respond successfully using ILIKE keyword matching."""
        conn, cur = mock_db
        # Every table probe returns zero rows — we only care that the
        # endpoint returned 200 with mode=keyword, not the query results.
        cur.fetchall.return_value = []

        with patch('embedding_utils.get_embedding', return_value=None), \
             patch('utils.table_has_column', return_value=True):
            resp = client.get(
                '/api/v1/search?q=blood+pressure&scope=observations',
                headers=auth_headers,
            )

        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body['mode'] == 'keyword'
        assert body['scope'] == 'observations'
        assert body['query'] == 'blood pressure'
        assert isinstance(body['results'], list)


class TestSearchDateWindow:
    """minowa-mcp-bug-report.md Bug 2: the `to` bound must include the whole
    `to` day (exclusive next-day bound), not cut off at its midnight."""

    def _run(self, client, cur, auth_headers, url):
        cur.fetchall.return_value = []
        # Patch the name routes.search actually calls — patching
        # utils.table_has_column would leave search's imported reference
        # pointing at the real function, which returns False against a
        # MagicMock conn and silently skips every table.
        with patch('embedding_utils.get_embedding', return_value=None), \
             patch('routes.search.table_has_column', return_value=True):
            return client.get(url, headers=auth_headers)

    def test_to_bound_is_exclusive_next_day(self, client, mock_db, auth_headers):
        from datetime import datetime
        conn, cur = mock_db

        resp = self._run(
            client, cur, auth_headers,
            '/api/v1/search?q=Allegra&scope=observations&from=2026-05-01&to=2026-05-31',
        )
        assert resp.status_code == 200

        # The bound datetime params of the keyword query: from at May 1
        # midnight, to strictly greater than May 31 midnight (next day).
        bound = [c for c in cur.execute.call_args_list if len(c.args) == 2]
        assert bound, 'no parameterized search query executed'
        params = [p for p in bound[-1].args[1] if isinstance(p, datetime)]
        assert len(params) == 2
        from_p, to_p = params
        assert (from_p.month, from_p.day) == (5, 1)
        assert (to_p.month, to_p.day) == (6, 1), (
            'to bound must be the exclusive next day, got %r' % to_p)

    def test_single_day_window_is_valid(self, client, mock_db, auth_headers):
        """from == to must be a 24h window, not an empty one."""
        from datetime import datetime
        conn, cur = mock_db

        resp = self._run(
            client, cur, auth_headers,
            '/api/v1/search?q=x&scope=observations&from=2026-05-18&to=2026-05-18',
        )
        assert resp.status_code == 200
        bound = [c for c in cur.execute.call_args_list if len(c.args) == 2]
        params = [p for p in bound[-1].args[1] if isinstance(p, datetime)]
        from_p, to_p = params
        assert to_p > from_p

    def test_applied_echoed(self, client, mock_db, auth_headers):
        conn, cur = mock_db
        resp = self._run(
            client, cur, auth_headers,
            '/api/v1/search?q=x&scope=observations&from=2026-05-01&to=2026-05-31',
        )
        assert resp.get_json()['applied'] == {'from': '2026-05-01', 'to': '2026-05-31'}

    def test_dst_transition_to_bound(self, client, mock_db, auth_headers):
        """The exclusive bound must be the next LOCAL midnight, not the UTC
        instant + 24h. 2026-03-08 is the US spring-forward date; the test
        user's home_timezone is America/Los_Angeles (conftest), so local
        midnight 2026-03-09 PDT is 07:00 UTC. The add-24h bug would land at
        08:00 UTC (01:00 PDT) and include an extra hour of March 9."""
        from datetime import datetime, timezone
        conn, cur = mock_db

        resp = self._run(
            client, cur, auth_headers,
            '/api/v1/search?q=x&scope=observations&from=2026-03-01&to=2026-03-08',
        )
        assert resp.status_code == 200
        bound = [c for c in cur.execute.call_args_list if len(c.args) == 2]
        params = [p for p in bound[-1].args[1] if isinstance(p, datetime)]
        _, to_p = params
        assert to_p.astimezone(timezone.utc) == datetime(
            2026, 3, 9, 7, 0, tzinfo=timezone.utc)

    def test_garbage_dates_400(self, client, mock_db, auth_headers):
        resp = self._run(
            client, cur=mock_db[1], auth_headers=auth_headers,
            url='/api/v1/search?q=x&from=not-a-date',
        )
        assert resp.status_code == 400
