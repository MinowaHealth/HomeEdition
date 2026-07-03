# Exposing Postgres for an external tool / Postgres MCP

**Date:** 2026-06-18 12:45 PDT

First and Foremost: If you give an LLM direct access to Postgres you MUST expect to encounter 'tableshitting' - AI models will do things like ignoring numeric fields in a table and just writing in a comment field. They'll gratuitously add a comment field to facilitate this. They'll make new tables on a whim. The ONLY time we use direct access is when we're troubleshooting some sort of disagreement between a running system and DataModel3. This is REALLY rare, just two incidents during the first half of 2026. If you use this just because it seems expedient you will end up with a mess.

If you want to explore your health data, that's a job for UserMCP. If you need to expose some new aspect, do so by modifying the UserMCP code. The MCP server has a read only Postgres account because write access will also lead to tableshitting. If you need to load an additional datasource use an importer patterned after the HealthKit or Garmin imports.

Home Edition runs one PostgreSQL 18 + pgvector container (`hb-local-postgres`, database `healthv10`) as part of the single appliance stack. By default the database port is **bound to loopback only** — nothing off the box can reach it. This doc explains how to deliberately expose Postgres so a tinkerer can point a database GUI, a `psql` session, or a **Postgres MCP server** at it, and what that exposure means for household privacy.

This is an *opt-in* path. If you just want AI access to the data through the normal, per-user-scoped contract, use **UserMCP** instead (it proxies through the app and enforces `user_id` scoping). A Postgres MCP is the opposite: a raw, unscoped connection to the whole database. The two are complementary — see "Privacy: read the warning" below before you open the port.

## 1. Open the port

Postgres has its own bind knob, **`PG_BIND_ADDR`**, separate from `BIND_ADDR`. It defaults to `127.0.0.1` (loopback) **even on a LAN appliance** — so opening the app to the LAN (`BIND_ADDR=0.0.0.0`) never exposes the database. The DB port opens only when you set this knob, and only the DB port: the web UI (`:80`) and UserMCP (`:13282`) keep following `BIND_ADDR`, untouched.

To make Postgres reachable from other devices on the home LAN, set it in `local.env`:

```ini
# local.env
PG_BIND_ADDR=0.0.0.0
```

Then recreate the stack so the new bind takes effect (from the repo root):

```bash
docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env up -d
```

Postgres is now listening on `<appliance-ip>:5432`, while the web UI and UserMCP stay wherever `BIND_ADDR` put them. This is the deliberate split: a local-only app box can still hand a LAN tool a DB connection, and a full LAN appliance can still keep its database loopback-only.

## 2. Connection details

| Field | Value |
|-------|-------|
| Host | `<appliance-ip>` (or `localhost` on the box itself) |
| Port | `5432` |
| Database | `healthv10` |
| Superuser | `postgres` / `POSTGRES_PASSWORD` from `local.env` |
| App role | `healthv10_app` / `APP_DB_PASSWORD` from `local.env` (read-write on all tables) |

Connection string (app role):

```
postgresql://healthv10_app:<APP_DB_PASSWORD>@<appliance-ip>:5432/healthv10
```

## 3. A read-only role for the MCP (recommended)

Don't hand a tinkering tool the superuser or the read-write app role. Create a dedicated read-only login and point the MCP at that, so the worst a stray query can do is read:

```sql
-- run once as the postgres superuser
CREATE ROLE pg_mcp_ro WITH LOGIN PASSWORD 'choose-a-password';
GRANT USAGE ON SCHEMA public TO pg_mcp_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO pg_mcp_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO pg_mcp_ro;
```

Then connect as `pg_mcp_ro`. Most Postgres MCP servers also have a read-only mode flag — turn it on as well, belt-and-suspenders.

## 4. Pointing a Postgres MCP at it

A typical Postgres MCP server takes a connection string via env or argument. Example (illustrative — use your MCP's own config shape):

```json
{
  "mcpServers": {
    "healthv10-postgres": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres",
               "postgresql://pg_mcp_ro:<password>@<appliance-ip>:5432/healthv10"]
    }
  }
}
```

## 5. Privacy: read the warning

Home Edition enforces privacy **between household members in the application layer** — every query carries an explicit `user_id` predicate. There is **no Row-Level Security** in the database (it was removed; `tenant_id` is always `1`). That means a raw database connection — including any Postgres MCP — **sees every household member's rows with no per-user filtering**. It bypasses the entire privacy model that UserApp and UserMCP enforce.

So:

- A Postgres MCP is a **god's-eye view** of the household's health data. Only connect one you trust, only on a network you trust, and prefer the read-only role above.
- This is a deliberate tinkerer/admin capability, not a per-user feature. If you want AI access that respects per-user privacy, use **UserMCP**, not a Postgres MCP.
- It does not weaken anything for other members *through the app* — it's a separate, direct channel you are choosing to open.

## 6. Locking it back down

Set `PG_BIND_ADDR=127.0.0.1` in `local.env`, then recreate the stack. The DB port returns to loopback-only (the web UI and UserMCP are unaffected — they follow `BIND_ADDR`). Revoke the read-only role if you're done with it:

```sql
DROP ROLE IF EXISTS pg_mcp_ro;
```
