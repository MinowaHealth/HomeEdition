"""
Unit tests for auth-related routes in app.py.

Tests login, logout, session, and 2FA endpoints.
"""
import json
import uuid
from unittest.mock import patch, MagicMock

import pytest


class TestLogin:
    def test_api_login_success(self, client):
        fake_user = {
            'id': str(uuid.uuid4()),
            'email': 'user@example.com',
            'display_name': 'Test User',
            'tenant_id': 1,
        }
        with patch('auth.authenticate_user', return_value=fake_user), \
             patch('auth.check_2fa_required', return_value=(False, fake_user['id'], None)), \
             patch('auth.create_session', return_value=str(uuid.uuid4())):
            resp = client.post(
                '/api/v1/login',
                data=json.dumps({'username': 'user@example.com', 'password': 'pass123'}),
                content_type='application/json',
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert 'token' in data

    def test_api_login_bad_credentials(self, client):
        with patch('auth.authenticate_user', return_value=None):
            resp = client.post(
                '/api/v1/login',
                data=json.dumps({'username': 'bad@example.com', 'password': 'wrong'}),
                content_type='application/json',
            )
            assert resp.status_code == 401

    def test_api_login_missing_fields(self, client):
        resp = client.post(
            '/api/v1/login',
            data=json.dumps({}),
            content_type='application/json',
        )
        assert resp.status_code in (400, 401)


class TestSession:
    def test_get_session_authenticated(self, client, auth_headers):
        resp = client.get('/api/v1/session', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('email') == 'test@example.com'

    def test_session_includes_home_timezone(self, client, auth_headers):
        """UserMCP tools/_time.home_tz reads this key — dropping it silently
        UTC-breaks every MCP time tool (found live 2026-07-16)."""
        resp = client.get('/api/v1/session', headers=auth_headers)
        assert resp.get_json().get('home_timezone') == 'America/Los_Angeles'

    def test_get_session_unauthenticated(self, client):
        with patch('utils.auth.get_session', return_value=None):
            resp = client.get(
                '/api/v1/session',
                headers={'Authorization': 'Bearer bad-token'},
            )
            assert resp.status_code == 401


class TestLogout:
    def test_api_logout(self, client, auth_headers):
        with patch('auth.delete_session', return_value=True):
            resp = client.get('/api/v1/logout', headers=auth_headers)
            assert resp.status_code == 200


class Test2FAStatus:
    def test_returns_2fa_status(self, client, auth_headers):
        """Route calls auth.get_user_2fa_status() — mock that, not the DB."""
        with patch('auth.get_user_2fa_status', return_value={'enabled': False}):
            resp = client.get('/api/v1/2fa/status', headers=auth_headers)
            assert resp.status_code == 200
            data = resp.get_json()
            assert 'enabled' in data
