"""Unit tests for security defenses — Track 4a (Phase 4a, inside-test).

Doctrine: [SecurityHardening.md](../../../SecurityHardening.md) Track 4a.
Companion: [livetest/flows/security_*.py](../../livetest/flows/) — these
unit tests cover the cases that *can't* be exercised over HTTP from a
running server, primarily because they require booting the app with
specific environment variables (the F3 case).

Tests for already-defended cases (e.g., bearer token validation) run
green. Tests for known-but-unfixed defects (F3 today) are marked
``@pytest.mark.xfail(strict=True)``: they pass under the broken behavior
and *fail loudly* (``XPASS``) when the fix lands, forcing the implementer
to remove the marker in the same PR.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# F3 — get_first_user_record auth fallback
# ---------------------------------------------------------------------------
# Current state (utils.py:107-129): if Authorization bearer matches
# HEALTHKIT_SYNC_TOKEN and HEALTHKIT_SYNC_USERNAME is empty/unset, the
# fallback at line 115 fires `get_first_user_record()` and grants the
# token holder access to the lowest-ID active user. .env.example ships
# HEALTHKIT_SYNC_USERNAME empty, so production defaults aim straight at
# this branch.
#
# Expected behavior post-fix: when token is set but username is unset,
# the route returns 401 (the env config is broken — refuse rather than
# silently selecting an arbitrary user).
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason=(
        "F3 — get_first_user_record fallback at utils.py:115 still fires when "
        "HEALTHKIT_SYNC_USERNAME is unset. When the fix lands and this test "
        "starts passing (XPASS), remove this marker in the same PR."
    ),
)
def test_f3_healthkit_token_with_empty_username_must_reject(
    client, monkeypatch
):
    """Bearer matches HEALTHKIT_SYNC_TOKEN, USERNAME unset → expect 401.

    Today's behavior: the request returns 200 because the fallback grants
    the token holder access to the first active user. The test asserts
    the post-fix expectation; xfail catches the assertion failure today
    and will flip to xpass when the fix lands.
    """
    monkeypatch.setenv("HEALTHKIT_SYNC_TOKEN", "test-token-deadbeef")
    monkeypatch.setenv("HEALTHKIT_SYNC_USERNAME", "")

    fake_first_user = {
        "id": "00000000-0000-0000-0000-000000000001",
        "username": "first@example.com",
        "email": "first@example.com",
        "database_name": "healthv10",
    }
    # The autouse `mock_auth` fixture patches utils.auth.get_session —
    # that's irrelevant here because the bearer path is exercised, not
    # the session path. We do need to mock get_first_user_record() so
    # the test doesn't try to open a real admin DB connection.
    with patch("utils.get_first_user_record", return_value=fake_first_user):
        resp = client.get(
            "/api/v1/session",
            headers={"Authorization": "Bearer test-token-deadbeef"},
        )

    assert resp.status_code == 401, (
        f"F3: token-with-empty-username should return 401 (broken env config); "
        f"got {resp.status_code}. Today the fallback at utils.py:115 silently "
        f"grants access to the lowest-ID user."
    )


# ---------------------------------------------------------------------------
# F3 negative control — same shape, but USERNAME is set and resolves.
# ---------------------------------------------------------------------------

def test_healthkit_token_with_resolvable_username_succeeds(
    client, monkeypatch
):
    """Bearer matches HEALTHKIT_SYNC_TOKEN, USERNAME set → expect 200.

    This is the *correct* shape of the integration: the env declares
    which user the token acts on behalf of. No fallback, no F3.
    """
    monkeypatch.setenv("HEALTHKIT_SYNC_TOKEN", "test-token-deadbeef")
    monkeypatch.setenv("HEALTHKIT_SYNC_USERNAME", "alice@example.com")

    alice = {
        "id": "11111111-1111-1111-1111-111111111111",
        "username": "alice@example.com",
        "email": "alice@example.com",
        "database_name": "healthv10",
    }
    with patch("auth.get_user_by_username", return_value=alice):
        resp = client.get(
            "/api/v1/session",
            headers={"Authorization": "Bearer test-token-deadbeef"},
        )

    assert resp.status_code == 200, (
        f"sync-token + resolvable USERNAME should succeed; got {resp.status_code}: "
        f"{resp.get_data(as_text=True)[:200]}"
    )


# ---------------------------------------------------------------------------
# F3 negative control — bearer that *doesn't* match HEALTHKIT_SYNC_TOKEN
# falls through normal session handling and returns 401.
# ---------------------------------------------------------------------------

def test_random_bearer_without_session_returns_401(client, monkeypatch):
    """A bearer that doesn't match the sync token and isn't a session UUID
    must produce 401, not the F3 fallback. Confirms the F3 branch is
    keyed strictly on the token string.
    """
    monkeypatch.setenv("HEALTHKIT_SYNC_TOKEN", "the-real-sync-token")
    monkeypatch.setenv("HEALTHKIT_SYNC_USERNAME", "")

    # Defeat the autouse mock_auth fixture for this test — we want
    # get_session to genuinely return None (no session), so the bearer
    # falls through every auth branch.
    with patch("utils.auth.get_session", return_value=None):
        resp = client.get(
            "/api/v1/session",
            headers={"Authorization": "Bearer not-the-sync-token"},
        )

    assert resp.status_code == 401, (
        f"non-matching bearer should return 401; got {resp.status_code}"
    )
