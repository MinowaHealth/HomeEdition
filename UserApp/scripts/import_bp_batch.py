#!/usr/bin/env python3
"""import_bp_batch.py — Batch-import cuff-meter BP readings from a text file.

Input format, one reading per line (blank lines ignored):

    2:36 PM 132 60 76        # time-of-day, systolic, diastolic, pulse

Lines carry no date, so --date (YYYY-MM-DD) is required and applies to every
row. Times are read in the user's home_timezone (from their users row) and
stored UTC. Every imported row is labeled position='supine', arm='left wrist',
device='cuff meter', and notes='untrusted: supine cuff import' — the notes
string is the greppable marker for excluding or purging this batch later.

Idempotent: inserts use ON CONFLICT DO NOTHING against the sync-dedupe index
(tenant_id, user_id, measured_at, systolic, diastolic), so re-running the
same file is safe.

Home Edition: the app-role connection carries no RLS, so every statement
scopes explicitly by tenant_id/user_id (household trust model).

Usage (inside the webapp container):
    docker exec healthv10-web python scripts/import_bp_batch.py <user_id> scripts/cuff.csv --date 2026-07-18
    docker exec healthv10-web python scripts/import_bp_batch.py <user_id> scripts/cuff.csv --date 2026-07-18 --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime

import pytz

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "webapp")),
                   os.path.normpath(os.path.join(_SCRIPT_DIR, ".."))):
    if os.path.isfile(os.path.join(_candidate, "db_manager.py")):
        sys.path.insert(0, _candidate)
        break

import db_manager  # noqa: E402

POSITION = 'supine'
ARM = 'left wrist'
DEVICE = 'cuff meter'
NOTES = 'untrusted: supine cuff import'
TENANT_ID = 1


def parse_line(line: str, day: str, tz: pytz.BaseTzInfo) -> tuple[datetime, int, int, int]:
    """'2:36 PM 132 60 76' -> (utc timestamp, systolic, diastolic, pulse)."""
    time_part, ampm, sys_s, dia_s, pulse_s = line.split()
    local = tz.localize(datetime.strptime(f"{day} {time_part} {ampm}", "%Y-%m-%d %I:%M %p"))
    return local.astimezone(pytz.utc), int(sys_s), int(dia_s), int(pulse_s)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or '').split('\n', 1)[0])
    parser.add_argument('user_id', help='user UUID to import readings for')
    parser.add_argument('csv_path', help='readings file: TIME AM/PM SYS DIA PULSE per line')
    parser.add_argument('--date', required=True,
                        help='date (YYYY-MM-DD, local) the readings were taken')
    parser.add_argument('--dry-run', action='store_true',
                        help='parse and print rows without inserting')
    args = parser.parse_args()

    datetime.strptime(args.date, '%Y-%m-%d')  # fail fast on a bad --date

    conn = db_manager.get_direct_connection_for_user(args.user_id)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT home_timezone FROM users
            WHERE tenant_id = %s AND id = %s
        """, (TENANT_ID, args.user_id))
        row = cur.fetchone()
        if not row or not row['home_timezone']:
            print('ERROR: could not resolve home_timezone for this user')
            return 1
        tz = pytz.timezone(row['home_timezone'])

        with open(args.csv_path) as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        readings = [parse_line(ln, args.date, tz) for ln in lines]

        print(f"{len(readings)} readings parsed ({tz.zone}, {args.date}):")
        for measured_at, systolic, diastolic, pulse in readings:
            print(f"  {measured_at.isoformat()}  {systolic}/{diastolic}  pulse {pulse}")

        if args.dry_run:
            print(f"Dry-run: would insert with position={POSITION!r}, arm={ARM!r}, "
                  f"device={DEVICE!r}, notes={NOTES!r}")
            return 0

        inserted = 0
        for measured_at, systolic, diastolic, pulse in readings:
            cur.execute("""
                INSERT INTO health_blood_pressure_readings
                    (tenant_id, id, user_id, measured_at, systolic, diastolic,
                     pulse, position, arm, device, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (TENANT_ID, uuid.uuid4(), args.user_id, measured_at, systolic,
                  diastolic, pulse, POSITION, ARM, DEVICE, NOTES))
            inserted += cur.rowcount
        conn.commit()
        skipped = len(readings) - inserted
        print(f"Inserted {inserted} readings"
              + (f", {skipped} already present (dedupe)" if skipped else "") + ".")
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
