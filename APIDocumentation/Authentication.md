# Authentication

**Date**: 2026-02-24

## Overview

The platform has two parallel auth systems sharing the same database:

| System | Table | Session TTL | Cookie/Header |
|--------|-------|-------------|---------------|
| **User auth** | `sessions` | 24 hours | Session cookie or `Authorization: Bearer <session_id>` |
| **Provider auth** | `provider_sessions` | 8 hours | `Authorization: Bearer <session_id>` only |

Both store session IDs as UUIDs in their respective tables. The bearer token IS the session ID — there are no JWTs or separate token formats.

---

## User Authentication

### Login

```
POST /api/v1/login
```

**Request:**
```json
{
  "email": "user@example.com",
  "password": "secret"
}
```

Also accepts `username` or `identifier` in place of `email`.

**Response (success):**
```json
{
  "success": true,
  "email": "user@example.com",
  "display_name": "User Name",
  "token": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": 1,
  "database": "healthv10",
  "created_at": "2026-01-15T08:30:00+00:00"
}
```

**Response (2FA required):**
```json
{
  "success": false,
  "requires_2fa": true,
  "pending_2fa_token": "uuid",
  "message": "Please enter your 2FA code"
}
```

The `pending_2fa_token` is a temporary token (5-minute TTL) that must be passed to the 2FA verify endpoint.

### 2FA Verification (during login)

```
POST /api/v1/2fa/verify
```

**Request:**
```json
{
  "pending_2fa_token": "uuid-from-login",
  "code": "123456"
}
```

Accepts either a TOTP code or a backup code. On success, returns the same response as a successful login.

**Response (success):**
```json
{
  "success": true,
  "email": "user@example.com",
  "display_name": "User Name",
  "token": "session-id-uuid",
  "database": "healthv10",
  "backup_code_used": false
}
```

If a backup code was used, includes `backup_codes_remaining` count and a warning when low.

### Using the Session

All subsequent requests authenticate via one of:

1. **Bearer token** (API clients, mobile): `Authorization: Bearer <token>`
2. **Session cookie** (web UI): Set automatically by Flask on login

The server populates `g.user` with:
```json
{
  "tenant_id": 1,
  "user_id": "uuid",
  "email": "user@example.com",
  "display_name": "User Name",
  "database_name": "healthv10",
  "created_at": "2026-01-15T08:30:00+00:00"
}
```

### Session Info

```
GET /api/v1/session
```

Returns the current session's user info. Useful for mobile apps to verify a stored token is still valid.

### Logout

```
GET /api/v1/logout
```

Deletes the session from the database. Returns `{"success": true}`.

### Password Change

```
POST /api/v1/change-password
```

**Request:**
```json
{
  "current_password": "old-secret",
  "new_password": "new-secret-min-8-chars"
}
```

### Password Reset Flow

1. `POST /api/v1/request-password-reset` with `{"email": "..."}` — Always returns success (prevents enumeration). Sends email with reset link if account exists.
2. `POST /api/v1/reset-password` with `{"token": "...", "new_password": "..."}` — Resets password and invalidates all sessions.

### Signup Flow

1. `POST /api/v1/signup` with `{"email": "...", "display_name": "..."}` — Sends verification email.
2. `POST /api/v1/verify-email` with `{"token": "..."}` — Returns `{"valid": true, "email": "..."}`.
3. `POST /api/v1/complete-signup` with `{"token": "...", "password": "..."}` — Creates account.

---

## 2FA Management

All endpoints below require an active session (`@require_auth`).

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/2fa/status` | GET | Check if 2FA enabled, backup codes remaining |
| `/api/v1/2fa/setup` | POST | Generate TOTP secret + QR URI |
| `/api/v1/2fa/verify-setup` | POST | Verify code and enable 2FA. Returns backup codes. |
| `/api/v1/2fa/disable` | POST | Disable 2FA. Requires `password`. |
| `/api/v1/2fa/regenerate-backup-codes` | POST | Generate new backup codes. Requires `password`. |

### 2FA Setup Response

```json
{
  "secret": "BASE32SECRET",
  "uri": "otpauth://totp/Minowa:user@example.com?secret=BASE32SECRET&issuer=Minowa"
}
```

### 2FA Verify-Setup Response

```json
{
  "success": true,
  "message": "2FA enabled successfully. Save these backup codes.",
  "backup_codes": ["CODE1", "CODE2", "CODE3", "CODE4", "CODE5", "CODE6", "CODE7", "CODE8"]
}
```

---

## Provider Authentication

### Login

```
POST /provider/login
```

**Request:**
```json
{
  "email": "doctor@clinic.com",
  "password": "secret",
  "totp_code": "123456"
}
```

`totp_code` is optional unless 2FA is enabled for this provider. If 2FA is mandatory (`REQUIRE_2FA=true`) and the provider hasn't set it up, login returns 403.

**Response (success):**
```json
{
  "session_id": "uuid",
  "expires_at": "2026-02-25T10:00:00",
  "provider": {
    "id": "uuid",
    "email": "doctor@clinic.com",
    "display_name": "Dr. Smith"
  }
}
```

### Session Verification

```
GET /provider/session
```

**Response:**
```json
{
  "provider": {
    "id": "uuid",
    "email": "doctor@clinic.com",
    "display_name": "Dr. Smith"
  },
  "in_patient_context": false
}
```

### Logout

```
POST /provider/logout
```

Returns `{"status": "logged_out"}`.

---

## Security Notes

- **Password hashing**: Werkzeug `pbkdf2:sha256` (users), Argon2id (providers)
- **TOTP**: PyOTP with SHA1, 6-digit codes, 30-second window
- **Backup codes**: 8 codes, stored as Argon2 hashes, single-use
- **Session storage**: Database-backed (not JWT) — revocable immediately
- **Anti-enumeration**: Login, signup, and password reset always return generic messages
- **IP tracking**: Sessions store `ip_address` and `user_agent` for audit
