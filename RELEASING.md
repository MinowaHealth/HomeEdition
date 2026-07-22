# Releasing Home Edition

**Date: 2026-07-22 10:00 PDT**

## The model

- **`main` is always the most recent release.** Partial work lives in
  branches; nothing merges to main until it is releasable.
- **Release version = database schema version** — the marker in
  `Infrastructure/init/docker-init-home/02-home_schema.sql` (e.g.
  `11.1.0-home`). One number identifies the code, the schema, the tag, and
  what `/api/v1/healthz` reports.
- The `VERSION` file at the repo root is the single source of truth.
  `update.sh` / `regen.sh` export it as the `APP_VERSION` build arg.
- Schema changes ship with an **idempotent apply script**
  (`scripts/apply_<version>.py`, psycopg — never psql) so running boxes
  upgrade in place while fresh installs get the same result from the schema
  init. Re-running an applied script must be a no-op.

## Cutting a release

1. **Merge the work to main** (feature branch → main, ff or merge — main
   must end releasable).
2. **Bump the version everywhere it lives** (all must match):
   - `Infrastructure/init/docker-init-home/02-home_schema.sql` — header
     comment + `schema_version` INSERT
   - `VERSION` file
   - `APP_VERSION` in `local.env` (what a cold install's first build bakes
     in — `update.sh`/`regen.sh` override it from `VERSION`)
   - New `scripts/apply_<version>.py` if the schema changed
3. **Gates — all green, no exceptions:**
   - `cd UserApp && ../.venv/bin/pytest` and `cd UserMCP && ../.venv/bin/pytest`
   - `.venv/bin/python scripts/user_scope_audit.py` — flag count at the
     documented baseline (12 pre-auth exceptions); any delta explained
   - `.venv/bin/python DataModel3/code_query_audit.py` — zero errors
   - `.venv/bin/python APIDocumentation/route_spec_audit.py` — zero gaps
   - Schema apply script run against the dev box, then re-run to prove
     idempotence
   - Live smoke: `UserMCP/livetest/smoke.sh <email>` — no ERROR tools
4. **Update `CHANGELOG.md`** — new top section for the version.
5. **Tag and push:**
   ```bash
   git tag -a v<version> -m "<one-line summary>"
   git push origin HEAD:main
   git push origin v<version>
   ```
6. **Publish the GitHub Release** (notes from the CHANGELOG section):
   ```bash
   gh release create v<version> --title "Home Edition <version>" --notes-file <notes.md>
   ```
7. **Cold-install check** — prove a stranger's first install works:
   clone the tag into a scratch directory, follow `HowToDeploy/` from
   scratch (fresh volumes → fresh DB init), provision a user with
   `admin.py`, log in, and confirm `/api/v1/healthz` reports the new
   version. See "Upgrading an appliance" in `HowToDeploy/README.md` for
   the upgrade-path counterpart.

## Upgrading a running box

Users run `cd UserApp && ./update.sh` — it pulls main, rebuilds webapp +
usermcp with the new `VERSION`, restarts, prints the healthz version, and
lists any `scripts/apply_*.py` to run. The release notes must name the
apply script when there is one.
