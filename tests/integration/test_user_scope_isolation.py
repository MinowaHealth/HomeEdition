"""Runtime cross-user isolation test — regression guard for PotentialRLSBug.md.

Home Edition has no RLS: every query on a user-owned table must carry an
explicit ``user_id`` predicate, or one household member's API reads return
another member's rows (the bug recorded in ``PotentialRLSBug.md``).

The static scope audit (``scripts/user_scope_audit.py``) cannot see queries
assembled with psycopg ``sql.SQL().format()`` composition — that is exactly how
the leaking handlers were built, so the audit passed them silently. This
behavioral test is the reliable guard: it logs in as each household member and
asserts each member's list endpoints return *exactly their own* rows
(API ``pagination.total`` == DB row count owned by that user), and that search
results only ever contain rows the searcher owns.

It is an integration test: it needs a running appliance with the household
fixture seeded (``TestData/seed_users.py`` then
``python -m TestData.three_month_seed``). It SKIPS — never fails — when the
stack or DB is unreachable or fewer than two household users exist, so a plain
``pytest`` run on a dev box without the stack stays green.

Env overrides:
    SCOPE_TEST_API_BASE   default http://localhost:80
    SEED_DB_HOST/PORT/NAME default localhost / 5432 / healthv10
    SEED_DB_USER/PASSWORD  default postgres / Password2026
"""
from __future__ import annotations

import os
import time

import pytest

httpx = pytest.importorskip("httpx")
psycopg = pytest.importorskip("psycopg")

API_BASE = os.environ.get("SCOPE_TEST_API_BASE", "http://localhost:80").rstrip("/")
TEST_PASSWORD = os.environ.get("SCOPE_TEST_PASSWORD", "Password2026")
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000000"
MAX_USERS = 3  # keep total logins under the /login 5-per-minute limit

# (api_path, table, metric_type or None) — single-table list endpoints whose
# pagination.total must equal the caller's owned row count.
ENDPOINTS = [
    ("/api/v1/blood-pressure", "health_blood_pressure_readings", None),
    ("/api/v1/weight", "health_metrics", "weight"),
    ("/api/v1/temperature", "health_metrics", "temperature"),
    ("/api/v1/food-log", "health_food_logv2", None),
    ("/api/v1/health-input-log", "health_input_log", None),
    ("/api/v1/sleep", "health_metrics", "sleep"),
    ("/api/v1/nutrition", "health_metrics", "nutrition"),
    ("/api/v1/medication-metrics", "health_metrics", "medication"),
]

# Tables the /search endpoint reads, with the column holding the row's text.
SEARCH_TABLES = {
    "health_observations": "content",
    "health_conditions": "name",
    "health_allergies": "allergen",
    "health_inputs": "name",
    "health_food_itemsv2": "name",
}


def _db():
    try:
        conn = psycopg.connect(
            host=os.environ.get("SEED_DB_HOST", "localhost"),
            port=int(os.environ.get("SEED_DB_PORT", "5432")),
            dbname=os.environ.get("SEED_DB_NAME", "healthv10"),
            user=os.environ.get("SEED_DB_USER", "postgres"),
            password=os.environ.get("SEED_DB_PASSWORD", "Password2026"),
            connect_timeout=3,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"appliance DB unreachable: {exc}")
    return conn


def _has_column(conn, table: str, column: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            (table, column),
        )
        return cur.fetchone() is not None


def _login(email: str) -> str:
    try:
        r = httpx.post(
            f"{API_BASE}/login",
            json={"email": email, "password": TEST_PASSWORD},
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"appliance API unreachable at {API_BASE}: {exc}")
    if r.status_code != 200:
        pytest.skip(f"login failed for {email}: HTTP {r.status_code}")
    return r.json()["token"]


@pytest.fixture(scope="module")
def household():
    """(conn, [(user_id, email, token), ...]) for up to MAX_USERS members."""
    conn = _db()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, email FROM users "
            "WHERE tenant_id = 1 AND id <> %s::uuid ORDER BY email",
            (SYSTEM_USER_ID,),
        )
        rows = cur.fetchall()
    if len(rows) < 2:
        pytest.skip(
            f"need >=2 household users seeded, found {len(rows)} "
            "(run TestData/seed_users.py + the temporal seeder)"
        )
    members = []
    for i, (uid, email) in enumerate(rows[:MAX_USERS]):
        if i:
            time.sleep(1)  # gentle spacing so we don't hammer the box
        members.append((str(uid), email, _login(email)))
    yield conn, members
    conn.close()


def _api_total(token: str, path: str) -> int:
    r = httpx.get(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": 1},
        timeout=15,
    )
    assert r.status_code == 200, f"GET {path} -> HTTP {r.status_code}"
    body = r.json()
    pag = body.get("pagination") if isinstance(body, dict) else None
    assert isinstance(pag, dict) and "total" in pag, (
        f"GET {path} did not return a pagination.total envelope"
    )
    return int(pag["total"])


def _db_owned_count(conn, table: str, metric_type, user_id: str) -> int:
    where = ["tenant_id = 1", "user_id = %s"]
    params = [user_id]
    if metric_type is not None:
        where.append("metric_type = %s")
        params.append(metric_type)
    if _has_column(conn, table, "is_deleted"):
        where.append("is_deleted = 0")
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} WHERE {' AND '.join(where)}", params)
        return cur.fetchone()[0]


@pytest.mark.parametrize("path,table,metric_type", ENDPOINTS)
def test_list_endpoint_returns_only_callers_rows(household, path, table, metric_type):
    """Each member's list total == that member's own DB rows — no leak, no loss."""
    conn, members = household
    for user_id, email, token in members:
        api_total = _api_total(token, path)
        db_total = _db_owned_count(conn, table, metric_type, user_id)
        assert api_total == db_total, (
            f"{path}: {email} saw {api_total} rows but owns {db_total} "
            f"in {table}"
            + (f" (metric_type={metric_type})" if metric_type else "")
            + " — user_id scoping is wrong (over- or under-filtering)."
        )


def test_no_endpoint_leaks_the_whole_household(household):
    """Defense-in-depth: two members with differing data must not receive
    identical non-zero totals (the original leak's signature)."""
    conn, members = household
    if len(members) < 2:
        pytest.skip("need 2 members for the leak-signature check")
    for path, table, metric_type in ENDPOINTS:
        owned = {
            uid: _db_owned_count(conn, table, metric_type, uid)
            for uid, _, _ in members
        }
        if len(set(owned.values())) < 2:
            continue  # members happen to own equal counts here; not discriminating
        totals = {email: _api_total(tok, path) for _, email, tok in members}
        assert len(set(totals.values())) > 1, (
            f"{path}: every member saw the same total {totals} despite owning "
            f"different row counts {owned} — cross-user leak."
        )


def test_search_returns_only_callers_rows(household):
    """Every row id returned by /search must belong to the searching user."""
    conn, members = household
    for user_id, email, token in members:
        r = httpx.get(
            f"{API_BASE}/api/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": "a", "k": 25},
            timeout=20,
        )
        if r.status_code != 200:
            pytest.skip(f"/search unavailable: HTTP {r.status_code}")
        results = r.json().get("results", [])
        for hit in results:
            table = hit.get("table")
            row_id = hit.get("id")
            if table not in SEARCH_TABLES or not row_id:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT user_id FROM {table} WHERE id = %s", (row_id,)
                )
                row = cur.fetchone()
            assert row is not None, f"/search returned unknown {table}.{row_id}"
            assert str(row[0]) == user_id, (
                f"/search leaked {table}.{row_id} (owner {row[0]}) to {email}"
            )
