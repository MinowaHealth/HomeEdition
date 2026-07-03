# API Keys

**Date:** 2026-05-10 14:00 PST
**Status:** Live in UserApp
**Source:** [UserApp/webapp/app.py:585-656](../../UserApp/webapp/app.py#L585-L656) (endpoints), [UserApp/webapp/auth.py:1150-1330](../../UserApp/webapp/auth.py#L1150-L1330) (CRUD + lookup), [UserApp/webapp/utils.py](../../UserApp/webapp/utils.py) (`require_auth` integration)
**Schema:** [Infrastructure/init/docker-init-home/02-home_schema.sql:2058](../../Infrastructure/init/docker-init-home/02-home_schema.sql#L2058)

---

## Overview

Long-lived bearer tokens for MCP clients, mobile apps, and integrations. A user creates a key from an authenticated web session, copies the raw value once, and uses it indefinitely as a bearer token until they revoke it. No daily refresh, no session re-login.

**Key format:** `hbk_` + 32 hex chars (128 bits of entropy) — e.g. `hbk_a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6`.

**Storage:** Only the SHA-256 hash of the full key is stored. The raw key is shown exactly once, in the POST response.

### Why SHA-256, not Argon2id

API keys are 128-bit random tokens, not human-chosen passwords. Brute-forcing 2^128 takes ~5.4×10^13 years regardless of hash speed; Argon2id would only add ~50ms per request for zero security gain. Argon2id exists to slow down dictionary attacks against low-entropy passwords — not applicable here.

---

## Schema

Table `public.api_tokens`. Shared with HealthKit sync, mobile-device, and integration tokens — discriminated by `token_type`. MCP-issued keys are inserted with `token_type = 'mcp'`.

### Columns

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `tenant_id` | `SMALLINT` | NOT NULL | `1` | Tenant FK → `tenants(id)` |
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | Primary key |
| `user_id` | `UUID` | NOT NULL | — | Owner FK → `users(tenant_id, id)` |
| `token_hash` | `VARCHAR(255)` | NOT NULL | — | SHA-256 hex digest of full raw key — never plaintext |
| `device_name` | `TEXT` | | — | Human-readable label (e.g. "Claude Desktop", "the maintainer's iPhone") |
| `token_type` | `TEXT` | | `'mobile'` | Discriminator: `'mcp'`, `'mobile'`, `'healthkit'`, `'integration'` |
| `key_prefix` | `VARCHAR(12)` | | — | First 12 chars of raw key — display + fast prefix lookup |
| `created_at` | `TIMESTAMPTZ` | | `now()` | Creation timestamp |
| `last_used_at` | `TIMESTAMPTZ` | | — | Updated on each successful auth (fire-and-forget) |
| `expires_at` | `TIMESTAMPTZ` | | — | NULL = never expires (MCP keys are NULL today) |
| `revoked_at` | `TIMESTAMPTZ` | | — | Soft-delete timestamp; NULL = active |
| `totp_verified_at` | `TIMESTAMPTZ` | | — | When 2FA was verified (if applicable) |
| `created_ip` | `INET` | | — | Client IP at creation |
| `last_ip` | `INET` | | — | Client IP at last use |

```sql
PRIMARY KEY (tenant_id, id)
FOREIGN KEY (tenant_id, user_id) REFERENCES users(tenant_id, id) ON DELETE CASCADE
```

### Indexes

| Name | Columns | Condition | Purpose |
|------|---------|-----------|---------|
| `idx_api_tokens_user` | `(tenant_id, user_id)` | `WHERE revoked_at IS NULL` | List keys by user |
| `idx_api_tokens_hash` | `(token_hash)` | `WHERE revoked_at IS NULL` | Hash equality on lookup |
| `idx_api_tokens_prefix` | `(key_prefix)` | `WHERE revoked_at IS NULL` | Fast prefix probe during auth |

### Access control

All four auth-module functions (`create_api_key`, `lookup_api_key`, `list_api_keys`, `revoke_api_key`) run on admin connections. `lookup_api_key` runs pre-auth by necessity — user identity is not yet established when the key is being verified. The other three enforce per-user isolation with explicit `WHERE user_id = %s` filters, matching the session-management pattern.

---

## Auth Flow Integration

`require_auth` ([UserApp/webapp/utils.py](../../UserApp/webapp/utils.py)) checks bearer tokens in this order:

1. `HEALTHKIT_SYNC_TOKEN` environment variable
2. Session UUID → `auth.get_session(bearer)`
3. **`hbk_`-prefixed bearer → `auth.lookup_api_key(bearer)`** — string prefix check first, then DB lookup keyed on `key_prefix` + `token_hash` SHA-256 equality
4. Cookie session fallback

The `hbk_` prefix check is a pure string comparison — no DB hit unless the prefix matches.

---

## Endpoints

Both `/api/v1/api-keys` and `/api/v2/api-keys` are registered; they are aliases for the same handler.

### POST /api/v1/api-keys

Create a new long-lived API key. The raw key is returned **once** and never retrievable afterward.

**Auth:** `@require_auth`. Any active auth works (web session or another API key) — there is no extra session-only gate today.

**Request:**
```json
{ "label": "Claude Desktop" }
```

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `label` | string | No | `"MCP"` | Stored in `device_name`; capped at 100 chars |

**Response 201:**
```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "key": "hbk_EXAMPLE_NOT_A_REAL_KEY",
  "label": "Claude Desktop",
  "key_prefix": "hbk_a1b2c3d4",
  "created_at": "2026-05-10T21:00:00+00:00"
}
```

**Response 409** — over the per-user key cap:
```json
{ "error": "Maximum of 5 active API keys reached" }
```

### GET /api/v1/api-keys

List active (non-revoked) API keys for the authenticated user. Never returns the raw key or hash.

**Auth:** `@require_auth`.

**Response 200:**
```json
[
  {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "key_prefix": "hbk_a1b2c3d4",
    "device_name": "Claude Desktop",
    "token_type": "mcp",
    "created_at": "2026-05-10T21:00:00+00:00",
    "last_used_at": "2026-05-10T21:45:00+00:00"
  }
]
```

> **Field-name wart:** the JSON field is `device_name`, not `label` — `auth.list_api_keys` selects the raw column. POST takes/returns `label`, GET returns `device_name`. Same value, different name. Treat as a known inconsistency; clean up by aliasing `device_name AS label` in the SELECT when convenient.

Sorted by `created_at DESC`.

### DELETE /api/v1/api-keys/:id

Revoke a key. Soft delete — sets `revoked_at` so the row stays for audit.

**Auth:** `@require_auth`. Only the owner can revoke (enforced by `WHERE user_id = %s` in the UPDATE).

**Response 200:**
```json
{ "success": true }
```

**Response 404** — key not found or already revoked:
```json
{ "error": "API key not found or already revoked" }
```

Revocation takes effect immediately — subsequent requests with the revoked key fall through the auth chain and fail with 401. Revoked rows do **not** count toward the 5-key limit.

---

## Using API Keys

Bearer-token usage from any HTTP client:

```bash
export API_KEY="hbk_..."   # the raw key returned by POST /api/v1/api-keys
curl -H "Authorization: Bearer $API_KEY" \
     https://localhost/api/v1/session
```

### MCP (Claude Desktop)

In `claude_desktop_config.json`, pass the key on the `--token` flag of the MCP server proxy:

```json
{
  "mcpServers": {
    "minowa": {
      "command": "npx",
      "args": [
        "-y", "supergateway",
        "--sse", "https://localhost/sse",
        "--header", "Authorization: Bearer hbk_EXAMPLE_NOT_A_REAL_KEY"
      ]
    }
  }
}
```

The key never expires — no daily refresh.

---

## Constraints

- **Max active keys per user:** 5 (override via `MAX_API_KEYS_PER_USER` env var on UserApp)
- **Expiration:** None for MCP keys (`expires_at IS NULL`); the column exists for future use
- **Scope:** Full user access, identical to a session bearer — no per-key scoping today

## Not yet implemented

- **Audit row on revoke.** § 164.312(b) wants audit controls on PHI-access primitives; today `revoke_api_key` is a bare `UPDATE ... SET revoked_at` with no audit log. The session-management code has the same gap. Worth closing alongside the broader auth-audit work, not as a one-off.
- **Per-key scopes.** All keys grant full user access — there is no per-key scoping today.
- **Key rotation API.** Users revoke + recreate manually; there's no atomic rotate endpoint.
