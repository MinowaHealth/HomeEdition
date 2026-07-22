"""Tests for POST /api/v1/documents/chat-summaries and the text/plain
upload embedding path (2026-07-15 documents feature).

Chat summaries are ordinary documents rows (source='chat_summary',
'AI Sessions' system folder) — patient-authored PHI, so every create must
write an audit_log row (HIPAA §164.312(b)).
"""
from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from conftest import TEST_USER_ID

FOLDER_ID = uuid.UUID('11111111-2222-3333-4444-555555555555')
NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _doc_row(**overrides):
    row = {
        'id': uuid.uuid4(),
        'folder_id': FOLDER_ID,
        'filename': 'Lab-review.md',
        'mime_type': 'text/markdown',
        'file_size_bytes': 7,
        'sha256': 'ab' * 32,
        'source': 'chat_summary',
        'ocr_status': 'not_needed',
        'title': 'Lab review',
        'category': 'ai_session',
        'created_at': NOW,
    }
    row.update(overrides)
    return row


@pytest.fixture
def tmp_storage(tmp_path):
    """Point document storage at a per-test directory."""
    from routes import documents as documents_routes
    with patch.object(documents_routes, 'STORAGE_ROOT', tmp_path):
        yield tmp_path


class TestChatSummaryValidation:
    def _post(self, client, auth_headers, body):
        return client.post('/api/v1/documents/chat-summaries',
                           headers=auth_headers, json=body)

    def test_missing_title_400(self, client, mock_db, auth_headers):
        resp = self._post(client, auth_headers, {'summary_markdown': 'x'})
        assert resp.status_code == 400
        assert 'title is required' in resp.get_json()['error']

    def test_overlong_title_400(self, client, mock_db, auth_headers):
        resp = self._post(client, auth_headers,
                          {'title': 'T' * 201, 'summary_markdown': 'x'})
        assert resp.status_code == 400

    def test_missing_summary_400(self, client, mock_db, auth_headers):
        resp = self._post(client, auth_headers, {'title': 'T'})
        assert resp.status_code == 400
        assert 'summary_markdown is required' in resp.get_json()['error']

    def test_oversized_summary_400(self, client, mock_db, auth_headers):
        resp = self._post(client, auth_headers,
                          {'title': 'T', 'summary_markdown': 'x' * (256 * 1024 + 1)})
        assert resp.status_code == 400

    def test_source_tools_must_be_string_list_400(self, client, mock_db, auth_headers):
        resp = self._post(client, auth_headers,
                          {'title': 'T', 'summary_markdown': 'x',
                           'source_tools': 'search_my_data'})
        assert resp.status_code == 400
        assert 'source_tools' in resp.get_json()['error']


class TestChatSummaryCreate:
    BODY = {
        'title': 'Lab review',
        'summary_markdown': '# Notes\n\nAll good.',
        'model_id': 'claude-x',
        'source_tools': ['search_my_data'],
        'created_via': 'usermcp',
    }

    def _post(self, client, auth_headers, body=None):
        return client.post('/api/v1/documents/chat-summaries',
                           headers=auth_headers, json=body or self.BODY)

    def test_create_returns_201_with_links(self, client, mock_db, auth_headers, tmp_storage):
        conn, cur = mock_db
        cur.fetchone.side_effect = [{'id': FOLDER_ID}, _doc_row()]

        with patch('routes.documents.embed_field') as embed:
            resp = self._post(client, auth_headers)

        assert resp.status_code == 201, resp.get_json()
        doc = resp.get_json()
        assert doc['source'] == 'chat_summary'
        assert doc['category'] == 'ai_session'
        assert doc['links'] == {
            'web': f"/?activity=documents&doc={doc['id']}",
            'view': f"/api/v1/documents/{doc['id']}/view",
            'download': f"/api/v1/documents/{doc['id']}/download",
        }
        # Inline embedding over title + body (silent-fail contract).
        embed.assert_called_once()
        assert embed.call_args.args[5] == 'Lab review\n\n# Notes\n\nAll good.'

    def test_summary_file_written_to_storage(self, client, mock_db, auth_headers, tmp_storage):
        conn, cur = mock_db
        cur.fetchone.side_effect = [{'id': FOLDER_ID}, _doc_row()]

        with patch('routes.documents.embed_field'):
            resp = self._post(client, auth_headers)

        assert resp.status_code == 201
        files = list(tmp_storage.glob(f'1/{TEST_USER_ID}/*/original.md'))
        assert len(files) == 1
        assert files[0].read_text(encoding='utf-8') == '# Notes\n\nAll good.'

    def test_audit_row_written(self, client, mock_db, auth_headers, tmp_storage):
        """HIPAA §164.312(b): the PHI write must land an audit_log row with
        action document.chat_summary_created and created_via provenance."""
        conn, cur = mock_db
        cur.fetchone.side_effect = [{'id': FOLDER_ID}, _doc_row()]

        with patch('routes.documents.embed_field'):
            resp = self._post(client, auth_headers)

        assert resp.status_code == 201
        audit_calls = [c for c in cur.execute.call_args_list
                       if 'INSERT INTO audit_log' in c.args[0]]
        assert len(audit_calls) == 1
        sql_text, params = audit_calls[0].args
        assert 'document.chat_summary_created' in sql_text
        details = json.loads([p for p in params if isinstance(p, str)
                              and p.startswith('{')][0])
        assert details['created_via'] == 'usermcp'
        assert details['model_id'] == 'claude-x'

    def test_provenance_recorded_on_document(self, client, mock_db, auth_headers, tmp_storage):
        conn, cur = mock_db
        cur.fetchone.side_effect = [{'id': FOLDER_ID}, _doc_row()]

        with patch('routes.documents.embed_field'):
            self._post(client, auth_headers)

        insert_calls = [c for c in cur.execute.call_args_list
                        if 'INSERT INTO documents' in c.args[0]]
        assert len(insert_calls) == 1
        params = insert_calls[0].args[1]
        provenance = json.loads([p for p in params if isinstance(p, str)
                                 and p.startswith('{')][0])
        assert provenance['model_id'] == 'claude-x'
        assert provenance['source_tools'] == ['search_my_data']
        assert provenance['created_via'] == 'usermcp'

    def test_folder_self_heal_for_pre_delta_users(self, client, mock_db, auth_headers, tmp_storage):
        """Accounts created before the 2026-07-15 delta have no 'AI Sessions'
        folder — the route creates it under the user's own RLS context."""
        conn, cur = mock_db
        cur.fetchone.side_effect = [None, {'id': FOLDER_ID}, _doc_row()]

        with patch('routes.documents.embed_field'):
            resp = self._post(client, auth_headers)

        assert resp.status_code == 201
        folder_inserts = [c for c in cur.execute.call_args_list
                          if 'INSERT INTO document_folders' in c.args[0]]
        assert len(folder_inserts) == 1
        assert 'AI Sessions' in folder_inserts[0].args[1]

    def test_embed_failure_is_non_fatal(self, client, mock_db, auth_headers, tmp_storage):
        """embed_field returning None (Ollama down) never blocks the save."""
        conn, cur = mock_db
        cur.fetchone.side_effect = [{'id': FOLDER_ID}, _doc_row()]

        with patch('routes.documents.embed_field', return_value=None):
            resp = self._post(client, auth_headers)

        assert resp.status_code == 201

    def test_schema_not_ready_503(self, client, mock_db, auth_headers, tmp_storage):
        """Pre-delta database (source CHECK missing 'chat_summary') must
        surface a clear 503, not a raw 500 — prod applies delta before code."""
        conn, cur = mock_db
        cur.fetchone.side_effect = [{'id': FOLDER_ID}]

        def explode(sql_text, *args):
            if 'INSERT INTO documents' in sql_text:
                raise Exception(
                    'new row violates check constraint "documents_source_check"')

        cur.execute.side_effect = explode

        resp = self._post(client, auth_headers)

        assert resp.status_code == 503
        assert resp.get_json()['code'] == 'SCHEMA_NOT_READY'


class TestTextUploadEmbedding:
    """text/plain uploads skip the OCR pipeline, so /documents/upload must
    populate ocr_text_full (FTS) and embed inline — otherwise text uploads
    are invisible to search."""

    def test_text_upload_populates_fts_and_embeds(self, client, mock_db, auth_headers, tmp_storage):
        conn, cur = mock_db
        cur.fetchone.side_effect = [
            {'id': FOLDER_ID},  # system Documents folder
            _doc_row(filename='note.txt', mime_type='text/plain',
                     source='upload', title='note.txt', category=None),
        ]

        with patch('routes.documents.embed_field') as embed:
            resp = client.post(
                '/api/v1/documents/upload',
                headers=auth_headers,
                data={'file': (io.BytesIO(b'magnesium helps my sleep'), 'note.txt', 'text/plain')},
                content_type='multipart/form-data',
            )

        assert resp.status_code == 201, resp.get_json()
        insert_calls = [c for c in cur.execute.call_args_list
                        if 'INSERT INTO documents' in c.args[0]]
        assert 'magnesium helps my sleep' in insert_calls[0].args[1], (
            'ocr_text_full not populated — text upload invisible to FTS')
        embed.assert_called_once()
        assert 'magnesium helps my sleep' in embed.call_args.args[5]
