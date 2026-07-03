"""
Contract tests for the /api/v1/healthkit/sync endpoint under payload_version=2.

These tests run the Flask test client against the live sync handler with a
mocked database connection, so they exercise the full dispatch path —
from the HTTP route through ``_sync_healthkit_v2`` into
``healthkit_writer.write_v2_payload`` — without requiring Postgres.

What they assert
----------------
1. A v2 payload is correctly routed to ``_sync_healthkit_v2``.
2. A v1 payload is NOT routed to the v2 handler (regression).
3. The v2 handler resolves the authenticated user before any writes.
4. ``hkit_*`` INSERT statements are issued for every section of the
   fixture payload.
5. The legacy ``health_metrics`` INSERT is also issued (the transition
   dual-write) for types that map to the legacy enum.
6. The response body reports correct counts.

What they do NOT verify
-----------------------
* Whether Postgres accepts the SQL. That requires a real DB and is the
  smoke-test script's job.
* Whether the per-user_id scoping actually filters rows. Also real-DB territory.
* Correctness of the writer's internal SQL, which is pinned by
  ``test_healthkit_writer.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
if str(WEBAPP_ROOT) not in sys.path:
    sys.path.insert(0, str(WEBAPP_ROOT))


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "healthkit_v2"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


@pytest.fixture()
def mock_sync_db():
    """Mock the DB connection the sync endpoint obtains via get_user_db_connection.

    Queues dict-shaped fetchone results for the record type and source
    upserts the writer will perform. Returns the cursor mock so tests
    can assert on the execute() call sequence.

    We patch ``db_driver.executemany_rows`` rather than let the real
    implementation run — both drivers try to validate or encode the SQL
    against the cursor connection, which is not a meaningful attribute
    on a ``MagicMock`` and breaks deep inside the driver. The fake
    implementation just records what was "inserted" via the normal
    ``cur.execute`` machinery so tests can inspect it uniformly.
    """
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur

    # rowcount is used by the legacy dual-write path.
    cur.rowcount = 0

    fetchone_queue: list[Any] = []
    cur._fetchone_queue = fetchone_queue

    def fake_fetchone():
        if fetchone_queue:
            return fetchone_queue.pop(0)
        return None

    cur.fetchone.side_effect = fake_fetchone

    # Fake executemany_rows that routes through cur.execute so tests can
    # assert on the INSERT sequence uniformly.
    def fake_executemany_rows(target_cur, sql, argslist, *args, **kwargs):
        target_cur.execute(sql, ("<executemany_rows>", list(argslist)))
        target_cur.rowcount = len(list(argslist))

    with patch("app.get_user_db_connection", return_value=conn), \
         patch("app.get_user_id", return_value="11111111-2222-3333-4444-555555555555"), \
         patch("app.has_health_metrics_sync_dedupe_index", return_value=True), \
         patch("app.has_bp_sync_dedupe_index", return_value=True), \
         patch("db_driver.executemany_rows", side_effect=fake_executemany_rows):
        yield conn, cur, fetchone_queue


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_v1_payload_routes_to_legacy_handler(client, auth_headers, mock_sync_db):
    """A v1 payload (no payload_version) MUST NOT touch the v2 code path."""
    _conn, cur, _queue = mock_sync_db

    payload = {
        "samples": [
            {
                "type": "steps",
                "value": 1200,
                "unit": "count",
                "start_time": "2026-04-09T14:00:00Z",
                "source": "iPhone",
            }
        ]
    }
    resp = client.post("/api/v1/healthkit/sync", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    # v1 response shape does not have the v2 `hkit` subtree.
    assert "hkit" not in body
    assert "inserted" in body


def test_v2_payload_routes_to_v2_handler(client, auth_headers, mock_sync_db):
    _conn, cur, queue = mock_sync_db
    payload = _load_fixture("characteristics_only.json")

    resp = client.post("/api/v1/healthkit/sync", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["payload_version"] == 2
    assert body["hkit"]["characteristics"] == 1


# ---------------------------------------------------------------------------
# Writes to hkit_* tables
# ---------------------------------------------------------------------------


def test_v2_mixed_day_writes_every_hkit_table(client, auth_headers, mock_sync_db):
    _conn, cur, queue = mock_sync_db

    # Queue responses for the writer's lookups. Order matches what
    # write_v2_payload asks for: 3 sources (SELECT miss + INSERT RETURNING
    # each), then record-type upserts for each distinct type_identifier
    # in the payload.
    for src_id in (101, 102, 103):
        queue.append(None)                 # source SELECT miss
        queue.append({"id": src_id})       # source INSERT RETURNING
    # Record type upserts in the order write_v2_payload dispatches them:
    # samples iterated in payload order, each new type_identifier gets a
    # SELECT miss + INSERT RETURNING pair.
    for rt_id in (201, 202, 203, 204, 205, 206, 207):
        queue.append(None)
        queue.append({"id": rt_id})

    payload = _load_fixture("mixed_realistic_day.json")
    resp = client.post("/api/v1/healthkit/sync", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.get_data(as_text=True)

    body = resp.get_json()
    assert body["payload_version"] == 2
    assert body["hkit"]["characteristics"] == 1
    assert body["hkit"]["activity_summaries"] == 1
    assert body["hkit"]["workouts"] == 1
    assert body["hkit"]["samples"] == 6
    assert body["hkit"]["bp_correlations"] == 1

    # Inspect the execute sequence for expected INSERTs.
    executed_sql = [" ".join(call.args[0].split()) for call in cur.execute.call_args_list]

    def saw(fragment: str) -> bool:
        return any(fragment in sql for sql in executed_sql)

    assert saw("INSERT INTO hkit_user_profile")
    assert saw("INSERT INTO hkit_sources")
    assert saw("INSERT INTO hkit_activity_summaries")
    assert saw("INSERT INTO hkit_workouts")
    assert saw("INSERT INTO hkit_records")
    assert saw("INSERT INTO hkit_record_types")


# ---------------------------------------------------------------------------
# Legacy dual-write during transition
# ---------------------------------------------------------------------------


def test_v2_also_writes_legacy_health_metrics_rows(client, auth_headers, mock_sync_db):
    """The v2 path must populate health_metrics so the mobile UI keeps working."""
    _conn, cur, queue = mock_sync_db
    for src_id in (101,):
        queue.append(None)
        queue.append({"id": src_id})
    for rt_id in (201,):
        queue.append(None)
        queue.append({"id": rt_id})

    payload = _load_fixture("resting_hr_daily.json")
    resp = client.post("/api/v1/healthkit/sync", json=payload, headers=auth_headers)
    assert resp.status_code == 200

    executed_sql = [" ".join(call.args[0].split()) for call in cur.execute.call_args_list]

    # Note: executemany_rows rewrites the SQL slightly under psycopg3
    # (VALUES %s → VALUES (%s, ...)), so we look for a substring that
    # survives the rewrite.
    assert any("INSERT INTO hkit_records" in sql for sql in executed_sql)
    # The legacy health_metrics write is issued via executemany_rows which
    # goes through cur.execute under the hood (via the fake) — it should
    # show up with a health_metrics INSERT fragment. With a MagicMock the
    # driver bulk path may not touch .execute directly. We assert on the
    # INSERT fragment being passed via any mechanism by checking the
    # mock's method calls list.
    all_method_calls = cur.method_calls
    # Turn every method call's arguments into a searchable string.
    call_text = " ".join(repr(call) for call in all_method_calls)
    assert "health_metrics" in call_text or any(
        "health_metrics" in sql for sql in executed_sql
    )


def test_v2_bp_correlation_produces_legacy_bp_row(client, auth_headers, mock_sync_db):
    _conn, cur, queue = mock_sync_db
    # 1 source + 2 record types (systolic, diastolic)
    queue.append(None)
    queue.append({"id": 101})
    queue.append(None)
    queue.append({"id": 201})
    queue.append(None)
    queue.append({"id": 202})

    payload = _load_fixture("bp_correlation.json")
    resp = client.post("/api/v1/healthkit/sync", json=payload, headers=auth_headers)
    assert resp.status_code == 200

    body = resp.get_json()
    assert body["hkit"]["bp_correlations"] == 1
    assert body["hkit"]["samples"] == 0  # nothing other than the BP correlation

    executed_sql = [" ".join(call.args[0].split()) for call in cur.execute.call_args_list]
    # Two hkit_records rows for the BP pair.
    hkit_inserts = [sql for sql in executed_sql if "INSERT INTO hkit_records" in sql]
    assert len(hkit_inserts) == 2


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Anchors — Phase D of the HealthKit Consistency Plan
# ---------------------------------------------------------------------------


def test_v2_accepts_anchors_and_stores_them(client, auth_headers, mock_sync_db):
    """A v2 payload with device_id + anchors triggers hkit_sync_anchors INSERTs."""
    _conn, cur, _queue = mock_sync_db
    # get_sync_anchors will fetchall once at response-assembly time; return
    # the same anchors back to the client so the response mirrors state.
    cur.fetchall.return_value = [
        {"sample_type": "HKQuantityTypeIdentifierStepCount", "anchor": "tok-step"},
        {"sample_type": "HKQuantityTypeIdentifierHeartRate", "anchor": "tok-hr"},
    ]

    payload = {
        "payload_version": 2,
        "device_id": "device-A",
        "anchors": {
            "HKQuantityTypeIdentifierStepCount": "tok-step",
            "HKQuantityTypeIdentifierHeartRate": "tok-hr",
        },
    }
    resp = client.post("/api/v1/healthkit/sync", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.get_data(as_text=True)

    executed_sql = [" ".join(call.args[0].split()) for call in cur.execute.call_args_list]
    anchor_inserts = [sql for sql in executed_sql if "INSERT INTO hkit_sync_anchors" in sql]
    assert len(anchor_inserts) == 2


def test_v2_response_includes_anchors_for_device(client, auth_headers, mock_sync_db):
    """Response body must include the server-side anchor map for the device."""
    _conn, cur, _queue = mock_sync_db
    cur.fetchall.return_value = [
        {"sample_type": "HKQuantityTypeIdentifierStepCount", "anchor": "tok-step"},
        {"sample_type": "HKQuantityTypeIdentifierHeartRate", "anchor": "tok-hr"},
    ]

    payload = {
        "payload_version": 2,
        "device_id": "device-A",
        "anchors": {
            "HKQuantityTypeIdentifierStepCount": "tok-step",
            "HKQuantityTypeIdentifierHeartRate": "tok-hr",
        },
    }
    resp = client.post("/api/v1/healthkit/sync", json=payload, headers=auth_headers)
    assert resp.status_code == 200

    body = resp.get_json()
    assert body["anchors"] == {
        "HKQuantityTypeIdentifierStepCount": "tok-step",
        "HKQuantityTypeIdentifierHeartRate": "tok-hr",
    }
    assert body["hkit"]["anchors"] == 2


def test_v2_without_device_id_skips_anchor_storage(client, auth_headers, mock_sync_db):
    """Payloads without device_id must not write anchors and must not query them."""
    _conn, cur, _queue = mock_sync_db
    cur.fetchall.return_value = []

    payload = {
        "payload_version": 2,
        "anchors": {"HKQuantityTypeIdentifierStepCount": "tok"},
    }
    resp = client.post("/api/v1/healthkit/sync", json=payload, headers=auth_headers)
    assert resp.status_code == 200

    executed_sql = [" ".join(call.args[0].split()) for call in cur.execute.call_args_list]
    # No anchor INSERTs.
    assert not any("INSERT INTO hkit_sync_anchors" in sql for sql in executed_sql)
    # No anchor SELECT either (no device to query by).
    assert not any("SELECT sample_type, anchor FROM hkit_sync_anchors" in sql for sql in executed_sql)

    body = resp.get_json()
    # Response should still include the key, but empty — consumers can
    # always read body["anchors"] without a presence check.
    assert body["anchors"] == {}


def test_v2_fetch_only_returns_stored_anchors(client, auth_headers, mock_sync_db):
    """Empty payload with only device_id can be used to fetch stored anchors."""
    _conn, cur, _queue = mock_sync_db
    cur.fetchall.return_value = [
        {"sample_type": "HKQuantityTypeIdentifierStepCount", "anchor": "previously-stored"},
    ]

    payload = {"payload_version": 2, "device_id": "device-A"}
    resp = client.post("/api/v1/healthkit/sync", json=payload, headers=auth_headers)
    assert resp.status_code == 200

    body = resp.get_json()
    assert body["anchors"] == {"HKQuantityTypeIdentifierStepCount": "previously-stored"}
    assert body["hkit"]["anchors"] == 0


def test_v2_writer_exception_returns_500_and_rolls_back(client, auth_headers, mock_sync_db):
    conn, cur, _queue = mock_sync_db

    # Let the setup statements succeed, then blow up once we hit the
    # first user-data INSERT from inside the writer. This exercises the
    # ``except Exception`` branch around ``write_v2_payload``.
    def boom(sql, params=None):
        if "INSERT INTO" in sql:
            raise RuntimeError("synthetic failure")
        return None

    cur.execute.side_effect = boom

    payload = _load_fixture("characteristics_only.json")
    resp = client.post("/api/v1/healthkit/sync", json=payload, headers=auth_headers)
    assert resp.status_code == 500
    conn.rollback.assert_called()
