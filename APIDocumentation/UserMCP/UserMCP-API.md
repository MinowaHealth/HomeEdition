# UserMCP — MCP Tool Reference

**Date**: 2026-03-08 16:00 PST
**Server**: `UserMCP/mcp_server.py` (Starlette + SSE transport, port 13282)
**Auth**: Bearer token (session UUID or `hbk_` API key) in `Authorization` header

---

## Connection

```
GET /sse
Authorization: Bearer <token>
```

Returns an SSE stream for the MCP protocol. Messages are sent via:

```
POST /messages/
Authorization: Bearer <token>
```

Health check (no auth):
```
GET /health
```

---

## Tools

### get_health_data

Fetch health data by type and date range. Returns individual timestamped records. All types are queried through `POST /api/v1/health-query` with proper date filtering.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `data_types` | string[] | Yes | Data types to fetch (see supported list below) |
| `start_date` | string | Yes | Start date in `YYYY-MM-DD` format |
| `end_date` | string | Yes | End date in `YYYY-MM-DD` format |

**Supported data types:**
`active_energy`, `blood_glucose`, `blood_oxygen`, `blood_pressure`, `food_log`, `heart_rate`, `medication_log`, `respiratory_rate`, `sleep_stages`, `sleep_summary`, `steps`, `stress`, `temperature`, `weight`

**Example call:**
```json
{
  "name": "get_health_data",
  "arguments": {
    "data_types": ["heart_rate", "blood_pressure", "medication_log"],
    "start_date": "2026-02-20",
    "end_date": "2026-02-27"
  }
}
```

**Response shape:**
```json
{
  "heart_rate": [
    { "timestamp": "2026-02-20T14:00:00-08:00", "value": 72.5, ... }
  ],
  "blood_pressure": [
    { "timestamp": "2026-02-20T09:00:00-08:00", "systolic": 120, "diastolic": 80, ... }
  ],
  "medication_log": [
    { "timestamp": "2026-02-20T08:00:00-08:00", "name": "Lisinopril", "dosage": "10mg", ... }
  ]
}
```

**Notes:**
- Multiple types are fetched concurrently (not sequentially)
- Date format is strictly `YYYY-MM-DD` — full datetime strings are rejected
- Unknown data types return an error message with the supported list
- API errors for one type don't block other types (partial results returned)

---

### get_health_snapshot

Get a combined health data snapshot for appointment prep or quick review. Returns all requested data types in one response with period metadata.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `data_types` | string[] | Yes | — | Data types to include |
| `days` | integer | No | 30 | Days to look back from today |
| `start_date` | string | No | — | Absolute start date (`YYYY-MM-DD`). Overrides `days`. |
| `end_date` | string | No | — | Absolute end date (`YYYY-MM-DD`). Overrides `days`. |

**Example — relative range:**
```json
{
  "name": "get_health_snapshot",
  "arguments": {
    "data_types": ["heart_rate", "food_log", "medication_log"],
    "days": 14
  }
}
```

**Example — absolute range:**
```json
{
  "name": "get_health_snapshot",
  "arguments": {
    "data_types": ["heart_rate", "blood_pressure", "weight"],
    "start_date": "2026-01-15",
    "end_date": "2026-01-28"
  }
}
```

**Response shape:**
```json
{
  "period": {
    "start": "2026-01-15",
    "end": "2026-01-28",
    "days": 14
  },
  "heart_rate": [...],
  "blood_pressure": [...],
  "weight": [...]
}
```

**Notes:**
- Uses local time (not UTC) for relative date calculations to avoid day-boundary mismatch
- `start_date` + `end_date` take priority over `days` if both are provided
- All types are fetched concurrently

---

### get_health_config

Get the user's health configuration: medications, supplements, stacks (bundles), and scheduled timeframes. Shows what is prescribed/configured, how items are grouped, and when they are scheduled.

**Parameters:** None (scoped to the authenticated user)

**Response shape:**
```json
{
  "health_inputs": [
    { "name": "Lisinopril", "dosage": "10mg", "form": "tablet", "input_type": "medication", ... }
  ],
  "stacks": [
    { "name": "Morning Meds", "timeframe": "Wake", "inputs": [...] }
  ],
  "timeframes": [
    { "name": "Wake", "time": "07:00" }
  ],
  "summary": { "total_inputs": 12, "active_inputs": 10 }
}
```

**Notes:**
- No parameters — returns the full health config for the authenticated user
- Proxies `GET /api/v1/health-inputs`, `GET /api/v1/stacks`, and `GET /api/v1/timeframes`

---

### get_lab_results

Get the user's latest lab results from HealthKit clinical records. Returns the most recent result for each test type.

**Parameters:** None

**Response shape:**
```json
{
  "results": [
    {
      "test_name": "Hemoglobin A1c",
      "loinc_code": "4548-4",
      "value": "5.7",
      "unit": "%",
      "reference_range": "4.0-5.6",
      "interpretation": "high",
      "recorded_at": "2026-02-15T09:00:00-08:00"
    }
  ]
}
```

**Notes:**
- Proxies `GET /api/v1/lab-results`
- Returns only the most recent result per test type (deduped by LOINC code)

---

### send_feedback

Submit feedback about the MCP server's behavior, data quality, or usability to the Minowa team.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `content` | string | Yes | The feedback text |
| `feedback_type` | string | Yes | Category: `bug`, `feature`, `general`, or `praise` |

**Response shape:**
```json
{
  "id": "uuid",
  "message": "Feedback created"
}
```

**Notes:**
- Posts to `POST /api/v1/feedback` and routes to the configured Slack channel
- Use when the user reports problems with data quality, missing information, or has feature requests

---

## Resource: minowa://suggestions

Non-diagnostic hints for exploring health data with Claude. This is an MCP **Resource** (not a tool) — access via the MCP resources API, not `call_tool()`.

**URI:** `minowa://suggestions`
**MIME type:** `application/json`

**Response shape:**
```json
{
  "disclaimer": "...",
  "caveats": ["..."],
  "exploration_ideas": ["..."],
  "data_quality_checks": ["..."],
  "next_steps": ["..."]
}
```

**Notes:**
- Static content — no API call, no parameters, same output every time
- Access pattern: `read_resource("minowa://suggestions")`

---

## Error Handling

| Error | Cause | User sees |
|-------|-------|-----------|
| Token expired | 401 from Flask API | "Authentication failed — your session token or API key may have expired. Please refresh your token or generate a new API key." |
| Query timeout | 503 from Flask API (`QUERY_TIMEOUT`) | "The query took too long and was cancelled by the server. Try a shorter date range or fewer data types." |
| Connection error | Flask API unreachable | "Connection error: could not reach the health data API" |
| Unknown tool | Invalid tool name | "Unknown tool: {name}" |
| No auth | Missing `Authorization` header | HTTP 401 JSON response |
| Bad dates | Non-YYYY-MM-DD format or start > end | "Invalid date format. Use YYYY-MM-DD" |

Internal URLs and infrastructure details are never exposed in error messages.

### Server-Side Query Limits

The Flask API enforces a **30-second statement timeout** on all database queries. If a query exceeds this limit (e.g., scanning a very large date range across multiple data types), PostgreSQL cancels the query and the API returns HTTP 503 with code `QUERY_TIMEOUT`. The UserMCP server translates this into a user-friendly error message.

Additionally, the Flask API enforces a **90-day maximum date range** on date-filtered queries — requests exceeding this return HTTP 400.
