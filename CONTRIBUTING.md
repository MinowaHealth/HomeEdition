# Contributing to Minowa Home Edition

Date: 2026-07-01 23:20 PDT

We welcome contributions — especially interface and app work. This page tells you where the guardrails are and why they exist. If you're vibe coding with an LLM assistant, point it at this file first.

## Where help is most welcome

- **Web UI** (`UserApp/webapp/static/`, templates, themes) — the safest and most wanted area.
- Docs, install experience, and anything that makes the appliance easier for a household to run.
- Bug reports with reproductions, even without a fix.

## The one rule that protects families: per-user scoping

Home Edition has **no database-level Row-Level Security**. Privacy between household members lives entirely in the application code: every query against a user-owned table must filter on the authenticated user — `WHERE tenant_id = %s AND user_id = %s`. A query that forgets this doesn't error; it silently returns (or modifies) other family members' health data.

If you touch anything that talks to the database:

1. Resolve the user with `get_user_id()` and scope **every** statement to it.
2. Run the audit: `.venv/bin/python scripts/user_scope_audit.py` — it must show no new flags.
3. Run the tests: `.venv/bin/python -m pytest UserApp/webapp/tests/ -q`.

**Please don't modify the API routes, `db_manager.py`, `auth.py`, or other data-access code unless you're comfortable with the design skills this requires.** This is not gatekeeping — it's that the failure mode is one family member reading another's medical history. UI work never requires touching this layer; the API already exposes what the UI needs.

## The API contract

`APIDocumentation/openapi.yaml` is the contract the MinowaMobile client (and future editions) validate against, and we keep Home Edition in sync with the enterprise backend in this area.

- PRs against the API are welcome, but they are **cherry-picked**, not merged wholesale — we reconcile every change against the enterprise repo so the editions don't drift.
- Additive changes have a much better chance than modifications. PRs that remove existing routes will be reviewed but almost certainly not accepted.
- API changes must update `openapi.yaml` and pass the contract tests (`tests/contracts/`).

## The data model

`DataModel3/` and the schema (`Infrastructure/init/docker-init-home/02-home_schema.sql`) mirror the enterprise system deliberately — tables keep columns Home Edition doesn't use yet, because a sync service to the central system is planned. The same caution applies as with the API: **don't propose schema changes unless you understand the multi-tenant, RLS-protected structure they must map back to.** Empty columns are not cruft; please don't send PRs deleting them.

If you want to experiment against the database directly, use a CRUD-only account that can't alter table structure (see `PostgresAccess.md`), and keep MCP access read-only.

## Housekeeping

- Python 3.12, PEP 8, type hints on function signatures. psycopg 3 only — `psycopg2` imports are banned by ruff and will fail CI.
- Timestamps are stored in UTC (`TIMESTAMPTZ`), localized only for display.
- Run everything from the repo root with the `.venv` virtualenv.

## License and name

Contributions are accepted under the repo's BSD 3-Clause license — you can use this software however you like, and anything you contribute can be used by us, including in our commercial editions. The Minowa name may not be used to endorse or promote derived products without prior arrangement.

Security issues: see [SECURITY.md](SECURITY.md) — never a public issue.
