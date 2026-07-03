#!/usr/bin/env python3
"""
Code SQL Usage Audit — verify every SQL string in the codebase against the
schema source of truth at Infrastructure/init/docker-init-home/02-home_schema.sql.

Doctrine: DataModel3/CodeQueryAudit.md
Stack:    Python ast (extract SQL from .execute() calls)
        + sqlglot (parse SQL, extract tables/columns)

Usage:
    python3 DataModel3/code_query_audit.py --service UserApp
    python3 DataModel3/code_query_audit.py --service UserApp --json
    python3 DataModel3/code_query_audit.py --service UserApp --out /tmp/x.md
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import sqlglot
from sqlglot import exp

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "Infrastructure/init/docker-init-home/02-home_schema.sql"
ROUTE_AUDIT_ALLOWLIST = REPO / "Compliance/route-audit-allowlist.md"
SENSITIVE_WRITES_INVENTORY = REPO / "Compliance/sensitive-write-sites.md"

EXECUTE_METHODS = {"execute", "executemany", "executescript"}

SKIP_DIRS = {".venv", ".pytest_cache", ".ruff_cache", "__pycache__",
             "logs", "node_modules", ".git", "_optivault", ".codesight"}

# ---------------------------------------------------------------------------
# Track 3 (SecurityHardening.md) — Flask-route AST rules
# ---------------------------------------------------------------------------
#
# Detection vocabulary. Decorators that imply the route enforces auth at the
# function level — used by Rules 1 (CSRF) and 3 (rate limit on unauth routes).
# These match the actual decorator names in the codebase as of 2026-04-29.
AUTH_DECORATORS = {
    "require_auth", "require_bearer_token", "require_api_key",
    "admin_required",
}

# Decorators that opt a route out of the CSRF requirement either by enforcing
# CSRF (csrf.protect) or by explicitly exempting (csrf_exempt, csrf.exempt).
CSRF_OPT_DECORATORS = {
    "csrf.protect", "csrf.exempt", "csrf_exempt", "csrf_required",
}

# Mutating HTTP methods. `OPTIONS` and `HEAD` are read-side, never CSRF
# targets — Flask handles them automatically anyway.
MUTATING_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Column-name pattern that triggers Rule 4 (sensitive-write must be in the
# Track 1 inventory). Liberal substring match — the spec in SecurityHardening.md
# says `*_password|*_secret|*_token` strictly, but any column with these
# words is on the credential path and either holds the secret, its hash, or
# metadata that should be touched only by the canonical writers. False
# positives on metadata columns (e.g. `password_changed_at`) are absorbed
# by the writing function being listed in the inventory anyway, so the
# precision comes from the function-must-be-listed check, not the column
# pattern.
SENSITIVE_COL_PATTERN = re.compile(r"password|secret|token", re.IGNORECASE)

# Track 7 (SecurityHardening.md) — schema column-comment crypto contract.
# Every sensitive-pattern column must carry a COMMENT ON COLUMN with an
# ``algo: <name>`` annotation. Recognised values:
#
#   argon2id, bcrypt, sha256          - hash algorithms
#   fernet, aes-gcm                   - symmetric encryption
#   plaintext                         - intentionally plaintext
#   tbd                               - placeholder for not-yet-encrypted
#                                        sensitive material (the F2 case)
#   not-a-credential                  - column name matched the pattern
#                                        but the value isn't a credential
#
# See the schema-end "Security column annotations" section for the full
# rationale of each entry.
RECOGNISED_ALGOS = frozenset({
    "argon2id", "bcrypt", "sha256", "fernet", "aes-gcm",
    "plaintext", "tbd", "not-a-credential",
})

# Algo string is matched after `algo:` in the comment text. Liberal
# whitespace handling so comments stay readable.
_ALGO_RE = re.compile(r"\balgo:\s*([a-z0-9_-]+)", re.IGNORECASE)


def extract_crypto_contract_findings(
    tables: dict[str, set[str]],
    column_comments: dict[tuple[str, str], str],
) -> list[Finding]:
    """Track 7 — every sensitive-pattern column must declare its algo.

    Two error categories:
      * ``crypto_contract_missing`` — column matches the sensitive-name
        pattern and has no ``COMMENT ON COLUMN`` with an ``algo:`` directive.
      * ``crypto_contract_unknown`` — column has an ``algo:`` directive but
        the value is outside ``RECOGNISED_ALGOS``. Catches typos and
        undeclared algorithms.

    Both are errors. Schema-side fixes are cheap (one COMMENT statement)
    and the contract gates the eventual F2 encryption work — Rule 4 will
    bind helper-vs-algo against this annotation in a follow-up commit.
    """
    findings: list[Finding] = []
    schema_path = str(SCHEMA.relative_to(REPO))
    for table in sorted(tables):
        for col in sorted(tables[table]):
            if not SENSITIVE_COL_PATTERN.search(col):
                continue
            comment = column_comments.get((table, col))
            if comment is None:
                findings.append(Finding(
                    "error", "crypto_contract_missing",
                    f"Sensitive-pattern column '{table}.{col}' has no "
                    f"COMMENT ON COLUMN. Add an `algo: <name>` annotation "
                    f"per SecurityHardening.md Track 7.",
                    schema_path, 0,
                ))
                continue
            algo = parse_algo_from_comment(comment)
            if algo is None:
                findings.append(Finding(
                    "error", "crypto_contract_missing",
                    f"Sensitive-pattern column '{table}.{col}' has a "
                    f"comment but no `algo:` directive. Add one per "
                    f"SecurityHardening.md Track 7.",
                    schema_path, 0,
                ))
                continue
            if algo not in RECOGNISED_ALGOS:
                findings.append(Finding(
                    "error", "crypto_contract_unknown",
                    f"Sensitive-pattern column '{table}.{col}' declares "
                    f"`algo: {algo}` which is not in the recognised set "
                    f"{sorted(RECOGNISED_ALGOS)}. Either correct the algo "
                    f"or extend RECOGNISED_ALGOS in code_query_audit.py.",
                    schema_path, 0,
                ))
    return findings

# System-catalog schemas. Tables here are not part of the application schema
# and must not be flagged. Match by the exact bare-table names that appear in
# information_schema and pg_catalog — sqlglot strips the schema qualifier and
# only the table name reaches us.
SYSTEM_TABLES = {
    # information_schema views
    "tables", "columns", "schemata", "views", "table_constraints",
    "key_column_usage", "referential_constraints", "check_constraints",
    "role_table_grants", "table_privileges", "column_privileges",
    "routines", "parameters", "triggers",
    # pg_catalog views and tables
    "pg_class", "pg_indexes", "pg_policy", "pg_roles", "pg_database",
    "pg_namespace", "pg_attribute", "pg_constraint", "pg_index",
    "pg_tables", "pg_views", "pg_settings", "pg_stat_activity",
    "pg_stat_user_tables", "pg_stat_user_indexes", "pg_stat_replication",
    "pg_locks", "pg_extension", "pg_proc", "pg_type", "pg_trigger",
}

# Per CodeQueryAudit.md doctrine: MCP services are HTTP proxies and must
# never open a database connection directly. Any SQL site here is an error.
MCP_SERVICE_ROOTS = {"UserMCP"}


@dataclass
class SqlSite:
    file: str
    line: int
    sql: str
    kind: str  # static | fstring | concat | name_resolved | dynamic | unresolvable
    notes: list[str] = field(default_factory=list)


@dataclass
class Finding:
    severity: str  # error | warning | info
    category: str
    message: str
    file: str
    line: int


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def parse_schema(path: Path) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return ({table: {columns}}, {column: {tables_having_it}}) — both lowercase."""
    text = path.read_text()
    tables: dict[str, set[str]] = {}
    pattern = re.compile(
        r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+public\.(\w+)\s*\((.*?)\n\);",
        re.DOTALL,
    )
    # Trailing space on "exclude " is load-bearing: it matches the
    # `EXCLUDE [USING method] (...)` and `EXCLUDE (...)` constraint syntax
    # without false-matching a real column literally named `excludes`
    # (e.g. diet_catalog.excludes JSONB). PostgreSQL's grammar allows
    # `EXCLUDE(...)` without space too — that variant fails the column-
    # token regex below and is dropped harmlessly.
    skip_starts = ("primary key", "foreign key", "unique", "constraint",
                   "check (", "check(", "like ", "exclude ", ")")
    for m in pattern.finditer(text):
        table = m.group(1).lower()
        body = m.group(2)
        # Strip line-end comments BEFORE the comma split — otherwise commas
        # inside `-- foo, bar, baz` comments cause the split to slice the
        # following column line in half (losing it). This bug is also present
        # in unused_columns_audit.py but is symmetric there so it self-cancels.
        body = re.sub(r"--[^\n]*", "", body)
        # Top-level comma split (parens-aware).
        depth, buf, lines = 0, [], []
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
        cols: set[str] = set()
        for raw in lines:
            line = re.sub(r"--.*$", "", raw, flags=re.MULTILINE).strip()
            if not line or line.lower().startswith(skip_starts):
                continue
            # Match column name only — don't anchor with `$`. Some columns
            # have continuation lines (e.g. `status TEXT DEFAULT 'scheduled'`
            # followed by `CHECK (status IN (...))` on the next line). A
            # `$`-anchored regex with `.*` won't match because `.` doesn't
            # cross newlines and `$` won't reach end-of-string.
            tok = re.match(r'^"?(\w+)"?\s', line)
            if not tok:
                continue
            col = tok.group(1).lower()
            if col in {"primary", "foreign", "unique", "constraint",
                       "check", "like", "exclude"}:
                continue
            cols.add(col)
        if cols:
            tables[table] = cols
    col_index: dict[str, set[str]] = {}
    for t, cs in tables.items():
        for c in cs:
            col_index.setdefault(c, set()).add(t)
    return tables, col_index


def parse_column_comments(path: Path) -> dict[tuple[str, str], str]:
    """Extract every ``COMMENT ON COLUMN public.<table>.<col> IS '<text>';``.

    Returns a dict keyed by (table, column) — both lowercased — with the
    raw comment text as value (unquoted, escapes preserved literally).

    Supports both single-line and continued forms; gives up on truly
    multi-line strings since the schema in this repo doesn't use them.
    """
    text = path.read_text()
    out: dict[tuple[str, str], str] = {}
    pattern = re.compile(
        r"COMMENT\s+ON\s+COLUMN\s+public\.(\w+)\.(\w+)\s+IS\s+"
        r"'((?:[^']|'')*)';",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        table = m.group(1).lower()
        col = m.group(2).lower()
        body = m.group(3).replace("''", "'")
        out[(table, col)] = body
    return out


def parse_algo_from_comment(comment: str) -> str | None:
    """Return the algo name declared in `algo: <name>`, or None if missing.

    The match is case-insensitive on the directive but the returned algo
    is normalised to lowercase so callers can do set-membership tests
    against ``RECOGNISED_ALGOS`` without re-casing.
    """
    m = _ALGO_RE.search(comment)
    if m is None:
        return None
    return m.group(1).lower()


# ---------------------------------------------------------------------------
# AST extraction
# ---------------------------------------------------------------------------

def resolve_sql_arg(
    node: ast.AST,
    env: dict[str, ast.AST],
    depth: int = 0,
) -> tuple[str | None, str, list[str]]:
    """Best-effort resolution of the first arg to .execute().

    Returns (sql_text_or_None, kind, notes). For dynamic kinds (fstring,
    concat-with-dynamic, .format()), sql_text may be a partial reconstruction
    where interpolated identifiers are replaced with the safe placeholder %s
    so sqlglot can still parse the literal portions.
    """
    notes: list[str] = []
    if depth > 4:
        return None, "unresolvable", ["resolution depth exceeded"]

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, "static", notes

    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        has_interp = False
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                has_interp = True
                parts.append("%s")
        sql = "".join(parts)
        return (sql, "fstring" if has_interp else "static", notes)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        l_sql, l_kind, _ = resolve_sql_arg(node.left, env, depth + 1)
        r_sql, r_kind, _ = resolve_sql_arg(node.right, env, depth + 1)
        if l_sql is not None and r_sql is not None:
            kind = "concat" if (l_kind == "static" and r_kind == "static") else "dynamic"
            return l_sql + r_sql, kind, notes
        return None, "unresolvable", ["mixed-type concatenation"]

    if isinstance(node, ast.Name) and node.id in env:
        sql, kind, sub_notes = resolve_sql_arg(env[node.id], env, depth + 1)
        if sql is not None and kind == "static":
            return sql, "name_resolved", sub_notes
        return sql, kind, sub_notes

    if isinstance(node, ast.Call):
        # query.format(...) — parse the template; interpolations are dynamic.
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            base, _, _ = resolve_sql_arg(node.func.value, env, depth + 1)
            if base is not None:
                # Replace {} / {name} placeholders with %s.
                template = re.sub(r"\{[^{}]*\}", "%s", base)
                return template, "dynamic", ["template uses .format()"]
        return None, "unresolvable", ["call expression"]

    return None, "unresolvable", [f"node type {type(node).__name__}"]


def collect_assignments(scope: ast.AST) -> dict[str, ast.AST]:
    """Collect last value assigned to each Name target in a scope.

    Walks into nested control-flow blocks (if/while/for/try/with) so that
    `query = "..."` inside a `while True: try:` block is visible to a later
    `cur.execute(query)` in the same function. Stops at nested function /
    class / lambda boundaries — those are separate scopes handled by
    find_call_envs.

    When a name is assigned multiple times across branches, the last
    visited assignment wins. This is best-effort: a true union of
    branch-conditional values would require a full dataflow analysis.
    """
    env: dict[str, ast.AST] = {}

    def visit(node: ast.AST) -> None:
        # New scopes — don't pull their internal assignments into ours.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            return
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    env[tgt.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            env[node.target.id] = node.value
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            # query += "..." — keep the rhs but mark dynamic later.
            existing = env.get(node.target.id)
            if existing is not None:
                env[node.target.id] = ast.BinOp(left=existing, op=ast.Add(), right=node.value)
        for child in ast.iter_child_nodes(node):
            visit(child)

    body = getattr(scope, "body", None)
    if body is None:
        return env
    for stmt in body:
        visit(stmt)
    return env


def find_call_envs(tree: ast.Module) -> dict[int, dict[str, ast.AST]]:
    """For every Call node, compute the merged (module + enclosing-fn) env."""
    module_env = collect_assignments(tree)
    call_envs: dict[int, dict[str, ast.AST]] = {}

    def visit(node: ast.AST, env: dict[str, ast.AST]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            new_env = dict(env)
            new_env.update(collect_assignments(node))
            for child in ast.iter_child_nodes(node):
                visit(child, new_env)
            return
        if isinstance(node, ast.Call):
            call_envs[id(node)] = env
        for child in ast.iter_child_nodes(node):
            visit(child, env)

    visit(tree, module_env)
    return call_envs


def extract_sites(file: Path) -> list[SqlSite]:
    sites: list[SqlSite] = []
    try:
        text = file.read_text()
        tree = ast.parse(text, filename=str(file))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return sites

    call_envs = find_call_envs(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in EXECUTE_METHODS:
            continue
        if not node.args:
            continue
        env = call_envs.get(id(node), {})
        sql, kind, notes = resolve_sql_arg(node.args[0], env)
        sites.append(SqlSite(
            file=str(file),
            line=node.lineno,
            sql=sql or "",
            kind=kind,
            notes=notes,
        ))
    return sites


# ---------------------------------------------------------------------------
# SQL verification
# ---------------------------------------------------------------------------

def verify_sql(
    site: SqlSite,
    tables: dict[str, set[str]],
    col_index: dict[str, set[str]],
    referenced_tables: set[str] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []

    if site.kind == "unresolvable":
        findings.append(Finding(
            "info", "unresolvable_sql",
            f"SQL argument could not be resolved statically ({'; '.join(site.notes) or 'opaque'})",
            site.file, site.line,
        ))
        return findings

    if site.kind in ("fstring", "dynamic"):
        findings.append(Finding(
            "info", "dynamic_sql",
            f"Dynamic SQL ({site.kind}); only literal portions verified",
            site.file, site.line,
        ))
        # Continue and verify what we can.

    if not site.sql.strip():
        return findings

    # Skip non-DML utility statements that have nothing to verify.
    head = site.sql.lstrip().lower()[:32]
    if head.startswith(("set ", "begin", "commit", "rollback", "savepoint",
                        "release", "show ", "vacuum", "analyze", "listen",
                        "notify", "explain", "reset ")):
        return findings
    # Skip DDL — it's verified by the schema-vs-prod audit (step 1 of the chain).
    if head.startswith(("create ", "alter ", "drop ", "truncate ", "comment ",
                        "grant ", "revoke ", "refresh ")):
        return findings

    try:
        stmts = sqlglot.parse(site.sql, dialect="postgres")
    except Exception as e:
        findings.append(Finding(
            "info", "parse_error",
            f"sqlglot could not parse: {type(e).__name__}: {str(e)[:120]}",
            site.file, site.line,
        ))
        return findings

    for stmt in stmts:
        if stmt is None:
            continue
        findings.extend(_verify_statement(stmt, site, tables, col_index, referenced_tables))
    return findings


def _verify_statement(
    stmt: exp.Expression,  # pyright: ignore[reportPrivateImportUsage]
    site: SqlSite,
    tables: dict[str, set[str]],
    col_index: dict[str, set[str]],
    referenced_tables: set[str] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []

    # Build alias map and collect referenced table names.
    alias_to_real: dict[str, str] = {}
    seen_tables: set[str] = set()
    for t in stmt.find_all(exp.Table):
        real = t.name.lower()
        if not real:
            continue
        seen_tables.add(real)
        if referenced_tables is not None and real in tables:
            referenced_tables.add(real)
        alias_to_real[real] = real
        if t.alias:
            alias_to_real[t.alias.lower()] = real

    # CTE names are referenced as Tables but aren't real tables.
    cte_names = {c.alias_or_name.lower() for c in stmt.find_all(exp.CTE)}

    # Set table refs.
    for tname in sorted(seen_tables):
        if tname in cte_names:
            continue
        if tname in SYSTEM_TABLES:
            continue
        if tname.startswith(("pg_", "information_schema_")):
            continue
        # Placeholder identifier from a dynamic-SQL substitution we recorded
        # earlier as info; avoid double-reporting structural errors against it.
        if tname in {"?", "%", "%s", "$1"} or len(tname) <= 1:
            continue
        if tname not in tables:
            findings.append(Finding(
                "error", "missing_table",
                f"Table '{tname}' not in schema",
                site.file, site.line,
            ))

    # SELECT * surfaces.
    for s in stmt.find_all(exp.Star):
        # Only flag the outermost SELECT *, not e.g. count(*).
        parent = s.parent
        if isinstance(parent, exp.Count):
            continue
        findings.append(Finding(
            "warning", "select_star",
            "SELECT * — column-level checks cannot cover this site",
            site.file, site.line,
        ))
        break  # one warning per statement

    # INSERT INTO target (cols).
    if isinstance(stmt, exp.Insert):
        tgt = stmt.this
        target_table: str | None = None
        insert_cols: list[str] = []
        if isinstance(tgt, exp.Schema):
            inner = tgt.this
            if isinstance(inner, exp.Table):
                target_table = inner.name.lower()
            insert_cols = [
                i.name.lower() for i in tgt.expressions
                if isinstance(i, exp.Identifier)
            ]
        elif isinstance(tgt, exp.Table):
            target_table = tgt.name.lower()
        if target_table and target_table in tables:
            schema_cols = tables[target_table]
            for c in insert_cols:
                if c not in schema_cols:
                    findings.append(Finding(
                        "error", "missing_column",
                        f"INSERT into '{target_table}': column '{c}' not in schema",
                        site.file, site.line,
                    ))

    # UPDATE target SET col = ...
    if isinstance(stmt, exp.Update):
        tgt = stmt.this
        target_table = tgt.name.lower() if isinstance(tgt, exp.Table) else None
        if target_table and target_table in tables:
            schema_cols = tables[target_table]
            for assign in (stmt.expressions or []):
                # SET col = expr  →  EQ(this=Column(col), expression=expr)
                if isinstance(assign, exp.EQ) and isinstance(assign.this, exp.Column):
                    c = assign.this.name.lower()
                    if c not in schema_cols:
                        findings.append(Finding(
                            "error", "missing_column",
                            f"UPDATE '{target_table}': SET column '{c}' not in schema",
                            site.file, site.line,
                        ))

    # General column reference checks (covers SELECT, WHERE, JOIN ON, ORDER BY, etc.).
    real_from_tables = {alias_to_real.get(t, t) for t in seen_tables} - cte_names
    real_from_tables_in_schema = {t for t in real_from_tables if t in tables}

    # If the statement has CTEs or nested SELECTs, derived columns can appear
    # that won't exist on any base table. Static scope analysis to resolve
    # those is out of scope for this audit; we suppress unqualified-column
    # errors in that case (qualified errors and INSERT/UPDATE checks still fire).
    has_derived_scope = (
        bool(cte_names)
        or any(
            isinstance(s, exp.Select) and s is not stmt
            for s in stmt.find_all(exp.Select)
        )
    )

    # Collect SELECT-list aliases — `count(*) AS cnt` makes `cnt` a valid
    # unqualified reference in HAVING / ORDER BY (and in WHERE under Postgres).
    select_aliases: set[str] = set()
    for proj in stmt.find_all(exp.Alias):
        if proj.alias:
            select_aliases.add(proj.alias.lower())

    for col in stmt.find_all(exp.Column):
        cname = col.name.lower()
        if not cname or cname == "*":
            continue
        tref = col.table.lower() if col.table else ""

        if tref:
            real = alias_to_real.get(tref, tref)
            if real in cte_names:
                continue  # column on a CTE — out of scope for static check
            if real in tables and cname not in tables[real]:
                findings.append(Finding(
                    "error", "missing_column",
                    f"Column '{real}.{cname}' not in schema",
                    site.file, site.line,
                ))
        else:
            # Unqualified column. Only flag if it doesn't exist on ANY of the
            # FROM tables (and at least one FROM table is in schema).
            if not real_from_tables_in_schema:
                continue
            if has_derived_scope:
                continue  # could be from a CTE or subquery — out of scope
            if cname in select_aliases:
                continue  # SELECT-list alias used in ORDER BY / HAVING / WHERE
            tables_with_col = col_index.get(cname, set())
            if not (tables_with_col & real_from_tables_in_schema):
                if cname not in col_index:
                    findings.append(Finding(
                        "error", "missing_column",
                        f"Unqualified column '{cname}' not in schema "
                        f"(from {sorted(real_from_tables_in_schema)})",
                        site.file, site.line,
                    ))

    return findings


# ---------------------------------------------------------------------------
# Track 3 — Flask-route AST rules
# ---------------------------------------------------------------------------

def _decorator_name(d: ast.AST) -> str | None:
    """Return the dotted name of a decorator, or None if not resolvable.

    Handles `@require_auth`, `@bp.route(...)`, `@limiter.limit('5/min')`,
    `@app.foo.bar`. The Call wrapper is unwrapped so `@route('/x')` and
    `@route` both yield 'route'.
    """
    if isinstance(d, ast.Call):
        d = d.func
    if isinstance(d, ast.Name):
        return d.id
    if isinstance(d, ast.Attribute):
        parts: list[str] = []
        cur: ast.AST = d
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
    return None


def _is_route_decorator(d: ast.AST) -> bool:
    """Detect `@bp.route(...)`, `@app.route(...)`, `@something.route(...)`."""
    if isinstance(d, ast.Call):
        d = d.func
    return isinstance(d, ast.Attribute) and d.attr == "route"


def _extract_methods(d: ast.AST) -> set[str]:
    """Return uppercased HTTP methods from a `@route(..., methods=[...])`.

    Defaults to {'GET'} (Flask's implicit default) if no methods kwarg.
    """
    if not isinstance(d, ast.Call):
        return {"GET"}
    for kw in d.keywords:
        if kw.arg == "methods" and isinstance(kw.value, ast.List):
            return {
                e.value.upper()
                for e in kw.value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
    return {"GET"}


def _file_has_blueprint_before_request(tree: ast.Module) -> bool:
    """Detect `bp.before_request(fn)` or `@bp.before_request` in the same file.

    Some blueprint files don't decorate routes individually but rely on
    a same-file `before_request` hook to populate the auth context. This
    is common in Flask blueprints. When detected, the file's mutating
    routes are presumed-protected for Rule 1/3 purposes (still flagged if
    they have no rate limit, but not the 'no_auth' branch).

    A cross-file `@app.before_request` blanket auth hook is *not*
    detected here — the allowlist absorbs that case via a `dir:` entry.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "before_request":
                return True
        if isinstance(node, ast.FunctionDef):
            for d in node.decorator_list:
                if isinstance(d, ast.Attribute) and d.attr == "before_request":
                    return True
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "before_request":
                    return True
    return False


def _enclosing_func_name(tree: ast.Module, target: ast.AST) -> str | None:
    """Find the FunctionDef that contains `target` in `tree`."""
    target_id = id(target)

    def visit(node: ast.AST, current_func: str | None) -> str | None:
        if id(node) == target_id:
            return current_func
        if isinstance(node, ast.FunctionDef):
            current_func = node.name
        for child in ast.iter_child_nodes(node):
            found = visit(child, current_func)
            if found is not None:
                return found
        return None

    return visit(tree, None)


# ---------------------------------------------------------------------------
# Allowlist + inventory parsing
# ---------------------------------------------------------------------------

# Allowlist scopes — each row in the markdown table has a Scope cell whose
# value is one of these forms:
#   func:<file_path>:<func_name>
#   file:<file_path>
#   dir:<directory_path>/
# Paths are repo-relative.

@dataclass
class AllowlistEntry:
    rule: str   # rule id (e.g. 'csrf_missing', 'send_from_dir_root', ...)
    scope: str  # raw scope string from the markdown
    reason: str
    tracked_in: str

    def matches(self, rel_file: str, func_name: str | None) -> bool:
        s = self.scope
        if s.startswith("func:"):
            try:
                _, fpath, fname = s.split(":", 2)
            except ValueError:
                return False
            return rel_file == fpath and func_name == fname
        if s.startswith("file:"):
            return rel_file == s[len("file:"):]
        if s.startswith("dir:"):
            d = s[len("dir:"):]
            if not d.endswith("/"):
                d = d + "/"
            return rel_file.startswith(d)
        return False


def _parse_markdown_tables(text: str) -> list[tuple[str, list[dict[str, str]]]]:
    """Return [(section_heading, rows)] for every markdown table preceded by a heading.

    Each row is a dict mapping header cell to value cell. Cells are stripped of
    surrounding whitespace; markdown formatting (backticks, [text](url)) is left
    intact for the caller to clean.
    """
    sections: list[tuple[str, list[dict[str, str]]]] = []
    lines = text.splitlines()
    current_heading: str | None = None
    in_table = False
    headers: list[str] = []
    rows: list[dict[str, str]] = []

    for raw in lines:
        line = raw.rstrip()
        # Heading detection — '##', '###'.
        if line.startswith("#"):
            if in_table and rows and current_heading is not None:
                sections.append((current_heading, rows))
            current_heading = line.lstrip("# ").strip()
            in_table = False
            headers = []
            rows = []
            continue
        if line.startswith("|") and not in_table:
            cells = [c.strip() for c in line.strip("|").split("|")]
            headers = cells
            in_table = True
            rows = []
            continue
        if in_table and re.match(r"^\|[\s\-:|]+\|?\s*$", line):
            continue  # divider row
        if in_table and line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) == len(headers):
                rows.append(dict(zip(headers, cells)))
            continue
        if in_table and not line.startswith("|"):
            if rows and current_heading is not None:
                sections.append((current_heading, rows))
            in_table = False
            headers = []
            rows = []

    if in_table and rows and current_heading is not None:
        sections.append((current_heading, rows))
    return sections


def _strip_md(value: str) -> str:
    """Remove backticks and [text](url) markup; return the inner text."""
    s = value.strip()
    m = re.match(r"^\[([^\]]*)\]\(([^)]+)\)$", s)
    if m:
        s = m.group(2)
    if s.startswith("`") and s.endswith("`") and len(s) > 1:
        s = s[1:-1]
    return s.strip()


# Rule-id → markdown heading substring (case-insensitive). The allowlist
# document uses one section per rule.
_RULE_HEADINGS = {
    "csrf_missing":         "rule 1",
    "send_from_dir_root":   "rule 2",
    "sensitive_write":      "rule 4",
}


def parse_route_audit_allowlist(path: Path) -> dict[str, list[AllowlistEntry]]:
    """Parse Compliance/route-audit-allowlist.md into per-rule entry lists.

    Missing file → empty allowlist (rules fire as if no allowlist exists).
    A heading containing 'rule N' (case-insensitive) anchors the table for
    rule N. Each table row needs a `Scope` and `Reason` column at minimum.
    """
    out: dict[str, list[AllowlistEntry]] = {k: [] for k in _RULE_HEADINGS}
    if not path.exists():
        return out
    text = path.read_text()
    sections = _parse_markdown_tables(text)
    for heading, rows in sections:
        h_lower = heading.lower()
        for rule_id, marker in _RULE_HEADINGS.items():
            if marker in h_lower:
                for row in rows:
                    scope = _strip_md(row.get("Scope", ""))
                    if not scope:
                        continue
                    out[rule_id].append(AllowlistEntry(
                        rule=rule_id,
                        scope=scope,
                        reason=_strip_md(row.get("Reason", "")),
                        tracked_in=_strip_md(row.get("Tracked in", "")),
                    ))
                break
    return out


def parse_sensitive_writes_inventory(path: Path) -> set[tuple[str, str]]:
    """Parse Compliance/sensitive-write-sites.md → set of (func_name, rel_file).

    Rule 4 uses this set as the "approved sensitive writes" list. A SQL write
    to a sensitive-named column from a function not in this set fails Rule 4.
    """
    approved: set[tuple[str, str]] = set()
    if not path.exists():
        return approved
    text = path.read_text()
    sections = _parse_markdown_tables(text)
    for heading, rows in sections:
        if "code write sites" not in heading.lower():
            continue
        for row in rows:
            fn_raw = row.get("Function", "")
            file_raw = row.get("File", "")
            fn = _strip_md(fn_raw)
            # Strip suffix annotations like "name (CLI)" → "name".
            fn = fn.split(" ")[0].strip("`")
            if fn.startswith("**") and fn.endswith("**"):
                fn = fn[2:-2]
            file_path = _strip_md(file_raw)
            # Compliance/* lives one dir below repo root, so links are
            # relative ("../UserApp/..."). Normalise.
            file_path = file_path.lstrip("./")
            if file_path.startswith("../"):
                file_path = file_path[3:]
            if fn and file_path:
                approved.add((fn, file_path))
    return approved


# ---------------------------------------------------------------------------
# Route audit (Rules 1–3)
# ---------------------------------------------------------------------------

def extract_route_findings(
    file: Path,
    allowlist: dict[str, list[AllowlistEntry]],
) -> list[Finding]:
    """Apply Rules 1–3 to the routes defined in `file`.

    Rule 1 (CSRF missing): mutating route without a CSRF decorator and not
    bearer-auth'd → severity `warning` until the codebase-wide CSRF rollout
    lands (see SecurityHardening.md Track 4a). Flipping to `error` is a one-line
    change once CSRFProtect is registered.

    Rule 2 (`send_from_directory('.', ...)`): any call to send_from_directory
    whose first positional arg is the literal string '.' → severity `error`.

    Rule 3 (rate limit on unauth mutating route): mutating route without
    any auth decorator AND without any rate-limit decorator → severity
    `error`.
    """
    findings: list[Finding] = []
    try:
        text = file.read_text()
        tree = ast.parse(text, filename=str(file))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return findings

    try:
        rel_file = str(file.relative_to(REPO))
    except ValueError:
        rel_file = str(file)

    bp_protected = _file_has_blueprint_before_request(tree)

    def is_allowlisted(rule_id: str, func_name: str | None) -> bool:
        for entry in allowlist.get(rule_id, []):
            if entry.matches(rel_file, func_name):
                return True
        return False

    # Pass 1 — Rules 1 & 3 (per-route checks).
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        route_decos = [d for d in node.decorator_list if _is_route_decorator(d)]
        if not route_decos:
            continue
        methods: set[str] = set()
        for d in route_decos:
            methods |= _extract_methods(d)
        if not (methods & MUTATING_METHODS):
            continue  # GET/HEAD only — no Rule 1/3 trigger

        deco_names: set[str] = set()
        for d in node.decorator_list:
            if _is_route_decorator(d):
                continue
            n = _decorator_name(d)
            if n is not None:
                deco_names.add(n)

        has_auth = bool(deco_names & AUTH_DECORATORS) or bp_protected
        has_csrf_opt = bool(deco_names & CSRF_OPT_DECORATORS)

        # Rule 1 — CSRF
        if not has_csrf_opt and not is_allowlisted("csrf_missing", node.name):
            # Bearer-token-auth'd routes don't strictly need CSRF (no cookie
            # ambient credential). Skip those unless the function has session
            # auth markers.
            session_auth = ("require_auth" in deco_names) or (
                not deco_names & {"require_bearer_token", "require_api_key"}
            )
            if session_auth:
                findings.append(Finding(
                    "warning", "csrf_missing",
                    f"Mutating route '{node.name}' methods={sorted(methods)} "
                    f"has no CSRF decorator (SecurityHardening.md F1; flip to error "
                    f"once CSRFProtect is rolled out)",
                    str(file), node.lineno,
                ))

    # Pass 2 — Rule 2 (send_from_directory('.', ...)).
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = None
        if isinstance(node.func, ast.Name):
            fname = node.func.id
        elif isinstance(node.func, ast.Attribute):
            fname = node.func.attr
        if fname != "send_from_directory":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value == ".":
            enclosing = _enclosing_func_name(tree, node)
            if not is_allowlisted("send_from_dir_root", enclosing):
                findings.append(Finding(
                    "error", "send_from_dir_root",
                    "send_from_directory('.', ...) — base dir is the entire "
                    "app working tree; scope to a real static dir "
                    "(SecurityHardening.md F6)",
                    str(file), node.lineno,
                ))

    return findings


# ---------------------------------------------------------------------------
# Sensitive-column write audit (Rule 4)
# ---------------------------------------------------------------------------

def _enclosing_func_for_lineno(tree: ast.Module, lineno: int) -> str | None:
    """Find the FunctionDef whose body contains the given lineno.

    Returns the innermost matching function name, or None if the line is
    at module scope. Used to map a SQL site (file, line) back to its
    enclosing function for Rule 4's inventory cross-check.
    """
    best: tuple[int, str] | None = None  # (start_line, name)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            start = node.lineno
            end = getattr(node, "end_lineno", None) or start
            if start <= lineno <= end:
                if best is None or start > best[0]:
                    best = (start, node.name)
    return best[1] if best else None


def _sensitive_columns_in_statement(
    stmt: exp.Expression,  # pyright: ignore[reportPrivateImportUsage]
) -> set[str]:
    """Return the set of sensitive-named columns this INSERT/UPDATE writes."""
    hits: set[str] = set()
    if isinstance(stmt, exp.Insert):
        tgt = stmt.this
        if isinstance(tgt, exp.Schema):
            for ident in tgt.expressions:
                if isinstance(ident, exp.Identifier):
                    name = ident.name.lower()
                    if SENSITIVE_COL_PATTERN.search(name):
                        hits.add(name)
    if isinstance(stmt, exp.Update):
        for assign in (stmt.expressions or []):
            if isinstance(assign, exp.EQ) and isinstance(assign.this, exp.Column):
                name = assign.this.name.lower()
                if SENSITIVE_COL_PATTERN.search(name):
                    hits.add(name)
    return hits


def extract_sensitive_write_findings(
    site: SqlSite,
    file_trees: dict[str, ast.Module],
    approved_sites: set[tuple[str, str]],
) -> list[Finding]:
    """Apply Rule 4 to a SQL site.

    A site that writes to a sensitive-named column from a function not
    listed in `approved_sites` (the Track 1 inventory) → severity `error`.
    Static-only writes that we couldn't parse are skipped (covered by
    code_query_audit's existing `unresolvable_sql` info finding).
    """
    findings: list[Finding] = []
    if not site.sql.strip():
        return findings
    try:
        stmts = sqlglot.parse(site.sql, dialect="postgres")
    except Exception:
        return findings

    sensitive_hits: set[str] = set()
    for stmt in stmts:
        if stmt is None:
            continue
        sensitive_hits |= _sensitive_columns_in_statement(stmt)
    if not sensitive_hits:
        return findings

    tree = file_trees.get(site.file)
    if tree is None:
        return findings
    func_name = _enclosing_func_for_lineno(tree, site.line)
    if not func_name:
        return findings

    try:
        rel_file = str(Path(site.file).relative_to(REPO))
    except ValueError:
        rel_file = site.file

    if (func_name, rel_file) in approved_sites:
        return findings

    findings.append(Finding(
        "error", "sensitive_write",
        f"Function '{func_name}' writes to sensitive column(s) "
        f"{sorted(sensitive_hits)} but is not listed in "
        f"Compliance/sensitive-write-sites.md (SecurityHardening.md Track 1 contract)",
        site.file, site.line,
    ))
    return findings


# ---------------------------------------------------------------------------
# Track 2 — Permissive-default heuristic (warnings)
# ---------------------------------------------------------------------------
#
# Per SecurityHardening.md Track 2: silent-fallback-to-permissive-default is the
# class of bug F3 belongs to. The detectors below are heuristic — they
# emit *warnings*, not errors. False positives are expected and
# acceptable; the report's job is to give a reviewer a list of suspicious
# places, not to gate CI.
#
# Four patterns:
#   1. Function name matches `get_first_*`, `get_default_*`, `fallback_*`,
#      `*_or_anon`, `*_or_admin`, `_default_user_*`.
#   2. SQL `ORDER BY ... LIMIT 1` inside an auth-context function.
#   3. Bare `except:` block whose body returns a value (not raise).
#   4. `if not <auth_var>: return <non-error>`.
#
# Output category: ``permissive_default``. Severity: ``warning``.

# Pattern 1 — function-name regex.
_SUSPICIOUS_FUNC_NAME = re.compile(
    r"^(get_first_|get_default_|fallback_|_default_user_)"
    r"|(_or_anon|_or_admin)$",
    re.IGNORECASE,
)

# Pattern 2 — auth-context signals. A function is "auth context" if its
# decorator stack or body source mentions any of these. Deliberately
# narrow to avoid flagging every paginated query in the codebase.
_AUTH_CONTEXT_DECORATORS = AUTH_DECORATORS  # reuse Track 3's set
_AUTH_CONTEXT_BODY_SIGNALS = (
    "g.user", "g.admin", "g.tenant_id",
    "current_user_id", "current_tenant_id",
    "auth_header", "bearer_token", "session_id",
)

# Auth-variable names for Pattern 4. A guard like `if not g.user: return ...`
# whose body returns something that isn't a 4xx response is the F3 shape.
#
# Deliberately narrow to *qualified* attribute accesses — bare names like
# `user`, `session`, `token` are too common in non-auth contexts (CLI
# tools that fetch a user record by name, auth.py helpers that take a
# user param, etc.) and produce too many false positives. The qualified
# forms below are unambiguously auth-state.
_AUTH_VAR_NAMES = {
    "g.user", "g.admin", "g.tenant_id",
    "current_user", "current_tenant",
    "current_user_id", "current_tenant_id",
}


def _function_body_text(node: ast.FunctionDef, file_text: str) -> str:
    """Extract the source text of a FunctionDef from the surrounding file.

    Used only for substring signal-detection (no AST round-trip needed).
    """
    start = node.lineno - 1  # ast.lineno is 1-based; splitlines is 0-based
    end = getattr(node, "end_lineno", None)
    lines = file_text.splitlines()
    if end is None or end > len(lines):
        end = len(lines)
    return "\n".join(lines[start:end])


def _is_auth_context(
    node: ast.FunctionDef, file_text: str
) -> bool:
    """Pattern 2 heuristic: does this function look like auth-related code?"""
    # Decorator check
    for d in node.decorator_list:
        n = _decorator_name(d)
        if n in _AUTH_CONTEXT_DECORATORS:
            return True
    # Body signal check
    body = _function_body_text(node, file_text)
    for signal in _AUTH_CONTEXT_BODY_SIGNALS:
        if signal in body:
            return True
    # Function-name pattern check
    return bool(_SUSPICIOUS_FUNC_NAME.search(node.name))


def _looks_like_error_return(stmt: ast.Return) -> bool:
    """Decide whether `return X` reads as "return an HTTP error response".

    Used by patterns 3 and 4. We don't try to parse Flask's full response
    syntax — just look for the common 4xx/5xx tuple and a few sentinels:
        return jsonify({...}), 401         → error
        return Response(..., status=401)   → error
        return None                        → not "permissive default"
        return                             → not "permissive default"
        return user_record                 → permissive default (not error)
    """
    val = stmt.value
    if val is None:
        return True  # bare `return` — caller knows there's no value
    if isinstance(val, ast.Constant) and val.value is None:
        return True  # `return None`
    # `return jsonify(...), 401` or any `(_, int >= 400)` tuple
    if isinstance(val, ast.Tuple) and len(val.elts) >= 2:
        last = val.elts[-1]
        if isinstance(last, ast.Constant) and isinstance(last.value, int):
            if last.value >= 400:
                return True
        # `return False, "message"` / `return None, "message"` — the
        # `(success, message)` convention used heavily in
        # UserApp/webapp/auth.py and the admin CLIs. The first element
        # being False/None signals failure regardless of what the second
        # element is.
        first = val.elts[0]
        if isinstance(first, ast.Constant) and first.value in (False, None):
            return True
    # `abort(401)` style — function call to a name 'abort' or attribute .abort
    if isinstance(val, ast.Call):
        fname = (
            val.func.id if isinstance(val.func, ast.Name)
            else val.func.attr if isinstance(val.func, ast.Attribute)
            else None
        )
        if fname == "abort":
            return True
    return False


def _has_value_return(handler: ast.ExceptHandler) -> bool:
    """Pattern 3: does this except block return a non-error value?"""
    for child in ast.walk(handler):
        if isinstance(child, ast.Return):
            if not _looks_like_error_return(child):
                return True
    return False


def _name_or_attr_str(node: ast.AST) -> str | None:
    """Render `g.user`, `session_id`, `g.admin.role` to a dotted string."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts: list[str] = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
    return None


def extract_permissive_default_findings(
    file: Path, tree: ast.Module, sites: list[SqlSite]
) -> list[Finding]:
    """Apply the four Track 2 heuristics to ``file``.

    All findings are severity ``warning``, category ``permissive_default``.
    """
    findings: list[Finding] = []
    try:
        text = file.read_text()
    except (OSError, UnicodeDecodeError):
        return findings

    # ---- Pattern 1 — suspicious function names -----------------------
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            m = _SUSPICIOUS_FUNC_NAME.search(node.name)
            if m:
                findings.append(Finding(
                    "warning", "permissive_default",
                    f"Function name '{node.name}' matches the "
                    f"silent-fallback name pattern (SecurityHardening.md Track 2 "
                    f"Pattern 1) — review whether the false-branch grants "
                    f"more access than the true-branch.",
                    str(file), node.lineno,
                ))

    # ---- Pattern 2 — ORDER BY LIMIT 1 in auth-context functions ------
    # Map every site to its enclosing function (if any).
    for site in sites:
        if site.file != str(file):
            continue
        sql_lower = site.sql.lower()
        if "order by" not in sql_lower or "limit 1" not in sql_lower:
            continue
        # Find enclosing function
        enclosing: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                start = node.lineno
                end = getattr(node, "end_lineno", None) or start
                if start <= site.line <= end:
                    if enclosing is None or node.lineno > enclosing.lineno:
                        enclosing = node
        if enclosing is None:
            continue
        if not _is_auth_context(enclosing, text):
            continue
        findings.append(Finding(
            "warning", "permissive_default",
            f"`ORDER BY ... LIMIT 1` inside auth-context function "
            f"'{enclosing.name}' (SecurityHardening.md Track 2 Pattern 2) — F3 "
            f"shape: arbitrary-row selection in code that decides who "
            f"the caller is.",
            site.file, site.line,
        ))

    # ---- Pattern 3 — bare `except:` with a value return --------------
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if handler.type is not None:
                    continue  # not bare
                if _has_value_return(handler):
                    findings.append(Finding(
                        "warning", "permissive_default",
                        "Bare `except:` block returns a non-error value "
                        "(SecurityHardening.md Track 2 Pattern 3) — review "
                        "whether 'swallow exception, return success' is "
                        "the intended behavior on this path.",
                        str(file), handler.lineno,
                    ))

    # ---- Pattern 4 — `if not <auth_var>: return <non-error>` ---------
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        # Test must be `not <name-or-attr>`
        if not (
            isinstance(node.test, ast.UnaryOp)
            and isinstance(node.test.op, ast.Not)
        ):
            continue
        var_str = _name_or_attr_str(node.test.operand)
        if var_str is None:
            continue
        if var_str not in _AUTH_VAR_NAMES:
            continue
        # Body must contain a non-error return at the top level
        for stmt in node.body:
            if isinstance(stmt, ast.Return) and not _looks_like_error_return(stmt):
                findings.append(Finding(
                    "warning", "permissive_default",
                    f"`if not {var_str}: return ...` returns a non-error "
                    f"value (SecurityHardening.md Track 2 Pattern 4) — F3 shape: "
                    f"missing-auth branch grants something instead of "
                    f"refusing.",
                    str(file), node.lineno,
                ))
                break

    return findings


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def gather_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                files.append(Path(dirpath) / fn)
    return files


def render_report(
    service: str,
    files_scanned: int,
    sites: list[SqlSite],
    findings: list[Finding],
    schema_tables: set[str] | None = None,
    referenced_tables: set[str] | None = None,
) -> str:
    sev_counts = Counter(f.severity for f in findings)
    cat_counts = Counter(f.category for f in findings)
    kind_counts = Counter(s.kind for s in sites)

    out: list[str] = []
    out.append(f"# Code SQL Usage Audit — {service}")
    out.append("")
    out.append(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ")
    out.append(f"Schema: `{SCHEMA.relative_to(REPO)}`  ")
    out.append("Doctrine: [`CodeQueryAudit.md`](CodeQueryAudit.md)  ")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append(f"- Python files scanned: **{files_scanned}**")
    out.append(f"- SQL sites extracted: **{len(sites)}**")
    for k, n in kind_counts.most_common():
        out.append(f"  - `{k}`: {n}")
    out.append(
        f"- Findings: errors=**{sev_counts.get('error', 0)}** "
        f"warnings=**{sev_counts.get('warning', 0)}** "
        f"info=**{sev_counts.get('info', 0)}**"
    )
    for c, n in cat_counts.most_common():
        out.append(f"  - `{c}`: {n}")
    out.append("")

    for sev in ("error", "warning", "info"):
        sev_findings = [f for f in findings if f.severity == sev]
        if not sev_findings:
            continue
        out.append(f"## {sev.title()}s ({len(sev_findings)})")
        out.append("")
        by_cat: dict[str, list[Finding]] = {}
        for f in sev_findings:
            by_cat.setdefault(f.category, []).append(f)
        for cat in sorted(by_cat):
            entries = by_cat[cat]
            out.append(f"### `{cat}` ({len(entries)})")
            out.append("")
            for f in entries[:200]:
                rel = (
                    Path(f.file).relative_to(REPO)
                    if f.file.startswith(str(REPO))
                    else Path(f.file)
                )
                out.append(f"- `{rel}:{f.line}` — {f.message}")
            if len(entries) > 200:
                out.append(f"- ...and {len(entries) - 200} more")
            out.append("")

    # Doctrine §6: tables in schema not referenced from this service.
    # Skipped when no SQL sites were extracted (e.g. UserMCP) —
    # a "no SQL = nothing referenced" listing would be 100% noise.
    if schema_tables is not None and referenced_tables is not None and sites:
        unreferenced = sorted(schema_tables - referenced_tables)
        out.append(
            f"## Tables in schema not referenced from this service "
            f"({len(unreferenced)} of {len(schema_tables)})"
        )
        out.append("")
        if unreferenced:
            out.append(
                "A table that no service references at all is a stronger signal "
                "of orphan than an unused column. Cross-link with "
                "[`UnusedColumnsAudit.md`](UnusedColumnsAudit.md)."
            )
            out.append("")
            for t in unreferenced:
                out.append(f"- `{t}`")
        else:
            out.append("All schema tables are referenced from this service.")
        out.append("")

    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Audit SQL in Python source against the schema source of truth.",
    )
    ap.add_argument("--service", required=True,
                    help="Service folder name (under repo root) e.g. UserApp")
    ap.add_argument("--out", help="Output report path (default: DataModel3/CodeQueryAudit-<service>.md)")
    ap.add_argument("--json", action="store_true",
                    help="Print JSON summary to stdout instead of writing a report")
    args = ap.parse_args()

    service_root = REPO / args.service
    if not service_root.exists():
        print(f"Service root not found: {service_root}", file=sys.stderr)
        return 2
    if not SCHEMA.exists():
        print(f"Schema file not found: {SCHEMA}", file=sys.stderr)
        return 2

    tables, col_index = parse_schema(SCHEMA)
    column_comments = parse_column_comments(SCHEMA)
    files = gather_python_files(service_root)

    # Parse each file once into an AST. extract_sites also parses internally;
    # we re-parse here so Rules 1-3 (route audit) and Rule 4 (sensitive-write)
    # can share the same tree. Cost is negligible at the scale of one service.
    file_trees: dict[str, ast.Module] = {}
    for f in files:
        try:
            file_trees[str(f)] = ast.parse(f.read_text(), filename=str(f))
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue

    sites: list[SqlSite] = []
    for f in files:
        sites.extend(extract_sites(f))

    findings: list[Finding] = []
    referenced_tables: set[str] = set()
    for s in sites:
        findings.extend(verify_sql(s, tables, col_index, referenced_tables))

    # Architecture rule for MCP services.
    if args.service in MCP_SERVICE_ROOTS:
        for s in sites:
            findings.append(Finding(
                "error", "mcp_direct_db_access",
                f"MCP service has direct .execute() call (kind={s.kind})",
                s.file, s.line,
            ))

    # Track 3 — Flask-route AST rules (Rules 1, 2, 3).
    route_allowlist = parse_route_audit_allowlist(ROUTE_AUDIT_ALLOWLIST)
    for f in files:
        findings.extend(extract_route_findings(f, route_allowlist))

    # Track 3 — Rule 4: sensitive-column writes vs Track 1 inventory.
    approved_sites = parse_sensitive_writes_inventory(SENSITIVE_WRITES_INVENTORY)
    for s in sites:
        findings.extend(extract_sensitive_write_findings(s, file_trees, approved_sites))

    # Track 2 — permissive-default heuristic warnings.
    for f in files:
        tree = file_trees.get(str(f))
        if tree is not None:
            findings.extend(extract_permissive_default_findings(f, tree, sites))

    # Track 7 — schema column-comment crypto contract. Schema-global
    # check; emit findings exactly once (only on the canonical UserApp
    # run) so re-running every service doesn't multi-count the same
    # missing-annotation. The rule is keyed on the schema, not on the
    # service code, so the choice of "which service to attach to" is
    # arbitrary — UserApp gets it because it's the primary one.
    if args.service == "UserApp":
        findings.extend(extract_crypto_contract_findings(tables, column_comments))

    if args.json:
        sev = Counter(f.severity for f in findings)
        kind = Counter(s.kind for s in sites)
        print(json.dumps({
            "service": args.service,
            "schema_tables": len(tables),
            "files_scanned": len(files),
            "sql_sites": len(sites),
            "site_kinds": dict(kind),
            "errors": sev.get("error", 0),
            "warnings": sev.get("warning", 0),
            "info": sev.get("info", 0),
            "referenced_tables": len(referenced_tables),
            "unreferenced_tables": (
                sorted(set(tables.keys()) - referenced_tables) if sites else []
            ),
        }, indent=2))
    else:
        out_path = (
            Path(args.out) if args.out
            else REPO / f"DataModel3/CodeQueryAudit-{args.service}.md"
        )
        out_path.write_text(render_report(
            args.service, len(files), sites, findings,
            schema_tables=set(tables.keys()),
            referenced_tables=referenced_tables,
        ))
        sev = Counter(f.severity for f in findings)
        print(f"Report: {out_path}")
        print(
            f"  files={len(files)} sites={len(sites)} "
            f"errors={sev.get('error', 0)} warnings={sev.get('warning', 0)} "
            f"info={sev.get('info', 0)}"
        )

    return 1 if any(f.severity == "error" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
