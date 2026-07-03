# MacDeploy.md — Running the Appliance Locally

**Date**: 2026-06-13 17:30 PDT
**Target**: A Mac (or any Docker host) running the Minowa.ai Home Edition appliance
**Status**: Ready for use

---

## Overview

Home Edition is a **single-box appliance**: one Docker Compose stack, bound to the LAN, serving one household. There is no account segmentation, no VPS, and no Cloudflare — that was all enterprise topology. This guide runs the stack on a Mac with OrbStack (Docker Desktop works too).

The stack is **three containers** plus Ollama on the host:

| Service | Port | Container | Notes |
|---------|------|-----------|-------|
| PostgreSQL + pgvector | 5432 | `hb-local-postgres` | `healthv10` database, no RLS |
| UserApp (Flask) | 80 | `hb-local-webapp` | Household API + web UI; in-process OCR |
| UserMCP (Python) | 13282 | `hb-local-usermcp` | MCP server for Claude Desktop |
| Ollama (host) | 11434 | — | Embeddings (`nomic-embed-text-v2-moe`); runs on the host, optional at runtime |

All container ports bind to `0.0.0.0` so a phone on the same Wi-Fi can reach the API.

---

## Quick Start

### Prerequisites

1. **OrbStack** (or Docker Desktop) running, ~2 GB RAM allocated
2. A clone of this repo
3. **Ollama** on the host with the embedding model pulled (optional but recommended):
   ```bash
   ollama pull nomic-embed-text-v2-moe
   ```
   If Ollama isn't running, writes still succeed — documents and records just won't get a vector until re-embedded.
4. No conflicting services on ports 80, 5432, 13282

### Start

```bash
# Run from the repo root (NOT from HowToDeploy/)
docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env up -d
```

First run takes 2–3 minutes (image build + PostgreSQL init scripts apply the home schema and create the `healthv10_app` role).

### Verify

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"

# Database (home schema loads ~86 tables — wait ~30s on first start)
docker exec hb-local-postgres psql -U postgres -d healthv10 \
    -c "SELECT count(*) FROM pg_tables WHERE schemaname='public';"

# UserApp login page
curl -s -o /dev/null -w "%{http_code}" http://localhost/login   # → 200

# UserMCP health
curl -s -o /dev/null -w "%{http_code}" http://localhost:13282/health   # → 200
```

### Create a household account

There is no test-data seeder in Home Edition — accounts are provisioned from the CLI by whoever runs the box:

```bash
docker exec -it hb-local-webapp python admin.py provision-user alice
# follow the prompt to set a password; reset later with:
docker exec -it hb-local-webapp python admin.py reset-password alice
```

(For quick throwaway local dev, `scripts/local-init-db.sh` also creates a single `test@example.com` / `Password2026` login.)

---

## How the Services Connect

```
Phone (React Native)
    │  HTTP (port 80)
    ▼
UserApp (:80)  ◀──HTTP──  UserMCP (:13282)  ◀──SSE──  Claude Desktop
    │
    │  SQL (app-level user_id scoping; no RLS)
    ▼
PostgreSQL (:5432)        Ollama (host :11434) ◀── embeddings, best-effort
```

**Key point**: UserMCP never touches the database. It is a stateless HTTP proxy that forwards every request to UserApp, which handles authentication, per-user scoping, and SQL. That is why it could run on a separate machine — it only needs HTTP access to UserApp.

---

## Mobile Testing (Phone on Same Wi-Fi)

All ports bind to `0.0.0.0`, so any device on the same network can reach them.

```bash
ipconfig getifaddr en0    # find your Mac's Wi-Fi IP, e.g. 192.168.88.50
```

Point the React Native app at `http://<your-mac-ip>` (port 80). The mobile app talks only to the UserApp API — not to UserMCP or the database.

> **Security note**: `0.0.0.0` bindings expose the API to your local network. Fine for a home network; don't do this on public Wi-Fi.

If the phone can't connect: confirm both devices are on the same Wi-Fi, check the Mac firewall (System Settings → Network → Firewall), and verify the binding with `docker port hb-local-webapp` (should show `80/tcp -> 0.0.0.0:80`).

---

## Claude Desktop Integration

UserMCP supports Claude Desktop via the `supergateway` bridge (SSE-to-stdio adapter).

```bash
cd UserMCP && ./get-token.sh   # logs in via UserApp and prints a bearer token
```

Paste into `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "usermcp-local": {
      "command": "npx",
      "args": [
        "-y", "supergateway",
        "--sse", "http://localhost:13282/sse",
        "--header", "authorization:Bearer YOUR_TOKEN_HERE"
      ]
    }
  }
}
```

---

## Rebuilding After Code Changes

```bash
# Run from repo root
DC="docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env"

$DC up -d --build webapp     # rebuild UserApp
$DC up -d --build usermcp    # rebuild UserMCP
$DC up -d --build            # rebuild everything
```

The webapp has **live mount volumes** for Python files and templates — most code changes take effect on the next request without rebuilding. Only `requirements.txt` or `Dockerfile` changes need a rebuild.

## Full Reset

```bash
DC="docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env"
$DC down -v      # stop and delete volumes (loses all data)
$DC up -d
```

---

## Troubleshooting

**Port 5432 already in use**: `brew services stop postgresql` or `sudo lsof -i :5432`

**Port 80 permission denied**: OrbStack handles this automatically. If it fails: `sudo lsof -i :80` and stop Apache (`sudo apachectl stop`).

**Build fails**: `docker builder prune -f`, then rebuild with `--no-cache`.

**Database shows 0 tables**: Init scripts haven't finished. Wait 30 seconds and re-check.

**MCP returns "unauthorized"**: Tokens expire (24h). Re-run `UserMCP/get-token.sh`.

**Embeddings missing / semantic search empty**: Ollama isn't reachable on the host (`http://host.docker.internal:11434`). Pull the model and start Ollama; embedding is best-effort and never blocks writes.

---

## See Also

- [README.md](README.md) — the appliance guide index
- [MacInsights.md](MacInsights.md) — OrbStack notes and gotchas
- [MacRubyIssues.md](MacRubyIssues.md) — macOS/Ruby toolchain troubleshooting
