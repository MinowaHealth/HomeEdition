# Port Map — Minowa.ai Home Edition
**Date**: 2026-06-12 17:30 PDT

All services, all ports. One file. Home Edition is a single-box appliance bound to the LAN — there are no tunnels, no VPS pair, and no production/testing domain split.

## Application Services

| Port | Service | Bind | Notes |
|------|---------|------|-------|
| 80 | UserApp (Flask/Gunicorn) | LAN | Household API + web UI; in-process OCR |
| 13282 | UserMCP (Python SSE) | LAN | User MCP server (proxies UserApp) |

## Data Stores

| Port | Service | Notes |
|------|---------|-------|
| 5432 | PostgreSQL 18 + pgVector | `healthv10` database (single app role, no RLS) |

## Host Processes

| Port | Service | Notes |
|------|---------|-------|
| 11434 | Ollama | Embeddings (`nomic-embed-text-v2-moe`); runs on the host, not a container |

## Bind / Access

The appliance binds to the LAN so household devices (and mobile clients on the same Wi-Fi) can reach it. Every route — web UI **and** API — is further restricted by `source_ip_filter()` in UserApp to an explicit source-IP allowlist: RFC1918 LANs, CGNAT/Tailscale (`100.64.0.0/10`), and loopback. There is no public ingress — remote access, if ever wanted, is the household's own business (e.g. a personal VPN, or Tailscale, whose CGNAT range the filter already trusts) and is not shipped.

## Local Dev (HowToDeploy)

`HowToDeploy/docker-compose.local.yml` — the appliance stack, all containerized and LAN-accessible:

| Port | Service | Bind |
|------|---------|------|
| 80 | UserApp | `0.0.0.0` |
| 5432 | PostgreSQL | `0.0.0.0` |
| 13282 | UserMCP | `0.0.0.0` |
