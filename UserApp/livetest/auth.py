"""Live test authentication: login, return primed httpx.Client."""
from __future__ import annotations

import httpx

from livetest.config import LiveTestConfig


def login(cfg: LiveTestConfig) -> httpx.Client:
    """POST /api/v1/login with cfg.test_email/cfg.test_password, capture
    the session cookie, return a primed httpx.Client.

    Uses /api/v1/login (not the browser /login) because api.* hosts
    reject non-/api paths at the hostname gate (app.py:203-206).

    Raises RuntimeError if login fails — callers cannot recover.
    """
    session = httpx.Client(follow_redirects=True, timeout=cfg.timeout)
    url = cfg.base_url.rstrip("/") + "/api/v1/login"
    resp = session.post(
        url,
        json={"email": cfg.test_email, "password": cfg.test_password},
        headers={"Content-Type": "application/json"},
        timeout=cfg.timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"login failed: POST {url} returned {resp.status_code}: {resp.text}"
        )
    body = resp.json()
    token = body.get("token")
    if not token:
        raise RuntimeError(
            f"login returned 200 but response body has no 'token' field: {body}"
        )
    session.headers["Authorization"] = f"Bearer {token}"
    return session
