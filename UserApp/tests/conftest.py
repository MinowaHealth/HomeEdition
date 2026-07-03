"""
Pytest fixtures for Minowa API tests.

Reads configuration from tests/config.toml and provides authenticated API clients.
Integration tests are gated by RUN_INTEGRATION_TESTS=1.
"""
import os
from pathlib import Path

import httpx
import pytest
import tomllib


def _load_config():
    """Load test configuration from config.toml."""
    config_path = Path(__file__).parent / "config.toml"
    if not config_path.exists():
        pytest.skip("tests/config.toml not found (copy config.toml.example to config.toml)")
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def _integration_enabled():
    return os.getenv("RUN_INTEGRATION_TESTS", "0").strip().lower() in ("1", "true", "yes")


def pytest_collection_modifyitems(config, items):
    """Skip integration suites unless explicitly enabled."""
    if _integration_enabled():
        return
    skip_integration = pytest.mark.skip(
        reason="Integration tests disabled. Set RUN_INTEGRATION_TESTS=1 to enable."
    )
    for item in items:
        path = str(item.fspath).replace("\\", "/").lower()
        if "/userapp/tests/" in path and "/userapp/webapp/tests/" not in path:
            item.add_marker(skip_integration)


def _authenticated_session(base_url, username, password):
    """Create authenticated httpx.Client for a user."""
    if not _integration_enabled():
        pytest.skip("Integration tests disabled. Set RUN_INTEGRATION_TESTS=1 to enable.")

    session = httpx.Client(follow_redirects=True, timeout=30)

    resp = session.post(
        f"{base_url}/api/v1/login",
        json={"username": username, "password": password},
        follow_redirects=False,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Login failed for {username}: {resp.status_code} - {resp.text[:200]}")

    payload = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    token = payload.get("token")
    if token:
        session.headers.update({"Authorization": f"Bearer {token}"})

    check = session.get(f"{base_url}/api/v1/session")
    if check.status_code == 401:
        raise RuntimeError(f"Session not authenticated for {username}")

    return session


class ApiClient:
    """Simple HTTP client for API calls."""

    def __init__(self, session, base_url):
        self.session = session
        self.base_url = base_url

    def get(self, path, **kwargs):
        return self.session.get(f"{self.base_url}/api/v1{path}", **kwargs)

    def post(self, path, **kwargs):
        return self.session.post(f"{self.base_url}/api/v1{path}", **kwargs)

    def put(self, path, **kwargs):
        return self.session.put(f"{self.base_url}/api/v1{path}", **kwargs)

    def delete(self, path, **kwargs):
        return self.session.delete(f"{self.base_url}/api/v1{path}", **kwargs)


@pytest.fixture(scope="session")
def config():
    """Test configuration (loaded once per session)."""
    return _load_config()


@pytest.fixture(scope="session")
def base_url(config):
    """Base URL for API server."""
    return config["server"]["base_url"]


@pytest.fixture(scope="session")
def auth_session(base_url, config):
    """Authenticated httpx.Client for default test user."""
    user_config = config["users"]["default"]
    return _authenticated_session(base_url, user_config["email"], user_config["password"])


@pytest.fixture(scope="session")
def api(base_url, config):
    """Authenticated API client for default test user."""
    user_config = config["users"]["default"]
    session = _authenticated_session(base_url, user_config["email"], user_config["password"])
    return ApiClient(session, base_url)


@pytest.fixture(scope="session")
def api_owner1(base_url, config):
    """Authenticated API client for owner1."""
    user_config = config["users"]["owner1"]
    session = _authenticated_session(base_url, user_config["email"], user_config["password"])
    return ApiClient(session, base_url)


@pytest.fixture(scope="session")
def api_owner2(base_url, config):
    """Authenticated API client for owner2."""
    user_config = config["users"]["owner2"]
    session = _authenticated_session(base_url, user_config["email"], user_config["password"])
    return ApiClient(session, base_url)


@pytest.fixture(scope="session")
def api_owner3(base_url, config):
    """Authenticated API client for owner3."""
    user_config = config["users"]["owner3"]
    session = _authenticated_session(base_url, user_config["email"], user_config["password"])
    return ApiClient(session, base_url)


@pytest.fixture(scope="session")
def api_provider1(base_url, config):
    """Authenticated API client for provider1."""
    user_config = config["users"]["provider1"]
    session = _authenticated_session(base_url, user_config["email"], user_config["password"])
    return ApiClient(session, base_url)


@pytest.fixture(scope="session")
def api_provider2(base_url, config):
    """Authenticated API client for provider2."""
    user_config = config["users"]["provider2"]
    session = _authenticated_session(base_url, user_config["email"], user_config["password"])
    return ApiClient(session, base_url)


@pytest.fixture(scope="session")
def db_connection_params():
    """Database connection parameters from environment."""
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 5432)),
        "database": os.getenv("DB_NAME", "healthv10"),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", "postgres"),
    }


@pytest.fixture(scope="session")
def db_conn(db_connection_params):
    """Direct PostgreSQL connection for integration tests."""
    if not _integration_enabled():
        pytest.skip("Integration tests disabled. Set RUN_INTEGRATION_TESTS=1 to enable.")

    import psycopg
    params = {**db_connection_params}
    params["dbname"] = params.pop("database")
    try:
        conn = psycopg.connect(**params)
        yield conn
        conn.close()
    except psycopg.OperationalError as e:
        pytest.fail(
            f"Failed to connect to PostgreSQL at {db_connection_params['host']}:"
            f"{db_connection_params['port']}/{db_connection_params['database']}: {e}"
        )
