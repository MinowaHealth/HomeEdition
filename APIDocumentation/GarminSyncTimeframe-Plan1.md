# Garmin Sync Timeframe Picker — Plan 1

**Date:** 2026-07-15 09:05 local (America/Chicago)
**Status:** APPROVED 2026-07-15 (Week default; other recommendations stand)

## Problem

`POST /api/v1/garmin/sync` today takes optional `from_date`/`to_date`; the SPA
sends `{}` and always gets the server default (last 90 days, stretched back to
`last_sync` if older — `integrations.py:375-386`). Users can't choose a
smaller pull, and there is no way to pull full history. The worker already
handles arbitrary windows (`garmin_worker.py` day-loop), so this is a route
param + UI picker, not a pipeline change.

## Design

### 1. API — `range` parameter on the existing route

`POST /garmin/sync` body gains optional `range` ∈ `week | month | quarter | all`.

| range   | from_date (user-local today − N) | notes |
|---------|----------------------------------|-------|
| week    | 6 days back (7 inclusive)        | |
| month   | 29 days back (30 inclusive)      | |
| quarter | 89 days back (90 inclusive)      | matches today's default width |
| all     | `2010-01-01`                     | Garmin Connect era floor, module constant |

- Precedence: explicit `from_date`/`to_date` > `range` > current self-heal
  default. **No-body behavior is unchanged** (mobile/MCP callers unaffected).
- Invalid `range` → 400 with the allowed values.
- `to_date` still defaults to user-local today (`get_user_timezone()`).
- Dates land in `garmin_sync_jobs.start_date/end_date` as today; job status
  endpoints already surface them. `data_sync_log` rows unchanged (worker
  writes them regardless of window).
- Analytics: add `range` to the existing `garmin_sync_completed` capture.
- Both v1 and v2 blueprints share the handler, so both get it for free.

### 2. SPA — picker in `GarminSyncManager` (`index.html`)

- Segmented pill row above the Sync button: **Week · Month · Quarter**,
  default **Week** selected. Selection posts `{range: <choice>}`.
- **Everything** rendered apart from the pills — smaller, outline/muted
  style with a ⚠ prefix and helper text "Pulls your entire Garmin history.
  One day per request — this can run for hours." Clicking it asks
  `confirm()` before posting `{range: 'all'}`.
- Success message keeps echoing `sync_from → sync_to` so the user sees what
  the choice resolved to.

### 3. Docs + contract

- `APIDocumentation/UserAPI.md` + `openapi.yaml`: document `range` enum on
  the sync request body.
- Twin CONTRACT files: adding an optional body param may move the pinned
  route-contract hash — if it does, both copies get re-pinned together.

### 4. Tests (`test_integrations` / routes tests)

- `range=week|month|quarter` → job row window widths 7/30/90 inclusive.
- `range=all` → `start_date == 2010-01-01`.
- Bad `range` → 400.
- No body → existing self-heal default (regression pin).
- Explicit `from_date` + `range` → explicit wins.

## Decisions for Neal

1. ~~Default pill~~ — RESOLVED 2026-07-15: **Week** (regular users sync often; Neal).
2. **Keep the self-heal reach-back when a `range` is chosen?** Recommendation:
   no — an explicit choice means that window exactly; the no-body default
   keeps the self-heal behavior for scheduled/legacy callers.
3. **`all` floor date** — `2010-01-01` constant, or probe the Garmin profile
   for account age? Recommend the constant (probe is an extra API call for a
   date nobody sees; empty days no-op quickly in the worker).

## HIPAA compliance check

No new PHI access path: same authenticated route, same RLS-scoped
`garmin_credentials` read, same worker writing the same `garm_*` tables —
only the requested window changes.

- **§ 164.312(a) Access Control / § 164.312(d) Authentication:** unchanged —
  `@require_auth`, per-user credentials row, RLS context as today.
- **§ 164.502(b) Minimum Necessary:** the picker narrows typical pulls
  (week/month) below today's always-90-days default; `all` is user-initiated
  retrieval of their own data.
- **§ 164.312(b) Audit Controls / § 164.528 Accounting of Disclosures:**
  unchanged — every job is already recorded in `garmin_sync_jobs`
  (start/end dates included) and in `data_sync_log`; the chosen window is
  therefore fully reconstructible per job. No new audit row required.

No other § 164.3xx rules are implicated.

## Verification

1. Route tests above green; full UserApp suite green.
2. nealvm live: pick Week → job row shows 7-day window, feed shows the sync
   entry; Everything → confirm dialog, job starts at 2010-01-01.

## Deployment

Commit locally on `main` (no push — Neal cues promotion); deploy to nealvm
(`buddy` update.sh) for live verification.
