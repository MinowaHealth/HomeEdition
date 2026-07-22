# Plan: Stack Visibility for MCP + Acquisition Log

**Date:** 2026-07-16
**Timestamp:** 19:05 UTC
**Status:** Draft for Neal's annotations

Two items, independent — can ship separately. Part 1 is a thin MCP tool
(no schema change). Part 2 is a new table + full vertical (delta before
code on prod).

---

## Part 1 — `get_stacks` MCP tool (stack analysis)

### Context

The stack-invisibility rule (CLAUDE.md 2026-07-13) bans stacks from all MCP
surfaces **unless the tool's name explicitly contains "stack"** — the
carve-out was designed for exactly this request, and the enforcement sweep
(`test_stack_invisibility.py`) already skips stack-named tools. So this is
additive: one new tool, no rule change, no exemption edits.

### Design

- **New UserMCP tool `get_stacks`** (read-only, `tools/stacks.py`), proxying
  the existing `GET /api/v1/stacks` — which already returns each stack with
  its inputs (`input_name`, `dosage_override`), timeframe name, and
  `is_active`. Pagination params pass through.
- **Small additive API change** so the tool is useful for analysis without
  a second round-trip: extend the `json_build_object` in `get_stacks()`
  (`UserApp/webapp/routes/health_inputs.py`) with `input_type`,
  `default_dosage`, `default_unit` — the LLM needs the effective dose
  (`dosage_override` falling back to defaults) and med/supplement type.
  SPA unaffected (extra keys).
- Timeframe schedule context (`time_of_day`, `frequency`) comes through the
  existing joined `timeframe_name`; if analysis needs the actual times, add
  `t.time_of_day, t.frequency` to the same SELECT (also additive). Included
  in this plan — strike if premature.
- Tool description states the analysis purpose (composition review,
  overlap/interaction surface, schedule sanity) — not logging. Logging stays
  with the existing flows.
- **No stripping**: `activity.py`-style `stack`-field stripping must NOT be
  applied to this tool's responses (it is the named exception).
- Contract twins: `get_stacks` entry added to both CONTRACT files,
  `CONTRACT_SHA256` re-pinned same commit. Registry wire-sweep +
  invisibility sweep cover the rest automatically.
- Tests: tool-level test (envelope, pagination pass-through, no-auth error),
  plus a pin that the tool name contains "stack" (so a rename can't silently
  re-enter the banned zone).

### HIPAA compliance check (Part 1)

- **§164.312(a) Access Control:** proxy path unchanged — per-request bearer
  → UserApp `@require_auth` → RLS (`stacks`/`stack_inputs`/`timeframes` all
  carry the standard tenant+user policy). No new access path, only a new
  reader of an existing self-access endpoint.
- **§164.312(b) Audit Controls:** self-access read via the existing
  request-log posture, same as every other MCP read tool. No new audit row
  — matches `/search` and `get_recent_activity` precedent.
- **§164.312(d) Authentication:** unchanged (bearer per request).
- **§164.502(b) Minimum Necessary:** returns stack composition only —
  no log history, no PHI beyond what the user's own stack editor shows;
  paginated.
- **§164.528:** self-access, not a disclosure. No accounting impact.

---

## Part 2 — Acquisition log (`health_input_acquisitions`)

### Context

`health_inputs` carries current-state supply fields (`brand`,
`current_quantity`, `refills_remaining`, `pharmacy`) but no **event
history** of arrivals. The analysis goal — "did usage stop because supply
ran out, or by choice?" — needs dated acquisition events to line up against
`health_input_log` usage.

### Schema

New table (delta `Infrastructure/deltas/2026-07-16-health-input-acquisitions.sql`,
idempotent; mirrored into `02-healthv10_schema.sql`; prod: delta before code):

```sql
CREATE TABLE health_input_acquisitions (
    tenant_id       SMALLINT NOT NULL,
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sqlite_id       BIGINT,                -- mobile sync contract parity
    user_id         UUID NOT NULL REFERENCES users(id),
    health_input_id UUID REFERENCES health_inputs(id) ON DELETE SET NULL,
    item_name       TEXT NOT NULL,         -- snapshot; survives input deletion
    acquired_date   DATE NOT NULL,
    quantity        NUMERIC(10,2),
    unit            TEXT,                  -- 'tablets', 'ml', 'bottles'
    cost            NUMERIC(10,2),         -- USD assumed; currency col deferred
    brand           TEXT,
    vendor          TEXT,                  -- pharmacy / Amazon / clinic ...
    expiration_date DATE,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    synced_at       TIMESTAMPTZ
);
CREATE INDEX ... ON health_input_acquisitions (tenant_id, user_id, acquired_date DESC);
CREATE INDEX ... ON health_input_acquisitions (tenant_id, user_id, health_input_id);
```

- Standard inline RLS policy (USING + WITH CHECK, tenant+user).
- `item_name` snapshot + `SET NULL` FK: acquisition history is a financial/
  supply record — it must survive the catalog item being deleted.
- `acquired_date` is a calendar fact → DATE, not TIMESTAMPTZ (matches
  `start_date`/`end_date` convention on `health_inputs`).
- Deliberately NOT included until asked: currency, lot numbers, per-dose
  cost computation, insurance/copay split.
- **Open question for Neal:** should POST also bump
  `health_inputs.current_quantity` (inventory behavior), or is this purely
  a journal? Plan assumes **journal only** — no side effects.

### UserApp

- New blueprint or extend `health_inputs.py`: 
  - `POST /api/v1/acquisitions` (required: `item_name` or `health_input_id`,
    `acquired_date`; the rest optional)
  - `GET /api/v1/acquisitions` (pagination + standard date filtering +
    optional `health_input_id` filter)
  - `PUT/DELETE /api/v1/acquisitions/<id>`
- `/all-logs` gains `kind=acquisition` (12th source, same pattern as
  `document`): description `"Arrived: {item_name}"` (+ quantity/brand when
  present).

### UserMCP

- `get_recent_activity` kind enum gains `"acquisition"` (contract twins
  re-pinned).
- **New tool `get_acquisitions`** (read-only proxy of the GET route, item +
  date-range filters): supply analysis needs months of per-item history,
  which the 100-cap activity feed can't carry. Tool description states the
  supply-vs-choice analysis purpose so the model pairs it with
  usage-log queries.

### SPA

- Meds/Supplements item detail: "Log arrival" form (date, qty, unit, cost,
  brand, vendor) + arrivals list on the item. Feed shows acquisition
  entries with an ARR badge. Kept deliberately small — no inventory
  dashboards.

### Docs / tests

- `APIDocumentation/UserAPI.md` + `openapi.yaml` (new routes), ERD +
  `HealthDatabaseReportv10.md` + `UnusedColumnsAudit` regen.
- UserApp route tests (validation, RLS isolation via second user, date
  filter, feed integration); UserMCP tool tests + contract twins.

### HIPAA compliance check (Part 2)

- **§164.312(a) Access Control:** new table gets the standard inline
  tenant+user RLS policy (both clauses); all routes `@require_auth`; MCP
  path is per-request bearer → RLS. No delegate policy in v1 — providers
  do not see acquisitions until explicitly decided.
- **§164.312(b) Audit Controls:** patient self-writes via authenticated
  routes; request-log covers reads, consistent with the other
  `health_*` CRUD surfaces. No new delegate/third-party read path is
  created, so no new per-read audit row is required. If a delegate read
  path is added later, it must bring its own audit-row commitment.
- **§164.312(d) Authentication:** unchanged (session/bearer + RLS context).
- **§164.502(b) Minimum Necessary:** cost/vendor data is visible only to
  the owning user; MCP returns paginated, filterable slices, not bulk
  dumps.
- **§164.528:** self-access only in v1; nothing to account. Delegate
  visibility, if added, becomes a disclosure surface and re-triggers this
  gate.

---

## Order of work

1. Part 1 (no schema): API additive fields → tool → twins → tests → nealvm.
2. Part 2 delta on nealvm → routes → feed → MCP → SPA → docs → verify with
   a seeded gap scenario (acquisitions stop, usage continues, then stops —
   the exact supply-vs-choice signal).

## Verification

- Part 1: Claude Desktop asks "what's in my stacks?" → `get_stacks` returns
  composition with effective doses; invisibility sweep still green
  (16 existing tools untouched).
- Part 2: log 3 arrivals for one item across 2 months; `/all-logs` shows
  them; `get_acquisitions` filtered to the item returns all 3; second user
  sees none; usage-vs-arrival gap question answerable in Desktop.
