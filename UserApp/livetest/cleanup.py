"""Live test cleanup: delete rows the harness wrote to test tables.

Usage:
    python -m livetest.cleanup                    # destructive, all targets
    python -m livetest.cleanup --dry-run          # report only, no delete
    python -m livetest.cleanup --target meals     # restrict to one table
    python -m livetest.cleanup --fuzz-user UUID   # delete ALL rows for that user_id

Cleanup is scoped to the test user's RLS context — only rows belonging to
the configured TEST_EMAIL are touched. Three filter strategies are used:

1. **Name-prefix filter** (CLEANUP_TARGETS) — for tables where each flow
   writes a `livetest-<uuid>` prefix into a textual identifying column
   (name, content, allergen, …). This is the dominant case: every Phase A
   flow and most Phase B flows. Matches rows whose column LIKE 'livetest-%'.

2. **Sentinel-value filter** (SENTINEL_TARGETS) — for the three vitals
   tables that have no textual name column. The flows write impossible
   sentinel readings (systolic=222/diastolic=111 for BP, weight=333.3,
   temperature=109.9 °F) and clean up after themselves on a passing run.
   This list catches sentinel rows left behind by an aborted run, where
   the in-flow DELETE step never got a chance to fire. Sentinel values
   were chosen to be outside any plausible real reading, so a false
   positive against real user data is not possible — 222/111 BP would
   trigger an emergency alert, 333.3 lbs is past the high end of the
   weight column's plausible range, and 109.9 °F is hyperthermia.

3. **Fuzz-user filter** (FUZZ_USER_TARGETS) — when --fuzz-user UUID is
   passed, deletes by user_id alone across every table that holds fuzz
   user-owned rows. Schemathesis-generated payloads do not carry the
   livetest- prefix and rarely hit sentinel values, so the only reliable
   blast-radius bound is the user_id itself. RLS is already enforcing the
   tenant boundary, and the fuzz user is dedicated to the campaign, so
   user_id-only matching is safe.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# livetest/__init__.py puts UserApp/webapp on sys.path so db_driver is importable.
import db_driver
from db_driver import sql

from livetest.auth import login
from livetest.config import LiveTestConfig, load_config


# (table, name_column, purpose) — parents listed before their dependents
# so the FK cascades have room to run. In practice the order is not load-
# bearing because _prepare_cleanup has already nulled the two broken
# composite-FK columns before this loop runs.
CLEANUP_TARGETS: list[tuple[str, str, str]] = [
    # Phase A — meds/food stack
    ("health_food_itemsv2", "name", "food items created by livetest"),
    ("health_inputs", "name", "health inputs (meds/supps) created by livetest"),
    ("stacks", "name", "stacks created by livetest"),
    ("meals", "name", "meals created by livetest"),
    ("timeframes", "name", "timeframes created by livetest"),
    # Phase A — log promotions (Pass-5 pagination smoke flow)
    ("log_promotions", "free_text_original", "log promotions created by livetest"),
    # Phase B — observations (content carries the livetest prefix)
    ("health_observations", "content", "observations created by livetest"),
    # Phase B — clinical history
    ("health_conditions", "name", "conditions created by livetest"),
    ("health_allergies", "allergen", "allergies created by livetest"),
    ("health_blood_work", "test_name", "blood work results created by livetest"),
    ("health_family_history", "condition_name", "family history created by livetest"),
    ("health_vaccinations", "vaccine_name", "vaccinations created by livetest"),
    # Phase B — reminders + dietary settings
    ("reminders", "title", "reminders created by livetest"),
    ("dietary_settings", "notes", "dietary settings created by livetest"),
    # Phase B — feedback (admin-triaged alpha feedback, Pass-5 smoke flow)
    ("feedback", "content", "feedback created by livetest"),
    # Phase C — documents. Hard-DELETE of documents rows cascades to
    # document_pages and document_annotations via ON DELETE CASCADE,
    # so no explicit child cleanup is required. Catches both live and
    # soft-deleted livetest rows (title LIKE matches either).
    ("documents", "title", "documents created by livetest"),
]


# (table, where_clause, params, purpose) — sentinel-value targets for the
# three vitals tables that have no textual name column to LIKE-match.
# Each flow's happy path DELETEs the row it created; this list is the
# recovery path for an aborted run that crashed between INSERT and DELETE.
#
# The where_clause strings are module-level constants — never user input —
# and the actual values are passed as bound parameters, so this is not a
# SQL-injection surface despite the literal string concatenation in
# _build_sentinel_*_query below.
#
# Sentinel values must stay in sync with:
#   flows/vitals_bp.py     SENTINEL_SYSTOLIC=222, SENTINEL_DIASTOLIC=111
#   flows/vitals_weight.py SENTINEL_WEIGHT=333.3
#   flows/vitals_temp.py   SENTINEL_TEMP=109.9 (Fahrenheit, stored raw)
SENTINEL_TARGETS: list[tuple[str, str, tuple, str]] = [
    (
        "health_blood_pressure_readings",
        "systolic = %s AND diastolic = %s",
        (222, 111),
        "BP sentinel readings from aborted vitals_bp runs",
    ),
    (
        "health_metrics",
        "metric_type = %s AND value = %s",
        ("weight", 333.3),
        "weight sentinel readings from aborted vitals_weight runs",
    ),
    (
        "health_metrics",
        "metric_type = %s AND value = %s",
        ("temperature", 109.9),
        "temperature sentinel readings from aborted vitals_temp runs",
    ),
]


# Tables that hold fuzz user-owned rows. Deleted by user_id when --fuzz-user
# is set. RLS handles the tenant boundary; the fuzz user is dedicated to the
# campaign so user_id-only matching is safe and exhaustive.
#
# Includes api_tokens so any keys minted during the run (mint_key.py registers
# atexit revoke, but a hard kill leaves rows behind) get cleaned up. Includes
# sessions for the same reason.
FUZZ_USER_TARGETS: list[tuple[str, str]] = [
    # Auth / session artifacts
    ("api_tokens", "API keys minted during fuzz run"),
    ("sessions", "session tokens issued during fuzz run"),
    # Meds / food / stack rows
    ("health_food_itemsv2", "food items"),
    ("health_inputs", "health inputs (meds/supps)"),
    ("stacks", "stacks"),
    ("meals", "meals"),
    ("timeframes", "timeframes"),
    ("log_promotions", "log promotions"),
    # Observations + clinical history
    ("health_observations", "observations"),
    ("health_conditions", "conditions"),
    ("health_allergies", "allergies"),
    ("health_blood_work", "blood work results"),
    ("health_family_history", "family history"),
    ("health_vaccinations", "vaccinations"),
    # Reminders / dietary / feedback
    ("reminders", "reminders"),
    ("dietary_settings", "dietary settings"),
    ("feedback", "feedback rows"),
    # Documents
    ("documents", "documents (cascades to pages + annotations)"),
    # Vitals
    ("health_blood_pressure_readings", "blood pressure readings"),
    ("health_metrics", "weight / temperature / generic vitals"),
]


@dataclass
class CleanupResult:
    table: str
    filter_col: str
    deleted: int
    dry_run: bool
    purpose: str


def _build_delete_query(table: str, name_col: str) -> sql.Composed:
    """Compose ``DELETE FROM <table> WHERE <col> LIKE 'livetest-%'`` safely."""
    return sql.SQL(
        "DELETE FROM {} WHERE {} LIKE 'livetest-%'"
    ).format(sql.Identifier(table), sql.Identifier(name_col))


def _build_count_query(table: str, name_col: str) -> sql.Composed:
    """Compose ``SELECT count(*) AS n FROM <table> WHERE <col> LIKE 'livetest-%'``."""
    return sql.SQL(
        "SELECT count(*) AS n FROM {} WHERE {} LIKE 'livetest-%'"
    ).format(sql.Identifier(table), sql.Identifier(name_col))


def _build_fuzz_user_count_query(table: str) -> sql.Composed:
    """Compose ``SELECT count(*) AS n FROM <table> WHERE user_id = %s``."""
    return sql.SQL("SELECT count(*) AS n FROM {} WHERE user_id = %s").format(
        sql.Identifier(table)
    )


def _build_fuzz_user_delete_query(table: str) -> sql.Composed:
    """Compose ``DELETE FROM <table> WHERE user_id = %s``."""
    return sql.SQL("DELETE FROM {} WHERE user_id = %s").format(
        sql.Identifier(table)
    )


def _build_sentinel_count_query(table: str, where_clause: str) -> sql.Composed:
    """Compose ``SELECT count(*) AS n FROM <table> WHERE <where_clause>``.

    The where_clause is a module-level constant (see SENTINEL_TARGETS),
    and the parameter values are bound separately at execute time.
    """
    return sql.SQL("SELECT count(*) AS n FROM {table} WHERE {where}").format(
        table=sql.Identifier(table),
        where=sql.SQL(where_clause),
    )


def _build_sentinel_delete_query(table: str, where_clause: str) -> sql.Composed:
    """Compose ``DELETE FROM <table> WHERE <where_clause>``. See note above."""
    return sql.SQL("DELETE FROM {table} WHERE {where}").format(
        table=sql.Identifier(table),
        where=sql.SQL(where_clause),
    )


def _prepare_cleanup(conn: Any) -> None:
    """Null out the two columns with known composite-FK-SET-NULL bugs.

    Postgres's ``ON DELETE SET NULL`` on a composite FK nulls ALL columns
    in the FK tuple, including the ``tenant_id`` column which is ``NOT
    NULL`` on both of these tables. A raw DELETE against ``stacks`` or
    ``timeframes`` would otherwise fail when dependent log rows exist.

    This helper runs two scoped UPDATEs — one per known-bad FK — that
    touch only the pointer column, leaving ``tenant_id`` alone. When the
    schema is fixed (either by dropping ``tenant_id`` from the composite
    and adding ``UNIQUE (id)`` on the parent, or by changing the action
    to ``CASCADE``), this helper can be deleted.

    Matching paths:
      - ``health_input_log.stack_id`` → ``stacks`` (schema line 648)
      - ``health_food_logv2.timeframe_id`` → ``timeframes`` (schema line 1019)
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE health_input_log SET stack_id = NULL "
            "WHERE stack_id IN ("
            "  SELECT id FROM stacks WHERE name LIKE 'livetest-%'"
            ")"
        )
        cur.execute(
            "UPDATE health_food_logv2 SET timeframe_id = NULL "
            "WHERE timeframe_id IN ("
            "  SELECT id FROM timeframes WHERE name LIKE 'livetest-%'"
            ")"
        )
    finally:
        cur.close()


def _apply_cleanup(
    conn: Any,
    targets: list[tuple[str, str, str]],
    dry_run: bool,
) -> list[CleanupResult]:
    """Run COUNT (always) and DELETE (unless dry_run) per target.

    Returns one CleanupResult per target. The COUNT step gives the report
    its 'how many would be / were deleted' number; the DELETE step does
    the actual mutation when not in dry-run mode. When count is 0, no
    DELETE is issued (optimization + cleaner logs).
    """
    results: list[CleanupResult] = []
    cur = conn.cursor()
    try:
        for table, name_col, purpose in targets:
            count_q = _build_count_query(table, name_col)
            cur.execute(count_q)
            row = cur.fetchone()
            count = int(row["n"]) if row else 0

            if not dry_run and count > 0:
                delete_q = _build_delete_query(table, name_col)
                cur.execute(delete_q)

            results.append(
                CleanupResult(
                    table=table,
                    filter_col=f"{name_col} LIKE 'livetest-%'",
                    deleted=count,
                    dry_run=dry_run,
                    purpose=purpose,
                )
            )
    finally:
        cur.close()
    return results


def _apply_fuzz_user_cleanup(
    conn: Any,
    user_id: str,
    targets: list[tuple[str, str]],
    dry_run: bool,
) -> list[CleanupResult]:
    """Run COUNT and DELETE for every fuzz user-owned table.

    Deletes by user_id only — no name-prefix or sentinel filtering. The
    caller is responsible for passing the correct fuzz user UUID; this
    function will happily wipe whatever user_id you give it (RLS-scoped).
    Use only against the campaign's dedicated fuzz user, never against a
    real persona.
    """
    results: list[CleanupResult] = []
    cur = conn.cursor()
    try:
        for table, purpose in targets:
            count_q = _build_fuzz_user_count_query(table)
            cur.execute(count_q, (user_id,))
            row = cur.fetchone()
            count = int(row["n"]) if row else 0

            if not dry_run and count > 0:
                delete_q = _build_fuzz_user_delete_query(table)
                cur.execute(delete_q, (user_id,))

            results.append(
                CleanupResult(
                    table=table,
                    filter_col=f"user_id = '{user_id}'",
                    deleted=count,
                    dry_run=dry_run,
                    purpose=purpose,
                )
            )
    finally:
        cur.close()
    return results


def _apply_sentinel_cleanup(
    conn: Any,
    targets: list[tuple[str, str, tuple, str]],
    dry_run: bool,
) -> list[CleanupResult]:
    """Run COUNT and DELETE for sentinel-value targets.

    Mirrors _apply_cleanup but reads from SENTINEL_TARGETS, which uses a
    parameterized where clause instead of a name-prefix LIKE. Output rows
    use the where clause as the filter display string so the report is
    self-explanatory ('systolic = 222 AND diastolic = 111' rather than a
    column name that doesn't tell you what was matched).
    """
    results: list[CleanupResult] = []
    cur = conn.cursor()
    try:
        for table, where_clause, params, purpose in targets:
            count_q = _build_sentinel_count_query(table, where_clause)
            cur.execute(count_q, params)
            row = cur.fetchone()
            count = int(row["n"]) if row else 0

            if not dry_run and count > 0:
                delete_q = _build_sentinel_delete_query(table, where_clause)
                cur.execute(delete_q, params)

            # Render the bound parameters into the display string so the
            # report shows the actual values that were matched, not %s.
            display_filter = where_clause
            for p in params:
                display_filter = display_filter.replace(
                    "%s", repr(p), 1
                )

            results.append(
                CleanupResult(
                    table=table,
                    filter_col=display_filter,
                    deleted=count,
                    dry_run=dry_run,
                    purpose=purpose,
                )
            )
    finally:
        cur.close()
    return results


def _format_summary(results: list[CleanupResult]) -> str:
    total = sum(r.deleted for r in results)
    n_targets = len(results)
    mode = "would delete" if (results and results[0].dry_run) else "deleted"
    return f"Total {mode}: {total} rows across {n_targets} tables"


def _print_live(results: list[CleanupResult]) -> None:
    print("\n[livetest cleanup]")
    for r in results:
        verb = "would delete" if r.dry_run else "deleted"
        print(
            f"  ✓ {r.table} ({r.filter_col}): "
            f"{r.deleted} {verb}"
        )
    print(f"  → {_format_summary(results)}")


def _write_markdown(
    results: list[CleanupResult], cfg: LiveTestConfig, user_id: str
) -> Path:
    cfg.report_dir.mkdir(parents=True, exist_ok=True)
    mode = (
        "dry-run (no delete)"
        if results and results[0].dry_run
        else "destructive (real delete)"
    )
    lines = [
        f"# Live Test Cleanup Report — {cfg.run_id}",
        "",
        f"**Target:** {cfg.base_url}",
        f"**User:** {cfg.test_email} ({user_id})",
        f"**Started:** {datetime.now().isoformat()}",
        f"**Mode:** {mode}",
        "",
        "| Table | Filter | Count | Purpose |",
        "|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.table} | {r.filter_col} "
            f"| {r.deleted} | {r.purpose} |"
        )
    lines.append("")
    lines.append(f"**{_format_summary(results)}**")
    path = cfg.report_dir / f"livetest-cleanup-{cfg.run_id}.md"
    path.write_text("\n".join(lines))
    return path


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Delete rows created by the live test harness."
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be deleted without actually deleting.",
    )
    p.add_argument(
        "--target",
        action="append",
        default=[],
        help="Restrict cleanup to a specific table. Repeatable.",
    )
    p.add_argument(
        "--fuzz-user",
        type=str,
        default=None,
        metavar="UUID",
        help=(
            "Switch to fuzz-mode: delete ALL rows belonging to this user_id "
            "across every fuzz-relevant table. Bypasses livetest/sentinel "
            "filters entirely. Use only with the campaign's dedicated fuzz "
            "user UUID."
        ),
    )
    return p


def main() -> None:
    # Parse cleanup-specific args first; pass remaining (--base-url,
    # --env-file, etc.) to load_config.
    cleanup_parser = _build_parser()
    cleanup_args, remaining = cleanup_parser.parse_known_args()

    cfg = load_config(remaining)

    # Resolve user_id. In fuzz-user mode we use the passed UUID directly and
    # skip the API login flow — the fuzz user may not have password auth set
    # up, and we want cleanup to work even if the app is in a degraded state
    # post-stress test. In normal mode we log in as the configured test user
    # and read the user_id back from /api/v1/session.
    if cleanup_args.fuzz_user:
        user_id = cleanup_args.fuzz_user
        print(f"[livetest cleanup] fuzz-user mode: scoped to user_id={user_id}")
    else:
        session = login(cfg)
        resp = session.get(
            f"{cfg.base_url}/api/v1/session", timeout=cfg.timeout
        )
        resp.raise_for_status()
        user_id = resp.json()["user_id"]

    # Open an app-role PG connection (mirrors livetest.pg.open_rls_connection
    # but kept inline so cleanup.py is independently auditable). No RLS in
    # Home Edition — the delete targets below scope by tenant_id/user_id.
    conn = db_driver.connect(
        host=cfg.db_host,
        port=cfg.db_port,
        dbname=cfg.db_name,
        user=cfg.app_db_user,
        password=cfg.app_db_password,
    )
    conn.autocommit = True

    if cleanup_args.fuzz_user:
        # Fuzz-user mode: ignore livetest/sentinel target lists entirely.
        # --target is still honored as a fuzz-table restriction.
        fuzz_targets = FUZZ_USER_TARGETS
        if cleanup_args.target:
            wanted = set(cleanup_args.target)
            fuzz_targets = [t for t in FUZZ_USER_TARGETS if t[0] in wanted]
            if not fuzz_targets:
                available = sorted({t[0] for t in FUZZ_USER_TARGETS})
                print(
                    f"Error: no matching fuzz cleanup targets for {sorted(wanted)}",
                    file=sys.stderr,
                )
                print(f"Available: {available}", file=sys.stderr)
                conn.close()
                sys.exit(2)
        try:
            results = _apply_fuzz_user_cleanup(
                conn, user_id, fuzz_targets, dry_run=cleanup_args.dry_run
            )
        finally:
            conn.close()
    else:
        targets = CLEANUP_TARGETS
        sentinel_targets = SENTINEL_TARGETS
        if cleanup_args.target:
            wanted = set(cleanup_args.target)
            targets = [t for t in CLEANUP_TARGETS if t[0] in wanted]
            sentinel_targets = [
                t for t in SENTINEL_TARGETS if t[0] in wanted
            ]
            if not targets and not sentinel_targets:
                available = sorted(
                    {t[0] for t in CLEANUP_TARGETS}
                    | {t[0] for t in SENTINEL_TARGETS}
                )
                print(
                    f"Error: no matching cleanup targets for {sorted(wanted)}",
                    file=sys.stderr,
                )
                print(f"Available: {available}", file=sys.stderr)
                conn.close()
                sys.exit(2)

        try:
            # Always run the prepare step even in dry-run — the UPDATEs it
            # issues are safe (only null pointer columns for livetest-named
            # parents) and are required for the destructive path to work
            # without hitting the composite-FK cascade bug. Running them in
            # dry-run produces a pre-nulled state that slightly inflates the
            # next destructive run's value, but keeps the two modes behaviorally
            # consistent.
            if not cleanup_args.dry_run:
                _prepare_cleanup(conn)
            results = _apply_cleanup(
                conn, targets, dry_run=cleanup_args.dry_run
            )
            results.extend(
                _apply_sentinel_cleanup(
                    conn, sentinel_targets, dry_run=cleanup_args.dry_run
                )
            )
        finally:
            conn.close()

    _print_live(results)
    report_path = _write_markdown(results, cfg, user_id)
    print(f"\nReport: {report_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
