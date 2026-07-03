# Authentication — Home Edition

**Date:** 2026-07-03 03:45 PDT

This page documents the authentication data model and the auth flows built on it: password storage, web sessions, long-lived API keys, TOTP two-factor auth, account lifecycle, and the network posture that fronts all of it. The implementation lives in `UserApp/webapp/auth.py` (with the request-side resolution in `UserApp/webapp/utils.py` `require_auth`), and the schema source of truth is `Infrastructure/init/docker-init-home/02-home_schema.sql`.

## Overview

Home Edition is one box serving one household (~6 people) over the LAN. Everyone with a login is a household member; privacy *between* members is enforced in the application: every query against a user-owned table carries an explicit `user_id = %s` predicate resolved from the authenticated request. The documented exception is pre-auth lookups keyed by a globally-unique secret — a session UUID or an API-token hash — which by construction can only ever resolve to the one row that secret was minted for.

All tables carry a `tenant_id` column as a fixed app-level scoping convention (`tenant_id = 1`), included in composite primary keys and in query predicates alongside `user_id`.

Three credentials open the door:

| Credential | Table | Lifetime | Typical client |
|------------|-------|----------|----------------|
| Web session | `sessions` | 24 h sliding (configurable) | Browser (cookie), mobile app (bearer) |
| API key (`hbk_…`) | `api_tokens` | Until revoked (`expires_at` optional) | UserMCP / Claude Desktop, integrations |
| HealthKit sync token | env only (`HEALTHKIT_SYNC_TOKEN`) | Static | Background HealthKit sync |

## Auth Tables

### `users` — the auth-relevant columns

The `users` table is also the profile table; the columns that matter for authentication:

| Column | Type | Purpose |
|--------|------|---------|
| `tenant_id`, `id` | SMALLINT, UUID | Composite PK |
| `email` | TEXT | Login identifier, unique per `(tenant_id, email)`, lower-cased on lookup |
| `password_hash` | VARCHAR(255) | Argon2id hash (see below) |
| `is_active` | BOOLEAN | Checked at login **and** on every session/API-key resolution |
| `last_login` | TIMESTAMPTZ | Stamped on each successful password auth |
| `totp_secret` | TEXT | Base32 TOTP secret (present once 2FA setup starts) |
| `totp_enabled` | BOOLEAN | Whether 2FA is active (default `false`) |
| `totp_backup_codes` | TEXT[] | Argon2id-hashed single-use recovery codes |
| `totp_enabled_at` | TIMESTAMPTZ | When 2FA was enabled |

### `sessions` — short-lived web/API sessions

| Column | Type | Purpose |
|--------|------|---------|
| `tenant_id`, `session_id` | SMALLINT, UUID | Composite PK; `session_id` is the bearer secret |
| `user_id` | UUID | FK to `users` (`ON DELETE CASCADE`) |
| `created_at`, `expires_at` | TIMESTAMPTZ | `CHECK (expires_at > created_at)` |
| `ip_address`, `user_agent` | INET, TEXT | Client at login; `user_agent` doubles as the pending-2FA marker (below) |
| `last_activity` | TIMESTAMPTZ | Updated on every valid request |
| `session_type` | TEXT | `'web'` (default) or `'api'` |
| `totp_verified_at` | TIMESTAMPTZ | When 2FA was verified for this session (NULL if 2FA off) |

### `api_tokens` — long-lived keys

Kept separate from `sessions` so the two lifecycles never interfere: web logout deletes a session row; API keys survive until explicitly revoked.

| Column | Type | Purpose |
|--------|------|---------|
| `tenant_id`, `id` | SMALLINT, UUID | Composite PK |
| `user_id` | UUID | FK to `users` (`ON DELETE CASCADE`) |
| `token_hash` | VARCHAR(255) | SHA-256 of the raw key — plaintext is never stored |
| `key_prefix` | VARCHAR(12) | First 12 chars (`hbk_` + 8 hex) for display and indexed lookup |
| `device_name` | TEXT | Human label ("Claude Desktop", "HealthKit Sync") |
| `token_type` | TEXT | `'mobile'`, `'healthkit'`, `'integration'`, `'mcp'` |
| `created_at`, `last_used_at`, `expires_at` | TIMESTAMPTZ | `expires_at` NULL = never expires |
| `revoked_at` | TIMESTAMPTZ | Soft delete — revoked keys stay for the audit trail |
| `totp_verified_at`, `created_ip`, `last_ip` | TIMESTAMPTZ, INET, INET | Security tracking |

Partial indexes on `(tenant_id, user_id)`, `token_hash`, and `key_prefix` — all `WHERE revoked_at IS NULL` — keep active-key lookups fast without paying for the revocation history.

### `user_devices` — device registry

One row per physical device per user (`UNIQUE (tenant_id, user_id, device_id)`), tracking platform, OS/app version, hardware, and first/last-seen timestamps. It is a registry for capability reporting and analytics, not a credential store — possession of a `device_id` grants nothing.

### Seam tables

The schema also carries `password_reset_tokens` and `email_verification_tokens`. Neither is on an active code path — account recovery is CLI-only (see Account Lifecycle) — they exist as seams for a possible future in-app flow.

`audit_log` records auth-adjacent actions (`user_id`, `action`, `target`, `details` JSONB, `ip_address`).

## Password Storage — Argon2id

- **Hashing:** `argon2-cffi`'s `PasswordHasher` with library defaults (`$argon2id$v=19$m=65540,t=3,p=4$…`), via `hash_password()` / `verify_password()` in `auth.py`.
- **Verification** raises-and-catches `VerifyMismatchError` / `InvalidHash`, returning a plain boolean.
- `looks_like_password_hash()` guards CLI paths against double-hashing an already-hashed value (matches the `$argon2…$` prefix).
- Backup codes reuse the same Argon2id hashing — a leaked `users` row exposes no usable second factor.

## Web Sessions

Lifecycle (all in `auth.py`):

1. **Login** (`authenticate_user`): case-normalized email + password check within `tenant_id = 1`; inactive users are refused before password verification result matters; `last_login` is stamped.
2. **Creation** (`create_session`): random UUIDv4 `session_id`, expiry = now + `SESSION_DURATION_HOURS` (default **24 hours**; `SESSION_TIMEOUT_MINUTES` takes precedence when set, for short-timeout installs).
3. **Validation** (`get_session`): looked up by `session_id` alone — the UUID is the globally-unique secret, the documented pre-auth exception — then joined to `users` to re-check `is_active`. Expired rows are deleted on contact.
4. **Sliding window:** every valid request updates `last_activity` and pushes `expires_at` forward another full duration; an actively-used browser never gets logged out mid-task.
5. **Logout** (`delete_session`): deletes that one session row. Other devices' sessions and all API keys are untouched.
6. **Cleanup** (`cleanup_expired_sessions`, mirrored by a SQL function of the same name): bulk-deletes anything past `expires_at`.

**Pending-2FA rows:** when a 2FA-enabled user passes the password check, `/login` inserts a challenge row with `user_agent = '2FA_PENDING'` and returns its id as the `pending_2fa_token`. `get_session` filters these out with `user_agent IS DISTINCT FROM '2FA_PENDING'` (`IS DISTINCT FROM`, not `<>`, so legitimate NULL user agents still match) — a password-only attacker cannot present the pending token as a bearer and skip the second factor.

## Bearer Resolution Order

`require_auth` (in `utils.py`) resolves credentials in this order:

1. `Authorization: Bearer <HEALTHKIT_SYNC_TOKEN>` — static env token for background HealthKit sync, mapped to `HEALTHKIT_SYNC_USERNAME`.
2. Bearer as a **session id** — mobile clients authenticate with the same session UUID a browser holds in its cookie.
3. Bearer starting with `hbk_` — **API key** lookup (below).
4. Flask session cookie — the browser path.

A bearer that is present but resolves to nothing gets a hard `401`; API paths never fall through to the login redirect.

## API Keys

Long-lived, per-user keys for MCP clients and integrations.

- **Format:** `hbk_` + 32 hex chars (128 bits from `secrets.token_hex(16)`).
- **At rest:** only the SHA-256 hash (`_hash_api_key`) plus the 12-char `key_prefix` are stored. SHA-256 without stretching is appropriate here because the input is a high-entropy random token, not a human password. The raw key is returned **once** at creation and never again.
- **Lookup** (`lookup_api_key`): indexed by `key_prefix`, verified against `token_hash`, filtered to non-revoked and non-expired, joined to `users` for the `is_active` check. `last_used_at` is updated fire-and-forget — a failed stamp never fails auth.
- **Cap:** `MAX_API_KEYS_PER_USER` (default **5**) active keys per user, checked at creation.
- **Revocation** (`revoke_api_key`): sets `revoked_at` (soft delete), scoped to the requesting user — one member cannot revoke another's keys.

Endpoints (session-authenticated, in `app.py`):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/api-keys` (also v2) | POST | Create; returns `{id, key, label, key_prefix, created_at}` — `key` shown once |
| `/api/v1/api-keys` (also v2) | GET | List own keys — metadata and prefix only, never hashes |
| `/api/v1/api-keys/<id>` (also v2) | DELETE | Revoke own key |

`UserMCP/get-token.sh` wraps the whole dance for Claude Desktop setup: log in with email/password, POST an API key labeled "Claude Desktop", print the MCP client config. UserMCP itself is a stateless proxy — it forwards the caller's bearer per request and holds no credentials of its own.

## Two-Factor Authentication (TOTP)

Optional per-user TOTP (PyOTP), **off by default**; recommended for a LAN box that multiple people can reach.

- **Setup** (`setup_2fa`): generate a base32 secret, store it (not yet enabled), return the `otpauth://` URI for the QR code.
- **Enable** (`verify_and_enable_2fa`): user proves possession with a live code; only then does `totp_enabled` flip, and **10 backup codes** (8 hex chars each) are generated — plaintext shown once, Argon2id hashes stored in `totp_backup_codes`.
- **Login** (`verify_2fa_login`): accepts a TOTP code (`valid_window=1`, ±30 s clock tolerance) or a backup code; a used backup code is removed from the array atomically.
- **Disable / regenerate** (`disable_2fa`, `regenerate_backup_codes`): both require the current password. Disabling keeps the secret (re-enabling doesn't force re-provisioning of the authenticator) but clears the backup codes.
- `check_2fa_required` runs after password verification to decide whether `/login` issues a real session or a pending-2FA challenge row.

## Account Lifecycle

Account management is **CLI-only**, via `UserApp/admin.py`, run by whoever has shell access to the box. There are no email flows — no signup links, no reset links, nothing that requires the appliance to send mail.

```bash
./admin.py provision-user alice@example.com MySecurePass123 "Alice Smith"
./admin.py reset-password alice@example.com NewPassword456
./admin.py list-users
```

- **Provisioning** creates the `users` row with an Argon2id hash.
- **Reset** overwrites `password_hash` directly — physical/shell access to the appliance *is* the recovery credential.
- **In-app change:** `POST /api/v1/change-password` (also v2) calls `change_password()`, which requires the **current** password before accepting the new one. Fully offline, works for any member without touching the CLI.

## Network Posture — LAN Allowlist

Before any credential is even examined, `source_ip_filter` (a Flask `before_request` hook in `app.py`) refuses requests from outside the household:

| Allowed range | What it is |
|---------------|------------|
| `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` | RFC1918 LAN |
| `100.64.0.0/10` | CGNAT / RFC6598 — Tailscale et al. |
| `127.0.0.0/8`, `::1/128` | Loopback |
| `fc00::/7` | IPv6 ULA (incl. Tailscale v6) |

Everything else gets `403` on **every** route, API included. The ranges are enumerated explicitly rather than via `ipaddress.is_private`, whose membership varies across Python versions. Only the real peer (`request.remote_addr`) is matched — `X-Forwarded-For` is deliberately ignored, since there is no reverse proxy and a forwarded header would be attacker-spoofable. IPv4-mapped IPv6 peers (`::ffff:a.b.c.d`) are unwrapped before matching. This sits as defense-in-depth beneath the `BIND_ADDR` knob (`127.0.0.1` loopback-only by default; `0.0.0.0` to serve the LAN): even bound wide open or fronted by a tunnel, the box never answers a public peer.

## Security Properties at a Glance

| Concern | Mitigation |
|---------|------------|
| Password theft from DB | Argon2id hashes; backup codes hashed the same way |
| API key theft from DB | SHA-256 at rest; plaintext shown once at creation |
| 2FA bypass via pending token | `2FA_PENDING` rows excluded from session resolution |
| Stolen device / leaked key | Per-key revocation (`revoked_at`), per-session logout |
| Stale sessions | Sliding expiry + delete-on-contact + `cleanup_expired_sessions()` |
| Off-LAN probing | Source-IP allowlist rejects public peers on every route |
| Cross-member reads | Explicit `user_id` predicate on every user-owned-table query |
