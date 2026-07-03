"""Unit tests for UserApp/webapp/validate_env.py.

Doctrine: [SecurityHardening.md](../../../SecurityHardening.md) Track 6.

The validator is a pure function over a Mapping[str, str]; no Flask app
or DB needed. Each rule gets:
  - one positive case (rule fires when it should)
  - one negative case (rule passes when it should)
"""
from __future__ import annotations

import pytest

from validate_env import assert_env_valid, validate_env


# ---------------------------------------------------------------------------
# F3 — HEALTHKIT_SYNC_TOKEN paired with empty HEALTHKIT_SYNC_USERNAME
# ---------------------------------------------------------------------------

class TestHealthkitSyncPair:
    def test_token_set_username_empty_fails(self):
        errors = validate_env({
            "HEALTHKIT_SYNC_TOKEN": "deadbeef",
            "HEALTHKIT_SYNC_USERNAME": "",
        })
        assert any("F3" in e for e in errors), (
            f"expected an F3 error in {errors!r}"
        )

    def test_token_set_username_unset_fails(self):
        errors = validate_env({"HEALTHKIT_SYNC_TOKEN": "deadbeef"})
        assert any("F3" in e for e in errors)

    def test_token_set_username_whitespace_only_fails(self):
        # A `.env` file with HEALTHKIT_SYNC_USERNAME=` ` (space) is
        # functionally as bad as empty — the runtime fallback would still
        # fire because `auth.get_user_by_username('')` returns None.
        errors = validate_env({
            "HEALTHKIT_SYNC_TOKEN": "deadbeef",
            "HEALTHKIT_SYNC_USERNAME": "   ",
        })
        assert any("F3" in e for e in errors)

    def test_both_set_passes(self):
        errors = validate_env({
            "HEALTHKIT_SYNC_TOKEN": "deadbeef",
            "HEALTHKIT_SYNC_USERNAME": "alice@example.com",
        })
        assert errors == []

    def test_neither_set_passes(self):
        # The integration is just disabled — no F3 surface to attack.
        errors = validate_env({})
        assert errors == []

    def test_username_set_token_unset_passes(self):
        # Unusual but harmless — username with no token doesn't activate
        # the bearer auth branch at all.
        errors = validate_env({
            "HEALTHKIT_SYNC_USERNAME": "alice@example.com",
        })
        assert errors == []


# ---------------------------------------------------------------------------
# assert_env_valid — the entrypoint helper
# ---------------------------------------------------------------------------

class TestAssertEnvValid:
    def test_passes_silently_when_clean(self):
        # No exception raised.
        assert_env_valid({"HEALTHKIT_SYNC_TOKEN": "x", "HEALTHKIT_SYNC_USERNAME": "y"})

    def test_raises_with_all_errors(self):
        with pytest.raises(RuntimeError) as exc_info:
            assert_env_valid({"HEALTHKIT_SYNC_TOKEN": "x"})
        msg = str(exc_info.value)
        assert "Refusing to start" in msg
        assert "F3" in msg
        assert "HEALTHKIT_SYNC_USERNAME" in msg

    def test_message_references_doctrine(self):
        with pytest.raises(RuntimeError) as exc_info:
            assert_env_valid({"HEALTHKIT_SYNC_TOKEN": "x"})
        # The reader of the error needs a pointer back to the plan that
        # explains the rule — without that the operator just sees a 5xx
        # at boot and has no way to find the why.
        assert "SecurityHardening.md" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Reads from os.environ when env=None
# ---------------------------------------------------------------------------

def test_validate_env_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("HEALTHKIT_SYNC_TOKEN", "live-token")
    monkeypatch.delenv("HEALTHKIT_SYNC_USERNAME", raising=False)
    errors = validate_env()
    assert any("F3" in e for e in errors)
