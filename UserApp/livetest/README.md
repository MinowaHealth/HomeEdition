# Live Tests — RunBook

**Date:** 2026-06-13
**Time:** 18:00 UTC

Live integration test harness for UserApp. Runs real HTTP requests against a running server and verifies data persistence by querying Postgres directly as the single `healthv10_app` role. Home Edition has no RLS — there is no session context to set; verification queries scope rows explicitly with `tenant_id` and `user_id` predicates (e.g. `WHERE tenant_id=%s AND user_id=%s`), the same way the app enforces privacy in its own SQL. Complements mocked unit tests under `webapp/tests/` with tests that cannot be fooled by a mock that returns what the test expects.

---

## Why This Exists

Mocked-DB tests give green CI on bugs that kill production. The motivating example: `/api/v1/food-items` POST had a test that pre-stubbed `cur.fetchone.return_value` and asserted `conn.commit.assert_called()`. The test passed whether the real INSERT would succeed, fail silently, or hit a nonexistent table.

Live tests close that gap: POST to the real endpoint, SELECT from Postgres as the `healthv10_app` role scoping by `tenant_id`/`user_id`, assert on actual row counts.

---

## Preconditions

1. The UserApp is running and reachable from the runner machine (typically the local Mac Docker stack, or the appliance on the LAN).
2. `test@example.com` exists on the target with password `Password2026`. The local Mac stack's `scripts/local-init-db.sh` creates it; otherwise provision it directly:
   ```
   cd UserApp && ./admin.py provision-user test@example.com password 'Test User'
   ```
3. `UserApp/.env.livetest` exists on the runner machine (see `.env.livetest.example` for the template). If the harness runs on a different machine than the target, `DB_HOST` must point at the target's Postgres (LAN address or local port).

---

## Running

### All flows

```
cd UserApp
python -m livetest.run_all --base-url http://localhost:80
```

Exit code `0` if all passed, `1` if any failed. Markdown report written to `livetest/reports/`.

### One flow

```
python -m livetest.flows.food_items --base-url http://localhost:80
```

Every flow is both importable (for `run_all.py`) and directly runnable.

---

## Interpreting Output

Stdout shows each step with `PASS` / `FAIL` / `SKIP` markers, duration, and message. The markdown report under `livetest/reports/` has a summary table (one row per flow) and per-flow detail tables (one row per step).

---

## Security-defense flows

The `security_*` flows test that defenses against known security findings
(F1) reject forged requests. Each flow has two kinds of step:

- **Baseline / negative-control steps** that prove the route is reachable
  under normal conditions. These pass green.
- **Ratchet steps** marked `xfail="<finding>: ..."` that assert the
  *correct* (post-fix) behavior. Today they fail (the defense isn't in
  place yet) and the runner records `xfail` (yellow `x` marker, counts
  as pass). When the underlying fix lands, the step starts succeeding —
  the runner records `xpass` (red `X` marker, counts as **fail** — strict
  xpass) and the next PR must remove the `xfail=` argument.

Flows:

| Flow | Finding | Today | Once finding is fixed |
|---|---|---|---|
| `security_csrf` | F1 — no CSRFProtect | xfail (route returns 201) | xpass → remove marker |

The companion HEALTHKIT_SYNC env-var fallback test (F3) is a unit test
under `webapp/tests/test_security_defense.py` (rather than a livetest
flow) because it requires booting the app with specific environment
variables — easier to monkeypatch in pytest than to coordinate with a
running server.

## Common Failures

| Symptom | Diagnosis |
|---|---|
| `401 Unauthorized` at login | `test@example.com` missing or wrong password. Re-provision via admin.py. |
| `psycopg.OperationalError` opening the DB connection | `APP_DB_PASSWORD` in `.env.livetest` wrong, or PG unreachable. |
| `expected 201, got 500` | Backend exception. Check the webapp logs: `docker compose logs webapp`. |
| `delta 0, expected 1` | **Silent write loss** — the bug class this harness exists to catch. |
| `assert any(...)` fails on GET after POST | Write succeeded but the verification query can't see it — check the `tenant_id`/`user_id` predicate matches the logged-in user, or the app wrote the row under a different `user_id`. |

---

## Cleanup

The harness leaves data behind by design — delta-based assertions tolerate accumulated rows. When buildup gets noisy, run the cleanup subcommand:

```
cd UserApp
python -m livetest.cleanup                 # destructive, all targets
python -m livetest.cleanup --dry-run       # report what would be deleted
python -m livetest.cleanup --target meals  # restrict to one table (repeatable)
```

Two filter strategies in one pass:
- **Name-prefix** — 12 tables where flows write `livetest-<uuid>` into a text column. Matched via `LIKE 'livetest-%'`.
- **Sentinel-value** — 3 vitals tables with no name column. Matched on impossible readings (systolic=222/diastolic=111, weight=333.3, temperature=109.9 F).

**Composite-FK prep step:** Before DELETEs, cleanup nulls `health_input_log.stack_id` and `health_food_logv2.timeframe_id` for livetest-named parents. This works around a schema bug where `ON DELETE SET NULL` on a composite FK would null the dependent's `NOT NULL` `tenant_id`. When the schema bug is fixed, this prep step can be removed from `cleanup.py`.

**Steady state:** A successful `cleanup` then `run_all` cycle leaves exactly one `dietary_settings` row (singleton-with-history pattern). Everything else is at zero.

---

## Design Decisions

- **Sequential execution** — parallelism would race delta assertions. One session, one PG connection, one report.
- **No per-run cleanup** — delta-based assertions don't need it; standalone cleanup is the operator tool.
- **Unique naming** — `livetest-<uuid>` prevents collisions across runs without cleanup.
- **Sentinel values for vitals** — chosen to be clinically impossible (222/111 BP, 333.3 lbs, 109.9 F) so false positives against real data cannot happen.

---

## Not Covered

- **HealthKit sync** — covered by `scripts/healthkit_sync_smoke_test.py`
- **Garmin sync, OCR, fax, MCP tools** — each needs its own test pattern
- **CI integration** — deferred until harness proves stable
- **Parallel execution** — breaks delta assertions
