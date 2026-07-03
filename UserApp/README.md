# UserApp — Household API

**Date:** 2026-06-13 · **Updated:** 2026-06-17T11:45-07:00

The UserApp is the backend that your mobile app talks to. It is a Flask API behind Gunicorn, running on the home appliance and bound to your LAN. Everything below is written for mobile/frontend developers who need to call the API — backend internals are covered in the repo-root `CLAUDE.md`.

---

## Quick Reference

| Item | Value |
|------|-------|
| Base URL (appliance on the LAN) | `http://<appliance-lan-ip>:80` |
| Base URL (local dev) | `http://localhost:80` (Mac Docker stack) |
| Auth | Bearer token (session token from `/api/v1/login`) |
| Content-Type | `application/json` for all requests and responses |
| Max request body | 500 MB |
| Timestamps | Always UTC (`ISO 8601` with timezone) |
| IDs | UUID v4 strings |

---

## Authentication

### Login Flow

**`POST /api/v1/login`**

```json
// Request
{ "email": "user@example.com", "password": "MyPassword123" }

// Success response
{
  "success": true,
  "token": "a1b2c3d4-...",       // ← this is your bearer token
  "email": "user@example.com",
  "display_name": "Alice",
  "username": "alice",
  "tenant_id": 1,
  "database": "healthv10"
}

// 2FA required response
{
  "success": false,
  "requires_2fa": true,
  "pending_2fa_token": "x9y8z7-...",
  "message": "Please enter your 2FA code"
}
```

The `token` field is a session ID (UUID). Sessions expire after 24 hours by default.

The `identifier` field is also accepted in place of `email` — the backend accepts either email or username.

### Using the Token

Pass the token as a bearer on every subsequent request:

```
Authorization: Bearer a1b2c3d4-...
```

### Auth Methods (priority order)

The backend tries these in order. The mobile app should use **bearer session** (#2):

1. **HealthKit sync token** — fixed env-var token for background sync jobs
2. **Bearer session** — the `token` from login (what the mobile app uses)
3. **API key** — long-lived `hbk_`-prefixed keys created via `/api/v1/api-keys`
4. **Cookie session** — browser-only, not relevant for mobile

### 2FA Verification

If login returns `requires_2fa: true`, the user must submit their TOTP code:

**`POST /api/v1/2fa/verify`** with `{ "pending_2fa_token": "...", "code": "123456" }`

On success, you get the same response shape as a normal login (with `token`).

### Account Provisioning

Home Edition has no self-service signup or email-based password reset (there are no outbound email flows). Accounts are created and managed on the appliance with the `admin.py` CLI — see [User Management CLI](#user-management-cli) below. A logged-in user can still change their own password via **`POST /api/v1/change-password`** (requires the current password).

### Session Check

**`GET /api/v1/session`** — returns current user info if the token is still valid, or `401` if expired.

---

## API Versions: v1 vs v2

v2 is a full parallel API layer with 10 dedicated blueprint files. Every v1 endpoint has a v2 equivalent. They behave identically except:

- **v2 endpoints accept optional embedding vectors** on create/update operations (for pgvector similarity search)
- **v2 has `mobile_events`** — a batch event endpoint with no v1 counterpart
- **v2 blueprints:** `health_inputs_v2`, `food_v2`, `vitals_v2`, `logging_routes_v2`, `analytics_v2`, `integrations_v2`, `feedback_v2`, `embeddings_v2`, `mobile_events_v2`

If you are not working with embeddings, v1 and v2 are interchangeable. The mobile app currently uses v1.

---

## Endpoint Map

### Auth & Session (defined in `app.py`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/login` | Login (email + password) |
| GET | `/api/v1/logout` | End session |
| GET | `/api/v1/session` | Current user info |
| POST | `/api/v1/change-password` | Change password (requires current password) |
| POST | `/api/v1/api-keys` | Create long-lived API key |
| GET | `/api/v1/api-keys` | List API keys |
| DELETE | `/api/v1/api-keys/<key_id>` | Revoke an API key |
| GET | `/api/v1/2fa/status` | Check if 2FA is enabled |
| POST | `/api/v1/2fa/setup` | Begin 2FA setup (returns QR code) |
| POST | `/api/v1/2fa/verify-setup` | Confirm 2FA setup with first code |
| POST | `/api/v1/2fa/verify` | Verify 2FA code during login |
| POST | `/api/v1/2fa/disable` | Disable 2FA |
| POST | `/api/v1/2fa/regenerate-backup-codes` | Get new backup codes |
| GET | `/api/v1/me/uuid` | Get current user's UUID |
| GET | `/api/v1/config` | Theme/app configuration |
| GET | `/api/v1/mcp-config` | MCP connection config for Claude Desktop |

### Health Inputs — Meds & Supplements (`health_inputs` blueprint)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health-inputs` | List all health inputs (meds, supplements) |
| POST | `/api/v1/health-inputs` | Create a health input |
| PUT | `/api/v1/health-inputs/<id>` | Update a health input |
| DELETE | `/api/v1/health-inputs/<id>` | Delete a health input |
| GET | `/api/v1/stacks` | List stacks (grouped inputs by time-of-day) |
| POST | `/api/v1/stacks` | Create a stack |
| PUT | `/api/v1/stacks/<id>` | Update a stack |
| DELETE | `/api/v1/stacks/<id>` | Delete a stack |
| GET | `/api/v1/timeframes` | List timeframes |

### Food & Meals (`food` blueprint)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/food-items` | List food items in catalog |
| POST | `/api/v1/food-items` | Create a food item |
| PUT | `/api/v1/food-items/<id>` | Update a food item |
| DELETE | `/api/v1/food-items/<id>` | Delete a food item |
| GET | `/api/v1/meals` | List meals |
| POST | `/api/v1/meals` | Create a meal |
| PUT | `/api/v1/meals/<id>` | Update a meal |
| DELETE | `/api/v1/meals/<id>` | Delete a meal |

### Logging — Intake Tracking (`logging_routes` blueprint)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/log-stack` | Log a stack intake |
| POST | `/api/v1/log-meal` | Log a meal |
| POST | `/api/v1/log-food-item` | Log a single food item (freeform) |
| GET | `/api/v1/all-logs` | Get all log entries (filterable by date) |

### Vitals (`vitals` blueprint)

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/v1/blood-pressure` | Blood pressure readings |
| GET/POST | `/api/v1/temperature` | Temperature readings |
| GET/POST | `/api/v1/weight` | Weight readings |
| GET/POST | `/api/v1/observations` | Generic observations |

### Clinical History (`clinical_history` blueprint)

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/v1/conditions` | Medical conditions |
| GET/POST | `/api/v1/allergies` | Allergies |
| GET/POST | `/api/v1/blood-work` | Lab / blood-work results |
| GET/POST | `/api/v1/family-history` | Family medical history |

### Integrations — Wearables & Health Sync (`integrations` blueprint + `app.py`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/healthkit/sync` | Sync Apple HealthKit data (defined in `app.py`) |
| GET/POST | `/api/v1/garmin/*` | Garmin device integration |

### Documents (`documents` blueprint)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/documents` | List uploaded documents |
| POST | `/api/v1/documents` | Upload a document |
| GET | `/api/v1/documents/<id>` | Get document details |
| GET | `/api/v1/documents/<id>/annotations` | Get OCR annotations |

### Provider Contacts (`provider_contacts` blueprint)

A personal address book of the user's own healthcare providers. Home Edition: plain contact entries — no NPI-verification pipeline, no delegation.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/provider-contacts` | List the user's provider contacts |
| POST | `/api/v1/provider-contacts` | Add a provider contact |
| GET | `/api/v1/provider-contacts/<id>` | Get a provider contact |
| PUT | `/api/v1/provider-contacts/<id>` | Update a provider contact |
| DELETE | `/api/v1/provider-contacts/<id>` | Delete a provider contact |

### Analytics (`analytics` blueprint)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/your-week` | 7-day heatmap (steps, sleep, stress, HR) |
| GET | `/api/v1/sleep-heatmap` | 28-day sleep heatmap |
| GET | `/api/v1/stress-heatmap` | 28-day stress heatmap |
| GET | `/api/v1/dashboard` | Aggregate dashboard summary |

### Dietary Settings (`dietary_settings` blueprint)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/dietary-settings` | Get current dietary settings |
| POST | `/api/v1/dietary-settings` | Create dietary settings |
| PUT | `/api/v1/dietary-settings` | Update dietary settings |
| DELETE | `/api/v1/dietary-settings/<id>` | Delete a dietary setting |

### Reminders (`reminders` blueprint)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/reminders` | List reminders |
| POST | `/api/v1/reminders` | Create a reminder |
| PUT | `/api/v1/reminders/<id>` | Update a reminder |
| DELETE | `/api/v1/reminders/<id>` | Delete a reminder |
| POST | `/api/v1/reminders/<id>/complete` | Mark a reminder complete |
| POST | `/api/v1/reminders/<id>/snooze` | Snooze a reminder |

### Embeddings (`embeddings` blueprint)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/sync-embeddings` | Trigger embedding sync for user data |
| POST | `/api/v1/semantic-search` | Semantic search across user health data |

### Correlation Report (`correlation_report` blueprint — stub)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/correlation-report` | AI-powered health data correlation insights |

### Feedback (`feedback` blueprint)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/feedback` | List user feedback |
| POST | `/api/v1/feedback` | Submit feedback |
| PUT | `/api/v1/feedback/<id>` | Update feedback |
| DELETE | `/api/v1/feedback/<id>` | Delete feedback |

### Fax (`fax` blueprint)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/fax/send` | Send a fax |
| GET | `/api/v1/fax/inbox` | Received faxes |
| GET | `/api/v1/fax/outbox` | Sent faxes |

### Mobile Events (v2 only)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v2/mobile-events` | Batch mobile analytics events |

---

## Error Handling

All errors return JSON. The app never returns HTML to API clients.

```json
// 401 Unauthorized (expired/missing token)
{ "error": "Unauthorized" }

// 400 Bad Request (missing fields, validation failure)
{ "error": "Email and password required" }

// 404 Not Found (bad resource ID)
{ "error": "Not found" }

// 409 Conflict (uniqueness violation, or per-user API-key cap reached)
{ "error": "Maximum of 5 active API keys reached" }

// 500 Internal Server Error (never exposes stack traces)
{ "error": "Internal server error" }
```

The `error` field is always a human-readable string. There are no error codes — match on HTTP status.

---

## Things the Mobile App Should Know

### Timestamps are UTC

The backend stores and returns all timestamps in UTC as ISO 8601 strings. The mobile app is responsible for converting to the user's local timezone for display. The user's preferred timezone is stored in the `home_timezone` field on their profile.

### HealthKit Sync Behavior

`POST /api/v1/healthkit/sync` accepts batch payloads with mixed metric types. Unsupported types are silently skipped (not rejected). Common aliases are mapped automatically:

| What the device sends | What the backend stores |
|----------------------|------------------------|
| `body_temperature` / `basal_body_temperature` | `temperature` |
| `oxygen_saturation` | `blood_oxygen` |
| `resting_heart_rate` | `heart_rate` |

Blood pressure data is handled via a dedicated insert path within the same sync endpoint.

### v4 Response Field Names

The API enforces v4 field naming conventions. Legacy v3 aliases for timeframes (`typical_time`) have been removed — use `time_of_day` in all requests.

### Request IDs

Every response includes an `X-Request-ID` header. Include this in bug reports — it maps directly to the backend's structured logs.

### CORS

The backend allows credentials from configured origins. For local React Native development against the Mac Docker stack, the default allows `http://localhost:80`, `http://127.0.0.1:80`, and `http://10.0.2.2:80` (Android emulator). Additional origins can be set via `CORS_ORIGINS` env var on the backend.

---

## Accounts

There is no shipped test-user fixture. Real accounts are provisioned on the appliance with `admin.py provision-user` (see [User Management CLI](#user-management-cli)).

For local development only, `scripts/local-init-db.sh` creates a single throwaway account:

| Environment | Email | Password | Notes |
|-------------|-------|----------|-------|
| Local Mac dev stack | `test@example.com` | `Password2026` | Throwaway account created by `scripts/local-init-db.sh` |

---

## Running Locally (for backend changes)

If you need to run the appliance stack locally to test API changes, bring up the three containers (pgvector, UserApp webapp on port 80, UserMCP on 13282) from the repo root:

```bash
# From the repo root
docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env up -d
scripts/local-init-db.sh   # Apply schema + create the throwaway test@example.com account
```

The API will be available at `http://localhost:80`. Host Ollama supplies embeddings. Full guide: `HowToDeploy/MacDeploy.md`.

### User Management CLI

```bash
./admin.py provision-user alice@example.com MyPassword123 "Alice Smith"
./admin.py list-users
./admin.py disable-user alice@example.com
./admin.py enable-user alice@example.com
./admin.py reset-password alice@example.com NewPassword456
./admin.py delete-user alice@example.com
```

---

## Where to Find More

| Topic | Location |
|-------|----------|
| Full architecture + all services | Repo root `CLAUDE.md` |
| Appliance deploy / local stack | `HowToDeploy/MacDeploy.md` |
| Database schema (source of truth) | `Infrastructure/init/docker-init-home/02-home_schema.sql` |
| Data model documentation + ERD | `DataModel3/HomeDatabaseERD.md` |
