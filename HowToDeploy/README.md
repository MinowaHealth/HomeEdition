# HowToDeploy

**Date**: 2026-07-22 10:00 PDT

How to run the **Minowa.ai Home Edition** appliance. Home Edition is a single-box, single-household setup: one Docker Compose stack bound to the LAN. There is no VPS, no Cloudflare, no multi-account shell model, and no production/testing split — those were enterprise concerns and have been removed.

## Guides

| I want to... | Read this |
|---|---|
| **Run the appliance** (Mac or any Docker host) | [MacDeploy.md](MacDeploy.md) |
| OrbStack notes and gotchas | [MacInsights.md](MacInsights.md) |
| macOS / Ruby toolchain troubleshooting | [MacRubyIssues.md](MacRubyIssues.md) |

## The Stack

Three containers plus Ollama on the host:

| Service | Port | Notes |
|---------|------|-------|
| PostgreSQL 18 + pgvector | 5432 | `healthv10` database, single app role, no RLS |
| UserApp (Flask) | 80 | Household API + web UI; in-process OCR |
| UserMCP (Python) | 13282 | MCP server for Claude Desktop |
| Ollama (host) | 11434 | Embeddings (`nomic-embed-text-v2-moe`); optional at runtime |

Compose file: [docker-compose.local.yml](docker-compose.local.yml). Start it from the repo root:

```bash
docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env up -d
```

Accounts are created from the CLI: `docker exec -it hb-local-webapp python admin.py provision-user <name>`. See [MacDeploy.md](MacDeploy.md) for the full walkthrough.

## Upgrading an appliance

`main` is always the most recent release (see `RELEASING.md`); the installed
version is the `VERSION` file at the repo root and is reported by
`GET /api/v1/healthz`.

To upgrade a running box:

```bash
cd UserApp && ./update.sh
```

It pulls main, rebuilds webapp + usermcp with the new version baked in,
restarts them, prints the healthz version, and lists any pending schema
apply scripts.

**Schema releases** ship an idempotent upgrade script named in the release
notes (e.g. `scripts/apply_11_1_0_home.py`). Run it after `update.sh`:

```bash
.venv/bin/python scripts/apply_11_1_0_home.py
```

Re-running an already-applied script is safe — every apply script is
idempotent by contract. Fresh installs never need them: the schema init in
`Infrastructure/init/docker-init-home/` already contains the final schema.

Postgres data lives in the named volume `hb-local-pgdata`; an upgrade never
touches it. `docker compose ... down` without `-v` also preserves it — only
`down -v` destroys data.
