# Data Sync Log — Plan 1

**Date:** 2026-07-14
**Timestamp:** 2026-07-14T10:05:00Z (rev 2 — added observation feed fix rider)
**Status:** Draft for review

## Context

When a Garmin sync or HealthKit import completes, the user has no visible record of it — sync state lives only in `garmin_sync_jobs` / `healthkit_import_jobs`, reachable through job-status endpoints nobody browses. Requirement (Neal, 2026-07-13): sync runs write a timestamped entry the user can see in their activity log. This is a new class of feed data — system/source events, not health observations — and the design must extend to future ecosystems (Samsung Health, etc.) without rework.

Decision already made (2026-07-13): **new unified `data_sync_log` table** that every ecosystem's worker writes on completion, rather than projecting the per-source job tables into the feed at read time. One table, one feed source, uniform shape; a new ecosystem is a new `source` value, not a new query. Failed syncs appear in the feed alongside completed ones (answers "why is my data missing" without support involvement); pending/running runs stay hidden.

## Schema

New table in `Infrastructure/init/docker-init-v10/02-healthv10_schema.sql` (source of truth) + idempotent delta file for existing hosts:

```sql
CREATE TABLE data_sync_log (
    tenant_id SMALLINT NOT NULL DEFAULT 1,
    id uuid DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    source text NOT NULL,                -- 'garmin' | 'healthkit' | future: 'samsung', ...
    job_id uuid,                         -- id of the originating job row (garmin_sync_jobs / healthkit_import_jobs)
    status text NOT NULL CHECK (status IN ('completed', 'failed')),
    detail jsonb,                        -- per-source counts, e.g. {"heart_rate": 1440, "sleep": 1}
    error_message text,                  -- populated when status = 'failed'
    synced_at timestamptz NOT NULL,      -- run completion time (UTC) = feed timestamp
    created_at timestamptz DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX idx_data_sync_log_user_time
    ON data_sync_log (tenant_id, user_id, synced_at DESC);

ALTER TABLE data_sync_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_sync_log FORCE ROW LEVEL SECURITY;

CREATE POLICY data_sync_log_tenant_user ON data_sync_log
    FOR ALL
    USING (tenant_id = current_setting('app.current_tenant_id')::SMALLINT
           AND user_id = current_setting('app.current_user_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::SMALLINT
                AND user_id = current_setting('app.current_user_id')::uuid);
```

Design notes:
- Append-only; no UPDATE path. Rows are small (counts, not data).
- `source` is deliberately unconstrained text — adding Samsung is an INSERT with a new value, no ALTER. `status` gets a CHECK because its vocabulary is fixed by the feature (only terminal states are logged).
- `job_id` has no FK because its target table differs per source; it's a drill-down pointer to the existing `/garmin/jobs/<id>` / `/healthkit/jobs/<id>` endpoints.
- Job tables stay untouched and remain the operational record (pending/running/progress); `data_sync_log` is the user-facing history.
- ERD: add to `DataModel3/HealthDatabaseERDv10.md` table inventory + mermaid block (`users ||--o{ data_sync_log`).
- Delta file: `Infrastructure/init/docker-init-v10/deltas/2026-07-14-data-sync-log.sql` (idempotent — `CREATE TABLE IF NOT EXISTS`, `DROP POLICY IF EXISTS` + `CREATE POLICY`). Neal applies on prod manually; agents apply on nealvm/testing.

## Writers

Both workers already have the terminal-state UPDATE with the values we need; each gains one INSERT in the same transaction, both success and failure paths:

1. **`UserApp/webapp/garmin_worker.py` — `process_garmin_sync()`**
   - Success path (currently `status='completed', completed_at=now(), progress=...` at ~174-180): also `INSERT INTO data_sync_log (user_id, source, job_id, status, detail, synced_at) VALUES (%s, 'garmin', %s, 'completed', %s::jsonb, now())` with the same counts dict.
   - Failure path (~210-216): same INSERT with `status='failed'`, `error_message`, `detail` NULL.
2. **`UserApp/webapp/healthkit_worker.py` — `process_healthkit_job()`**
   - Success path (~101-123): INSERT with `source='healthkit'`, `detail` = the per-category counts dict from `import_healthkit_export()` (records, workouts, clinical_records, ...), not just the flattened total.
   - Failure path (~144-150): `status='failed'` + `error_message`.

Workers run with the user's RLS context already set (they update the RLS-forced job tables today), so `WITH CHECK` passes with no connection changes.

**Backfill: dropped** (Neal, 2026-07-14) — forward-only; the log starts empty and fills as syncs run.

## Feed surface

**`GET /all-logs`** (`UserApp/webapp/routes/logging_routes.py`, `get_all_logs`):
- Add `sync` to `ALL_SOURCES` and a ninth per-source SELECT over `data_sync_log` (LIMIT 100, participates in `sources_truncated` like the others), emitting:
  ```json
  {
    "type": "sync",
    "source": "garmin",
    "status": "completed",
    "detail": {"daily_summaries": 1, "sleep": 1, "heart_rate": 1440, "stress": 96},
    "error_message": null,
    "job_id": "…",
    "timestamp": "2026-07-13T09:12:44Z"
  }
  ```
- `KIND_SOURCES` gains `'sync': {'sync'}` so `?kind=sync` filters to sync events only, honored in the `applied` echo.

**MCP `get_recent_activity`** (`UserMCP/tools/activity.py`): `kind` enum gains `"sync"`; description gains one clause ("…and data-source sync events (Garmin, HealthKit)"). No stack involvement — the stack-invisibility sweep passes untouched. The existing coverage-honesty logic picks up the new kind for free via the `applied` echo.

**SPA** (`UserApp/webapp/index.html`, feed renderer around the `/all-logs` fetch at ~line 3540): add a `type === 'sync'` case — one line, e.g. `✓ Garmin sync — 1,440 HR samples, sleep, stress` / `✗ HealthKit import failed`. Unknown-type handling today should be checked; if the renderer drops unknown types silently, the backend can ship first and the SPA case follows.

## Rider: observations missing from the feed (fix in the same change)

Found while scoping this plan; Neal ruled it must be addressed (2026-07-14).

**Problem.** `/all-logs` never queries `health_observations` — observations are absent from the activity feed entirely. Meanwhile the MCP tool's contract promises them twice over: `get_recent_activity`'s description says the feed includes "observations," and its `kind` enum offers `"observation"`. Today that call returns every feed type *except* observations, plus a coverage gap saying the filter wasn't applied. The route even documents the drift (`logging_routes.py:635-637`: "the client enum will drift before this route does").

**Fix** (same mechanics as the sync source, so it rides along):
- Add an `observations` source SELECT over `health_observations` (LIMIT 100, `sources_truncated` participation), emitting:
  ```json
  {
    "type": "observation",
    "category": "symptom",
    "content": "…",
    "severity": 3,
    "tags": ["…"],
    "timestamp": "<observed_at>"
  }
  ```
- `ALL_SOURCES` gains `observations`; `KIND_SOURCES` gains `'observation': {'observations'}`.
- MCP side needs **no change** — the existing description and enum become truthful, and the coverage-gap warning disappears because `applied.kind` now echoes `observation`.
- Update the route docstring (the "eight per-source SELECTs" count becomes ten with sync + observations) and the `UserAPI.md` `/all-logs` section.

**HIPAA note for the rider:** observation `content` (including `mental_health_flag`-tagged rows) enters the merged feed. This is self-access — the dedicated `/observations` endpoints already return the same rows to the same authenticated user under the same RLS policy (`health_observations_isolation`), so no new disclosure class and no new audit row; § 164.502(b) unchanged.

## Documentation

- `APIDocumentation/UserAPI.md`: `/all-logs` section — new `type: sync` entry shape + `kind=sync` filter.
- `openapi.yaml`: **deferred** — the file carries in-flight uncommitted unit-system edits; same treatment as the Garmin point-in-time endpoints (route audit allowlist if the drift gate complains, spec entry when the file is clean).
- `DataModel3/HealthDatabaseERDv10.md`: table inventory + mermaid (see Schema).

## HIPAA compliance check

New PHI-adjacent read/write path (sync metadata in a user-visible feed):
- **§ 164.312(a) Access Control:** standard inline tenant+user RLS policy (USING + WITH CHECK), same pattern as every user-data table; workers write under the user's RLS context.
- **§ 164.312(d) Person/Entity Authentication:** unchanged — feed is behind existing session/bearer auth; MCP path proxies per-request bearer through UserApp.
- **§ 164.502(b) Minimum Necessary:** rows carry counts and status only — no health values. `error_message` must stay operational (exception text), never echo record content; worker code review point.
- **§ 164.312(b) Audit Controls / § 164.528 Accounting of Disclosures:** this is self-access to the user's own sync metadata via an already-logged endpoint — no new disclosure class, no new audit row required. Existing UserApp request logging covers the access; the `data_sync_log` rows themselves are the durable record of the sync events.
No other § 164.3xx rules are implicated.

## Testing

- Worker tests: success + failure paths INSERT the expected row (both workers).
- Route test: `/all-logs` returns `type=sync` and `type=observation` events merged in timestamp order; `kind=sync` and `kind=observation` honored in `applied`.
- MCP: `get_recent_activity(kind="sync")` round-trips; existing gap-test for unapplied kinds updated (observation no longer gaps); the every-tool wire sweep and stack-invisibility sweep cover the rest automatically.
- RLS: new table must appear in the deterministic audit chain (`rls_audit.py` will flag a policy-less table — the inline policy above satisfies it).

## Rollout

1. Schema: source-of-truth edit + delta file. Apply delta on nealvm; hand Neal the delta path for prod (his manual workflow).
2. Workers + feed + MCP in one promotion (backend-complete without SPA).
3. SPA renderer case.
4. Verify on nealvm: trigger `POST /garmin/sync`, confirm feed entry appears via `/all-logs` and `get_recent_activity(kind="sync")` in Claude Desktop (⌘Q restart).

## Open items — resolved 2026-07-14

1. Backfill: **out** (Neal) — forward-only.
2. `cancelled` Garmin jobs: **not logged** — user-initiated cancel isn't a sync event.
3. SPA rendering: **minimal now** — SYNC/OBS badges + description text; design pass later if Dima wants one.

**Status: APPROVED — built 2026-07-14, deployed to nealvm.**
