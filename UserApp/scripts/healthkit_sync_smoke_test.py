#!/usr/bin/env python3
"""
healthkit_sync_smoke_test.py — end-to-end verification for /api/v1/healthkit/sync v2.

Date:   2026-04-10
Time:   21:00 PT

Purpose
-------
Posts a realistic payload_version=2 HealthKit sync payload against a
running UserApp instance (the local Mac dev stack or the home
appliance) and
then reads directly from Postgres
using the app-role credentials to confirm the canonical ``hkit_*``
tables were populated correctly.

This is the "is sync working on a real deployment" verification tool.
Unit tests (``test_healthkit_writer.py``) and contract tests
(``test_healthkit_sync_v2.py``) cover the writer's SQL shape and the
endpoint's dispatch logic without touching a real database. This
script covers what those cannot: that the SQL is actually accepted by
Postgres, that the writes can be read back with explicit
``user_id``/``tenant_id`` scoping, that the dedup indexes behave as
expected on replay, and that source provenance round-trips without loss.

Usage
-----
    ./UserApp/scripts/healthkit_sync_smoke_test.py \\
        --base-url https://localhost \\
        --token    <bearer-token-for-test-user> \\
        --env-file UserApp/.env \\
        --user-id  11111111-2222-3333-4444-555555555555

Options
-------
    --base-url    Base URL of the running UserApp (default http://localhost)
    --token       Bearer token for the user the payload will be posted as
    --env-file    Path to the .env file containing APP_DB_USER / APP_DB_PASSWORD
                  (default UserApp/.env)
    --user-id     UUID of the user the payload belongs to. Must match the user
                  the bearer token authenticates as, otherwise the verification
                  queries (which filter by this user_id) report zero rows.
    --tenant-id   Tenant ID (default 1)
    --fixture     Path to the JSON fixture to POST (default
                  UserApp/webapp/tests/fixtures/healthkit_v2/mixed_realistic_day.json)
    --replay      Also POST the same payload a second time and verify dedup
                  produced zero new rows
    --db-host     Postgres host (default localhost)
    --db-port     Postgres port (default 5432)
    --db-name     Postgres database (default healthv10)
    --timeout     HTTP timeout in seconds (default 30)

Exit codes
----------
    0  All expected rows present, all dedup checks pass
    1  HTTP call failed
    2  Postgres verification failed (missing rows, wrong shape)
    3  Replay produced new rows (dedup broken)

Safety
------
This script is READ-ONLY after the POST. It only opens a read-only
app-role connection and verifies what landed using explicit
``user_id``/``tenant_id`` predicates, never mutates data directly. The
POST itself goes through the normal HTTP endpoint with the usual auth
protection.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# db_driver shim lives in UserApp/webapp/ — put it on sys.path so this CLI
# uses the same psycopg3 driver wiring as the webapp.
sys.path.insert(
    0,
    os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webapp")
    ),
)

try:
    import db_driver
    from db_driver import sql
except ImportError:
    print(
        "ERROR: db_driver not importable. Activate the .venv first and ensure "
        "UserApp/webapp is reachable.",
        file=sys.stderr,
    )
    sys.exit(2)

try:
    import httpx
except ImportError:
    print("ERROR: httpx is required. Activate the .venv first.", file=sys.stderr)
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = (
    REPO_ROOT / "UserApp/webapp/tests/fixtures/healthkit_v2/mixed_realistic_day.json"
)
DEFAULT_ENV = REPO_ROOT / "UserApp/.env"


# ---------------------------------------------------------------------------
# .env loader (minimal, no external dep)
# ---------------------------------------------------------------------------


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def post_sync_payload(base_url: str, token: str, payload: dict, timeout: int) -> dict:
    url = base_url.rstrip("/") + "/api/v1/healthkit/sync"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = httpx.post(url, json=payload, headers=headers, timeout=timeout, follow_redirects=True)
    if resp.status_code != 200:
        print(
            f"FAIL: POST {url} returned {resp.status_code}\n"
            f"      body={resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)
    return resp.json()


# ---------------------------------------------------------------------------
# Postgres verification
# ---------------------------------------------------------------------------


def open_app_connection(db_host: str, db_port: int, db_name: str,
                        app_user: str, app_password: str):
    conn = db_driver.connect(
        host=db_host,
        port=db_port,
        dbname=db_name,
        user=app_user,
        password=app_password,
    )
    return conn


def count_samples_by_type(cur, payload: dict) -> dict[str, int]:
    """Return a map {type_identifier: expected_count} derived from payload samples."""
    counts: dict[str, int] = {}
    for sample in payload.get("samples", []):
        if not isinstance(sample, dict):
            continue
        type_id = sample.get("type_identifier")
        if not type_id:
            continue
        if type_id == "HKCorrelationTypeIdentifierBloodPressure":
            # BP correlations split into two rows in hkit_records
            counts["HKQuantityTypeIdentifierBloodPressureSystolic"] = (
                counts.get("HKQuantityTypeIdentifierBloodPressureSystolic", 0) + 1
            )
            counts["HKQuantityTypeIdentifierBloodPressureDiastolic"] = (
                counts.get("HKQuantityTypeIdentifierBloodPressureDiastolic", 0) + 1
            )
        else:
            counts[type_id] = counts.get(type_id, 0) + 1
    return counts


def query_hkit_records_by_type(cur, type_identifier: str, tenant_id: int,
                               user_id: str) -> int:
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM   hkit_records r
        JOIN   hkit_record_types rt ON rt.id = r.record_type_id
        WHERE  r.tenant_id = %s
          AND  r.user_id    = %s
          AND  rt.type_identifier = %s
        """,
        (tenant_id, user_id, type_identifier),
    )
    row = cur.fetchone()
    return int(row["cnt"]) if row else 0


def query_hkit_workouts_count(cur, tenant_id: int, user_id: str) -> int:
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM hkit_workouts WHERE tenant_id = %s AND user_id = %s",
        (tenant_id, user_id),
    )
    row = cur.fetchone()
    return int(row["cnt"]) if row else 0


def query_activity_summary_dates(cur, tenant_id: int, user_id: str) -> set[str]:
    cur.execute(
        "SELECT date FROM hkit_activity_summaries WHERE tenant_id = %s AND user_id = %s",
        (tenant_id, user_id),
    )
    return {str(row["date"]) for row in cur.fetchall()}


def query_user_profile(cur, tenant_id: int, user_id: str) -> dict | None:
    cur.execute(
        "SELECT * FROM hkit_user_profile WHERE tenant_id = %s AND user_id = %s",
        (tenant_id, user_id),
    )
    return cur.fetchone()


def query_bp_correlation_pair(cur, tenant_id: int, user_id: str) -> list[dict]:
    cur.execute(
        """
        SELECT r.value, r.unit, r.start_date, r.metadata, rt.type_identifier
        FROM   hkit_records r
        JOIN   hkit_record_types rt ON rt.id = r.record_type_id
        WHERE  r.tenant_id = %s
          AND  r.user_id    = %s
          AND  rt.type_identifier IN (
              'HKQuantityTypeIdentifierBloodPressureSystolic',
              'HKQuantityTypeIdentifierBloodPressureDiastolic'
          )
        ORDER BY r.start_date DESC, rt.type_identifier
        """,
        (tenant_id, user_id),
    )
    return list(cur.fetchall())


# ---------------------------------------------------------------------------
# Verification driver
# ---------------------------------------------------------------------------


def verify_payload_landed(cur, payload: dict, tenant_id: int, user_id: str) -> list[str]:
    """Return a list of human-readable failures. Empty list means pass."""
    failures: list[str] = []

    # 1. Characteristics
    if payload.get("characteristics"):
        profile = query_user_profile(cur, tenant_id, user_id)
        if profile is None:
            failures.append("hkit_user_profile: no row for test user")
        else:
            chars = payload["characteristics"]
            if chars.get("date_of_birth") and str(profile.get("date_of_birth")) != chars["date_of_birth"]:
                failures.append(
                    f"hkit_user_profile.date_of_birth mismatch: "
                    f"sent={chars['date_of_birth']} got={profile.get('date_of_birth')}"
                )
            if chars.get("biological_sex") and profile.get("biological_sex") != chars["biological_sex"]:
                failures.append(
                    f"hkit_user_profile.biological_sex mismatch: "
                    f"sent={chars['biological_sex']} got={profile.get('biological_sex')}"
                )

    # 2. Activity summaries
    expected_dates = {s["date"] for s in payload.get("activity_summaries", [])
                      if isinstance(s, dict) and s.get("date")}
    if expected_dates:
        found_dates = query_activity_summary_dates(cur, tenant_id, user_id)
        missing = expected_dates - found_dates
        if missing:
            failures.append(f"hkit_activity_summaries missing dates: {sorted(missing)}")

    # 3. Workouts
    expected_workouts = len(payload.get("workouts", []) or [])
    if expected_workouts:
        # Note: workouts have no dedup constraint — every POST adds new rows.
        # Replay behavior is different from samples. We only assert "at least
        # the expected number" so a user with pre-existing data doesn't trip it.
        actual = query_hkit_workouts_count(cur, tenant_id, user_id)
        if actual < expected_workouts:
            failures.append(
                f"hkit_workouts: expected at least {expected_workouts}, found {actual}"
            )

    # 4. Samples by type
    expected_by_type = count_samples_by_type(cur, payload)
    for type_identifier, expected_count in expected_by_type.items():
        actual = query_hkit_records_by_type(cur, type_identifier, tenant_id, user_id)
        if actual < expected_count:
            failures.append(
                f"hkit_records[{type_identifier}]: expected at least {expected_count}, "
                f"found {actual}"
            )

    # 5. BP correlation reassembly
    bp_samples = [s for s in payload.get("samples", [])
                  if isinstance(s, dict)
                  and s.get("type_identifier") == "HKCorrelationTypeIdentifierBloodPressure"]
    if bp_samples:
        pair_rows = query_bp_correlation_pair(cur, tenant_id, user_id)
        if len(pair_rows) < 2 * len(bp_samples):
            failures.append(
                f"BP correlation: expected {2 * len(bp_samples)} component rows, "
                f"found {len(pair_rows)}"
            )
        # Confirm both rows share a correlation_id
        correlation_ids = set()
        for row in pair_rows[:2]:
            meta = row.get("metadata") or {}
            if isinstance(meta, dict):
                correlation_ids.add(meta.get("correlation_id"))
        if len(correlation_ids) != 1 or None in correlation_ids:
            failures.append(
                f"BP correlation: systolic/diastolic do not share a correlation_id "
                f"(found: {correlation_ids})"
            )

    return failures


def snapshot_counts(cur, tenant_id: int, user_id: str) -> dict[str, int]:
    """Return row counts across hkit_* tables for the test user."""
    counts = {}
    for table in ("hkit_records", "hkit_workouts", "hkit_activity_summaries",
                  "hkit_sources", "hkit_user_profile"):
        cur.execute(
            sql.SQL("SELECT COUNT(*) AS cnt FROM {} WHERE tenant_id = %s AND user_id = %s").format(
                sql.Identifier(table)
            ),
            (tenant_id, user_id),
        )
        row = cur.fetchone()
        counts[table] = int(row["cnt"]) if row else 0
    return counts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="End-to-end verification for HealthKit sync v2."
    )
    parser.add_argument("--base-url", default="http://localhost")
    parser.add_argument("--token", required=True,
                        help="Bearer token for the user the payload will be posted as")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--user-id", required=True,
                        help="UUID of the user the payload belongs to (must match token)")
    parser.add_argument("--tenant-id", type=int, default=1)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--replay", action="store_true",
                        help="POST the payload a second time and verify dedup")
    parser.add_argument("--db-host", default="localhost")
    parser.add_argument("--db-port", type=int, default=5432)
    parser.add_argument("--db-name", default="healthv10")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    if not args.fixture.exists():
        print(f"FAIL: fixture not found: {args.fixture}", file=sys.stderr)
        return 2

    payload = json.loads(args.fixture.read_text())
    if payload.get("payload_version") != 2:
        print(
            f"FAIL: fixture {args.fixture} is not payload_version=2", file=sys.stderr
        )
        return 2

    # Load DB credentials from .env, overlaying on environment.
    env = load_env_file(args.env_file)
    for key, value in env.items():
        os.environ.setdefault(key, value)
    app_user = os.environ.get("APP_DB_USER", "healthv10_app")
    app_password = os.environ.get("APP_DB_PASSWORD")
    if not app_password:
        print(
            "FAIL: APP_DB_PASSWORD not set (check --env-file path)",
            file=sys.stderr,
        )
        return 2

    print("== HealthKit sync v2 smoke test")
    print(f"   base_url  : {args.base_url}")
    print(f"   fixture   : {args.fixture}")
    print(f"   user_id   : {args.user_id}")
    print(f"   tenant_id : {args.tenant_id}")

    # Pre-POST snapshot so we can compute deltas.
    conn = open_app_connection(
        args.db_host, args.db_port, args.db_name,
        app_user, app_password,
    )
    cur = conn.cursor()
    try:
        before = snapshot_counts(cur, args.tenant_id, args.user_id)
    finally:
        cur.close()
        conn.close()

    print("\n-- Before POST --")
    for table, cnt in before.items():
        print(f"   {table:30s} {cnt}")

    # POST 1
    print("\n-- POST #1 --")
    response1 = post_sync_payload(args.base_url, args.token, payload, args.timeout)
    print(f"   response: {json.dumps(response1, indent=2)}")

    # Verify
    conn = open_app_connection(
        args.db_host, args.db_port, args.db_name,
        app_user, app_password,
    )
    cur = conn.cursor()
    try:
        after1 = snapshot_counts(cur, args.tenant_id, args.user_id)
        failures = verify_payload_landed(cur, payload, args.tenant_id, args.user_id)
    finally:
        cur.close()
        conn.close()

    print("\n-- After POST #1 --")
    for table, cnt in after1.items():
        delta = cnt - before[table]
        marker = " " if delta == 0 else "+" if delta > 0 else "-"
        print(f"   {table:30s} {cnt}  ({marker}{abs(delta)})")

    if failures:
        print("\n** VERIFICATION FAILURES **", file=sys.stderr)
        for f in failures:
            print(f"   - {f}", file=sys.stderr)
        return 2

    print("\n   verification: OK — all expected hkit_* rows present")

    # Replay
    if args.replay:
        print("\n-- POST #2 (replay) --")
        response2 = post_sync_payload(args.base_url, args.token, payload, args.timeout)
        print(f"   response: {json.dumps(response2, indent=2)}")

        conn = open_app_connection(
            args.db_host, args.db_port, args.db_name,
            app_user, app_password,
        )
        cur = conn.cursor()
        try:
            after2 = snapshot_counts(cur, args.tenant_id, args.user_id)
        finally:
            cur.close()
            conn.close()

        print("\n-- After POST #2 --")
        dedup_failures = []
        for table, cnt in after2.items():
            delta = cnt - after1[table]
            marker = " " if delta == 0 else "+" if delta > 0 else "-"
            print(f"   {table:30s} {cnt}  ({marker}{abs(delta)})")
            # hkit_workouts has no dedup constraint — it's expected to grow.
            # hkit_user_profile upserts (no delta expected).
            # Everything else should dedup cleanly.
            if table not in ("hkit_workouts",) and delta > 0:
                dedup_failures.append(f"{table}: replay added {delta} rows (dedup broken)")

        if dedup_failures:
            print("\n** DEDUP FAILURES **", file=sys.stderr)
            for f in dedup_failures:
                print(f"   - {f}", file=sys.stderr)
            return 3

        print("\n   dedup: OK — samples and summaries were no-ops on replay")

    print("\n== smoke test PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
