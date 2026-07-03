#!/usr/bin/env python3
"""Count rows per table for a user, reading from a pg_dump SQL file.

Standalone — no DB connection, no third-party deps. Works on plain pg_dump
output (default COPY format), including gzip-compressed dumps.

This is the file-based counterpart to `UserApp/admin.py count-records`. Use
it when you have a backup file but no running database (or no Python
environment with psycopg installed).

Limitations:
  - Plain SQL dumps only (pg_dump default, or -Fp). Custom-format dumps (-Fc)
    and directory-format dumps (-Fd) need pg_restore to flatten first.
  - Default COPY format only. Dumps generated with --inserts or
    --column-inserts use INSERT statements and would need a different parser.
  - Schema must be `public` — table identifiers in the COPY header must match
    `COPY public.<name> (...)`.

Usage:
    count_records_from_dump.py <dump.sql[.gz]> <email> [--tenant N]
"""

import argparse
import gzip
import re
import sys
from collections import OrderedDict
from pathlib import Path


COPY_RE = re.compile(r"^COPY public\.(\w+) \(([^)]+)\) FROM stdin;\s*$")


def open_dump(path: Path):
    """Open the dump file, transparently handling gzip."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def parse_columns(col_str: str) -> list[str]:
    """Parse the column list from a COPY header line.

    'tenant_id, user_id, "timestamp", heart_rate'
        -> ['tenant_id', 'user_id', 'timestamp', 'heart_rate']
    """
    return [c.strip().strip('"') for c in col_str.split(",")]


def find_user(dump_path: Path, email: str, tenant_filter: int | None):
    """Scan the dump for the users COPY block; return user info for `email`.

    Returns (tenant_id, user_id, display_name) or None if not found.
    """
    email_lower = email.lower().strip()
    in_users_copy = False
    cols: list[str] = []
    tenant_idx = id_idx = email_idx = -1
    display_idx: int | None = None

    with open_dump(dump_path) as f:
        for line in f:
            if not in_users_copy:
                m = COPY_RE.match(line)
                if not (m and m.group(1) == "users"):
                    continue
                cols = parse_columns(m.group(2))
                if not {"tenant_id", "id", "email"}.issubset(cols):
                    return None
                tenant_idx = cols.index("tenant_id")
                id_idx = cols.index("id")
                email_idx = cols.index("email")
                display_idx = cols.index("display_name") if "display_name" in cols else None
                in_users_copy = True
                continue
            if line.startswith("\\."):
                return None
            fields = line.rstrip("\n").split("\t")
            if len(fields) < len(cols):
                continue
            row_email = fields[email_idx].lower().strip()
            if row_email != email_lower:
                continue
            tenant_id = int(fields[tenant_idx])
            if tenant_filter is not None and tenant_id != tenant_filter:
                continue
            user_id = fields[id_idx]
            display = fields[display_idx] if display_idx is not None else None
            if display == "\\N":
                display = None
            return (tenant_id, user_id, display)
    return None


def count_rows(dump_path: Path, tenant_id: int, user_id: str) -> "OrderedDict[str, int]":
    """Scan the dump; return {table: count} for every public.<table> COPY block
    whose column list contains both tenant_id and user_id, counting rows that
    match the given (tenant_id, user_id) pair.
    """
    counts: "OrderedDict[str, int]" = OrderedDict()
    in_copy = False
    tenant_idx = user_idx = -1
    current_table: str | None = None
    tenant_str = str(tenant_id)

    with open_dump(dump_path) as f:
        for line in f:
            if not in_copy:
                m = COPY_RE.match(line)
                if not m:
                    continue
                cols = parse_columns(m.group(2))
                if "tenant_id" not in cols or "user_id" not in cols:
                    continue
                current_table = m.group(1)
                tenant_idx = cols.index("tenant_id")
                user_idx = cols.index("user_id")
                counts.setdefault(current_table, 0)
                in_copy = True
                continue
            if line.startswith("\\."):
                in_copy = False
                current_table = None
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) <= max(tenant_idx, user_idx):
                continue
            if fields[tenant_idx] == tenant_str and fields[user_idx] == user_id:
                assert current_table is not None
                counts[current_table] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Count rows per table for a user, from a pg_dump SQL file."
    )
    parser.add_argument("dump", type=Path, help="pg_dump SQL file (optionally gzipped)")
    parser.add_argument("email", help="User email")
    parser.add_argument("--tenant", type=int, help="Restrict to this tenant_id")
    args = parser.parse_args()

    if not args.dump.exists():
        print(f"Error: dump file not found: {args.dump}", file=sys.stderr)
        return 2

    print(f"Scanning {args.dump} for user '{args.email}'...", file=sys.stderr)
    user = find_user(args.dump, args.email, args.tenant)
    if user is None:
        print(f"Error: User '{args.email}' not found in dump", file=sys.stderr)
        return 1

    tenant_id, user_id, display = user
    print(
        f"Found: tenant={tenant_id} id={user_id} display={display!r}",
        file=sys.stderr,
    )
    print("Counting rows...", file=sys.stderr)

    counts = count_rows(args.dump, tenant_id, user_id)
    sorted_counts = sorted(counts.items(), key=lambda r: (-r[1], r[0]))
    total = sum(counts.values())
    nonempty = sum(1 for n in counts.values() if n > 0)

    print()
    print("=" * 60)
    print(f"User:        {args.email}")
    print(f"Display:     {display or '(no display name)'}")
    print(f"Tenant / ID: {tenant_id} / {user_id}")
    print(f"Dump:        {args.dump}")
    print("=" * 60)
    print(f"{'Table':<40} {'Rows':>15}")
    print("-" * 60)
    for table, n in sorted_counts:
        print(f"{table:<40} {n:>15,}")
    print("-" * 60)
    print(f"{'TOTAL':<40} {total:>15,}")
    print(
        f"\nScanned {len(counts)} tables with both tenant_id and user_id, "
        f"{nonempty} contained rows for this user."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
