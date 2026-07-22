# Episode Analysis Reports — Storage Plan (Plan 1)

**Date:** 2026-07-20
**Timestamp:** 2026-07-20 09:30 PT
**Status:** Draft for review
**Author:** Claude Code session with Neal

## What this is

The `health-episode-report` Desktop skill produces a single-page, self-contained
HTML report (Chart.js graph, clinical narrative, data table, verbatim
observations, caveats) for a user-specified time window. Today that artifact
dies in the Desktop session's scratch filesystem. This plan gives it durable,
structured storage: reports become **documents** — embedded, full-text
searchable, foldered — with episode metadata that makes them queryable by time
window, and MCP tools so future sessions ("the system") can list, fetch, and
compare past episodes.

Decision already made (Neal, 2026-07-20): ride the existing documents
pipeline — embeddings + related metadata — not a parallel table.

## Design

### Storage shape

One episode report = one `documents` row + one file on disk, exactly the
chat-summary pattern with three differences:

| Aspect | Chat summary | Episode report |
|---|---|---|
| `source` | `chat_summary` | **`episode_report`** (new CHECK value) |
| System folder | `AI Sessions` | **`Episode Reports`** (new system folder, self-healing resolve) |
| File | `original.md`, `text/markdown` | **`original.html`**, `text/html` (the artifact people keep) |

**Searchable text ≠ the artifact.** Raw HTML is markup noise for FTS and
embeddings. The write API takes both:

- `report_html` — the verbatim single-page artifact → file on disk (≤ 2 MB)
- `narrative_text` — plain text the caller assembles from lead + narrative +
  verbatim observations + caveats (≤ 256 KB) → stored in `ocr_text_full`,
  which drives the existing generated `fts` tsvector and the
  `embedding_content` inline embedding

`search_my_data` (scope=documents) then finds episodes by keyword and
semantically with **zero new search code**.

### Episode metadata (in `provenance` JSONB)

```json
{
  "episode_start": "2026-07-19T08:21:00Z",
  "episode_end":   "2026-07-19T11:52:00Z",
  "version": 2,
  "supersedes_document_id": "<uuid or null>",
  "annotations": {
    "spans":   [{"from": "...", "to": "...", "label": "thumpy"}],
    "events":  [...],
    "caveats": ["..."],
    "discarded_readings": [...]
  },
  "model_id": "...", "source_tools": [...], "created_via": "usermcp"
}
```

- `episode_start`/`episode_end` are the **unpadded analyzed window**, stored as
  UTC ISO 8601 strings. ISO-UTC strings compare correctly as text, so window
  overlap filtering works with plain `provenance->>'episode_start'`
  comparisons. No new columns on `documents`; no index at alpha scale
  (dozens of reports/user/year — seq scan within an RLS-filtered user is
  nothing). Add a partial expression index if that ever changes.
- `version` + `supersedes_document_id` model the skill's existing `vN` re-run
  chain. Reports are immutable: a re-run **creates a new document** that
  supersedes the old one; nothing is edited in place. Old versions remain
  (deletable via normal document delete).
- `annotations.spans` matter: felt episodes ("thumpy" spells) exist **only**
  in reports — this is genuinely new structured data, kept queryable.

### Schema delta (10.19.0)

`Infrastructure/deltas/2026-07-20-episode-reports.sql` (+ mirrored into
`02-healthv10_schema.sql`, ERD, `schema_version`):

1. `documents_source_check` CHECK gains `'episode_report'`
   (drop + re-add constraint, idempotent guard).
2. `seed_user_system_folders()` gains `Episode Reports` + backfill for
   existing users (same pattern as the 2026-07-15 AI Sessions delta).

No new tables, no RLS changes — documents policies already cover the rows.

### API (UserApp, documents blueprint)

- **`POST /api/v1/documents/episode-reports`** — mirror of
  `create_chat_summary`. Body: `title` (≤200), `report_html` (required, ≤2 MB),
  `narrative_text` (required, ≤256 KB), `episode_start`, `episode_end`
  (required, ISO 8601, end > start), `version` (default 1),
  `supersedes_document_id` (optional, must reference an own `episode_report`
  document), `annotations` (optional object), `model_id`, `source_tools`,
  `created_via`. Writes file + row + **audit row** + inline embedding.
- **`GET /api/v1/documents/episode-reports`** — envelope list (no HTML):
  id, title, episode window, version, supersedes id, created_at, links.
  Filters: `from`/`to` (window **overlap**), `latest_only` (default true —
  collapse supersession chains). Paginated.
- Viewing reuses the existing document `view`/`download` routes — **with one
  hardening change**: any `text/html` document is served with
  `Content-Security-Policy: sandbox allow-scripts` (opaque origin — no
  cookies, no same-origin fetch). Reports are LLM-generated HTML and documents
  are delegate-visible; without the sandbox this is a stored-XSS path from a
  patient session into a delegate's browser session. Chart.js loads from its
  CDN inside the sandbox and still renders.

### MCP tools (UserMCP)

- **`save_episode_report`** — write tool, `save_chat_summary` pattern
  (explicit-save behavioral gate in the description). Passes through the
  fields above; returns document id + links.
- **`list_episode_reports`** — wraps the GET; returns the envelope list so a
  session can see what episodes exist and pull narratives for comparison.
- Fetching one report: existing `get_document` already returns
  `ocr_text_full` (= narrative text) — no new fetch tool.
- Stack invisibility rule: untouched — no stack data in any of this.

### Skill update (outside this repo's deploy)

`health-episode-report` step 4 gains: after rendering and user confirmation,
call `minowa:save_episode_report` with the HTML, assembled narrative text,
window, version, and annotations. On an edit re-run, pass
`supersedes_document_id` from the prior save. (Neal re-packages the .skill.)

Out of scope (per 2026-07-20 discussion): server-side storage of the skill's
`HealthMonitoringNorms.md` — separate discussion if wanted.

## HIPAA compliance check

- **§ 164.312(a) Access Control** — rows land in `documents` under the
  existing RLS policies (user + delegate); no policy changes. The CSP-sandbox
  header closes an integrity gap in serving user-session-generated HTML to
  delegate sessions.
- **§ 164.312(b) Audit Controls** — every create writes an `audit_log` row:
  `action='document.episode_report_created'`, acting identity = the
  authenticated user (tenant_id + user_id), `target_type='document'`,
  `target_id=<doc id>`, details carrying `created_via`/`model_id`. Same table
  and query path as chat summaries.
- **§ 164.312(d) Person/Entity Authentication** — unchanged: session/bearer
  auth via `@require_auth`; MCP path is per-request bearer.
- **§ 164.502(b) Minimum Necessary** — the list endpoint returns envelope
  metadata only (never the HTML body); the narrative travels only on explicit
  single-document fetch. Window filters bound what a session pulls.
- **§ 164.528 Accounting of Disclosures** — creates are in `audit_log`
  keyed by `target_type='document'`, queryable alongside all other document
  events for patient accounting requests.
- Delegate visibility is **inherited from the documents policy** (same as
  chat summaries) — flagging explicitly: if Neal wants episode reports
  user-only, that requires a policy carve-out and becomes a schema/RLS change
  with its own review. Default here: inherited visibility.

## Rollout

1. Delta on nealvm (admin connection, idempotent), then code deploy
   (UserApp + UserMCP), then skill re-package by Neal.
2. Prod: delta file queued behind the existing pending deltas
   (2026-07-15 ×2, 2026-07-16, migration 007), applied manually by Neal.
3. Tests: route tests (create validations, overlap filter, latest_only,
   supersedes chain, CHECK-not-ready 503), MCP tool tests (envelope shape,
   passthrough, behavioral-gate description), CSP header test on HTML serve.
4. Docs: UserAPI.md + openapi.yaml (drift audit), ERD version row.
