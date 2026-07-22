# UserMCP Time Capabilities — Plan 1

**Date:** 2026-07-14 12:20 local (America/Chicago)
**Status:** IMPLEMENTED 2026-07-16 (Neal cued: "add date/time arithmetic
functions so the LLM doesn't handle what it's incapable of"). Recommended
options taken on all three open decisions. Scope grew one tool beyond this
plan: `date_math` (add/diff/weekday/window, server-side, month-clamp) —
general arithmetic, not just now/windows. Live on nealvm.

## Problem

Claude (the MCP client) has no authoritative clock. It works from its own
loosely-known UTC time and infers what "today", "this week", or "the last 30
days" mean for the user. Two concrete failure modes:

1. **Client-side guessing.** Claude Desktop doesn't know the user's
   `home_timezone` unless it happens to call `get_my_profile` first, and it
   never knows the current instant with authority. Date math ("from 2026-06-14
   to 2026-07-14") is inferred, sometimes wrong.
2. **Server-side UTC drift.** `resolve_window()` (`UserMCP/tools/_envelope.py:113`)
   computes the `days`-shorthand window end as `datetime.now(timezone.utc).date()`.
   For a user west of UTC in the evening, that's tomorrow — a 30-day lookback
   silently shifts a day. Affects the 4 tools using the shorthand:
   `activity`, `adherence`, `nutrition`, `vitals`.

## Design

### 1. New tool: `get_current_time` (`UserMCP/tools/time_context.py`)

No arguments. Fetches `home_timezone` from `GET /session` (same call
`get_my_profile` already makes), then answers entirely from stdlib
`zoneinfo` — no new UserApp surface, no new data class.

```json
{
  "data": {
    "utc": "2026-07-14T18:20:11+00:00",
    "timezone": "America/Chicago",
    "timezone_source": "profile",        // "profile" | "utc_fallback" (home_timezone null)
    "utc_offset": "-05:00",
    "local": "2026-07-14T13:20:11-05:00",
    "today": "2026-07-14",
    "weekday": "Tuesday",
    "yesterday": "2026-07-13",
    "common_windows": {                   // copy-paste-ready from/to pairs, local days
      "last_7_days":  {"from": "2026-07-08", "to": "2026-07-14"},
      "last_30_days": {"from": "2026-06-15", "to": "2026-07-14"},
      "last_90_days": {"from": "2026-04-16", "to": "2026-07-14"}
    }
  },
  "coverage": {...}, "sources": [], "disclaimer": ...
}
```

`common_windows` is the piece that kills "infer about timeframes": Claude
copies the pair verbatim into any tool's `from`/`to` args instead of doing
its own arithmetic. Windows match `window_block()` closed-interval semantics
(from == to − N + 1 for an N-day window).

Registered in `tools/__init__.py` `_TOOL_MODULES` — the registry-driven wire
sweep and stack-invisibility tests cover it automatically.

### 2. Fix the UTC-day drift in `resolve_window`

- New helper `tools/_time.py` → `async def home_tz(client) -> ZoneInfo`:
  reads `home_timezone` from `GET /session`, falls back to UTC on
  null/invalid/error. (Shared by the new tool and the fix below.)
- `resolve_window()` gains an optional `tz: ZoneInfo | None = None` param;
  when set, the `days`-shorthand end date becomes
  `datetime.now(tz).date()` — today *in the user's home timezone*.
- The 4 shorthand callers call `home_tz(client)` **only when `from`/`to`
  are absent** and pass it through. Cost: one extra `/session` GET per
  days-shorthand call; explicit-window calls pay nothing.
- Explicit `from`/`to` behavior is unchanged.

### 3. Steering text

- `prompts.py`: one line in the visit-prep prompt — call `get_current_time`
  before any date reasoning.
- Tool descriptions that take `from`/`to`/`days` gain a short pointer:
  "Dates are the user's local days; use get_current_time for today's date."

### 4. Tests

- `UserMCP/tests/test_time_context.py`: mocked `/session` → assert timezone
  passthrough, `utc_fallback` on null, `common_windows` arithmetic against a
  pinned instant, offset formatting (incl. a non-hour offset like Asia/Kolkata).
- `_envelope` tests: `resolve_window` with `tz` set crosses the UTC midnight
  boundary correctly (freeze a UTC instant where UTC date ≠ Chicago date).
- Wire sweep + stack-invisibility sweeps pick up the new tool with no edits.
- Twin CONTRACT files (`test_route_contracts.py`): no new UserApp routes, so
  the pinned hash should not move — verified, not assumed.

## Decisions for Neal

1. **`common_windows` presets** — include (recommended) or keep the tool to
   clock+timezone only?
2. **Server-side drift fix (§2)** — do it (recommended; it's a real
   off-by-one) or rely purely on Claude passing explicit `from`/`to`?
3. Tool name: `get_current_time` (recommended) vs `get_time_context`.

## HIPAA compliance check

This adds one MCP tool that discloses only the user's `home_timezone` plus
server clock arithmetic; it reads via the existing authenticated `/session`
path.

- **§ 164.502(b) Minimum Necessary:** satisfied — the tool returns timezone
  and clock values only; no health data, no identifiers beyond what the
  session already establishes.
- **§ 164.312(a) Access Control / § 164.312(d) Authentication:** unchanged —
  same per-request bearer-token proxy through UserApp, same RLS context;
  the tool cannot read another user's timezone.
- **§ 164.312(b) Audit Controls / § 164.528 Accounting of Disclosures:** the
  underlying `GET /session` call is logged by UserApp request logging exactly
  as it is for `get_my_profile` today; no new PHI class or access path is
  created, so no new audit row is required.
- The `resolve_window` change alters no access path — same endpoints, same
  auth; only the computed date window shifts to the user's local frame.

No other § 164.3xx rules are implicated.

## Verification

1. Unit suite (`UserMCP/tests/`) green, including new tests.
2. nealvm live: deploy, ⌘Q Claude Desktop, call `get_current_time` — timezone
   matches Neal's profile, `today` matches wall-clock; ask "what did I log
   yesterday?" and confirm the window Claude passes matches `common_windows`.

## Deployment

Commit locally on `main` (no push — Neal cues promotion); rsync-deploy to
nealvm `mcpuser` and restart UserMCP.
