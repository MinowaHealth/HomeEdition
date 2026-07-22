#!/usr/bin/env python3
"""backfill_lab_observations.py — Re-derive lab observation dates and names.

minowa-mcp-bug-report.md Bug 4: `parse_timestamp` only understood the
HealthKit export.xml format, so every FHIR `effectiveDateTime` parsed to
None and `hkit_lab_observations.effective_date` was stored NULL. The
importer's INSERT is `ON CONFLICT ... DO NOTHING`, so fixing the parser
only helps future imports — this script repairs existing rows by
re-parsing the parent clinical record's stored `raw_fhir`.

Also re-derives `display_name` for rows where it is NULL or equals the
raw LOINC code (payloads with `code.text` / `coding.display` that earlier
import bugs dropped).

Runs on an app-role connection with RLS session context — NOT an admin
connection: hkit_lab_observations is FORCE-RLS PHI and admin bypass for
user data is banned (CLAUDE.md). Run once per affected account.

Dry-run by default; pass --apply to write.

Usage:
    docker exec healthv10-web python scripts/backfill_lab_observations.py \
        --user-id 7d7431ce-0003-4912-9b6d-5fd9846c7fa1
    docker exec healthv10-web python scripts/backfill_lab_observations.py \
        --user-id 7d7431ce-0003-4912-9b6d-5fd9846c7fa1 --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "webapp")),
                   os.path.normpath(os.path.join(_SCRIPT_DIR, ".."))):
    if os.path.isfile(os.path.join(_candidate, "db_manager.py")):
        sys.path.insert(0, _candidate)
        break

import db_driver  # noqa: E402
from healthkit_importer import parse_timestamp  # noqa: E402


def extract_name_from_fhir(data: dict) -> tuple[str | None, str | None]:
    """(display_name, loinc_code) via the same chain as extract_lab_observation."""
    code_obj = data.get('code') or {}
    display_name = code_obj.get('text')
    loinc_code = None
    for coding in code_obj.get('coding', []):
        if coding.get('system') == 'http://loinc.org':
            loinc_code = coding.get('code')
            if not display_name:
                display_name = coding.get('display')
            break
    return display_name, loinc_code


def open_rls_connection(user_id: str, tenant_id: int):
    conn = db_driver.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        port=int(os.environ.get('DB_PORT', '5432')),
        dbname=os.environ.get('DB_NAME', 'healthv10'),
        user=os.environ.get('APP_DB_USER', 'healthv10_app'),
        password=os.environ['APP_DB_PASSWORD'],
    )
    cur = conn.cursor()
    db_driver.set_session_var(cur, 'app.current_tenant_id', str(tenant_id))
    db_driver.set_session_var(cur, 'app.current_user_id', str(user_id))
    cur.close()
    return conn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--user-id', required=True, help='UUID of the affected account')
    ap.add_argument('--tenant-id', type=int, default=int(os.environ.get('DEFAULT_TENANT_ID', '1')))
    ap.add_argument('--apply', action='store_true', help='write changes (default: dry run)')
    args = ap.parse_args()

    conn = open_rls_connection(args.user_id, args.tenant_id)
    cur = conn.cursor()

    cur.execute("""
        SELECT lo.id, lo.effective_date, lo.display_name, lo.loinc_code,
               cr.raw_fhir
        FROM hkit_lab_observations lo
        JOIN hkit_clinical_records cr
          ON cr.tenant_id = lo.tenant_id AND cr.id = lo.clinical_record_id
        WHERE lo.effective_date IS NULL
           OR lo.display_name IS NULL
           OR lo.display_name = lo.loinc_code
        ORDER BY lo.id
    """)
    rows = cur.fetchall()

    updated_dates = 0
    updated_names = 0
    unnamed_loinc: dict[str, int] = {}
    unparsed_dates = 0

    for row in rows:
        raw = row['raw_fhir']
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        raw = raw or {}

        sets = []
        params = []

        if row['effective_date'] is None:
            eff = parse_timestamp(raw.get('effectiveDateTime')) or parse_timestamp(raw.get('issued'))
            if eff is not None:
                sets.append('effective_date = %s')
                params.append(eff)
                updated_dates += 1
            else:
                unparsed_dates += 1

        if row['display_name'] is None or row['display_name'] == row['loinc_code']:
            name, _loinc = extract_name_from_fhir(raw)
            if name and name != row['display_name']:
                sets.append('display_name = %s')
                params.append(name)
                updated_names += 1
            else:
                code = row['loinc_code'] or '<no-loinc>'
                unnamed_loinc[code] = unnamed_loinc.get(code, 0) + 1

        if sets and args.apply:
            params.append(row['id'])
            cur.execute(
                f"UPDATE hkit_lab_observations SET {', '.join(sets)} WHERE id = %s",
                params,
            )

    if args.apply:
        conn.commit()

    mode = 'APPLIED' if args.apply else 'DRY RUN (pass --apply to write)'
    print(f"== backfill_lab_observations [{mode}]")
    print(f"   candidate rows            : {len(rows)}")
    print(f"   effective_date backfilled : {updated_dates}")
    print(f"   effective_date unparsable : {unparsed_dates}")
    print(f"   display_name backfilled   : {updated_names}")
    if unnamed_loinc:
        print("   still nameless (LOINC -> rows) — candidates for a")
        print("   LOINC_FALLBACK_NAMES dict in healthkit_importer.py:")
        for code, n in sorted(unnamed_loinc.items()):
            print(f"     {code}: {n}")

    cur.close()
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
