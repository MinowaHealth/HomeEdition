"""
Unit tests for healthkit_writer.

These tests use a MagicMock-style fake cursor (no real database). The
goal is to pin down the SQL the writer issues and the parameter
tuples it binds, which is where the correctness contract with the
hkit_* schema lives. Real-database verification happens in the
smoke-test script run against a restored prod snapshot on the local Mac dev stack.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytz

# Add the webapp root to the path so we can import the writer directly.
import sys

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
if str(WEBAPP_ROOT) not in sys.path:
    sys.path.insert(0, str(WEBAPP_ROOT))

from healthkit_writer import (  # noqa: E402  — path setup above is required
    WriterContext,
    WriterCounts,
    _coerce_numeric,
    _humanize_type_identifier,
    _parse_iso_timestamp,
    get_or_create_record_type,
    get_or_create_source,
    get_sync_anchors,
    upsert_activity_summary,
    upsert_bp_correlation,
    upsert_characteristics,
    upsert_sample,
    upsert_sync_anchor,
    upsert_workout,
    write_v2_payload,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "healthkit_v2"
TEST_TENANT_ID = 1
TEST_USER_ID = "11111111-2222-3333-4444-555555555555"


# ---------------------------------------------------------------------------
# Fake cursor that returns queued rows
# ---------------------------------------------------------------------------


class FakeCursor:
    """A minimal fake database cursor for writer unit tests.

    The fake records every ``execute`` call and returns queued rows in
    order from ``fetchone``. Tests pre-load the queue with the exact
    rows they expect each lookup/insert to return.
    """

    def __init__(
        self,
        fetchone_queue: list[dict[str, Any] | None] | None = None,
        fetchall_queue: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        self.execute_calls: list[tuple[str, tuple]] = []
        self._fetchone_queue: list[dict[str, Any] | None] = list(fetchone_queue or [])
        self._fetchall_queue: list[list[dict[str, Any]]] = list(fetchall_queue or [])

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.execute_calls.append((sql, params if params is not None else ()))

    def fetchone(self) -> dict[str, Any] | None:
        if not self._fetchone_queue:
            return None
        return self._fetchone_queue.pop(0)

    def fetchall(self) -> list[dict[str, Any]]:
        if not self._fetchall_queue:
            return []
        return self._fetchall_queue.pop(0)

    # convenience
    def executed_sql_substrings(self) -> list[str]:
        return [" ".join(sql.split()) for sql, _ in self.execute_calls]


def _load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_humanize_type_identifier_strips_prefixes_and_spaces_camel_case() -> None:
    assert _humanize_type_identifier("HKQuantityTypeIdentifierRestingHeartRate") == "Resting Heart Rate"
    assert _humanize_type_identifier("HKCategoryTypeIdentifierSleepAnalysis") == "Sleep Analysis"
    assert _humanize_type_identifier("HKCorrelationTypeIdentifierBloodPressure") == "Blood Pressure"
    assert _humanize_type_identifier("SomeUnknownIdentifier") == "Some Unknown Identifier"


def test_coerce_numeric_accepts_int_float_numeric_string() -> None:
    assert _coerce_numeric(42) == 42.0
    assert _coerce_numeric(3.14) == 3.14
    assert _coerce_numeric("  5.5 ") == 5.5


def test_coerce_numeric_rejects_bool_none_and_bad_strings() -> None:
    assert _coerce_numeric(None) is None
    assert _coerce_numeric(True) is None  # bool is not a measurement
    assert _coerce_numeric(False) is None
    assert _coerce_numeric("not a number") is None
    assert _coerce_numeric({"value": 1}) is None


def test_parse_iso_timestamp_handles_z_suffix_and_offsets() -> None:
    parsed = _parse_iso_timestamp("2026-04-09T14:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.year == 2026 and parsed.month == 4 and parsed.day == 9
    assert parsed.hour == 14

    parsed_offset = _parse_iso_timestamp("2026-04-09T14:00:00-05:00")
    assert parsed_offset is not None
    assert parsed_offset.hour == 19  # normalized to UTC


def test_parse_iso_timestamp_passthrough_naive_datetime_is_localized() -> None:
    naive = datetime(2026, 4, 9, 14, 0, 0)
    parsed = _parse_iso_timestamp(naive)
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_parse_iso_timestamp_rejects_empty_and_garbage() -> None:
    assert _parse_iso_timestamp(None) is None
    assert _parse_iso_timestamp("") is None
    assert _parse_iso_timestamp("not a date") is None


# ---------------------------------------------------------------------------
# get_or_create_record_type
# ---------------------------------------------------------------------------


def _ctx() -> WriterContext:
    return WriterContext(tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID)


def test_record_type_cache_hit_skips_database() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    ctx.record_type_cache["HKQuantityTypeIdentifierHeartRate"] = 99

    result = get_or_create_record_type(cur, ctx, "HKQuantityTypeIdentifierHeartRate")

    assert result == 99
    assert cur.execute_calls == []  # cache hit, no SQL issued


def test_record_type_existing_row_returns_id_and_caches() -> None:
    cur = FakeCursor(fetchone_queue=[{"id": 17}])
    ctx = _ctx()

    result = get_or_create_record_type(cur, ctx, "HKQuantityTypeIdentifierHeartRate")

    assert result == 17
    assert ctx.record_type_cache["HKQuantityTypeIdentifierHeartRate"] == 17
    assert len(cur.execute_calls) == 1
    sql, params = cur.execute_calls[0]
    assert "SELECT id FROM hkit_record_types" in sql
    assert params == ("HKQuantityTypeIdentifierHeartRate",)


def test_record_type_new_identifier_inserts_and_returns_new_id() -> None:
    cur = FakeCursor(
        fetchone_queue=[
            None,            # SELECT miss
            {"id": 42},      # INSERT ... RETURNING id
        ]
    )
    ctx = _ctx()

    result = get_or_create_record_type(cur, ctx, "HKQuantityTypeIdentifierRestingHeartRate")

    assert result == 42
    assert ctx.record_type_cache["HKQuantityTypeIdentifierRestingHeartRate"] == 42
    assert len(cur.execute_calls) == 2
    insert_sql, insert_params = cur.execute_calls[1]
    assert "INSERT INTO hkit_record_types" in insert_sql
    assert insert_params == (
        "HKQuantityTypeIdentifierRestingHeartRate",
        "quantity",
        "Resting Heart Rate",
    )


def test_record_type_rejects_empty_identifier() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    with pytest.raises(ValueError):
        get_or_create_record_type(cur, ctx, "")
    with pytest.raises(ValueError):
        get_or_create_record_type(cur, ctx, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# get_or_create_source
# ---------------------------------------------------------------------------


def test_source_lookup_by_bundle_id_returns_existing() -> None:
    cur = FakeCursor(fetchone_queue=[{"id": 7}])
    ctx = _ctx()
    source_info = {
        "source_name": "Apple Watch",
        "source_bundle_id": "com.apple.health",
        "source_version": "18.3",
        "device_name": "My Apple Watch",
        "device_model": "Watch6,5",
    }

    result = get_or_create_source(cur, ctx, source_info)

    assert result == 7
    sql, params = cur.execute_calls[0]
    assert "SELECT id FROM hkit_sources" in sql
    assert "source_bundle_id" in sql
    assert params == (TEST_TENANT_ID, TEST_USER_ID, "com.apple.health")


def test_source_new_bundle_inserts_full_provenance() -> None:
    cur = FakeCursor(
        fetchone_queue=[
            None,            # SELECT miss
            {"id": 3},       # INSERT ... RETURNING id
        ]
    )
    ctx = _ctx()
    source_info = {
        "source_name": "Omron Connect",
        "source_bundle_id": "com.omronhealthcare.omronconnect",
        "source_version": "7.6.0",
        "device_name": "Omron 10 Series",
        "device_model": "BP7450",
    }

    result = get_or_create_source(cur, ctx, source_info)

    assert result == 3
    insert_sql, insert_params = cur.execute_calls[1]
    assert "INSERT INTO hkit_sources" in insert_sql
    assert "source_bundle_id" in insert_sql
    assert "device_model" in insert_sql
    assert insert_params == (
        TEST_TENANT_ID,
        TEST_USER_ID,
        "Omron Connect",
        "com.omronhealthcare.omronconnect",
        "7.6.0",
        "Omron 10 Series",
        "BP7450",
    )


def test_source_without_bundle_id_falls_back_to_name_and_device_model() -> None:
    cur = FakeCursor(fetchone_queue=[{"id": 11}])
    ctx = _ctx()
    source_info = {
        "source_name": "Thermometer A",
        "device_model": "T-100",
    }

    result = get_or_create_source(cur, ctx, source_info)

    assert result == 11
    sql, _params = cur.execute_calls[0]
    assert "source_name = %s" in sql
    assert "device_model" in sql


def test_source_cache_hit_returns_without_sql() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    source_info = {
        "source_name": "Apple Watch",
        "source_bundle_id": "com.apple.health",
        "source_version": "18.3",
        "device_name": "My Apple Watch",
        "device_model": "Watch6,5",
    }

    # Prime the cache with the exact tuple that _source_cache_key produces.
    key = (
        TEST_TENANT_ID,
        TEST_USER_ID,
        "Apple Watch",
        "com.apple.health",
        "18.3",
        "My Apple Watch",
        "Watch6,5",
    )
    ctx.source_cache[key] = 55

    result = get_or_create_source(cur, ctx, source_info)
    assert result == 55
    assert cur.execute_calls == []


def test_source_rejects_missing_name() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    with pytest.raises(ValueError):
        get_or_create_source(cur, ctx, {"source_bundle_id": "x"})


# ---------------------------------------------------------------------------
# upsert_characteristics
# ---------------------------------------------------------------------------


def test_characteristics_full_payload_writes_expected_row() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    chars = _load_fixture("characteristics_only.json")["characteristics"]

    upsert_characteristics(cur, ctx, chars)

    assert len(cur.execute_calls) == 1
    sql, params = cur.execute_calls[0]
    assert "INSERT INTO hkit_user_profile" in sql
    assert "ON CONFLICT (tenant_id, user_id) DO UPDATE" in sql
    assert params == (
        TEST_TENANT_ID,
        TEST_USER_ID,
        "1978-03-14",
        "male",
        "O+",
        "III",
        False,
    )


def test_characteristics_empty_dict_is_noop() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    upsert_characteristics(cur, ctx, {})
    assert cur.execute_calls == []


def test_characteristics_coerces_truthy_to_bool() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    upsert_characteristics(cur, ctx, {"wheelchair_use": 1})
    _sql, params = cur.execute_calls[0]
    assert params[-1] is True


# ---------------------------------------------------------------------------
# upsert_activity_summary
# ---------------------------------------------------------------------------


def test_activity_summary_full_row_includes_move_time_columns() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    summary = _load_fixture("wheelchair_activity_summary.json")["activity_summaries"][0]

    upsert_activity_summary(cur, ctx, summary)

    assert len(cur.execute_calls) == 1
    sql, params = cur.execute_calls[0]
    assert "INSERT INTO hkit_activity_summaries" in sql
    assert "move_time" in sql
    assert "move_time_goal" in sql
    assert "ON CONFLICT (tenant_id, user_id, date)" in sql
    # Column order: tenant, user, date, active_energy, aeg, exercise_time, etg,
    # stand_hours, shg, move_time, move_time_goal
    assert params == (
        TEST_TENANT_ID,
        TEST_USER_ID,
        "2026-04-09",
        420,
        500,
        None,  # exercise_time not set
        None,  # exercise_time_goal not set
        11,
        12,
        38,
        30,
    )


def test_activity_summary_missing_date_raises() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    with pytest.raises(ValueError):
        upsert_activity_summary(cur, ctx, {"active_energy_burned": 100})


# ---------------------------------------------------------------------------
# upsert_workout
# ---------------------------------------------------------------------------


def test_workout_with_route_stashes_polyline_in_metadata() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    workout = _load_fixture("workout_with_route.json")["workouts"][0]

    upsert_workout(cur, ctx, workout, source_id=99)

    assert len(cur.execute_calls) == 1
    sql, params = cur.execute_calls[0]
    assert "INSERT INTO hkit_workouts" in sql
    (
        tenant_id,
        user_id,
        workout_type,
        source_id,
        start,
        end,
        duration_seconds,
        total_distance,
        total_energy_burned,
        metadata_json,
    ) = params
    assert tenant_id == TEST_TENANT_ID
    assert user_id == TEST_USER_ID
    assert workout_type == "HKWorkoutActivityTypeRunning"
    assert source_id == 99
    assert start.tzinfo is not None
    assert end.tzinfo is not None
    assert end > start
    assert duration_seconds == 2160
    assert total_distance == 5840
    assert total_energy_burned == 412
    metadata = json.loads(metadata_json)
    assert "route" in metadata
    assert len(metadata["route"]) == 4
    assert metadata["route"][0]["lat"] == pytest.approx(41.0828)
    assert "events" in metadata
    assert metadata["total_distance_unit"] == "m"
    assert metadata["total_energy_burned_unit"] == "kcal"
    assert metadata["HKAverageMETs"] == 8.2


def test_workout_without_route_emits_null_metadata_when_empty() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    workout = {
        "workout_type": "HKWorkoutActivityTypeYoga",
        "start": "2026-04-09T08:00:00Z",
        "end": "2026-04-09T09:00:00Z",
        "duration_seconds": 3600,
    }

    upsert_workout(cur, ctx, workout)

    _sql, params = cur.execute_calls[0]
    # With no metadata, no route, no events, no unit hints: metadata JSON is None.
    assert params[-1] is None


def test_workout_missing_type_raises() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    with pytest.raises(ValueError):
        upsert_workout(cur, ctx, {"start": "2026-04-09T08:00:00Z"})


# ---------------------------------------------------------------------------
# upsert_sample
# ---------------------------------------------------------------------------


def test_sample_looks_up_record_type_then_inserts() -> None:
    cur = FakeCursor(fetchone_queue=[{"id": 5}])  # record_type_id for HR
    ctx = _ctx()
    sample = _load_fixture("instantaneous_hr_batch.json")["samples"][0]

    assert upsert_sample(cur, ctx, sample, source_id=2) is True

    assert len(cur.execute_calls) == 2
    # First: record type lookup
    rt_sql, rt_params = cur.execute_calls[0]
    assert "hkit_record_types" in rt_sql
    assert rt_params == ("HKQuantityTypeIdentifierHeartRate",)
    # Second: insert into hkit_records
    ins_sql, ins_params = cur.execute_calls[1]
    assert "INSERT INTO hkit_records" in ins_sql
    assert "ON CONFLICT (tenant_id, user_id, record_type_id, source_id, start_date, end_date)" in ins_sql
    (
        tenant_id,
        user_id,
        record_type_id,
        source_id,
        value,
        unit,
        start,
        end,
        metadata_json,
    ) = ins_params
    assert tenant_id == TEST_TENANT_ID
    assert user_id == TEST_USER_ID
    assert record_type_id == 5
    assert source_id == 2
    assert value == 71.0
    assert unit == "count/min"
    assert start == pytz.utc.localize(datetime(2026, 4, 9, 14, 0, 0))
    assert end == pytz.utc.localize(datetime(2026, 4, 9, 14, 0, 0))
    assert json.loads(metadata_json) == {"HKMetadataKeyHeartRateMotionContext": "sedentary"}


def test_sample_resting_hr_preserves_type_identifier_no_alias() -> None:
    """Regression: the key behavior the old sync endpoint's alias was destroying."""
    cur = FakeCursor(fetchone_queue=[{"id": 6}])
    ctx = _ctx()
    sample = _load_fixture("resting_hr_daily.json")["samples"][0]

    upsert_sample(cur, ctx, sample)

    rt_sql, rt_params = cur.execute_calls[0]
    assert rt_params == ("HKQuantityTypeIdentifierRestingHeartRate",)
    # Critical: the type identifier is stored raw — NOT collapsed to
    # HKQuantityTypeIdentifierHeartRate. That means querying for resting HR
    # alone is possible in hkit_records.


def test_sample_rejects_missing_type_identifier() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    assert upsert_sample(cur, ctx, {"value": 10, "start": "2026-04-09T14:00:00Z"}) is False
    assert cur.execute_calls == []


def test_sample_rejects_unparseable_value() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    assert (
        upsert_sample(
            cur,
            ctx,
            {
                "type_identifier": "HKQuantityTypeIdentifierHeartRate",
                "value": "not a number",
                "start": "2026-04-09T14:00:00Z",
            },
        )
        is False
    )


def test_sample_missing_end_defaults_to_start() -> None:
    cur = FakeCursor(fetchone_queue=[{"id": 5}])
    ctx = _ctx()
    assert (
        upsert_sample(
            cur,
            ctx,
            {
                "type_identifier": "HKQuantityTypeIdentifierHeartRate",
                "value": 70,
                "start": "2026-04-09T14:00:00Z",
            },
        )
        is True
    )
    _sql, params = cur.execute_calls[1]
    assert params[6] == params[7]  # start == end


# ---------------------------------------------------------------------------
# upsert_bp_correlation
# ---------------------------------------------------------------------------


def test_bp_correlation_writes_two_rows_with_shared_correlation_id() -> None:
    cur = FakeCursor(
        fetchone_queue=[
            {"id": 10},  # systolic record_type lookup
            {"id": 11},  # diastolic record_type lookup
        ]
    )
    ctx = _ctx()
    correlation = _load_fixture("bp_correlation.json")["samples"][0]

    result = upsert_bp_correlation(cur, ctx, correlation, source_id=3)

    assert result is not None
    assert result["systolic"] == 122.0
    assert result["diastolic"] == 78.0
    assert result["unit"] == "mmHg"
    assert result["correlation_id"] == "omron-abc123"

    # Two record type lookups + two inserts = 4 SQL calls.
    assert len(cur.execute_calls) == 4

    systolic_ins = cur.execute_calls[2]
    diastolic_ins = cur.execute_calls[3]
    assert "INSERT INTO hkit_records" in systolic_ins[0]
    assert "INSERT INTO hkit_records" in diastolic_ins[0]

    systolic_meta = json.loads(systolic_ins[1][-1])
    diastolic_meta = json.loads(diastolic_ins[1][-1])
    assert systolic_meta["correlation_id"] == "omron-abc123"
    assert diastolic_meta["correlation_id"] == "omron-abc123"
    assert systolic_meta["component_role"] == "systolic"
    assert diastolic_meta["component_role"] == "diastolic"

    # Value ordering: systolic first, then diastolic.
    assert systolic_ins[1][4] == 122.0
    assert diastolic_ins[1][4] == 78.0
    # Both rows share record_type_id lookups and source_id.
    assert systolic_ins[1][2] == 10
    assert diastolic_ins[1][2] == 11
    assert systolic_ins[1][3] == 3
    assert diastolic_ins[1][3] == 3


def test_bp_correlation_without_external_uuid_synthesizes_correlation_id() -> None:
    cur = FakeCursor(
        fetchone_queue=[
            {"id": 10},
            {"id": 11},
        ]
    )
    ctx = _ctx()
    correlation = {
        "start": "2026-04-09T08:15:00Z",
        "end": "2026-04-09T08:15:00Z",
        "components": [
            {"type_identifier": "HKQuantityTypeIdentifierBloodPressureSystolic", "value": 120, "unit": "mmHg"},
            {"type_identifier": "HKQuantityTypeIdentifierBloodPressureDiastolic", "value": 80, "unit": "mmHg"},
        ],
    }

    result = upsert_bp_correlation(cur, ctx, correlation)

    assert result is not None
    assert result["correlation_id"].startswith("bp-")
    systolic_meta = json.loads(cur.execute_calls[2][1][-1])
    diastolic_meta = json.loads(cur.execute_calls[3][1][-1])
    assert systolic_meta["correlation_id"] == diastolic_meta["correlation_id"]


def test_bp_correlation_missing_component_returns_none() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    result = upsert_bp_correlation(
        cur,
        ctx,
        {
            "start": "2026-04-09T08:15:00Z",
            "components": [
                {"type_identifier": "HKQuantityTypeIdentifierBloodPressureSystolic", "value": 120},
            ],
        },
    )
    assert result is None
    assert cur.execute_calls == []


# ---------------------------------------------------------------------------
# write_v2_payload — end-to-end dispatch
# ---------------------------------------------------------------------------


def test_write_v2_payload_rejects_wrong_version() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    with pytest.raises(ValueError):
        write_v2_payload(cur, ctx, {"payload_version": 1})


def test_write_v2_payload_mixed_day_dispatches_every_section() -> None:
    payload = _load_fixture("mixed_realistic_day.json")

    # Queue the cursor responses in the exact order write_v2_payload will
    # request them. Order matters — this is how we pin the dispatch sequence.
    fetch_queue: list[dict[str, Any] | None] = []
    # 1. Characteristics upsert: 0 fetchone calls.
    # 2. Sources: 3 sources, each calls get_or_create_source, which does:
    #    SELECT (miss) → None, INSERT RETURNING → {"id": ...}
    for src_id in (101, 102, 103):
        fetch_queue.append(None)            # SELECT miss
        fetch_queue.append({"id": src_id})  # INSERT RETURNING
    # 3. Activity summary: 0 fetchone calls.
    # 4. Workout: 0 fetchone calls (no record type lookup).
    # 5. Samples:
    #    - StepCount: SELECT miss + INSERT RETURNING id (record type)
    fetch_queue.append(None)
    fetch_queue.append({"id": 201})
    #    - RestingHeartRate
    fetch_queue.append(None)
    fetch_queue.append({"id": 202})
    #    - HeartRate  (first instance)
    fetch_queue.append(None)
    fetch_queue.append({"id": 203})
    #    - HeartRate (second instance) — cache hit, no fetch
    #    - BodyMass
    fetch_queue.append(None)
    fetch_queue.append({"id": 204})
    #    - SleepAnalysis
    fetch_queue.append(None)
    fetch_queue.append({"id": 205})
    #    - BP correlation: two record_type lookups
    fetch_queue.append(None)
    fetch_queue.append({"id": 206})
    fetch_queue.append(None)
    fetch_queue.append({"id": 207})

    cur = FakeCursor(fetchone_queue=fetch_queue)
    ctx = _ctx()

    counts, bp_summaries = write_v2_payload(cur, ctx, payload)

    assert isinstance(counts, WriterCounts)
    assert counts.characteristics == 1
    assert counts.activity_summaries == 1
    assert counts.workouts == 1
    # mixed_realistic_day.json contains 6 ordinary samples:
    # StepCount, RestingHeartRate, HeartRate(x2), BodyMass, SleepAnalysis.
    # The BP correlation is counted separately.
    assert counts.samples == 6
    assert counts.bp_correlations == 1
    assert counts.skipped == 0
    assert len(bp_summaries) == 1
    assert bp_summaries[0]["systolic"] == 122.0
    assert bp_summaries[0]["diastolic"] == 78.0

    # Sanity: every fixture SQL should have been issued via INSERT.
    executed = cur.executed_sql_substrings()
    assert any("INSERT INTO hkit_user_profile" in sql for sql in executed)
    assert any("INSERT INTO hkit_activity_summaries" in sql for sql in executed)
    assert any("INSERT INTO hkit_workouts" in sql for sql in executed)
    assert any("INSERT INTO hkit_records" in sql for sql in executed)
    assert any("INSERT INTO hkit_sources" in sql for sql in executed)


def test_write_v2_payload_heart_rate_cache_avoids_duplicate_lookups() -> None:
    """The second HeartRate sample in mixed_realistic_day must hit the cache."""
    payload = _load_fixture("instantaneous_hr_batch.json")

    fetch_queue: list[dict[str, Any] | None] = []
    # One source: SELECT miss + INSERT returning id
    fetch_queue.append(None)
    fetch_queue.append({"id": 501})
    # First HeartRate: SELECT miss + INSERT returning record type id
    fetch_queue.append(None)
    fetch_queue.append({"id": 601})
    # The next two HeartRate samples should hit the record type cache.

    cur = FakeCursor(fetchone_queue=fetch_queue)
    ctx = _ctx()

    counts, _ = write_v2_payload(cur, ctx, payload)
    assert counts.samples == 3

    # Count SELECTs on hkit_record_types. The first sample does SELECT + INSERT
    # (2 execute calls). The next two should be cache hits with 0 SELECTs.
    record_type_selects = [
        1
        for sql, _ in cur.execute_calls
        if "SELECT id FROM hkit_record_types" in sql
    ]
    assert len(record_type_selects) == 1  # only the first sample touched the DB


# ---------------------------------------------------------------------------
# Sync anchors — Phase D of the HealthKit Consistency Plan
# ---------------------------------------------------------------------------


def test_upsert_sync_anchor_issues_insert_on_conflict() -> None:
    cur = FakeCursor()
    ctx = _ctx()

    upsert_sync_anchor(cur, ctx, "device-A", "HKQuantityTypeIdentifierStepCount", "anchor-token-1")

    assert len(cur.execute_calls) == 1
    sql, params = cur.execute_calls[0]
    compact = " ".join(sql.split())
    assert "INSERT INTO hkit_sync_anchors" in compact
    assert "ON CONFLICT" in compact
    assert "DO UPDATE" in compact
    # Params must be in declared column order.
    assert params == (
        ctx.tenant_id,
        ctx.user_id,
        "device-A",
        "HKQuantityTypeIdentifierStepCount",
        "anchor-token-1",
    )


def test_upsert_sync_anchor_rejects_empty_device_id() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    with pytest.raises(ValueError):
        upsert_sync_anchor(cur, ctx, "", "HKQuantityTypeIdentifierStepCount", "t")


def test_upsert_sync_anchor_rejects_empty_sample_type() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    with pytest.raises(ValueError):
        upsert_sync_anchor(cur, ctx, "device-A", "", "t")


def test_upsert_sync_anchor_rejects_empty_anchor() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    with pytest.raises(ValueError):
        upsert_sync_anchor(cur, ctx, "device-A", "HKQuantityTypeIdentifierStepCount", "")


def test_get_sync_anchors_returns_stored_map() -> None:
    cur = FakeCursor(
        fetchall_queue=[
            [
                {"sample_type": "HKQuantityTypeIdentifierStepCount", "anchor": "tok-step"},
                {"sample_type": "HKQuantityTypeIdentifierHeartRate", "anchor": "tok-hr"},
            ]
        ]
    )
    ctx = _ctx()

    out = get_sync_anchors(cur, ctx, "device-A")

    assert out == {
        "HKQuantityTypeIdentifierStepCount": "tok-step",
        "HKQuantityTypeIdentifierHeartRate": "tok-hr",
    }
    sql, params = cur.execute_calls[0]
    compact = " ".join(sql.split())
    assert "SELECT sample_type, anchor FROM hkit_sync_anchors" in compact
    assert params == (ctx.tenant_id, ctx.user_id, "device-A")


def test_get_sync_anchors_empty_when_no_rows() -> None:
    cur = FakeCursor()  # fetchall_queue empty → fetchall returns []
    ctx = _ctx()
    assert get_sync_anchors(cur, ctx, "device-A") == {}


def test_get_sync_anchors_rejects_empty_device_id() -> None:
    cur = FakeCursor()
    ctx = _ctx()
    with pytest.raises(ValueError):
        get_sync_anchors(cur, ctx, "")


def test_write_v2_payload_processes_anchors_block() -> None:
    """An anchors block with device_id should trigger one upsert per entry."""
    payload = {
        "payload_version": 2,
        "device_id": "device-A",
        "anchors": {
            "HKQuantityTypeIdentifierStepCount": "tok-step",
            "HKQuantityTypeIdentifierHeartRate": "tok-hr",
        },
    }
    cur = FakeCursor()
    ctx = _ctx()

    counts, bp_summaries = write_v2_payload(cur, ctx, payload)

    assert counts.anchors == 2
    assert bp_summaries == []
    anchor_inserts = [
        (sql, params)
        for sql, params in cur.execute_calls
        if "INSERT INTO hkit_sync_anchors" in " ".join(sql.split())
    ]
    assert len(anchor_inserts) == 2


def test_write_v2_payload_anchors_without_device_id_is_skipped() -> None:
    """Missing device_id means we can't key anchors — skip, do not raise."""
    payload = {
        "payload_version": 2,
        "anchors": {"HKQuantityTypeIdentifierStepCount": "tok"},
    }
    cur = FakeCursor()
    ctx = _ctx()

    counts, bp_summaries = write_v2_payload(cur, ctx, payload)

    assert counts.anchors == 0
    assert not any(
        "INSERT INTO hkit_sync_anchors" in " ".join(sql.split())
        for sql, _ in cur.execute_calls
    )


def test_write_v2_payload_anchors_empty_entries_are_skipped() -> None:
    """Empty anchor strings or non-string types must not be upserted."""
    payload = {
        "payload_version": 2,
        "device_id": "device-A",
        "anchors": {
            "HKQuantityTypeIdentifierStepCount": "",        # empty string — skip
            "HKQuantityTypeIdentifierHeartRate": None,      # None — skip
            "": "tok-orphan",                                # empty key — skip
            "HKQuantityTypeIdentifierRestingHeartRate": "tok-rhr",  # valid
        },
    }
    cur = FakeCursor()
    ctx = _ctx()

    counts, _ = write_v2_payload(cur, ctx, payload)

    assert counts.anchors == 1
    anchor_inserts = [
        params
        for sql, params in cur.execute_calls
        if "INSERT INTO hkit_sync_anchors" in " ".join(sql.split())
    ]
    assert len(anchor_inserts) == 1
    assert anchor_inserts[0][3] == "HKQuantityTypeIdentifierRestingHeartRate"
