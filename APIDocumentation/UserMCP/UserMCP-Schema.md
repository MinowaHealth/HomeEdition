# UserMCP — Schema Reference

**Date**: 2026-03-08 16:00 PST
**Source**: [`UserMCP/tools/health_data.py`](../UserMCP/tools/health_data.py)
**Related**: [`UserApp/webapp/routes/analytics.py`](../UserApp/webapp/routes/analytics.py) (`health_query` endpoint)

---

## Data Type Mapping

UserMCP routes ALL health data queries through `POST /api/v1/health-query`. The mapping from MCP `data_type` names to `health-query` `kind` values:

| MCP data_type | health-query kind | Source tables |
|---|---|---|
| `heart_rate` | `heart_rate` | `garm_hr`, `health_metrics` |
| `stress` | `stress` | `garm_stress`, `health_metrics` |
| `sleep_summary` | `sleep` | `garm_sleep_events`, `health_metrics` |
| `sleep_stages` | `sleep` | `garm_sleep_events` |
| `food_log` | `food` | `health_food_logv2` |
| `medication_log` | `medication` | `health_input_log` |
| `steps` | `steps` | `health_metrics` |
| `blood_pressure` | `blood_pressure` | `health_blood_pressure_readings` |
| `weight` | `weight` | `health_metrics` |
| `temperature` | `temperature` | `health_metrics` |
| `blood_oxygen` | `blood_oxygen` | `health_metrics` |
| `blood_glucose` | `blood_glucose` | `health_metrics` |
| `respiratory_rate` | `respiratory_rate` | `health_metrics` |
| `active_energy` | `active_energy` | `health_metrics` |

### Key Design Decisions

1. **Single query path**: All data types route through `health-query` — no individual `/garmin/*` or `/food-log` endpoint calls. This provides consistent date filtering and no hardcoded row limits.

2. **Concurrent fetching**: `asyncio.gather()` fetches all requested types in parallel. A request for 6 types = 6 concurrent HTTP calls (not 6 sequential).

3. **`sleep_summary` and `sleep_stages` both map to `sleep`**: The `health-query` endpoint returns all sleep data under the `sleep` kind. Clients should parse the response structure for summary vs stage data.

---

## health-query Endpoint Contract

```
POST /api/v1/health-query
Content-Type: application/json

{
  "kind": "heart_rate",
  "start": "2026-02-20T00:00:00",
  "end": "2026-02-25T23:59:59"
}
```

- `kind` (required): One of the kinds in the mapping table above
- `start` (required): ISO 8601 datetime string (start of range)
- `end` (required): ISO 8601 datetime string (end of range)
- Returns: Array of records with `timestamp` and metric-specific fields
- No hardcoded LIMIT — returns all matching records in the date range
- All results are filtered to the authenticated user (app-level `user_id` scoping)
- **Server-side 30s statement timeout** — queries exceeding this are cancelled (returns HTTP 503, code `QUERY_TIMEOUT`)
- **90-day max date range** on date-filtered queries (returns HTTP 400 if exceeded)

---

## Async HTTP Architecture

- **Transport**: `httpx.AsyncClient` (replaced `requests.Session`)
- **Lifecycle**: One client per SSE session, shared across POST handlers
- **Client timeout**: 30 seconds per API call (httpx)
- **Server timeout**: 30 seconds statement_timeout on PostgreSQL queries (server-side)
- **Error handling**: HTTP 401 → "token expired" message; HTTP 503 → "query timeout" message (no internal URL leaks)
