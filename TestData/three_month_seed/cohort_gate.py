"""Test-cohort gate. Refuses to run unless the DB holds exactly the canonical
single-household cohort (6 Borgia users on tenant_id=1).

This is the seeder's primary safety property: it makes loading against a
non-test database impossible via a deterministic count check, independent of
hostname/IP/DNS/env. Home Edition has no providers and no delegation, so the
gate only counts users.

If any library/sentinel accounts are ever added to the schema, list them in
SYSTEM_USER_IDS so they're excluded from the count and don't trip the gate.
"""
from __future__ import annotations
from typing import Any

EXPECTED_USERS = 6  # The Borgia nuclear household.

# Library/sentinel accounts that legitimately exist alongside the cohort and
# must not count toward the cohort total. The local dev stack's init script
# (scripts/local-init-db.sh) seeds a fixed test@example.com login on every
# fresh install; it is a dev-convenience sentinel, not a cohort member, so it
# is excluded here and does not trip the gate.
SYSTEM_USER_IDS: frozenset[str] = frozenset(
    {"11111111-1111-1111-1111-111111111111"}
)


class CohortGateError(RuntimeError):
    """Raised when DB cohort does not match the canonical 6 household users."""


def check_cohort(conn: Any) -> None:
    """Verify tenant=1 has exactly EXPECTED_USERS users (excluding
    SYSTEM_USER_IDS). Raise CohortGateError on mismatch with an actionable
    message. Never deletes anything.
    """
    sys_ids = sorted(SYSTEM_USER_IDS)
    with conn.cursor() as cur:
        if sys_ids:
            cur.execute(
                "SELECT COUNT(*) FROM users "
                "WHERE tenant_id = 1 AND id <> ALL(%s::uuid[]);",
                (sys_ids,),
            )
        else:
            cur.execute("SELECT COUNT(*) FROM users WHERE tenant_id = 1;")
        user_count = cur.fetchone()[0]

    if user_count < EXPECTED_USERS:
        raise CohortGateError(
            f"Stage 0 has not run: found {user_count} users, expected "
            f"{EXPECTED_USERS}. Run seed_users.py first."
        )
    if user_count > EXPECTED_USERS:
        raise CohortGateError(
            f"DB contains non-cohort rows: found {user_count} users, expected "
            f"exactly {EXPECTED_USERS} (system users excluded). Refusing to "
            f"run. NEVER auto-delete; investigate manually."
        )
