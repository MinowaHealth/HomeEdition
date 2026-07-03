# Test Dataset — Borgia Household

**Last updated: 2026-07-03 02:15 PDT**

Test data for **Minowa.ai Home Edition** qualitative QA and database volume testing. It is a single household of six members drawn from the **Borgia family** (public domain, Renaissance Italy), used in a fictional modern health-tracking context. All passwords are `Password2026`.

Home Edition is a single-household appliance: one box, one household, `tenant_id = 1` everywhere. There are no providers, organizations, or delegations in this dataset — those belong to the multi-tenant Central System and are deliberately absent here (see *Household trust model*).

---

## The household (6 users)

| Email | Member | Sex | Born | Role in testing |
|-------|--------|-----|------|-----------------|
| `rodrigo@borgia.family`  | Rodrigo Borgia        | male   | 1975 | Account owner / admin; daily BP pact with Vannozza |
| `vannozza@borgia.family` | Vannozza dei Cattanei | female | 1978 | Spouse; 07:00 BP pact partner |
| `lucrezia@borgia.family` | Lucrezia Borgia       | female | 2012 | Minor child; 8 scripted migraine episodes |
| `juan@borgia.family`     | Juan Borgia           | male   | 2014 | Minor child; light activity |
| `cesare@borgia.family`   | Cesare Borgia         | male   | 1970 | Extended family / elder; heavier stack logging |
| `adriana@borgia.family`  | Adriana de Mila       | female | 1940 | Elderly; 6 scripted missed-dose (warfarin) events |

All six share the deterministic UUID prefix `b015b015-0001-0001-000N-…` (N = 1..6) so the seeder's per-persona RNG and the `records/*.json` `user_id`s line up reproducibly on any host. Home timezone is `America/New_York`.

---

## Household trust model

Home Edition has **no Row-Level Security**. The Central System uses RLS to isolate tenants and model delegated access; the appliance drops that entire layer and instead enforces privacy **in the application**, with an explicit `user_id` predicate on every query against a user-owned table.

In the dataset that means:

- Every row is owned by exactly one of the six members via `user_id`.
- `tenant_id = 1` on every row.
- There is **no** cross-user grant, share, or visibility object anywhere — no delegation table, no provider access, no "caregiver can see X."
- The "caregiver / elder" framing of Adriana and Cesare is narrative only: each member's data is their own. Caregiving is *not* modeled as access over another member's records.

Free-text doctor names (e.g. `"Dr. Torrella"` in a record's `treating_doctor` field) are plain strings — narrative flavor on a patient's own record, not foreign keys to any provider table. They stay.

---

## Files

| Path | Purpose |
|------|---------|
| `seed_users.py` | **Stage 0** — provision the 6 accounts (psycopg 3, idempotent). Run first. |
| `three_month_seed/` | Stages 1–6 temporal seeder package (the volume driver). |
| `records/*.json` | Per-member clinical scaffolding (conditions, allergies, meds, stacks, history, vitals, vaccinations) — Stage 1 source. |
| `load_records.py` | Simple API loader for `records/*.json` — quick smoke tests against a live webapp. |

### Seeder stages

`python -m TestData.three_month_seed` generates a configurable volume of health activity over a rolling 90-day window, posting through the UserApp REST API (app-level `user_id` scoping; no RLS):

- **Stage 0** — accounts (`seed_users.py`, run separately, before the rest).
- **Stage 1** — clinical scaffolding from `records/*.json` via the API.
- **Stage 2** — per-day activity (BP, weight, `log_stack`, `log_meal`, observations, narrative beats). This is the volume driver, scaled by `SCALE` and `WINDOW_DAYS`.
- **Stage 5** — pgvector embedding fill (768-dim, via Ollama; direct DB).
- **Stage 6** — read-only verification (row-count guards).

A **cohort gate** runs first and refuses to proceed unless the DB holds exactly the six test users — it never auto-deletes.

---

## How to run

Run on the appliance (or the local dev stack), where the UserApp API and Postgres are reachable. Use the repo `.venv`. Stage 0 first, then the temporal seeder:

```bash
# Stage 0 — accounts
SEED_DB_USER=postgres SEED_DB_PASSWORD=<pw> \
  .venv/bin/python TestData/seed_users.py

# Stages 1–6 — temporal activity
SEED_TEST_DATA=true \
  SEED_DB_USER=postgres SEED_DB_PASSWORD=<pw> \
  SEED_DB_HOST=localhost SEED_DB_NAME=healthv10 \
  SEED_API_BASE_URL=http://localhost \
  OLLAMA_URL=http://localhost:11434 \
  .venv/bin/python -m TestData.three_month_seed
```

### Flags

- `--reset` — DELETE the seeder-owned `tenant=1` rows, then re-seed.
- `--verify-only` — skip Stages 1–2/5; run Stage 6 assertions only.
- `--no-embeddings` — skip Stage 5 (no Ollama needed; fast ad-hoc runs).
- `--no-stage1` — skip the scaffolding loader.
- `--persona <email-substring>` — seed a single member (debug aid).

### Env vars

- `SEED_TEST_DATA=true` — required gate (the seeder refuses to run otherwise).
- `OLLAMA_URL` — required (e.g. `http://localhost:11434`).
- `SEED_DB_USER`, `SEED_DB_PASSWORD` — required admin creds.
- `SEED_DB_HOST` (`localhost`), `SEED_DB_PORT` (`5432`), `SEED_DB_NAME` (`healthv10`).
- `SEED_API_BASE_URL` (`http://localhost`) — UserApp base URL the seeder POSTs to.
- `WINDOW_END` (`2026-05-08`), `WINDOW_DAYS` (`90`) — activity window.
- `SEED` (`42`), `SCALE` (`1.0`) — RNG seed + per-day event multiplier.
- `EMBEDDING_MODEL` (`nomic-embed-text-v2-moe:latest`), `OLLAMA_TIMEOUT` (`30`).
- `SEED_LOGIN_THROTTLE_SECS` (`13`), `SEED_POST_THROTTLE_SECS` (`0.55`) — pace logins/POSTs under UserApp's rate limits. Lower (or `0`) for high-volume runs where the limiter is bypassed or loopback-whitelisted.
- `LOG_LEVEL` (`INFO`), `OUTPUT_DIR` (`TestData/three_month_seed/output`).

**Volume testing.** Dial `SCALE` and `WINDOW_DAYS` up to drive larger row counts across `health_*` (and embeddings) without code changes; lower the throttles so a big run completes in reasonable wall-clock.

---

## Test scenarios

1. **Per-user isolation (the core Home Edition property).** Authenticate as Rodrigo — the API returns only Rodrigo's rows, never another member's. With no RLS, this is enforced entirely by the application's `user_id` predicates; a regression shows up as one member seeing another's data. (Runtime guard: `tests/integration/test_user_scope_isolation.py`.)
2. **Scripted clinical arcs.** Lucrezia's 8 migraine episodes, Adriana's 6 missed warfarin doses, and the Rodrigo/Vannozza 07:00 BP pact land on deterministic dates — useful for testing timelines, search, and summaries.
3. **Volume / load.** Raise `SCALE`/`WINDOW_DAYS` and confirm the appliance handles the resulting `health_*` and embedding volume.

---

## What this is / isn't

**In scope:** six-member single household, 90-day temporal activity, deterministic UUIDs + seeded RNG, pgvector embeddings, narrative beats.

**Out of scope:** HealthKit/Garmin source-table seeding (`hkit_*`, `garm_*` stay empty), mobile RxDB sync, and any multi-tenant / provider / delegation constructs (those are Central-only and intentionally absent).
