"""
Shared fixtures for webapp unit tests.

Provides a Flask test client with mocked auth and DB connections,
so route blueprints can be tested without a real database.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import uuid

import pytest

# Ensure webapp root is importable
WEBAPP_ROOT = Path(__file__).resolve().parents[1]
if str(WEBAPP_ROOT) not in sys.path:
    sys.path.insert(0, str(WEBAPP_ROOT))

# Set required env vars before any app imports
os.environ.setdefault('APP_DB_USER', 'healthv10_app')
os.environ.setdefault('APP_DB_PASSWORD', 'password')
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('TIMEZONE', 'America/Los_Angeles')

# SecurityHardening.md Track 6 — UserApp's startup validator (validate_env.py)
# refuses to boot when HEALTHKIT_SYNC_TOKEN is set without
# HEALTHKIT_SYNC_USERNAME. A developer's .env may load via load_dotenv()
# at app import and accidentally trip the validator under pytest. Clear
# the dangerous combo here so test runs aren't beholden to whatever's
# in the local .env. Tests that exercise the F3 fallback (Track 4a F3
# unit test) set the values explicitly via monkeypatch.setenv.
os.environ.pop('HEALTHKIT_SYNC_TOKEN', None)
os.environ.pop('HEALTHKIT_SYNC_USERNAME', None)

from app import app as flask_app


TEST_USER_ID = str(uuid.uuid4())
TEST_TENANT_ID = 1
TEST_SESSION_ID = str(uuid.uuid4())


@pytest.fixture()
def app():
    """Configure Flask app for testing."""
    flask_app.config['TESTING'] = True
    flask_app.config['SECRET_KEY'] = 'test-secret-key'
    return flask_app


@pytest.fixture()
def client(app):
    """Flask test client with mocked auth (all requests authenticated)."""
    with app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def mock_auth():
    """Auto-mock auth so all @require_auth routes pass."""
    fake_session = {
        'tenant_id': TEST_TENANT_ID,
        'session_id': TEST_SESSION_ID,
        'user_id': TEST_USER_ID,
        'username': 'test@example.com',
        'email': 'test@example.com',
        'display_name': 'Test User',
        'home_timezone': 'America/Los_Angeles',
        'database_name': 'healthv10',
    }
    with patch('utils.auth.get_session', return_value=fake_session):
        yield fake_session


@pytest.fixture()
def mock_db():
    """
    Mock database connection returned by get_db_connection().

    Returns (mock_conn, mock_cursor) so tests can configure query results:

        def test_something(client, mock_db):
            conn, cur = mock_db
            cur.fetchall.return_value = [{'id': '1', 'name': 'Aspirin'}]
            resp = client.get('/api/v1/health-inputs', ...)
    """
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch('utils.db_manager.get_direct_connection_for_user', return_value=mock_conn):
        yield mock_conn, mock_cursor


@pytest.fixture()
def mock_admin_db():
    """Mock admin DB connection (used by feedback/admin routes)."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch('db_manager.get_direct_admin_connection', return_value=mock_conn):
        yield mock_conn, mock_cursor


@pytest.fixture()
def auth_headers():
    """Bearer auth headers for API requests."""
    return {
        'Authorization': f'Bearer {TEST_SESSION_ID}',
        'Content-Type': 'application/json',
    }
