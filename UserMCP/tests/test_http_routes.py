"""
Layer 1: HTTP route tests using Starlette TestClient.

These tests hit the real Starlette `app` in-process (no uvicorn, no Docker).
They cover the routes and auth-rejection shapes that unit tests in
test_mcp_server.py can't reach because everything there mocks UserAppClient.

In particular these tests lock down the JSON-RPC 2.0 error-response shape
returned by the auth gate — the MCP client's Zod validator requires
`error.code` to be a number, so a regression to the old `{"error": "string"}`
shape would surface here instead of in production.
"""
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server import app, JSONRPC_AUTH_ERROR


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def assert_jsonrpc_auth_error(body: dict, expected_fragment: str) -> None:
    """Every auth-rejection body must be a valid JSON-RPC 2.0 error envelope."""
    assert body.get("jsonrpc") == "2.0"
    assert body.get("id") is None
    err = body.get("error")
    assert isinstance(err, dict), f"error must be an object, got {type(err).__name__}"
    assert isinstance(err.get("code"), int), "error.code must be an integer (JSON-RPC 2.0)"
    assert err["code"] == JSONRPC_AUTH_ERROR
    assert isinstance(err.get("message"), str) and err["message"]
    assert expected_fragment in err["message"]


# ============================================================================
# /health
# ============================================================================

def test_health_endpoint_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "usermcp"}


# ============================================================================
# /sse auth rejection
# ============================================================================

def test_sse_without_auth_header_returns_jsonrpc_error(client):
    r = client.get("/sse")
    assert r.status_code == 401
    assert_jsonrpc_auth_error(r.json(), "Authorization")


def test_sse_with_non_bearer_auth_returns_jsonrpc_error(client):
    r = client.get("/sse", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert r.status_code == 401
    assert_jsonrpc_auth_error(r.json(), "Authorization")


def test_sse_with_empty_bearer_returns_jsonrpc_error(client):
    r = client.get("/sse", headers={"Authorization": "Bearer "})
    assert r.status_code == 401
    assert_jsonrpc_auth_error(r.json(), "Empty bearer")


def test_sse_with_whitespace_bearer_returns_jsonrpc_error(client):
    r = client.get("/sse", headers={"Authorization": "Bearer    "})
    assert r.status_code == 401
    assert_jsonrpc_auth_error(r.json(), "Empty bearer")


# ============================================================================
# /messages/ auth rejection
# ============================================================================

def test_messages_without_auth_header_returns_jsonrpc_error(client):
    r = client.post("/messages/", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401
    assert_jsonrpc_auth_error(r.json(), "Authorization")


def test_messages_with_non_bearer_auth_returns_jsonrpc_error(client):
    r = client.post(
        "/messages/",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert r.status_code == 401
    assert_jsonrpc_auth_error(r.json(), "Authorization")


def test_messages_with_empty_bearer_returns_jsonrpc_error(client):
    r = client.post(
        "/messages/",
        headers={"Authorization": "Bearer "},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert r.status_code == 401
    assert_jsonrpc_auth_error(r.json(), "Empty bearer")


# ============================================================================
# JSON-RPC shape contract
# ============================================================================

def test_error_body_has_no_stray_keys(client):
    """Client Zod validators reject extra keys on the error response.
    Envelope must be exactly {jsonrpc, id, error}."""
    r = client.get("/sse")
    body = r.json()
    assert set(body.keys()) == {"jsonrpc", "id", "error"}
    assert set(body["error"].keys()) == {"code", "message"}


def test_error_code_is_int_not_string(client):
    """Regression: previously `error` was a string, so `error.code` was
    undefined (not a number), which tripped the MCP client's Zod validator."""
    r = client.get("/sse")
    body = r.json()
    assert type(body["error"]["code"]) is int
