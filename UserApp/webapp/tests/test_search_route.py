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
