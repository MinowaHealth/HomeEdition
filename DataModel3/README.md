# DataModel3 — Database Reference & Audit Tooling

**Date**: 2026-07-03 04:15 PDT

Schema documentation and audit tooling for the Home Edition database (`healthv10`).

## Authoritative SQL

The schema source of truth is [`Infrastructure/init/docker-init-home/02-home_schema.sql`](../Infrastructure/init/docker-init-home/02-home_schema.sql). Roles, grants, and query-performance indexes live in [`role/app-role-setup.sql`](../Infrastructure/init/docker-init-home/role/app-role-setup.sql). Everything in this folder documents or audits that schema — deploy from the init scripts, never from here.

## Documents

| File | Purpose |
|------|---------|
| **HomeDatabaseERD.md** | Entity relationship diagrams, one per domain |
| **HomeDatabaseReport.md** | Complete per-table schema reference (columns, constraints, indexes, FKs) |
| **AppleHealthKitDataModel.md** | HealthKit ingest tables (`hkit_*`) and the import pipeline |
| **AppleHealthKitERD.md** | ERD for the HealthKit domain |
| **HealthKitDataModel.md** | Apple HealthKit taxonomy (HK type identifiers) and the Minowa mapping |
| **Authentication.md** | Auth data model: sessions, API keys, 2FA, account lifecycle |
| **EmbeddingDesign.md** | pgvector embedding design and usage |
| **EnvironmentVariables.md** | Environment variable reference for the whole stack |
| **TIMESTAMPS.md** | UTC-at-rest and per-user timezone policy |
| **UpdatedAtPolicy.md** | When `updated_at` needs a trigger vs a route-side bump |
| **VariousDataSources.md** | The source-vs-derived rule (`hkit_*`/`garm_*` → `health_*`) |

## Audit tooling

| Tool | What it does | How to run |
|------|--------------|------------|
| `run_code_query_audit.sh` | Pre-commit gate: verifies every SQL string in UserApp/UserMCP against the schema | `bash DataModel3/run_code_query_audit.sh` |
| `run_full_audit.sh` | CI entrypoint: three-step chain (drift, column reachability, SQL legality) | `bash DataModel3/run_full_audit.sh [prod-snapshot]` |
| `code_query_audit.py` | The SQL-vs-schema audit engine (ast + sqlglot) | invoked by the wrappers, or `--service UserApp` |
| `unused_columns_audit.py` | Schema-column → code reachability; writes `UnusedColumnsAudit.md` | `python3 DataModel3/unused_columns_audit.py` |
| `compare_full.py` | Section-by-section drift diff between a live snapshot and `schema-reference.txt` | `python3 DataModel3/compare_full.py <snapshot.txt>` |
| `generate_reference_snapshot.sh` | Rebuilds `schema-reference.txt` from the schema SQL in a throwaway container | `bash DataModel3/generate_reference_snapshot.sh` |
| `export-schema-snapshot.sql` | psql report of a live database's full state | see `mkreport` |
| `count_records_from_dump.py` | Per-user row counts from a `pg_dump` file | `python3 DataModel3/count_records_from_dump.py <dump.sql>` |
| `forensics/` | Post-restore sanity-check runbook (`db_sanity_check.sh`) | see `forensics/README.md` |

Snapshot a live database for drift comparison:

```bash
cat DataModel3/export-schema-snapshot.sql | docker exec -i hb-local-postgres psql -U postgres -d healthv10 > snapshot.txt
python3 DataModel3/compare_full.py snapshot.txt
```

## psycopg3 conventions

All Postgres access goes through psycopg 3 via the `db_driver.py` shim (`UserApp/webapp/db_driver.py`) — touch the shim, not raw `psycopg`. The conventions that matter:

1. **`with conn:` closes the connection, it does not commit.** For transaction semantics use `with conn.transaction():`; manage connection lifetime through the shim's pool helpers.
2. **UUID columns return `uuid.UUID`, not `str`, under `dict_row`.** When a UUID column flows into a dict key, dict lookup, or string equality, coerce with `str(row['col_id'])`. Native UUID is the default everywhere else — that is a feature.
3. **Parameterized `SET name = %s` is rejected** by psycopg3's server-side binding. Use the shim's `set_session_var()` helper, which issues `SELECT set_config(name, value, is_local)` instead.
4. **The shim is the API.** It exposes a stable surface (`connect`, `make_pool`, `register_pgvector`, `executemany_rows`, `transaction`, `set_session_var`, error re-exports) and is the seam for any future driver change.

## Known schema conventions

**Garmin table prefix inconsistency**: data tables use a truncated `garm_` prefix while credential/sync tables use the full `garmin_` prefix. This is intentional — not worth migrating. All application code correctly references both prefixes; do not rename.

| Prefix | Tables |
|--------|--------|
| `garm_` | `garm_hr`, `garm_sleep`, `garm_sleep_events`, `garm_stress`, `garm_daily_summ`, `garm_rr` |
| `garmin_` | `garmin_credentials`, `garmin_sync_jobs` |
