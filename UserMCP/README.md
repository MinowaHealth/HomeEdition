# UserMCP v0.5.0 — SeenWhole Health Data MCP Server (Python)

**Date**: 2026-04-18
**Transport**: HTTP/SSE (`GET /sse` + `POST /messages/`)
**Port**: 13282
**Deploy account**: `mcpuser`

> **v0.5.0 — task-oriented surface.** The tool roster is now twelve
> task-oriented tools that each return a standard envelope
> (`data` + `coverage` + `sources` + `disclaimer` + `next_actions`).
> See the [redesign plan](UserMCPRedesign-Plan.md) for rationale. The
> old `get_health_data`/`get_health_snapshot`/`get_health_config`/
> `get_lab_results`/`get_medication_log` tools have been retired; their
> functionality moved to `get_vitals_timeline`, `get_wearable_summary`,
> `get_my_active_regimen`, `get_lab_history`, and `get_recent_activity`
> respectively.

---

## Architecture

```
Claude Desktop / Editor
       ↓
    supergateway (SSE-to-stdio bridge)
       ↓
UserMCP (this server, port 13282)
       ↓  HTTP proxy
UserApp Flask API (:80, /api/v1/*)
       ↓
PostgreSQL (healthv10 database, RLS enforced)
```

**Key principle:** UserMCP is a **stateless HTTP proxy** that adds MCP tools on top of the existing Flask API. It never touches the database directly. All data access goes through RLS — users only see their own data.

---

## Quick Start

### Local Development (Direct Python)

```bash
cd UserMCP
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your API_TOKEN and API_BASE_URL

# Run server directly
python mcp_server.py
```

### Docker (Local)

```bash
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### Docker + UserApp (Integration Testing)

If you're running UserApp locally and want UserMCP to reach it:

```bash
# In .env, set:
API_BASE_URL=http://host.docker.internal:80   # Docker for Mac/Windows
# or
API_BASE_URL=http://172.17.0.1:80             # Docker on Linux (host IP)

docker-compose up -d
```

---

## Response envelope

Every tool returns a JSON object with the same top-level shape:

```json
{
  "data":       { ... tool-specific payload ... },
  "coverage":   { "window": {...}, "counts": {...}, "gaps": [...], "truncated": false },
  "sources":    [ {"source": "manual", "last_sync": null, "connected": true}, ... ],
  "disclaimer": "Not medical advice. ...",
  "next_actions": [ {"tool": "...", "args": {...}, "why": "..."} ]
}
```

- `coverage.counts.rows` is the total row count in `data` (or `None` for
  non-listing tools).
- `coverage.gaps` is a list of `{source, reason}` entries when a sub-call
  failed or returned empty — tools degrade gracefully rather than raising.
- `coverage.truncated` is `true` when a server-side pagination cap was hit.
- `sources` is populated once per request by calling `/diagnostics/table-counts`,
  `/garmin/status`, and `/healthkit/jobs?limit=1` in parallel.
- `next_actions` is a non-authoritative hint to the LLM about useful
  follow-up tool calls — never required, always safe to ignore.

## MCP Tools

All twelve tools follow the same envelope contract above.

### Identity & configuration

| Tool | Purpose |
|------|---------|
| `get_my_profile` | Display name, timezone, dietary settings, authorized delegates. |
| `get_my_active_regimen` | Active medications, supplements, stacks, timeframes, reminders. |
| `get_my_clinical_history` | Conditions, allergies, family/surgical history, vaccinations. Flags medication/allergen name overlaps in `alerts`. |

### Observation & trends

| Tool | Purpose |
|------|---------|
| `get_vitals_timeline` | Blood pressure, weight, temperature over a window (default 30d, max 90d). Returns raw rows + per-category rollup. |
| `get_lab_history` | Latest lab result per test with LOINC + reference range. Optional `loinc_codes` filter. |
| `get_wearable_summary` | Garmin + HealthKit rollup (steps, resting HR, sleep, stress) with connection status per source. |

### Activity & adherence

| Tool | Purpose |
|------|---------|
| `get_recent_activity` | Unified `/all-logs` feed across medications/food/observations. Honors server-side pagination — sets `coverage.truncated` when the feed is clipped. |
| `get_adherence_report` | Per-input scheduled-dose-vs-logged percentage. Suggests `get_recent_activity` follow-up when any input is below 50%. |

### Food & nutrition

| Tool | Purpose |
|------|---------|
| `get_nutrition_report` | Daily calorie/macro rollup + dietary-setting violations (substring match on avoid_list). |

### Search & documents

| Tool | Purpose |
|------|---------|
| `search_my_data` | Semantic-first search across observations, notes, inputs, conditions, allergies, food items, and document annotations. Falls back to keyword if Ollama is unreachable; `coverage.mode` reports which path ran. |
| `get_document` | Full document metadata + per-page OCR text + optional annotations. Typically chained after a `search_my_data` hit on `document_annotations`. |

### Engagement

| Tool | Purpose |
|------|---------|
| `send_feedback` | Submit feedback about data quality, tool behavior, or product gaps. |

### Resources

| URI | Format | Purpose |
|-----|--------|---------|
| `usermcp://profile` | application/json | Live profile snapshot — identity, sources, active inputs. Read at conversation start. |
| `usermcp://disclaimers` | text/markdown | Long-form medical/data disclaimers. |
| `usermcp://data-sources` | text/markdown | Table-by-table index of what lives in each data source. |

### Prompts (slash commands)

| Name | Purpose |
|------|---------|
| `/visit-prep [provider?]` | Packet for an upcoming appointment: regimen, vitals, labs, history, open concerns, allergy warnings. |
| `/weekly-check-in` | 7-day rollup across wearables, vitals, adherence, food — one narrative answer. |

---

## Claude Desktop Integration

UserMCP uses SSE transport, so Claude Desktop connects via the `supergateway` bridge:

1. **Create an API key:**
   ```bash
   cd UserMCP && ./get-token.sh
   ```
   This logs in, creates a permanent `hbk_` API key, and prints the config snippet.

2. **Add to Claude Desktop config** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
   ```json
   {
     "mcpServers": {
       "usermcp": {
         "command": "npx",
         "args": [
           "-y", "supergateway",
           "--sse", "http://localhost:13282/sse",
           "--header", "authorization:Bearer hbk_YOUR_API_KEY"
         ]
       }
     }
   }
   ```

3. **Restart Claude Desktop** and start using the tools.

API keys do not expire. Revoke via `DELETE /api/v1/api-keys/<id>` or the web UI.

> **Tip**: For remote servers, replace `localhost:13282` with the server's IP or use an SSH tunnel. See `HowToDeploy/MacDeploy.md` for full examples.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_BASE_URL` | `http://localhost:80` | UserApp Flask API base URL |
| `API_TOKEN` | (required) | `hbk_` API key (permanent, from `./get-token.sh`) |
| `MCP_PORT` | 13282 | Port to listen on (SSE) |
| `UVICORN_LOG_LEVEL` | info | Python stdlib log level (critical, error, warning, info, debug, trace) |
| `MCP_TRACE` | false | Enable JSON-RPC message tracing (`1`, `true`, or `yes`). Prints raw protocol messages to stdout with color-coded output for debugging SSE transport issues. |

---

## Testing

### Unit Tests

```bash
cd UserMCP
source .venv/bin/activate
pytest tests/ -v
```

### Smoke Testing (Docker)

```bash
docker-compose up -d
curl -s http://localhost:13282/health
# → {"status": "ok"}
```

### Integration Testing (Against Live API)

1. Start UserApp locally (`cd UserApp && ./start.sh`)
2. Get a valid token: `./get-token.sh`
3. Set `API_BASE_URL=http://host.docker.internal:80` in `.env`
4. `docker-compose up -d` and test tool calls

---

## Deployment

UserMCP deploys to the `mcpuser` account (not `buddy` — that's UserApp).

```bash
# repoman@<server> pulls the repo; code is distributed to shell accounts
ssh mcpuser@<server>
cd ~/UserMCP
./setup.sh        # Creates .env, builds container
docker-compose up -d

# Verify
curl -s http://localhost:13282/health
```

See `HowToDeploy/PrototypeLinux.md` (Phase 5) or `HowToDeploy/PilotLinux.md` for full deployment steps.

---

## Security & Privacy

- **All data access is RLS-enforced** — users only see their own data
- **No credentials stored in MCP server** — only the bearer token (API key) is used
- **API keys (`hbk_`) do not expire** — revoke via `DELETE /api/v1/api-keys/<id>` or web UI
- **No direct database access** — all queries go through the Flask API
- **SSE transport** — for local or tunnel-protected use. Production uses Cloudflare Tunnel.

---

## References

- [Model Context Protocol (MCP)](https://modelcontextprotocol.io)
- [UserApp API](../UserApp/README.md)
- [Deployment Guides](../HowToDeploy/README.md)
