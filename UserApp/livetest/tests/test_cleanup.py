"""Tests for livetest.cleanup pure helpers.

Scope: unit tests for the in-memory pieces — query composition, result
formatting, and the orchestration driver using a mocked connection.
The destructive DELETE against a real DB is exercised by the manual
integration check (Pass 4 Task 4) against prodvps.
"""
from __future__ import annotations

from unittest.mock import MagicMock

# Importing the livetest package puts UserApp/webapp on sys.path so the shim
# resolves before we import sql.
import livetest  # noqa: F401  (side-effect: sys.path setup)
from db_driver import sql

from livetest.cleanup import (
    CleanupResult,
    SENTINEL_TARGETS,
    _apply_cleanup,
    _apply_sentinel_cleanup,
    _build_count_query,
    _build_delete_query,
    _build_sentinel_count_query,
    _build_sentinel_delete_query,
    _format_summary,
    _prepare_cleanup,
)


def _ident_names(composed) -> list[str]:
    """Return the bare names of every sql.Identifier in a Composed query."""
    out: list[str] = []
    for p in composed:
        if not isinstance(p, sql.Identifier):
            continue
        obj = getattr(p, "_obj", None)
        if obj:
            out.extend(obj)
    return out


def _sql_text(composed) -> str:
    """Return the concatenated SQL.SQL text from a Composed query."""
    chunks: list[str] = []
    for p in composed:
        if not isinstance(p, sql.SQL):
            continue
        s = getattr(p, "string", None) or getattr(p, "_obj", "")
        chunks.append(s)
    return " ".join(chunks)


def test_build_delete_query_uses_safe_identifier():
    """Table and column names must be sql.Identifier (not string-concat'd)
    so they get properly quoted, preventing SQL injection via a target
    table name even though all targets are hardcoded.
    """
    q = _build_delete_query("health_food_itemsv2", "name")
    identifier_names = _ident_names(q)
    assert "health_food_itemsv2" in identifier_names
    assert "name" in identifier_names
    sql_text = _sql_text(q)
    assert "DELETE FROM" in sql_text
    assert "LIKE 'livetest-%'" in sql_text


def test_build_count_query_uses_safe_identifier():
    q = _build_count_query("meals", "name")
    identifier_names = _ident_names(q)
    assert "meals" in identifier_names
    assert "name" in identifier_names
    sql_text = _sql_text(q)
    assert "count(*)" in sql_text
    assert "LIKE 'livetest-%'" in sql_text


def test_apply_cleanup_destructive_mode_calls_delete():
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = {"n": 5}

    targets = [("health_food_itemsv2", "name", "test purpose")]
    results = _apply_cleanup(conn, targets, dry_run=False)

    assert len(results) == 1
    r = results[0]
    assert isinstance(r, CleanupResult)
    assert r.table == "health_food_itemsv2"
    assert r.deleted == 5
    assert r.dry_run is False
    # Verify a DELETE statement was issued (not just a SELECT)
    delete_calls = [
        c for c in cur.execute.call_args_list
        if "DELETE" in str(c).upper()
    ]
    assert len(delete_calls) >= 1


def test_apply_cleanup_dry_run_skips_delete():
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = {"n": 7}

    targets = [("health_food_itemsv2", "name", "test purpose")]
    results = _apply_cleanup(conn, targets, dry_run=True)

    assert results[0].deleted == 7  # would-delete count from SELECT
    assert results[0].dry_run is True
    delete_calls = [
        c for c in cur.execute.call_args_list
        if "DELETE" in str(c).upper()
    ]
    assert len(delete_calls) == 0  # no DELETE in dry-run


def test_apply_cleanup_handles_multiple_targets():
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.side_effect = [{"n": 3}, {"n": 8}]

    targets = [
        ("table_a", "name", "purpose a"),
        ("table_b", "label", "purpose b"),
    ]
    results = _apply_cleanup(conn, targets, dry_run=False)

    assert len(results) == 2
    assert {r.table for r in results} == {"table_a", "table_b"}
    assert {r.deleted for r in results} == {3, 8}


def test_apply_cleanup_zero_count_skips_delete():
    """If count is 0, no DELETE should be issued (optimization + logs cleanly)."""
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = {"n": 0}

    targets = [("empty_table", "name", "nothing to clean")]
    results = _apply_cleanup(conn, targets, dry_run=False)

    assert results[0].deleted == 0
    delete_calls = [
        c for c in cur.execute.call_args_list
        if "DELETE" in str(c).upper()
    ]
    assert len(delete_calls) == 0


def test_prepare_cleanup_runs_workaround_updates():
    """The prepare step runs two UPDATE statements to null out the known-
    broken composite-FK-SET-NULL columns before the main DELETE loop.
    """
    conn = MagicMock()
    cur = conn.cursor.return_value

    _prepare_cleanup(conn)

    # Should have called execute twice, both UPDATEs, one per known-bad FK
    executed_sql = [
        str(c.args[0] if c.args else "")
        for c in cur.execute.call_args_list
    ]
    update_calls = [s for s in executed_sql if "UPDATE" in s.upper()]
    assert len(update_calls) == 2
    combined = " ".join(update_calls)
    assert "health_input_log" in combined
    assert "stack_id" in combined
    assert "health_food_logv2" in combined
    assert "timeframe_id" in combined


def test_format_summary_counts_total():
    results = [
        CleanupResult(table="t1", filter_col="name", deleted=5,
                      dry_run=False, purpose="x"),
        CleanupResult(table="t2", filter_col="name", deleted=3,
                      dry_run=False, purpose="y"),
        CleanupResult(table="t3", filter_col="notes", deleted=0,
                      dry_run=False, purpose="z"),
    ]
    summary = _format_summary(results)
    assert "8" in summary  # total deleted
    assert "3 tables" in summary or "3 targets" in summary


def test_format_summary_distinguishes_dry_run():
    results = [
        CleanupResult(table="t1", filter_col="name", deleted=5,
                      dry_run=True, purpose="x"),
    ]
    summary = _format_summary(results)
    assert "dry" in summary.lower() or "would" in summary.lower()


def test_sentinel_targets_cover_three_vitals_tables():
    """The sentinel list must cover the three vitals tables that have no
    name column to LIKE-match. If a fourth vitals flow gets added without
    a corresponding sentinel target, this test fails to flag the gap.
    """
    tables = {t[0] for t in SENTINEL_TARGETS}
    assert "health_blood_pressure_readings" in tables
    assert "health_metrics" in tables  # weight + temperature share this
    # health_metrics has two entries, one per metric_type
    metrics_targets = [
        t for t in SENTINEL_TARGETS if t[0] == "health_metrics"
    ]
    assert len(metrics_targets) == 2
    metric_types = {t[2][0] for t in metrics_targets}
    assert metric_types == {"weight", "temperature"}


def test_build_sentinel_count_query_uses_safe_identifier():
    q = _build_sentinel_count_query(
        "health_blood_pressure_readings",
        "systolic = %s AND diastolic = %s",
    )
    identifier_names = _ident_names(q)
    assert "health_blood_pressure_readings" in identifier_names
    sql_text = _sql_text(q)
    assert "count(*)" in sql_text
    assert "systolic = %s AND diastolic = %s" in sql_text


def test_build_sentinel_delete_query_uses_safe_identifier():
    q = _build_sentinel_delete_query(
        "health_metrics", "metric_type = %s AND value = %s"
    )
    identifier_names = _ident_names(q)
    assert "health_metrics" in identifier_names
    sql_text = _sql_text(q)
    assert "DELETE FROM" in sql_text
    assert "metric_type = %s AND value = %s" in sql_text


def test_apply_sentinel_cleanup_destructive_passes_params():
    """Destructive sentinel cleanup must (a) issue a SELECT with params,
    (b) issue a DELETE with the same params, and (c) report the resolved
    filter string with literal values rather than %s placeholders.
    """
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = {"n": 2}

    targets = [
        (
            "health_blood_pressure_readings",
            "systolic = %s AND diastolic = %s",
            (222, 111),
            "BP sentinels",
        ),
    ]
    results = _apply_sentinel_cleanup(conn, targets, dry_run=False)

    assert len(results) == 1
    r = results[0]
    assert r.table == "health_blood_pressure_readings"
    assert r.deleted == 2
    assert r.dry_run is False
    # Filter display string should have literal values, not %s
    assert "%s" not in r.filter_col
    assert "222" in r.filter_col
    assert "111" in r.filter_col

    # Both SELECT and DELETE should have been issued, and both should
    # have received the parameter tuple as the second positional argument.
    select_calls = [
        c for c in cur.execute.call_args_list
        if "SELECT" in str(c.args[0]).upper()
    ]
    delete_calls = [
        c for c in cur.execute.call_args_list
        if "DELETE" in str(c.args[0]).upper()
    ]
    assert len(select_calls) == 1
    assert len(delete_calls) == 1
    assert select_calls[0].args[1] == (222, 111)
    assert delete_calls[0].args[1] == (222, 111)


def test_apply_sentinel_cleanup_dry_run_skips_delete():
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = {"n": 4}

    targets = [
        (
            "health_metrics",
            "metric_type = %s AND value = %s",
            ("weight", 333.3),
            "weight sentinels",
        ),
    ]
    results = _apply_sentinel_cleanup(conn, targets, dry_run=True)

    assert results[0].deleted == 4
    assert results[0].dry_run is True
    delete_calls = [
        c for c in cur.execute.call_args_list
        if "DELETE" in str(c.args[0]).upper()
    ]
    assert len(delete_calls) == 0


def test_apply_sentinel_cleanup_zero_count_skips_delete():
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = {"n": 0}

    targets = [
        (
            "health_metrics",
            "metric_type = %s AND value = %s",
            ("temperature", 109.9),
            "temp sentinels",
        ),
    ]
    results = _apply_sentinel_cleanup(conn, targets, dry_run=False)

    assert results[0].deleted == 0
    delete_calls = [
        c for c in cur.execute.call_args_list
        if "DELETE" in str(c.args[0]).upper()
    ]
    assert len(delete_calls) == 0
