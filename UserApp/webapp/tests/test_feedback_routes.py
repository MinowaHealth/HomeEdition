"""
Unit tests for admin/feedback blueprint routes.

Tests feedback CRUD. These use admin DB connections (the admin role, not the app role).
"""
import json
import uuid
from datetime import datetime

import pytest


class TestGetFeedback:
    def test_returns_feedback(self, client, mock_admin_db, auth_headers):
        conn, cur = mock_admin_db
        # _total simulates count(*) OVER() — every row carries the same total
        cur.fetchall.return_value = [
            {
                '_total': 1,
                'id': uuid.uuid4(),
                'feedback_type': 'general',
                'content': 'Great app!',
                'page_context': '/dashboard',
                'app_version': '1.0.0',
                'status': 'open',
                'created_at': datetime(2026, 2, 24),
            }
        ]

        resp = client.get('/api/v1/feedback', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'entries' in data
        assert 'pagination' in data
        assert len(data['entries']) == 1
        assert '_total' not in data['entries'][0]
        assert data['pagination']['total'] == 1
        assert data['pagination']['has_more'] is False

    def test_empty_feedback(self, client, mock_admin_db, auth_headers):
        conn, cur = mock_admin_db
        cur.fetchall.return_value = []

        resp = client.get('/api/v1/feedback', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['entries'] == []
        assert data['pagination']['total'] == 0
        assert data['pagination']['has_more'] is False


class TestCreateFeedback:
    def test_creates_feedback(self, client, mock_admin_db, auth_headers):
        """POST /feedback uses get_direct_admin_connection, not user connection."""
        conn, cur = mock_admin_db
        cur.fetchone.return_value = {'id': str(uuid.uuid4())}
        payload = {
            'feedback': 'The blood pressure chart is really helpful',
            'feedback_type': 'feature_request',
            'page_context': '/vitals',
        }

        resp = client.post(
            '/api/v1/feedback',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 201
        conn.commit.assert_called()

    def test_requires_feedback_content(self, client, mock_admin_db, auth_headers):
        conn, cur = mock_admin_db
        payload = {'feedback_type': 'bug'}

        resp = client.post(
            '/api/v1/feedback',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 400

    def test_accepts_screen_as_page_fallback(self, client, mock_admin_db, auth_headers):
        """Mobile clients send the source page under `screen`; backend should
        treat it as equivalent to `page_context`/`page` so the value reaches
        both the DB row and the Slack payload."""
        conn, cur = mock_admin_db
        cur.fetchone.return_value = {'id': str(uuid.uuid4())}
        payload = {
            'feedback': 'Feedback from the mobile client',
            'feedback_type': 'general',
            'screen': 'SettingsAccount',
        }

        resp = client.post(
            '/api/v1/feedback',
            headers=auth_headers,
            data=json.dumps(payload),
        )
        assert resp.status_code == 201
        # The INSERT carries page_context as the 6th positional parameter
        # (tenant_id, id, user_id, feedback_type, content, page_context, app_version).
        insert_call = next(
            call for call in cur.execute.call_args_list
            if call.args and 'INSERT INTO feedback' in call.args[0]
        )
        params = insert_call.args[1]
        assert params[5] == 'SettingsAccount'


class TestDeleteFeedback:
    def test_deletes_feedback(self, client, mock_admin_db, auth_headers):
        conn, cur = mock_admin_db
        cur.rowcount = 1
        feedback_id = str(uuid.uuid4())

        resp = client.delete(
            f'/api/v1/feedback/{feedback_id}',
            headers=auth_headers,
        )
        assert resp.status_code == 200
        conn.commit.assert_called()
