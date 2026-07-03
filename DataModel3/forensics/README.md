# DataModel3/forensics/

One-shot diagnostic scripts produced during specific incidents. **Not on the
standard audit chain** (that's `run_full_audit.sh` one directory up) — these
run by hand when a similar shape of incident recurs.

**`db_sanity_check.sh` / `db_sanity_check.sql`** (origin: 2026-04-13 post-restore
validation) — Post-restore validation runbook. Confirms `healthv10` exists,
lists schemas + table counts, checks critical tables exist, samples row counts,
verifies required extensions, the app role, and that **no table policies are
present** (a single-household database expects none). Runs against local
Docker, or over ssh to the appliance box.

## Usage

```bash
# Local Docker
./DataModel3/forensics/db_sanity_check.sh

# Remote appliance over ssh
./DataModel3/forensics/db_sanity_check.sh remote user@home-box
```

## Reuse

Incidents recur in shape, not on a schedule. If a similar issue appears, start
by running the matching file here and tailoring it — don't reinvent. New
one-shot scripts from future incidents land in this folder.
