#!/usr/bin/env python3
"""user_scope_audit.py — Phase 2 per-user scoping audit (Open Decision 1).

Home Edition removed RLS; privacy is enforced at the application level.
Under RLS, a query missing a user_id filter still returned only the
caller's rows. Without RLS it returns the whole household's rows.

This audit flags every SQL statement in UserApp/webapp that touches a
user-owned table (a table with a user_id column in the home schema)
without constraining user_id in a predicate (WHERE / JOIN ON) — or,
for INSERTs, without user_id in the inserted column list.

Flagged sites are a triage worklist, not automatic bugs: lookups by a
globally-unique key (e.g. sessions by session_id) and deliberate
household-shared reads are acceptable, but each must be a conscious
exception.

Usage:
    .venv/bin/python scripts/user_scope_audit.py [--json]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# sqlglot exposes Expression through `exp` but omits it from
# expressions.__all__ (a sqlglot packaging gap), so pyright flags
# exp.Expression as a private import. It is sqlglot's documented public
# base class; suppress just that rule in this file.
# pyright: reportPrivateImportUsage=false
import sqlglot
from sqlglot import exp

from DataModel3.code_query_audit import (  # noqa: E402
    SCHEMA,
    SqlSite,
    extract_sites,
    gather_python_files,
    parse_schema,
)

SCAN_ROOT = REPO / "UserApp" / "webapp"
# tests/ mock the DB; livetest and scripts are operator tools, not handlers.
EXCLUDE_PARTS = {"tests"}

DML_HEADS = ("select", "insert", "update", "delete", "with")


def statement_tables(stmt: exp.Expression) -> set[str]:
    """All real table names referenced by the statement (lowercase)."""
    names: set[str] = set()
    for t in stmt.find_all(exp.Table):
        if t.name:
            names.add(t.name.lower())
    return names


def predicate_mentions_user_id(stmt: exp.Expression) -> bool:
    """True if a user_id column appears inside any WHERE / JOIN ON / HAVING."""
    predicate_roots: list[exp.Expression] = []
    predicate_roots.extend(stmt.find_all(exp.Where))
    predicate_roots.extend(stmt.find_all(exp.Having))
    for j in stmt.find_all(exp.Join):
        on = j.args.get("on")
        if on is not None:
            predicate_roots.append(on)
    for root in predicate_roots:
        for col in root.find_all(exp.Column):
            if col.name and col.name.lower() == "user_id":
                return True
    return False


def insert_carries_user_id(stmt: exp.Insert) -> bool:
    """True if the INSERT column list includes user_id."""
    target = stmt.this
    if isinstance(target, exp.Schema):  # INSERT INTO t (col, ...)
        for col in target.expressions:
            if col.name and col.name.lower() == "user_id":
                return True
        return False
    # INSERT INTO t VALUES (...) with no column list — cannot verify.
    return False


def audit_site(site: SqlSite, user_tables: set[str]) -> list[dict]:
    flags: list[dict] = []
    sql = site.sql.strip()
    if not sql or not sql.lstrip().lower().startswith(DML_HEADS):
        return flags
    try:
        stmts = sqlglot.parse(sql, dialect="postgres")
    except Exception:
        return [{
            "file": site.file, "line": site.line, "kind": "parse_error",
            "tables": [], "sql": sql[:160],
        }]
    for stmt in stmts:
        if stmt is None:
            continue
        touched = statement_tables(stmt) & user_tables
        if not touched:
            continue
        if isinstance(stmt, exp.Insert):
            select_part = stmt.expression
            has_select_tables = (
                select_part is not None
                and bool(statement_tables(select_part) & user_tables)
            )
            if insert_carries_user_id(stmt):
                # INSERT ... SELECT over user tables still needs a
                # user_id predicate on the SELECT side.
                if has_select_tables and not predicate_mentions_user_id(stmt):
                    flags.append({
                        "file": site.file, "line": site.line,
                        "kind": "insert_select_unscoped",
                        "tables": sorted(touched), "sql": sql[:160],
                    })
                continue
            flags.append({
                "file": site.file, "line": site.line,
                "kind": "insert_missing_user_id",
                "tables": sorted(touched), "sql": sql[:160],
            })
        else:
            if not predicate_mentions_user_id(stmt):
                flags.append({
                    "file": site.file, "line": site.line,
                    "kind": f"{stmt.key}_unscoped",
                    "tables": sorted(touched), "sql": sql[:160],
                })
    return flags


# ---------------------------------------------------------------------------
# Function-level scan (catches composed sql.SQL().format() queries that the
# per-statement check above cannot resolve statically).
#
# The per-statement audit parses each .execute() SQL literal. Home Edition's
# leaking handlers (PotentialRLSBug.md) build their query as
#   sql.SQL("... FROM <user_table> {where} ...").format(where=where_sql)
# where the user_id predicate, when present, lives in a SEPARATE `conditions`
# list fragment. The execute argument therefore resolves to "" (unresolvable)
# and is silently skipped. Worse, unwrapping it would not help: both the buggy
# and the fixed handler share the identical `{where}` template, so an
# execute-arg check cannot tell them apart.
#
# The discriminating signal is at the function level: a request handler that
# runs SQL against a user-owned table but never mentions `user_id` ANYWHERE in
# its body is the leak. The fixed handler references `user_id` (in the
# `conditions` fragment and via get_user_id()); the buggy one does not.
# ---------------------------------------------------------------------------

EXECUTE_METHODS = {"execute", "executemany"}
# Captures the table after FROM / JOIN / INTO / UPDATE in concatenated SQL
# fragments (handles an optional `public.` schema and quoting).
TABLE_REF_RE = re.compile(r'\b(?:from|join|into|update)\s+(?:public\.)?"?(\w+)"?',
                          re.IGNORECASE)


def _own_scope_children(func: ast.AST) -> list[ast.AST]:
    """Nodes inside ``func`` excluding nested def/lambda scopes."""
    out: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return
        out.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for child in ast.iter_child_nodes(func):
        visit(child)
    return out


def audit_handlers(file: Path, user_tables: set[str], source: str,
                   tree: ast.Module) -> list[dict]:
    """Flag handler functions that query a user-owned table with no user_id."""
    flags: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        scope = _own_scope_children(node)
        has_execute = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in EXECUTE_METHODS
            for n in scope
        )
        if not has_execute:
            continue
        sql_text = "\n".join(
            n.value for n in scope
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )
        touched = {m.group(1).lower() for m in TABLE_REF_RE.finditer(sql_text)}
        touched &= user_tables
        if not touched:
            continue
        # Source segment includes identifiers (get_user_id()) and comments,
        # not just string literals — the broadest check for an owner predicate.
        segment = ast.get_source_segment(source, node) or sql_text
        if "user_id" in segment:
            continue
        flags.append({
            "file": str(file), "line": node.lineno,
            "kind": "handler_no_user_id", "func": node.name,
            "tables": sorted(touched),
            "sql": sql_text[:160].replace(chr(10), " "),
        })
    return flags


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    tables, _ = parse_schema(SCHEMA)
    user_tables = {t for t, cols in tables.items() if "user_id" in cols}

    files = [
        f for f in gather_python_files(SCAN_ROOT)
        if not (EXCLUDE_PARTS & set(f.parts))
    ]

    all_flags: list[dict] = []
    n_sites = 0
    n_unresolved = 0
    for f in sorted(files):
        for site in extract_sites(f):
            n_sites += 1
            # Composed psycopg sql.SQL().format() queries resolve to "" — the
            # per-statement check can't see them. Count them so a clean
            # per-statement run no longer implies "everything was verified".
            if site.kind in ("unresolvable", "dynamic", "fstring") or not site.sql.strip():
                n_unresolved += 1
            all_flags.extend(audit_site(site, user_tables))
        # Function-level pass — the catch for composed, unresolvable queries.
        try:
            source = f.read_text()
            tree = ast.parse(source, filename=str(f))
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue
        all_flags.extend(audit_handlers(f, user_tables, source, tree))

    if args.json:
        print(json.dumps(all_flags, indent=2))
    else:
        by_file: dict[str, list[dict]] = {}
        for fl in all_flags:
            by_file.setdefault(fl["file"], []).append(fl)
        for path in sorted(by_file):
            rel = str(Path(path).relative_to(REPO))
            print(f"\n{rel}")
            for fl in sorted(by_file[path], key=lambda x: x["line"]):
                label = fl["kind"]
                if fl.get("func"):
                    label = f"{fl['kind']} ({fl['func']})"
                print(f"  L{fl['line']:<5} {label:<40} "
                      f"{','.join(fl['tables'])}")
                print(f"         {fl['sql'][:120].replace(chr(10), ' ')}")
        print(f"\n{len(all_flags)} flagged statement(s) "
              f"across {len(by_file)} file(s); "
              f"{n_sites} SQL sites scanned "
              f"({n_unresolved} composed/dynamic — not statically verifiable, "
              f"covered by the function-level handler_no_user_id check); "
              f"{len(user_tables)} user-owned tables in schema.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
