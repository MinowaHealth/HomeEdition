# Changelog

**Date: 2026-07-22 10:00 PDT**

Release version = database schema version (the `VERSION` file at the repo
root, surfaced at `GET /api/v1/healthz`). `main` is always the most recent
release; partial work lives in branches. See `RELEASING.md` for the cut
procedure.

## 11.1.0-home — 2026-07-22

First release for outside installation. Port of the July 2026 HBbackend
feature stream (~24 upstream commits), adapted for the single-household
trust model.

### UserMCP — 12 → 23 tools, new transport
- Streamable HTTP transport on `/mcp` (fixes stranded Claude Desktop
  sessions); legacy SSE kept.
- New tools: time context + date math, stacks, acquisitions, Garmin
  sync / minute-detail / sleep-events, observations detail, chat summaries,
  episode reports (save + list).
- Authoritative server-side time context; `/session` now returns
  `home_timezone`.

### UserApp
- **Acquisitions & inventory**: supply-arrival journal
  (`/api/v1/acquisitions`); dose logging decrements count-remaining,
  arrivals bump it; arrivals appear in the `/all-logs` feed.
- **Episode reports**: immutable HTML documents with supersedes chains,
  provenance, sandboxed inline view (`CSP: sandbox allow-scripts`).
- **Documents**: keyword (FTS) + semantic search with mode control, view
  links, chat-session summaries.
- **Blood pressure**: source filter + inventory endpoint, position/device
  on entry, user-configured meter pick list, backfill + batch-import
  scripts.
- **Detail windows**: shared `at`/`window_minutes`/`from`–`to` parsing
  across garmin minute-detail, sleep-events, and observations detail.
- **Garmin**: quiet MCP-triggered sync with device-staleness reporting,
  user-selectable sync timeframe, sync history (`data_sync_log`).

### Schema 11.1.0-home
New tables `data_sync_log`, `health_input_acquisitions`; `documents.fts`
generated tsvector + partial GIN, `provenance` JSONB, widened source CHECK;
`AI Sessions` + `Episode Reports` system folders;
`user_preferences.bp_devices`. Running boxes upgrade with
`scripts/apply_11_1_0_home.py` (idempotent, psycopg).

### Household trust model
Every ported SQL statement re-scoped with explicit `tenant_id`/`user_id`
predicates — upstream relies on RLS, which this box doesn't have. Thirteen
cross-user leak sites caught and fixed during the port; procedure documented
in `DataModel3/PortingBetweenEditions.md` (mirrored in both repos).

### Release plumbing
`VERSION` file at the repo root; `/api/v1/healthz` reports it;
`UserApp/update.sh` pulls main, rebuilds webapp + usermcp, and reminds
about pending schema apply scripts. Pillow bumped to 12.3.0 (CVE batch).
