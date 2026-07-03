# TIMESTAMPS.md — Timezone & Time Handling

**Date**: 2026-07-03 04:00 PDT

---

## Standard

**UTC storage. Localize for display.** (This is also a CLAUDE.md critical rule.)

- **Store**: UTC in the database (`TIMESTAMPTZ` columns, `datetime.now(pytz.utc)` in Python)
- **Display**: Convert to the user's `home_timezone` via `utc_to_local()` in `utils.py`
- **Input**: Convert from the user's timezone via `local_to_utc()` in `utils.py`
- **Converters**: Single source of truth in `UserApp/webapp/utils.py`

## How per-user timezone works

Each user has a `home_timezone` column in the `users` table. Session validation in `auth.py` fetches it into `g.user`, and `get_user_timezone()` in `utils.py` reads it with a fallback to `DEFAULT_TIMEZONE`. `utc_to_local()` / `local_to_utc()` accept an optional `tz` param and default to `get_user_timezone()`. Analytics "today" boundaries, Garmin sync date defaults, and feedback timestamps all resolve through this helper. UserMCP tools use UTC dates for default ranges.

## Known limitations

### 1. MCP date parameters are timezone-ambiguous (low priority)

UserMCP accepts date parameters as `YYYY-MM-DD` strings with no timezone info. The API endpoints receiving these use them as date boundaries in SQL queries. Since data is stored in UTC, a date boundary like `2026-03-16` means different things depending on the user's timezone. This is acceptable — the ±1 day ambiguity at date boundaries is minor for the "last N days" queries these tools generate.

### 2. Mixed timezone libraries (low priority — cosmetic)

| Module | Library | Pattern |
|--------|---------|---------|
| Routes (vitals, food, health_inputs, logging) | `pytz` | `datetime.now(pytz.utc)` |
| Auth (sessions, tokens) | stdlib | `datetime.now(timezone.utc)` |
| Logging config | stdlib | `datetime.now(timezone.utc)` |
| Analytics | `pytz` | `datetime.now(get_user_timezone())` |

Both work correctly. Python 3.9+ has `zoneinfo` in stdlib which could replace `pytz` entirely.

---

## See also

- [APIDocumentation/DateFiltering-API.md](../APIDocumentation/DateFiltering-API.md) — `start_date` / `end_date` query convention on list endpoints (the read-side consequence of UTC-at-rest + per-user `home_timezone`).
- [DataModel3/UpdatedAtPolicy.md](UpdatedAtPolicy.md) — when `updated_at` needs a trigger vs a route-side bump; `updated_at` is the last-write-wins sync cursor.
