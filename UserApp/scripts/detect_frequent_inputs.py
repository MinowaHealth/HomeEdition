#!/usr/bin/env python3
"""
detect_frequent_inputs.py — Weekly scan to auto-detect frequent standalone inputs.
2026-03-03T19:00Z

Scans health_input_log for inputs used standalone (not part of a stack)
at least once in the last 7 days.  Sets frequent_status = 'detected' on
those inputs.  Demotes stale 'detected' inputs back to NULL.  Never
touches 'sticky' (user-pinned) inputs.

Usage (inside webapp container):
    python3 /app/scripts/detect_frequent_inputs.py
    python3 /app/scripts/detect_frequent_inputs.py --dry-run

Via Docker from host:
    docker exec hb-local-webapp python3 /app/scripts/detect_frequent_inputs.py

Cron (weekly, Sunday 3am UTC):
    0 3 * * 0  docker exec hb-local-webapp python3 /app/scripts/detect_frequent_inputs.py >> /var/log/frequent_inputs.log 2>&1
"""

import argparse
import os
import sys
from datetime import datetime, timezone

# db_driver shim lives in UserApp/webapp/ — put it on sys.path. Works both
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
except ImportError:
    print("ERROR: db_driver not importable. Run inside the webapp container "
          "or ensure UserApp/webapp is on sys.path.")
    sys.exit(1)


def get_connection():
    """Connect as postgres superuser (bypasses RLS to scan all users).

    Row factory is set automatically by the shim.
    """
    return db_driver.connect(
        host=os.getenv('DB_HOST', 'pgvector'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'healthv10'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD', 'password'),
    )


def detect_frequent(conn, dry_run: bool = False) -> tuple[list, list]:
    """Promote and demote frequent_status based on recent standalone usage.

    Returns:
        (promoted_rows, demoted_rows) — each row has 'id' and 'name'.
    """
    cur = conn.cursor()

    # Step 1: Find inputs used standalone (stack_id IS NULL) in last 7 days
    cur.execute("""
        SELECT DISTINCT input_id
        FROM health_input_log
        WHERE stack_id IS NULL
          AND input_id IS NOT NULL
          AND logged_at >= NOW() - INTERVAL '7 days'
    """)
    frequent_ids = {row['input_id'] for row in cur.fetchall()}

    # Step 2: Promote — set 'detected' on frequent inputs that aren't already sticky
    if frequent_ids:
        id_list = [str(uid) for uid in frequent_ids]
        cur.execute("""
            UPDATE health_inputs
            SET frequent_status = 'detected', updated_at = NOW()
            WHERE id = ANY(%s::uuid[])
              AND (frequent_status IS NULL)
              AND is_active = true
            RETURNING id, name
        """, (id_list,))
        promoted = cur.fetchall()
    else:
        promoted = []

    # Step 3: Demote — clear 'detected' on inputs NOT in the frequent set
    if frequent_ids:
        cur.execute("""
            UPDATE health_inputs
            SET frequent_status = NULL, updated_at = NOW()
            WHERE frequent_status = 'detected'
              AND id != ALL(%s::uuid[])
            RETURNING id, name
        """, (id_list,))
    else:
        # No frequent inputs at all — demote everything that's 'detected'
        cur.execute("""
            UPDATE health_inputs
            SET frequent_status = NULL, updated_at = NOW()
            WHERE frequent_status = 'detected'
            RETURNING id, name
        """)
    demoted = cur.fetchall()

    if dry_run:
        conn.rollback()
    else:
        conn.commit()

    cur.close()
    return promoted, demoted


def main():
    parser = argparse.ArgumentParser(
        description="Detect and flag frequently-used standalone health inputs")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without committing")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    mode = "[DRY RUN] " if args.dry_run else ""
    print(f"{mode}detect_frequent_inputs: {now}")

    conn = get_connection()
    try:
        promoted, demoted = detect_frequent(conn, dry_run=args.dry_run)

        if promoted:
            print(f"  Promoted {len(promoted)} inputs to 'detected':")
            for row in promoted:
                print(f"    + {row['name']} ({row['id']})")
        else:
            print("  No new inputs to promote.")

        if demoted:
            print(f"  Demoted {len(demoted)} inputs back to NULL:")
            for row in demoted:
                print(f"    - {row['name']} ({row['id']})")
        else:
            print("  No stale inputs to demote.")

        print(f"  Summary: +{len(promoted)} promoted, -{len(demoted)} demoted")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
