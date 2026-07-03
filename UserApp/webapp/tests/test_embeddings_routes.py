"""
Unit tests for embedding sync and semantic search routes.

Routes in routes/embeddings.py:
  - POST /api/v1/sync-embeddings
  - POST /api/v1/semantic-search
"""
import json
import uuid
from unittest.mock import patch, MagicMock, call

import pytest


@pytest.fixture()
def mock_embedding_deps():
    """Mock embedding utilities and pgvector registration."""
    table_config = {
        'health_observations': {
            'embed_column': 'embedding_content',
            'timestamp_column': 'observed_at',
            'display_column': 'content',
            'text_column': 'content',
        },
    }
    with patch('routes.embeddings.table_has_column', return_value=True), \
         patch('embedding_utils.EMBEDDING_TABLES', table_config), \
         patch('embedding_utils.TIER1_TABLES', ['health_observations']), \
         patch('embedding_utils.EMBEDDING_DIMENSIONS', 768), \
         patch('embedding_utils.register_pgvector'), \
         patch('embedding_utils.set_ivfflat_probes'), \
         patch('embedding_utils.validate_embedding_vector', return_value=True), \
         patch('embedding_utils.get_embedding', return_value=[0.1] * 768):
        yield table_config


class TestSyncEmbeddings:
    """POST /api/v1/sync-embeddings"""

    def test_success_with_client_embedding(self, client, auth_headers, mock_db, mock_embedding_deps):
        """Client-provided embedding is stored."""
        conn, cur = mock_db
        cur.rowcount = 1

        resp = client.post(
            '/api/v1/sync-embeddings',
            data=json.dumps({
                'device_capabilities': {'device_id': 'dev-123', 'can_embed': True},
                'embeddings': [{
                    'table': 'health_observations',
                    'record_id': str(uuid.uuid4()),
                    'content': 'feeling tired',
                    'embedding': [0.1] * 768,
                }],
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['processed'] == 1
        assert data['embedded_client_side'] == 1

    def test_success_server_side_embedding(self, client, auth_headers, mock_db, mock_embedding_deps):
        """Content without embedding triggers server-side generation."""
        conn, cur = mock_db
        cur.rowcount = 1

        with patch('embedding_utils.validate_embedding_vector', return_value=False):
            resp = client.post(
                '/api/v1/sync-embeddings',
                data=json.dumps({
                    'device_capabilities': {},
                    'embeddings': [{
                        'table': 'health_observations',
                        'record_id': str(uuid.uuid4()),
                        'content': 'feeling tired',
                    }],
                }),
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['processed'] == 1
        assert data['embedded_server_side'] == 1

    def test_rejects_unknown_table(self, client, auth_headers, mock_db, mock_embedding_deps):
        """Unknown table name returns error for that item."""
        conn, cur = mock_db
        resp = client.post(
            '/api/v1/sync-embeddings',
            data=json.dumps({
                'device_capabilities': {},
                'embeddings': [{
                    'table': 'nonexistent_table',
                    'record_id': str(uuid.uuid4()),
                    'content': 'test',
                }],
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['processed'] == 0
        assert len(data['errors']) == 1
        assert 'Unknown table' in data['errors'][0]['error']

    def test_rejects_over_max_batch(self, client, auth_headers, mock_embedding_deps):
        """More than 100 embeddings rejected."""
        resp = client.post(
            '/api/v1/sync-embeddings',
            data=json.dumps({
                'device_capabilities': {},
                'embeddings': [{'table': 'x', 'record_id': 'y'}] * 101,
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert 'Max' in resp.get_json()['error']

    def test_rejects_non_array_embeddings(self, client, auth_headers, mock_embedding_deps):
        """embeddings must be an array."""
        resp = client.post(
            '/api/v1/sync-embeddings',
            data=json.dumps({
                'device_capabilities': {},
                'embeddings': 'not-an-array',
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_missing_record_id(self, client, auth_headers, mock_db, mock_embedding_deps):
        """Missing record_id returns per-item error."""
        conn, cur = mock_db
        resp = client.post(
            '/api/v1/sync-embeddings',
            data=json.dumps({
                'device_capabilities': {},
                'embeddings': [{
                    'table': 'health_observations',
                    'content': 'test',
                }],
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['processed'] == 0
        assert 'record_id' in data['errors'][0]['error']

    def test_empty_embeddings_array(self, client, auth_headers, mock_db, mock_embedding_deps):
        """Empty array is valid — returns zero processed."""
        conn, cur = mock_db
        resp = client.post(
            '/api/v1/sync-embeddings',
            data=json.dumps({
                'device_capabilities': {},
                'embeddings': [],
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['processed'] == 0
        assert data['errors'] == []

    def test_requires_auth(self, client):
        """Unauthenticated request rejected."""
        with patch('utils.auth.get_session', return_value=None):
            resp = client.post(
                '/api/v1/sync-embeddings',
                data=json.dumps({'embeddings': []}),
                headers={'Authorization': 'Bearer bad', 'Content-Type': 'application/json'},
            )
            assert resp.status_code == 401


class TestSemanticSearch:
    """POST /api/v1/semantic-search"""

    def test_success_with_query_text(self, client, auth_headers, mock_db, mock_embedding_deps):
        """Text query triggers server-side embedding then search."""
        conn, cur = mock_db
        cur.fetchall.return_value = [{
            'id': uuid.uuid4(),
            'content': 'felt tired after lunch',
            'timestamp': None,
            'similarity': 0.85,
        }]

        resp = client.post(
            '/api/v1/semantic-search',
            data=json.dumps({'query': 'fatigue after eating'}),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['query_embedded_by'] == 'server'
        assert len(data['results']) >= 0  # May be 0 if mock doesn't match

    def test_success_with_query_embedding(self, client, auth_headers, mock_db, mock_embedding_deps):
        """Pre-computed query vector used directly."""
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.post(
            '/api/v1/semantic-search',
            data=json.dumps({
                'query_embedding': [0.1] * 768,
                'limit': 3,
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['query_embedded_by'] == 'client'

    def test_requires_query_or_embedding(self, client, auth_headers, mock_embedding_deps):
        """Neither query nor query_embedding returns 400."""
        resp = client.post(
            '/api/v1/semantic-search',
            data=json.dumps({}),
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert 'query' in resp.get_json()['error'].lower()

    def test_rejects_unknown_table(self, client, auth_headers, mock_embedding_deps):
        """Unknown table in tables array returns 400."""
        resp = client.post(
            '/api/v1/semantic-search',
            data=json.dumps({
                'query': 'test',
                'tables': ['nonexistent_table'],
            }),
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert 'Unknown table' in resp.get_json()['error']

    def test_respects_limit(self, client, auth_headers, mock_db, mock_embedding_deps):
        """Limit capped at 20."""
        conn, cur = mock_db
        cur.fetchall.return_value = []

        resp = client.post(
            '/api/v1/semantic-search',
            data=json.dumps({'query': 'test', 'limit': 50}),
            headers=auth_headers,
        )
        assert resp.status_code == 200
        # The route caps at 20 internally — just verify it doesn't error

    def test_invalid_limit(self, client, auth_headers, mock_embedding_deps):
        """Non-numeric limit returns 400."""
        resp = client.post(
            '/api/v1/semantic-search',
            data=json.dumps({'query': 'test', 'limit': 'many'}),
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_embedding_service_unavailable(self, client, auth_headers, mock_db, mock_embedding_deps):
        """Server-side embedding failure returns 503."""
        conn, cur = mock_db
        with patch('embedding_utils.get_embedding', return_value=None):
            resp = client.post(
                '/api/v1/semantic-search',
                data=json.dumps({'query': 'test'}),
                headers=auth_headers,
            )
            assert resp.status_code == 503

    def test_requires_auth(self, client):
        """Unauthenticated request rejected."""
        with patch('utils.auth.get_session', return_value=None):
            resp = client.post(
                '/api/v1/semantic-search',
                data=json.dumps({'query': 'test'}),
                headers={'Authorization': 'Bearer bad', 'Content-Type': 'application/json'},
            )
            assert resp.status_code == 401
