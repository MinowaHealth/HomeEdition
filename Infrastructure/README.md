# Infrastructure — Database init scripts & schema

**Date:** 2026-06-18 10:00 PDT

This directory holds the **database initialization scripts and the schema source
of truth** for the Home Edition appliance. It no longer defines its own Docker
Compose stack — the single canonical compose file
(`HowToDeploy/docker-compose.local.yml`) mounts these files directly and runs
them when the `pgvector` container first initializes an empty data volume.

## What's here

| Path | Purpose |
|------|---------|
| `init/docker-init-home/` | Database init scripts, run once by the container on an empty data volume (alphabetical order). |
| `init/docker-init-home/02-home_schema.sql` | The running schema source of truth (`healthv10`, version `11.0.0-home`). |
| `init/docker-init-home/role/app-role-setup.sql` | Creates the unprivileged `healthv10_app` role, grants, and indexes. |

## Database

- **Engine:** `pgvector/pgvector:pg18`, database `healthv10`, single app role `healthv10_app` (no RLS — privacy is enforced in the app via `user_id` predicates).
- **Port:** `5432`, published by the canonical compose as `${BIND_ADDR:-127.0.0.1}:5432`. Loopback-only by default; set `BIND_ADDR=0.0.0.0` in `local.env` to serve the LAN. See `../PostgresAccess.md` for connecting an external tool / Postgres MCP.
- **Persistence:** `hb-local-pgdata` (database) and `hb-local-userdocs` (shared document storage), both declared in the canonical compose.

## Bringing the database up

The database is part of the single appliance stack — there is no separate
Infrastructure command. From the repo root:

```bash
docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env up -d
```

On a fresh data volume the init scripts here load the schema and create the app
role automatically. See `../HowToDeploy/` for the full setup guide.
