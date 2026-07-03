#!/usr/bin/env python3
"""
Analyze which database columns are never read in the UserApp codebase.

Strategy:
  1. Parse `CREATE TABLE` blocks from the schema SQL source of truth.
  2. Extract (table, column) pairs, skipping constraint/index/primary-key lines.
  3. For each column, count word-boundary matches in UserApp Python files
     (excluding .venv, .pytest_cache, .ruff_cache, __pycache__).
  4. Partition results:
     - "likely-unused" (0 hits on a distinctive name)
     - "common-name, needs review" (0 direct hits but name is generic)
     - "used" (>=1 hit) — not reported in detail
  5. Produce a markdown report in DataModel3/.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "Infrastructure/init/docker-init-home/02-home_schema.sql"
USERAPP = REPO / "UserApp"
REPORT = REPO / "DataModel3/UnusedColumnsAudit.md"

# Directories inside UserApp we skip (not source code)
SKIP_DIRS = {".venv", ".pytest_cache", ".ruff_cache", "__pycache__",
             "logs", "node_modules", ".git"}

# Column names that are so generic they guarantee false positives.
# We still check them, but segregate their findings under "common names".
COMMON_NAMES = {
    "id", "name", "status", "type", "value", "data", "notes", "title",
    "description", "date", "time", "start", "end", "key", "code", "source",
    "category", "count", "amount", "tags", "metadata", "result", "text",
    "url", "path", "version", "active", "enabled", "hash", "token", "group",
    "order", "label", "color", "size", "width", "height", "lat", "lng",
    "content", "body", "subject", "from", "to", "year", "month", "day",
    "min", "max", "sum", "avg", "unit", "units",
}

# Column names that are effectively automatic/implicit — DB defaults,
# timestamps, framework fields. We note them but flag separately since "never read"
# doesn't mean "can be removed" for these.
IMPLICIT_NAMES = {
    "tenant_id", "created_at", "updated_at", "deleted_at",
}

# Mobile-sync contract columns. These exist on ~24 tables as a deliberate
# data contract for mobile SQLite <-> hosted PostgreSQL sync.
# Bidirectional sync is DEFERRED per
# Synchronization/2026-03-28-DECISION.md pending per-user encryption, so
# these columns are legitimately unread in UserApp today — but they
# MUST NOT be dropped. Partitioned into their own bucket with a warning.
SYNC_CONTRACT_NAMES = {
    "sqlite_id", "synced_at",
}

def parse_schema(schema_path: Path) -> dict[str, list[tuple[str, str]]]:
    """
    Parse CREATE TABLE blocks and return {table_name: [(column, sql_type), ...]}.
    Skips lines that start with PRIMARY KEY, FOREIGN KEY, UNIQUE, CONSTRAINT,
    CHECK, or are blank/comments.
    """
    text = schema_path.read_text()
    tables: dict[str, list[tuple[str, str]]] = {}

    # Match CREATE TABLE ... ( ... );  (non-greedy, DOTALL)
    pattern = re.compile(
        r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+public\.(\w+)\s*\((.*?)\n\);",
        re.DOTALL,
    )

    # Trailing space on "exclude " is load-bearing: it matches the
    # `EXCLUDE [USING method] (...)` and `EXCLUDE (...)` constraint syntax
    # without false-matching a real column literally named `excludes`
    # (e.g. diet_catalog.excludes JSONB). Mirrors the same fix in
    # code_query_audit.py — keep the two parsers symmetric so a column
    # is either visible to both audits or invisible to both.
    skip_starts = (
        "primary key", "foreign key", "unique", "constraint",
        "check (", "check(", "like ", "exclude ", ")",
    )

    for m in pattern.finditer(text):
        table = m.group(1)
        body = m.group(2)
        cols: list[tuple[str, str]] = []
        # Split on commas that are NOT inside parentheses (naive but works
        # for this schema because CHECK/DEFAULT expressions don't use
        # unbalanced parens).
        depth = 0
        buf = []
        lines = []
        for ch in body:
            if ch == "(":
                depth += 1
                buf.append(ch)
            elif ch == ")":
                depth -= 1
                buf.append(ch)
            elif ch == "," and depth == 0:
                lines.append("".join(buf))
                buf = []
            else:
                buf.append(ch)
        if buf:
            lines.append("".join(buf))

        for raw in lines:
            # Strip inline comments
            line = re.sub(r"--.*$", "", raw, flags=re.MULTILINE).strip()
            if not line:
                continue
            low = line.lower()
            if low.startswith(skip_starts):
                continue
            # First token = column name (identifier)
            tok = re.match(r'^"?(\w+)"?\s+(.*)$', line)
            if not tok:
                continue
            col = tok.group(1)
            sql_type = tok.group(2).strip()
            # Filter: token must look like an identifier, not a constraint kw
            if col.lower() in ("primary", "foreign", "unique", "constraint",
                               "check", "like", "exclude"):
                continue
            cols.append((col, sql_type[:80]))
        if cols:
            tables[table] = cols
    return tables


def gather_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in-place
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith((".py", ".html", ".js", ".sql", ".jinja", ".j2")):
                files.append(Path(dirpath) / fn)
    return files


def build_search_corpus(files: list[Path]) -> dict[Path, str]:
    corpus: dict[Path, str] = {}
    for f in files:
        try:
            corpus[f] = f.read_text(errors="replace")
        except Exception:
            pass
    return corpus


def count_hits(column: str, corpus: dict[Path, str]) -> tuple[int, list[Path]]:
    """
    Word-boundary search. Returns (count, up_to_5_example_files).
    """
    pat = re.compile(r"\b" + re.escape(column) + r"\b")
    total = 0
    examples: list[Path] = []
    for path, text in corpus.items():
        hits = len(pat.findall(text))
        if hits:
            total += hits
            if len(examples) < 5:
                examples.append(path)
    return total, examples


def column_near_table(column: str, table: str, corpus: dict[Path, str]) -> bool:
    """
    Return True if the column name and table name co-occur within ~200
    characters in any single file. Weak evidence the column is really
    referenced in its table's context (useful for common names).
    """
    col_pat = re.compile(r"\b" + re.escape(column) + r"\b")
    tbl_pat = re.compile(r"\b" + re.escape(table) + r"\b")
    for text in corpus.values():
        # Quick exclusion
        if column not in text or table not in text:
            continue
        col_positions = [m.start() for m in col_pat.finditer(text)]
        tbl_positions = [m.start() for m in tbl_pat.finditer(text)]
        for cp in col_positions:
            for tp in tbl_positions:
                if abs(cp - tp) <= 400:
                    return True
    return False


def main() -> None:
    print(f"Parsing schema: {SCHEMA}")
    tables = parse_schema(SCHEMA)
    total_cols = sum(len(c) for c in tables.values())
    print(f"  {len(tables)} tables, {total_cols} columns")

    print(f"Gathering source files from {USERAPP}")
    files = gather_source_files(USERAPP)
    print(f"  {len(files)} files")

    print("Reading files into memory...")
    corpus = build_search_corpus(files)
    print(f"  {len(corpus)} files read")

    print("Analyzing columns...")
    # Results per table:
    # - unused_distinctive: 0 hits, distinctive name  -> strong candidates
    # - unused_common: 0 hits, common name            -> weak (false-positive prone)
    # - unused_implicit: 0 hits, implicit/framework   -> ignore for dropping
    results: dict[str, dict[str, list[tuple[str, str, int, bool]]]] = {}

    for table, cols in sorted(tables.items()):
        tbuckets = {
            "unused_distinctive": [],
            "unused_common": [],
            "unused_implicit": [],
            "unused_sync_contract": [],
        }
        for col, sqltype in cols:
            hits, _examples = count_hits(col, corpus)
            if hits > 0:
                continue
            near = False
            if col.lower() in SYNC_CONTRACT_NAMES:
                tbuckets["unused_sync_contract"].append((col, sqltype, hits, near))
            elif col.lower() in IMPLICIT_NAMES:
                tbuckets["unused_implicit"].append((col, sqltype, hits, near))
            elif col.lower() in COMMON_NAMES:
                near = column_near_table(col, table, corpus)
                tbuckets["unused_common"].append((col, sqltype, hits, near))
            else:
                tbuckets["unused_distinctive"].append((col, sqltype, hits, near))
        results[table] = tbuckets

    # Summaries
    distinctive_total = sum(len(r["unused_distinctive"]) for r in results.values())
    common_total = sum(len(r["unused_common"]) for r in results.values())
    implicit_total = sum(len(r["unused_implicit"]) for r in results.values())
    sync_total = sum(len(r["unused_sync_contract"]) for r in results.values())
    sync_tables = [t for t, r in results.items() if r["unused_sync_contract"]]

    print(f"  Distinctive never-read:    {distinctive_total}")
    print(f"  Common-name never-read:    {common_total}")
    print(f"  Implicit never-read:       {implicit_total}")
    print(f"  Mobile-sync contract:      {sync_total} ({len(sync_tables)} tables)")

    # ---- Render markdown ----
    out = []
    out.append("# Unused Columns Audit — UserApp")
    out.append("")
    out.append("Date: 2026-04-09")
    out.append("")
    out.append("## What this is")
    out.append("")
    out.append(
        "Automated audit of every column in the `healthv10` schema "
        f"(`{SCHEMA.relative_to(REPO)}`) against the UserApp code base "
        "(`UserApp/` — excluding `.venv`, `__pycache__`, `.pytest_cache`, "
        "`.ruff_cache`). A column is **not read** if no word-boundary match "
        "appears in any `.py`, `.html`, `.js`, `.sql`, or template file."
    )
    out.append("")
    out.append("## Methodology")
    out.append("")
    out.append(
        "1. Parse `CREATE TABLE public.<name>` blocks from the schema file, "
        "extracting column names (skipping constraint/foreign-key lines).\n"
        "2. Walk `UserApp/` recursively; read every source/template file.\n"
        "3. For each column, count word-boundary (`\\b<name>\\b`) matches "
        "across the corpus.\n"
        "4. Columns with **zero hits** are reported.\n"
        "5. Results are partitioned into four buckets:\n"
        "   - **Mobile-sync contract (DO NOT DROP)** — `sqlite_id` and "
        "`synced_at`. Deliberate data contract for mobile SQLite \u2194 "
        "hosted PostgreSQL sync. Covered separately below with cohort "
        "warning.\n"
        "   - **Distinctive never-read** — high confidence the column is "
        "genuinely unused.\n"
        "   - **Common-name never-read** — generic names (`id`, `name`, "
        "`value`, etc.) where zero hits is suspicious; if `near_table=True` "
        "the name co-occurs with its table name within 400 chars in some "
        "file (weak positive evidence).\n"
        "   - **Implicit/framework never-read** — `tenant_id`, `created_at`, "
        "`updated_at`, `deleted_at`. These are typically maintained by DB "
        "defaults or triggers, so zero Python hits does not mean "
        "removable."
    )
    out.append("")
    out.append("## Known limitations (caveats before you delete anything)")
    out.append("")
    out.append(
        "- **`SELECT *`** — if a route reads a table with `SELECT *`, every "
        "column of that table is implicitly read, but this audit will still "
        "flag unreferenced names. Check the table's code path before "
        "dropping.\n"
        "- **Dynamic column access** — `row[col_name]` where `col_name` is a "
        "variable cannot be resolved statically; those reads are invisible "
        "here.\n"
        "- **DB-only use** — columns used only in SQL triggers or views "
        "are not visible to this UserApp-scoped audit.\n"
        "- **Migration / seed scripts** — some columns exist to support "
        "account provisioning and imports (e.g. `username`).\n"
        "- **Future / planned work** — columns added for features not yet "
        "wired up will look unused."
    )
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append(f"- Tables parsed: **{len(tables)}**")
    out.append(f"- Columns parsed: **{total_cols}**")
    out.append(
        f"- **Mobile-sync contract columns never read (DO NOT DROP): "
        f"{sync_total}** across {len(sync_tables)} tables"
    )
    out.append(f"- Distinctive columns never read: **{distinctive_total}**")
    out.append(f"- Common-name columns never read: **{common_total}**")
    out.append(f"- Implicit/framework columns never read: **{implicit_total}**")
    out.append("")
    out.append(
        "## \u26a0\ufe0f Mobile-sync contract columns \u2014 DO NOT DROP"
    )
    out.append("")
    out.append(
        "**The `sqlite_id` and `synced_at` columns listed below are part "
        "of a deliberate mobile-app data contract.** They exist to "
        "support SQLite \u2194 hosted PostgreSQL sync from the mobile "
        "app. They are legitimately unread in UserApp today because "
        "**bidirectional mobile sync is DEFERRED** (see "
        "[`Synchronization/2026-03-28-DECISION.md`]"
        "(../Synchronization/2026-03-28-DECISION.md) \u2014 blocked on "
        "per-user encryption). The current `/api/v1/healthkit/sync` "
        "handler in `UserApp/webapp/app.py` is write-only for `hkit_*`, "
        "`health_metrics`, and `health_blood_pressure_readings`, and "
        "does not populate `sqlite_id` / `synced_at` on any table."
    )
    out.append("")
    out.append(
        "**Do not drop these columns, do not drop their indexes "
        "(`idx_users_sqlite`, `idx_health_inputs_sqlite`, "
        "`idx_promotions_sqlite`), and do not classify them as dead "
        "code.** Coordinate with the mobile developer and re-read the "
        "Synchronization decision docs before making any schema change "
        "that touches these columns. Note the schema explicitly "
        "excludes `mobile_events` from this contract: \"Append-only "
        "event log. Server-authoritative (no sqlite_id, no mobile "
        "sync).\""
    )
    out.append("")
    if sync_tables:
        out.append(f"Tables in the mobile-sync cohort ({len(sync_tables)}):")
        out.append("")
        out.append("| Table | Sync columns unread |")
        out.append("|---|---|")
        for table in sorted(sync_tables):
            cols = results[table]["unused_sync_contract"]
            names = ", ".join(f"`{c}`" for c, _, _, _ in sorted(cols))
            out.append(f"| `{table}` | {names} |")
        out.append("")
    else:
        out.append("_None._")
        out.append("")

    out.append("## Top candidates: distinctive columns never read")
    out.append("")
    out.append(
        "Columns here are strong candidates for review because their names "
        "are distinctive enough that zero hits is unlikely to be a false "
        "positive."
    )
    out.append("")
    any_rows = False
    for table in sorted(results.keys()):
        dist = results[table]["unused_distinctive"]
        if not dist:
            continue
        any_rows = True
        out.append(f"### `{table}` ({len(dist)} columns)")
        out.append("")
        out.append("| Column | Type |")
        out.append("|---|---|")
        for col, sqltype, _, _ in sorted(dist):
            out.append(f"| `{col}` | {sqltype} |")
        out.append("")
    if not any_rows:
        out.append("_None._")
        out.append("")

    out.append("## Review bucket: common-name columns never read")
    out.append("")
    out.append(
        "Generic column names where direct grep found no hits. "
        "`near_table=yes` means the column and table names co-occur "
        "somewhere in the corpus within 400 characters (weak evidence "
        "the column *is* referenced in its table's context)."
    )
    out.append("")
    any_rows = False
    for table in sorted(results.keys()):
        common = results[table]["unused_common"]
        if not common:
            continue
        any_rows = True
        out.append(f"### `{table}`")
        out.append("")
        out.append("| Column | Type | near_table |")
        out.append("|---|---|---|")
        for col, sqltype, _, near in sorted(common):
            out.append(f"| `{col}` | {sqltype} | {'yes' if near else 'no'} |")
        out.append("")
    if not any_rows:
        out.append("_None._")
        out.append("")

    out.append("## Implicit / framework columns never read")
    out.append("")
    out.append(
        "These are `tenant_id`, `created_at`, `updated_at`, `deleted_at`. "
        "Zero direct hits is usually benign — they're set by DB defaults, "
        "maintained by triggers, or serialized generically. **Do not drop "
        "these based on this report.**"
    )
    out.append("")
    any_rows = False
    for table in sorted(results.keys()):
        impl = results[table]["unused_implicit"]
        if not impl:
            continue
        any_rows = True
        out.append(f"- **{table}**: {', '.join(f'`{c}`' for c, _, _, _ in sorted(impl))}")
    if not any_rows:
        out.append("_None._")
    out.append("")

    out.append("## Tables with NO unused columns")
    out.append("")
    clean = [t for t in sorted(results.keys())
             if not any(results[t][k] for k in results[t])]
    if clean:
        out.append(", ".join(f"`{t}`" for t in clean))
    else:
        out.append("_None — every table has at least one zero-hit column._")
    out.append("")
    out.append("## How to re-run this audit")
    out.append("")
    out.append(
        "```\n"
        "python3 DataModel3/unused_columns_audit.py\n"
        "```\n\n"
        "The script re-parses the schema and re-scans `UserApp/` each "
        "time and overwrites this report. Adjust `SCHEMA`, `USERAPP`, "
        "and `SKIP_DIRS` constants at the top of the script to retarget "
        "it at another service tree."
    )
    out.append("")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(out))
    print(f"Wrote: {REPORT}")


if __name__ == "__main__":
    main()
