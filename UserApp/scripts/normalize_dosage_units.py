#!/usr/bin/env python3
"""
normalize_dosage_units.py — One-time backfill of health_inputs.default_unit.
2026-07-03T21:00Z

Runs every stored default_unit through the canonical alias map in
webapp/units.py ('IU' -> 'iu', 'mcg' -> 'ug', ...). Values that don't map
(e.g. 'tbd') are reported and left untouched — this script never guesses.

Unlike detect_frequent_inputs.py, this script is DRY-RUN BY DEFAULT because
it rewrites user data in bulk; pass --apply to commit.

Usage (inside webapp container):
    python3 /app/scripts/normalize_dosage_units.py           # report only
    python3 /app/scripts/normalize_dosage_units.py --apply   # commit changes

Via Docker from host:
    docker exec hb-local-webapp python3 /app/scripts/normalize_dosage_units.py
"""

import argparse
import os
import sys
from datetime import datetime, timezone

# db_driver + units live in UserApp/webapp/ — put it on sys.path. Works both
# inside the webapp Docker container (/app/scripts/ → /app/webapp/) and on
# the local Mac dev tree (UserApp/scripts/ → UserApp/webapp/).
sys.path.insert(
    0,
    os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webapp")
    ),
)

try:
    import db_driver
    from units import normalize_unit
except ImportError:
    print("ERROR: db_driver/units not importable. Run inside the webapp "
          "container or ensure UserApp/webapp is on sys.path.")
    sys.exit(1)


def get_connection():
    """Connect as postgres superuser (covers all household members)."""
    return db_driver.connect(
        host=os.getenv('DB_HOST', 'pgvector'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'healthv10'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD', 'password'),
    )


def normalize_units(conn, apply: bool = False) -> tuple[int, list, list]:
    """Normalize non-canonical default_unit values in health_inputs.

    Returns:
        (canonical_count, updated_rows, unmappable_rows) — updated rows carry
        'old'/'new' unit values; unmappable rows carry the offending 'old'.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT tenant_id, id, name, default_unit
        FROM health_inputs
        WHERE default_unit IS NOT NULL AND btrim(default_unit) <> ''
    """)
    rows = cur.fetchall()

    canonical = 0
    updated = []
    unmappable = []
    for row in rows:
        old = row['default_unit']
        try:
            new = normalize_unit(old)
        except ValueError:
            unmappable.append({**row, 'old': old})
            continue
        if new == old:
            canonical += 1
            continue
        cur.execute("""
            UPDATE health_inputs
            SET default_unit = %s, updated_at = NOW()
            WHERE tenant_id = %s AND id = %s
        """, (new, row['tenant_id'], row['id']))
        updated.append({**row, 'old': old, 'new': new})

    if apply:
        conn.commit()
    else:
        conn.rollback()
    cur.close()
    return canonical, updated, unmappable


def main():
    parser = argparse.ArgumentParser(
        description="Normalize health_inputs.default_unit to the canonical vocabulary")
    parser.add_argument("--apply", action="store_true",
                        help="Commit changes (default is a dry-run report)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    mode = "" if args.apply else "[DRY RUN] "
    print(f"{mode}normalize_dosage_units: {now}")

    conn = get_connection()
    try:
        canonical, updated, unmappable = normalize_units(conn, apply=args.apply)

        for row in updated:
            print(f"  + {row['name']} ({row['id']}): '{row['old']}' -> '{row['new']}'")
        for row in unmappable:
            print(f"  ! {row['name']} ({row['id']}): '{row['old']}' NOT MAPPABLE — left as-is")

        verb = "updated" if args.apply else "would update"
        print(f"  Summary: {canonical} already canonical, "
              f"{len(updated)} {verb}, {len(unmappable)} unmappable")
        if not args.apply and updated:
            print("  Re-run with --apply to commit.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
