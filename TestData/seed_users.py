#!/usr/bin/env python3
"""Stage 0 — provision the six Borgia household users for the test fixture.

Single-household Home Edition: six members, tenant_id=1, no providers,
organizations, or delegations. Deterministic UUIDs (b015b015-* prefix) so the
temporal seeder's per-persona RNG and the records/*.json user_ids line up.

Uses psycopg 3 directly (Home Edition rule: Postgres only through Python, never
the psql CLI). Idempotent via ON CONFLICT DO NOTHING.

Connection comes from env (same vars the temporal seeder uses):
  SEED_DB_HOST (default localhost), SEED_DB_PORT (5432),
  SEED_DB_NAME (healthv10), SEED_DB_USER (required), SEED_DB_PASSWORD (required)

Run:
  SEED_DB_USER=postgres SEED_DB_PASSWORD=Password2026 python TestData/seed_users.py
"""
from __future__ import annotations

import os
import sys

import psycopg

# Argon2id hash of the literal password "Password2026". The appliance verifies
# with argon2-cffi, which accepts any argon2id-encoded hash regardless of the
# params it was produced with. All six test accounts share this password.
PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$OU5iYWEtkC7kROYlkpsM5g$"
    "IiDiazkVxuHnhEKAFJBnBQlMujfrx2crgTVOTABLXM0"
)

TENANT_ID = 1

# (user_id, email, display_name, biological_sex, birth_year)
HOUSEHOLD = [
    ("b015b015-0001-0001-0001-b00000000001", "rodrigo@borgia.family",  "Rodrigo Borgia",        "male",   1975),
    ("b015b015-0001-0001-0002-b00000000002", "vannozza@borgia.family", "Vannozza dei Cattanei", "female", 1978),
    ("b015b015-0001-0001-0003-b00000000003", "lucrezia@borgia.family", "Lucrezia Borgia",       "female", 2012),
    ("b015b015-0001-0001-0004-b00000000004", "juan@borgia.family",     "Juan Borgia",           "male",   2014),
    ("b015b015-0001-0001-0005-b00000000005", "cesare@borgia.family",   "Cesare Borgia",         "male",   1970),
    ("b015b015-0001-0001-0006-b00000000006", "adriana@borgia.family",  "Adriana de Mila",       "female", 1940),
]

HOME_TZ = "America/New_York"


def main() -> int:
    try:
        db_user = os.environ["SEED_DB_USER"]
        db_password = os.environ["SEED_DB_PASSWORD"]
    except KeyError as e:
        print(f"ERROR: missing required env var: {e.args[0]}", file=sys.stderr)
        return 2

    conn = psycopg.connect(
        host=os.environ.get("SEED_DB_HOST", "localhost"),
        port=int(os.environ.get("SEED_DB_PORT", "5432")),
        dbname=os.environ.get("SEED_DB_NAME", "healthv10"),
        user=db_user,
        password=db_password,
    )
    inserted = 0
    with conn, conn.cursor() as cur:
        for user_id, email, display_name, sex, birth_year in HOUSEHOLD:
            cur.execute(
                """
                INSERT INTO users
                    (tenant_id, id, email, display_name, password_hash,
                     biological_sex, birth_year, home_timezone,
                     created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (tenant_id, id) DO NOTHING
                """,
                (TENANT_ID, user_id, email, display_name, PASSWORD_HASH,
                 sex, birth_year, HOME_TZ),
            )
            inserted += cur.rowcount
            cur.execute(
                """
                INSERT INTO user_preferences
                    (tenant_id, user_id, created_at, updated_at)
                VALUES (%s, %s, NOW(), NOW())
                ON CONFLICT (tenant_id, user_id) DO NOTHING
                """,
                (TENANT_ID, user_id),
            )
    conn.close()
    print(f"Stage 0: {len(HOUSEHOLD)} household users ensured "
          f"({inserted} newly inserted). All passwords: 'Password2026'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
