#!/usr/bin/env python3
"""backfill_bp_position_arm.py — One-shot backfill of position/arm on historic BP readings.

Sets position='seated' and arm='left' on every blood pressure reading that
doesn't already record a real value. NULL and the legacy import placeholder
'unknown' both count as missing; any reading carrying an explicit position
or arm keeps its value. Idempotent — safe to re-run.

Home Edition: the app-role connection carries no RLS, so every statement
scopes explicitly by tenant_id/user_id (household trust model).

Usage (inside the webapp container):
    docker exec healthv10-web python scripts/backfill_bp_position_arm.py <user_id>
    docker exec healthv10-web python scripts/backfill_bp_position_arm.py <user_id> --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "webapp")),
                   os.path.normpath(os.path.join(_SCRIPT_DIR, ".."))):
    if os.path.isfile(os.path.join(_candidate, "db_manager.py")):
        sys.path.insert(0, _candidate)
        break

import db_manager  # noqa: E402

POSITION = 'seated'
ARM = 'left'
TENANT_ID = 1


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or '').split('\n', 1)[0])
    parser.add_argument('user_id', help='user UUID whose readings to backfill')
    parser.add_argument(
        '--dry-run', action='store_true',
        help='report how many rows would change without updating',
    )
    args = parser.parse_args()

    conn = db_manager.get_direct_connection_for_user(args.user_id)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT count(*) AS total,
                   count(*) FILTER (
                       WHERE NULLIF(position, 'unknown') IS NULL
                          OR NULLIF(arm, 'unknown') IS NULL
                   ) AS fillable
            FROM health_blood_pressure_readings
            WHERE tenant_id = %s AND user_id = %s
        """, (TENANT_ID, args.user_id))
        row = cur.fetchone()
        print(f"{row['total']} readings, {row['fillable']} missing position and/or arm")

        if args.dry_run:
            print(f"Dry-run: would set position={POSITION!r}, arm={ARM!r} on those rows.")
            return 0

        cur.execute("""
            UPDATE health_blood_pressure_readings
            SET position = COALESCE(NULLIF(position, 'unknown'), %s),
                arm = COALESCE(NULLIF(arm, 'unknown'), %s)
            WHERE tenant_id = %s AND user_id = %s
              AND (NULLIF(position, 'unknown') IS NULL
                   OR NULLIF(arm, 'unknown') IS NULL)
        """, (POSITION, ARM, TENANT_ID, args.user_id))
        updated = cur.rowcount
        conn.commit()
        print(f"Updated {updated} readings.")
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
