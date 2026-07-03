# Date Filtering — Endpoint Reference

**Date**: 2026-03-08 16:30 PST
**Affected endpoints**: All listing GET endpoints below
**Source**: [`UserApp/webapp/routes/logging_routes.py`](../UserApp/webapp/routes/logging_routes.py), [`UserApp/webapp/routes/vitals.py`](../UserApp/webapp/routes/vitals.py)

---

## Overview

All listing endpoints now accept optional `start_date` and `end_date` query parameters to filter results by date range. Without these parameters, behavior is unchanged (returns most recent 100 records).

### Query Parameters

| Parameter | Type | Required | Format | Description |
|-----------|------|----------|--------|-------------|
| `start_date` | string | No | `YYYY-MM-DD` | Include records on or after this date |
| `end_date` | string | No | `YYYY-MM-DD` | Include records on or before this date (inclusive) |

### Response Headers

| Header | Value | Condition |
|--------|-------|-----------|
| `X-Truncated` | `true` | Returned when exactly 100 results hit the safety cap |

### Error Responses

| Status | Cause |
|--------|-------|
| 400 | Invalid date format (not YYYY-MM-DD) |
| 400 | `start_date` is after `end_date` |

---

## Affected Endpoints

### GET /api/v1/health-input-log

Medication/supplement intake logs.

```bash
# All logs (default — most recent 100)
GET /api/v1/health-input-log

# Filtered by date range
GET /api/v1/health-input-log?start_date=2026-01-01&end_date=2026-01-31

# Only start date (everything from Jan 1 onward)
GET /api/v1/health-input-log?start_date=2026-01-01
```

### GET /api/v1/food-log

Food consumption logs.

```bash
GET /api/v1/food-log?start_date=2026-02-01&end_date=2026-02-28
```

### GET /api/v1/blood-pressure

Blood pressure readings.

```bash
GET /api/v1/blood-pressure?start_date=2026-02-01&end_date=2026-02-28
```

### GET /api/v1/temperature

Temperature readings.

```bash
GET /api/v1/temperature?start_date=2026-02-01&end_date=2026-02-28
```

### GET /api/v1/weight

Weight readings.

```bash
GET /api/v1/weight?start_date=2026-02-01&end_date=2026-02-28
```

---

## Implementation Notes

- Date filtering uses parameterized SQL (`%s`) — no string interpolation
- The `end_date` is inclusive: `< end_date + INTERVAL '1 day'`
- The LIMIT 100 safety cap remains in place even with date filtering
- `X-Truncated: true` header signals the client that results were capped
- The `parse_date_range_params()` helper in `utils.py` provides consistent validation across all endpoints

---

## See also

- [DataModel3/TIMESTAMPS.md](../DataModel3/TIMESTAMPS.md) — UTC-at-rest storage policy and per-user `home_timezone`. Explains why a `YYYY-MM-DD` boundary has a ±1 day ambiguity at the user's local midnight.
- [TimeSystems.md](../TimeSystems.md) — scheduling architecture. Any future `start_date`/`end_date` parameters added to reminder or appointment list endpoints should follow the same convention defined here.
