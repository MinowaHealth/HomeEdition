# UserApp Fuzz Test Campaign

**Doctrine.** Aggressive Schemathesis fuzz testing against the local Home Edition appliance — one box on the LAN. Two run intensities are supported: ring 1 (unbounded, full intensity) and ring 2 (throttled, gentle smoke). Both target the same local appliance; ring 2 just caps the request rate.

**Plan.** This campaign defines six profiles (A–C, E–G) and 11 specific failure modes the ring-1 campaign is built to surface (both catalogued below).

## Layout

```
UserApp/fuzztest/
  mint_key.py    Idempotent fuzz API-key minter (login → revoke matching → mint)
  profiles.sh    Six Schemathesis profiles, dispatched by $PROFILE env var
  monitor.sh     Ring-1 instrumentation collector (docker stats, pg_stat_activity, disk, threads)
  report.py      NDJSON → markdown campaign summary
  README.md      this file
```

Companion tooling reused from elsewhere:
- `UserApp/livetest/cleanup.py --fuzz-user UUID` — user_id-scoped wipe of fuzz user-owned rows.
- `UserApp/admin.py provision-user` — creates the fuzz user.

## Prerequisites

| Item | Where |
|---|---|
| Target reachable | `curl -s ${BASE_URL}/api/v1/session` returns 401 (proves the app is up) |
| Fuzz user provisioned | `UserApp/admin.py provision-user <email> <password> <display_name>` |
| 2FA disabled on fuzz user | `UPDATE users SET totp_enabled=false WHERE email='<email>';` (no CLI yet) |
| Schemathesis v4 installed | `.venv` already has `schemathesis==4.18.1` |
| Cleanup safety net | `UserApp/livetest/cleanup.py --fuzz-user UUID --dry-run` returns sensible counts |

> **psql exception.** This is dev/test tooling, so `monitor.sh` and the SQL one-liners in the workflow below shell out to `psql` / `docker exec … psql` for read-only sampling and one-off fixups. That is a deliberate exception to the project's "no psql CLI" rule, which governs application code — not this throwaway harness.

## End-to-end workflow

### 1. Bring up the target

The appliance runs as a local docker-compose stack:
```bash
cd ~/HomeEditionPrep
docker compose --project-directory . \
  -f HowToDeploy/docker-compose.local.yml \
  --env-file local.env up -d
```

Fuzz accounts are created on demand via the CLI (step 2) — there is no
seed-persona loader.

### 2. Provision the fuzz user

```bash
cd ~/UserApp && ./admin.py provision-user \
    fuzz@appliance.local <pw> "Fuzz User"

# Disable 2FA for the new user (no CLI yet — direct SQL):
docker exec -i pgvector psql -U postgres -d healthv10 \
    -c "UPDATE users SET totp_enabled=false WHERE email='fuzz@appliance.local';"
```

Capture the user_id for the cleanup step — run a `SELECT id FROM users WHERE email = …` once and stash it.

### 3. Mint an API key

```bash
cd UserApp/fuzztest
source ../../.venv/bin/activate
export FUZZ_KEY=$(python mint_key.py \
    --base-url http://localhost \
    --email fuzz@appliance.local \
    --password <pw> \
    --ring 1)
echo "minted key prefix: ${FUZZ_KEY:0:12}…"
```

`mint_key.py` writes diagnostic output to stderr and the raw `hbk_*` value to stdout — `$(...)` captures it cleanly.

### 4. Run a profile

```bash
# Ring 1, all six profiles in sequence with triage gates between each.
export BASE_URL=http://localhost
export RING=1

for P in A B C E F; do
  PROFILE=$P ./profiles.sh || { echo "profile $P failed; triage before continuing"; break; }
done
```

For **profile G** (stress-to-failure), spawn `monitor.sh` in another shell first:

```bash
# Shell 1 (monitor) — runs on the appliance box, uses docker exec by default.
./monitor.sh
# OUT_DIR is printed; keep this shell open until you Ctrl-C.

# Shell 2 (fuzz)
PROFILE=G ./profiles.sh || echo "G exited $?"  # G is allowed to fail

# Shell 1: Ctrl-C the monitor; it writes SUMMARY.md and the TSV time-series.
```

**Ring 2** (throttled smoke) runs A–C only — same local appliance, capped request rate:

```bash
export BASE_URL=http://localhost
export RING=2
export FUZZ_KEY=$(python mint_key.py \
    --base-url $BASE_URL \
    --email fuzz@appliance.local \
    --password <pw> \
    --ring 2)

for P in A B C; do
  PROFILE=$P ./profiles.sh || echo "profile $P failed; triage"
done
```

### 5. Generate the campaign report

```bash
python report.py fuzz-reports/
# Writes fuzz-reports/CAMPAIGN_REPORT.md
```

### 6. Clean up

```bash
# Get the fuzz user's UUID once:
FUZZ_UUID=$(python mint_key.py --base-url $BASE_URL --email <fuzz email> --password <pw> --ring 1 --revoke-only 2>/dev/null; \
  docker exec -i pgvector psql -U postgres -d healthv10 \
      -At -c "SELECT id FROM users WHERE email='fuzz@appliance.local';")

cd ../livetest
python -m livetest.cleanup --fuzz-user "$FUZZ_UUID" --dry-run   # preview
python -m livetest.cleanup --fuzz-user "$FUZZ_UUID"             # actually delete
```

Run cleanup unconditionally — including after a non-zero exit from profile G.

## What each profile catches

| Profile | Catches | Typical wall-clock |
|---|---|---|
| **A** read-only smoke (unauth GETs) | Spec drift on stable read paths, 500-on-no-auth, content-type drift | 5 min (ring 1), 1–2 min (ring 2) |
| **B** authenticated CRUD | Wrong field names in create/update bodies, status-code drift (500 vs 400/422), missing required fields on 201 | 15 min (ring 1), 5 min (ring 2) |
| **C** negative & boundary (vitals POSTs) | Systolic 39/40/41, pulse 19/20/250/251, type confusion, off-by-one validation | 10 min (ring 1), 4 min (ring 2) |
| **E** stateful (OpenAPI links) | Workflow bugs — created resource not found for follow-up update/delete, soft-delete vs hard-delete divergence | 15 min |
| **F** destructive churn | pgvector embed daemon backpressure, statement-timeout 503 rate, lock contention on concurrent same-row CRUD | 12 min |
| **G** stress-to-failure | Failure modes catalog (see below) | Until something breaks — set a 4-hour ceiling |

## Failure-mode catalog (profile G deliverable)

This campaign defines 11 failure modes the ring-1 run should either *observe with metrics* or *rule out with evidence*. Both `monitor.sh` and `report.py` are built to feed this list:

1. Gunicorn worker OOM-kill — `docker stats`, `dmesg`
2. Postgres connection pool exhaustion — `pg_connections.tsv`
3. Statement-timeout cascade — 503 QUERY_TIMEOUT rate in `report.py` table
4. pgvector embedding daemon thread accumulation — `webapp_threads.tsv` (this is the leak detector)
5. _(retired)_ Rate-limit store OOM — Home Edition does no request rate limiting at all (no limiter, no rate-limit store), so there is nothing here to exhaust. Kept as a numbered slot so #6–#11 references stay stable.
6. Session table bloat — `sessions_count.tsv`
7. Disk fill — `disk.tsv`
8. _(retired)_ Mailgun queue depth — Home Edition has no Mailgun/email integration. Kept as a numbered slot so #9–#11 references stay stable.
9. API-key cap behavior under concurrent mint — application error rate in `report.py`
10. Lock contention — `pg_locks.tsv` (slow_locks column)
11. Gunicorn worker timeout firing — 502/504 status counts

`monitor.sh` writes a `SUMMARY.md` on shutdown that flags which of these modes hit observable thresholds.

## Triage gates (between profiles)

Four severity tiers decide whether to continue:

- **Critical** — 5xx where 4xx is correct, auth bypass, tenant data leak, P0 confirmed → block ring 2 until fixed.
- **High** — spec drift causing client breakage → fix before ring 2; unaffected profiles can proceed in parallel.
- **Medium** — response-shape drift on non-load-bearing fields, 422 vs 400 inconsistencies → file and continue.
- **Low** — documentation tidy-ups → single rollup issue.

Profile G findings are **informational, not gating** — they go into the campaign report regardless of severity.

## Safety properties

- **Local-only.** `profiles.sh` refuses any public host — it rejects an `https://` `BASE_URL` that is not a `localhost` target. Home Edition fuzzing only ever points at the local appliance.
- **One fuzz user per ring.** Cleanup is user_id-scoped, so even if `--fuzz-user` is invoked with a wrong UUID, only that user's rows are touched (and the wrong UUID has no rows).
- **Real account data untouched.** Fuzz only ever authenticates as the CLI-provisioned fuzz user, and cleanup is user_id-scoped to that user's `--fuzz-user` UUID.
- **API keys idempotent.** `mint_key.py` revokes any pre-existing key with the same label before minting, so re-runs never blow past `MAX_API_KEYS_PER_USER=5`.

## Common failure-mode interpretations

| Symptom | Likely root cause | First check |
|---|---|---|
| Many `response_schema_conformance` failures on one operation | Spec drift — response shape doesn't match openapi.yaml | Compare actual `response_body` from report.py against the schema |
| `not_a_server_error` failures (5xx) on POST endpoints | Either real bug or `503 QUERY_TIMEOUT` (DB timeout) | Filter report.py output by `503 (likely QUERY_TIMEOUT)` classification |
| pgvector thread count monotonic in `webapp_threads.tsv` | Daemon embedding thread leak ([`vitals.py:518`](../webapp/routes/vitals.py#L518)) | Check if drains within 5 minutes of POST traffic stopping |
| `sessions` table count growing without bound | No session pruner; 24h auto-extend keeps everything alive | Confirm by checking if any `sessions` rows have `expires_at < now()` |

## Doctrine

- Schemathesis is the load generator; the **deliverable on ring 1 is the failure-mode catalog**, not a "pass" verdict.
- Ring 2 is a *throttled smoke* of the same appliance — a gentle contract re-check, not a stress test. Ring 1 already exercised the app at full intensity.
- Drift the campaign surfaces is *first* a spec change OR an implementation fix — never a "tolerate the drift" decision. The OpenAPI contract is load-bearing for mobile clients and external integrations.
