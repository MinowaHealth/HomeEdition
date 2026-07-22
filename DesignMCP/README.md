# DesignMCP

<!-- 2026-06-12 17:30 PDT -->

MCP server that exposes the full UserApp REST surface to the Claude Design tool.

the product designer and the medical advisor use Claude Design to mock up Minowa UI surfaces. Design can build interfaces from descriptions of data shapes but cannot make HTTP calls itself. DesignMCP bridges that gap: it speaks the MCP protocol Design already knows, and proxies each tool call through to UserApp using a fixed demo identity.

## Audience and design point

This is *deliberately different* from [UserMCP](../UserMCP/), which exposes a curated task-oriented tool surface (23 tools as of July 2026). DesignMCP exposes the **raw API contract** so Design can ask questions like "what fields does a meal-log row have" or "what shape does /api/v1/health-inputs return" without us pre-curating answers.

Two tools cover the entire surface:

| Tool | Use |
|------|-----|
| `userapp_inventory` | List every UserApp endpoint: path, methods, blueprint, summary. Call first to discover the surface. |
| `userapp_request` | Make any GET/POST/PUT/PATCH/DELETE under `/api/v1/*` or `/api/v2/*`. Returns the raw response — no envelope translation. |

`userapp_request` enforces a path allowlist (`^/api/v[12]/`) and method whitelist. Internal routes (`/login`, `/metrics`, `/admin/*`) are not reachable.

## Design identity

DesignMCP carries a single long-lived API key bound to one household account (tenant=1). Use a throwaway account you provision on the appliance for design work, not anyone's real health record. The key can be revoked and reissued with the admin CLI at any time.

## Provisioning the API key

Provision a design account and issue its token via UserApp's admin CLI on the box:

```bash
~/UserApp/admin.py provision-user design
~/UserApp/admin.py issue-api-key design designmcp
```

The token (`hbk_` + 32 hex chars) prints once and is hashed in `api_tokens`. Paste it into `DesignMCP/.env`:

```
USERAPP_API_KEY=hbk_...
```

To rotate later:

```bash
~/UserApp/admin.py list-api-keys design
~/UserApp/admin.py revoke-api-key design <key_id>
~/UserApp/admin.py issue-api-key design designmcp
```

## Running locally

```bash
cp .env.example .env
# edit USERAPP_API_KEY=...
docker compose up -d
docker compose logs -f designmcp
curl http://localhost:33282/health
```

After a code change, rebuild and restart the container with `./regen.sh`.

## Connecting Claude Design

DesignMCP speaks MCP over SSE (`GET /sse` + `POST /messages/`), so an MCP
client connects through the `supergateway` SSE-to-stdio bridge — the same
pattern UserMCP uses. Point your MCP client config (e.g. Claude Desktop's
`~/Library/Application Support/Claude/claude_desktop_config.json`) at the
running server:

```json
{
  "mcpServers": {
    "designmcp": {
      "command": "npx",
      "args": [
        "-y", "supergateway",
        "--sse", "http://localhost:33282/sse",
        "--header", "authorization:Bearer hbk_YOUR_DESIGN_KEY"
      ]
    }
  }
}
```

Use the `hbk_` token from [Provisioning the API key](#provisioning-the-api-key).
From another device on the LAN, replace `localhost:33282` with the
appliance's LAN IP. Restart the client and the two tools
(`userapp_inventory`, `userapp_request`) appear.

## Tests

```bash
../.venv/bin/python -m pytest -v
```

Covers tool registry, path-safety rejection, method whitelist, and inventory dispatch.

## Configuration

| Env var | Default | Notes |
|---------|---------|-------|
| `USERAPP_BASE_URL` | `http://localhost` | In Docker: `http://hb-local-webapp:80` |
| `USERAPP_API_KEY` | _required_ | Long-lived `hbk_*` for the design account |
| `MCP_PORT` | `33282` | Continues the 13282/23282 series |
| `MCP_HOST` | `127.0.0.1` | `0.0.0.0` inside container |
| `UVICORN_LOG_LEVEL` | `info` | Python stdlib level — see CLAUDE.md |

## Deployment

DesignMCP runs on the household box alongside the other containers. Bring it
up with `docker compose up -d` (see [Running locally](#running-locally)) and
rebuild after code changes with `./regen.sh`.

It is LAN-bound like the rest of the appliance — no public edge.
