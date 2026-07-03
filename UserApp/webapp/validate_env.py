"""Startup config validator for UserApp.

Refuses to start when env-var combinations are dangerous in ways the
Flask app's runtime code can't tell from a healthy config — silent
fallbacks, permissive defaults. Promotes those defects from
runtime-silent to deploy-time-loud.

Doctrine: [SecurityHardening.md](../../SecurityHardening.md) Track 6. Companion to:
  * Track 4a's F3 unit test in webapp/tests/test_security_defense.py —
    asserts the runtime fallback shape.
  * Track 4a's livetest flows in livetest/flows/security_*.py — assert
    the runtime defenses repel forged requests.

The validator is a pure function: tests call it directly with a synthetic
env dict; the entrypoint calls it once at app import. Adding a new rule
is one line in ``_RULES``.
"""
from __future__ import annotations

import os
from typing import Callable, Mapping


# A rule is a callable: env → fatal-error message (str) or None (pass).
Rule = Callable[[Mapping[str, str]], "str | None"]


def _check_healthkit_sync_pair(env: Mapping[str, str]) -> "str | None":
    """F3 — HEALTHKIT_SYNC_TOKEN set requires HEALTHKIT_SYNC_USERNAME set.

    The token-auth branch in webapp/utils.py grants the token holder
    access to the lowest-ID active user when ``HEALTHKIT_SYNC_USERNAME``
    is empty (via ``get_first_user_record()``). That fallback is
    intra-tenant data leakage in disguise: an attacker holding the
    token doesn't need credentials, just a valid token, and they get a
    real user account to act as. ``.env.example`` ships
    ``HEALTHKIT_SYNC_USERNAME`` empty, so a copy-paste deploy aims
    straight at it.
    """
    token = env.get("HEALTHKIT_SYNC_TOKEN", "").strip()
    username = env.get("HEALTHKIT_SYNC_USERNAME", "").strip()
    if token and not username:
        return (
            "F3: HEALTHKIT_SYNC_TOKEN is set but HEALTHKIT_SYNC_USERNAME is "
            "empty. The token-auth fallback at webapp/utils.py:115 would "
            "grant the token holder access to the lowest-ID active user. "
            "Either unset HEALTHKIT_SYNC_TOKEN or set "
            "HEALTHKIT_SYNC_USERNAME to the email of the user the token "
            "should act for."
        )
    return None


_RULES: list[Rule] = [
    _check_healthkit_sync_pair,
]


def validate_env(env: Mapping[str, str] | None = None) -> list[str]:
    """Return a list of fatal config errors. Empty list = OK to boot.

    Pure function — does not raise, does not log, does not consult
    anything besides the supplied env. ``env=None`` reads from
    ``os.environ`` so the entrypoint can call ``validate_env()`` with
    no arguments.
    """
    if env is None:
        env = os.environ
    errors: list[str] = []
    for rule in _RULES:
        result = rule(env)
        if result is not None:
            errors.append(result)
    return errors


def assert_env_valid(env: Mapping[str, str] | None = None) -> None:
    """Raise RuntimeError if any rule fails. Intended for the entrypoint.

    The error message bundles every failing rule (not just the first),
    so a deploy with multiple misconfigurations gets one informative
    failure instead of a "fix one, find another" loop.
    """
    errors = validate_env(env)
    if errors:
        bullet = "\n  - ".join(errors)
        raise RuntimeError(
            "Refusing to start due to insecure env configuration:\n  - "
            + bullet
            + "\n\nDoctrine: SecurityHardening.md Track 6. Fix the env file or "
            "unset the dangerous variables and retry."
        )
