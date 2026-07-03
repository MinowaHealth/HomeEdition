"""Regression tests for schema parsers in code_query_audit.py and
unused_columns_audit.py.

Both audits parse `02-home_schema.sql` with a hand-rolled CREATE TABLE
splitter that uses a `skip_starts` heuristic to drop constraint clauses
(`PRIMARY KEY (...)`, `EXCLUDE USING gist (...)`, etc.) from the column list.

A 2026-05-09 PR (Diets Phase 2) added a column literally named `excludes` to
`diet_catalog`, which silently dropped from both parsers because
`startswith("exclude")` matched the column name. The fix uses `"exclude "`
(trailing space) so the heuristic only matches the constraint syntax. These
tests pin the fix and protect future columns whose names happen to start
with `exclude*` (e.g. `excluded_at`, `excludes_categories`) from the same
trap.

Run:
    .venv/bin/python -m pytest DataModel3/test_audit_schema_parsers.py -v

Pure parsing, no DB.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from code_query_audit import parse_schema as parse_schema_cqa  # noqa: E402
from unused_columns_audit import parse_schema as parse_schema_uca  # noqa: E402


@pytest.fixture()
def schema_with_exclude_column(tmp_path: Path) -> Path:
    """A synthetic schema that exercises every shape of `EXCLUDE` we care about:

    - column literally named `excludes` (must be indexed)
    - `EXCLUDE USING gist (...)` constraint (must be skipped)
    - `EXCLUDE (...)` constraint without USING (must be skipped)
    """
    sql = textwrap.dedent("""\
        CREATE TABLE IF NOT EXISTS public.diet_catalog (
            tenant_id        SMALLINT NOT NULL DEFAULT 1,
            code             TEXT NOT NULL,
            excludes         JSONB,
            nutrient_targets JSONB,
            PRIMARY KEY (tenant_id, code)
        );

        CREATE TABLE IF NOT EXISTS public.zzz_with_exclude_constraint (
            id   INTEGER,
            name TEXT,
            tag  TEXT,
            EXCLUDE USING gist (id WITH =, name WITH =),
            EXCLUDE (tag WITH =)
        );
        """)
    p = tmp_path / "schema.sql"
    p.write_text(sql)
    return p


def test_code_query_audit_indexes_excludes_column(schema_with_exclude_column: Path):
    """parse_schema in code_query_audit must index a column named `excludes`."""
    tables, col_index = parse_schema_cqa(schema_with_exclude_column)
    assert "excludes" in tables["diet_catalog"], (
        "Column `excludes` was dropped from diet_catalog — the `exclude` "
        "skip_starts heuristic is matching the column name. See "
        "DataModel3/code_query_audit.py:213 (must use 'exclude ' with "
        "trailing space, not 'exclude')."
    )
    assert col_index["excludes"] == {"diet_catalog"}


def test_code_query_audit_skips_exclude_constraints(schema_with_exclude_column: Path):
    """parse_schema must NOT index `EXCLUDE USING gist (...)` constraint
    fragments as if they were columns. The constraint's WITH clauses
    introduce identifiers (id, name, tag) that legitimately appear as
    columns elsewhere in the table — the constraint line itself must
    contribute nothing to the column set."""
    tables, _ = parse_schema_cqa(schema_with_exclude_column)
    cols = tables["zzz_with_exclude_constraint"]
    # The real columns are present.
    assert cols == {"id", "name", "tag"}
    # No constraint-syntax tokens leaked in.
    for noise in {"using", "gist", "with"}:
        assert noise not in cols


def test_unused_columns_audit_indexes_excludes_column(
    schema_with_exclude_column: Path,
):
    """The two parsers must stay symmetric: if a column is visible to one,
    it must be visible to the other. Otherwise unused_columns_audit reports
    a column as unused (or missing) while code_query_audit sees it
    referenced — or vice versa."""
    tables = parse_schema_uca(schema_with_exclude_column)
    cols = {c for c, _ in tables["diet_catalog"]}
    assert "excludes" in cols, (
        "Column `excludes` was dropped from diet_catalog. See "
        "DataModel3/unused_columns_audit.py:79 — must use 'exclude ' "
        "(trailing space) in skip_starts."
    )


def test_unused_columns_audit_skips_exclude_constraints(
    schema_with_exclude_column: Path,
):
    """Symmetric guard: unused_columns_audit must drop the EXCLUDE
    constraint clauses, not extract their internal identifiers as
    columns."""
    tables = parse_schema_uca(schema_with_exclude_column)
    cols = {c for c, _ in tables["zzz_with_exclude_constraint"]}
    assert cols == {"id", "name", "tag"}
    for noise in {"using", "gist", "with"}:
        assert noise not in cols
