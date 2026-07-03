# API Conventions

**Date**: 2026-03-16 16:00 UTC

## Content Type

All requests and responses use `application/json` unless otherwise noted (e.g., HealthKit file upload uses `multipart/form-data`).

## Authentication Header

```
Authorization: Bearer <session_id>
```

The session ID is a UUID returned from `/api/v1/login`. See [Authentication.md](Authentication.md) for details.

## Error Response Format

All errors return a JSON object with an `error` field:

```json
{
  "error": "Human-readable error message"
}
```

### HTTP Status Codes

| Code | Meaning | When |
|------|---------|------|
| 200 | Success | Reads, updates, deletes |
| 201 | Created | New resource created |
| 202 | Accepted | Async job queued (Garmin sync, HealthKit import) |
| 400 | Bad Request | Missing required fields, validation failure |
| 401 | Unauthorized | Missing/expired session, invalid credentials |
| 403 | Forbidden | 2FA required |
| 404 | Not Found | Resource doesn't exist or isn't visible to this user |
| 409 | Conflict | Duplicate resource |
| 413 | Payload Too Large | File upload exceeds limit |
| 500 | Server Error | Unexpected failure |

## Timestamps


**History**: That LLMs are "timeblind" was the source of much suffering in the early days of this system. the maintainer tried many ways to force it to use his native Pacific time zone. There are mentions of this in various places in historic GitHub commits and documentation. This was shifted to standardizing on UTC around the time we got serious about switching to Postgres. If you come across a mention of Pacific, it's just a digital artifact.

**Storage**: All timestamps stored in  UTC. The user's `home_timezone` is authoritative for them.

**Request format**: ISO 8601 string. The server converts from the user's local timezone.
```
"2026-02-24T14:30:00"
```

**Response format**: ISO 8601 string in UTC. The client is responsible for localizing to the user's timezone for display. The user's `home_timezone` (in the `users` table) is available via the session for server-side localization where needed (e.g., analytics "today" boundaries).
```
"2026-02-24T22:30:00+00:00"
```

## IDs

All resource IDs are UUIDs (v4), returned as strings:
```
"550e8400-e29b-41d4-a716-446655440000"
```

## Pagination

List endpoints return a standard envelope — an `entries` array plus a `pagination` object — using offset/limit (not cursor-based):

```
{
  "entries": [ ... ],
  "pagination": { "total": 1234, "limit": 50, "offset": 100, "has_more": true }
}
```

Query parameters:
- `limit` — page size (default `50`, clamped to the endpoint's max, which is `200` unless otherwise documented).
- `offset` — rows to skip (default `0`).

See [PaginationStandard.md](PaginationStandard.md) for the full contract, defaults, and per-endpoint exceptions. (Filters like `?status=pending` are endpoint-specific, documented per endpoint — not part of pagination.)

## Legacy Field Compatibility

Several endpoints accept legacy v3 field names alongside current v4 names:

| Legacy (v3) | Current (v4) | Endpoints |
|-------------|-------------|-----------|
| `carbs_total_g` | `carbs_g` | food-items |
| `fat_total_g` | `fat_g` | food-items |
| `quantity` | `servings` | meals, food-log |
| `content` | `feedback` | feedback |
| `category` | `feedback_type` | feedback |
| `page` | `page_context` | feedback |
| `identifier` | `email` | login |
| `old_password` | `current_password` | change-password |

The API normalizes these transparently — both forms work.

## Per-User Scoping

Every request resolves the authenticated `user_id` from the session. `tenant_id` is present on every table and is always `1` (kept for schema parity with the Central System) — there is no multi-tenancy on the appliance. Privacy between household members is enforced in the application: every query against a user-owned table carries an explicit `user_id = %s` predicate.

This means a 404 response could mean either "doesn't exist" or "exists but belongs to another household member" — by design, the API does not distinguish these cases.

## Rate Limiting

None. Home Edition is a single-household appliance on a home LAN, not a public internet host — the app does no request rate limiting.
