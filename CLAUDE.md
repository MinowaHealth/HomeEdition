## Project Overview

This repo is **Minowa.ai Home Edition** — a single-household, single-box health tracking appliance. It is a trimmed descendant of the multi-tenant Minowa.ai platform: the enterprise software features (providers, delegation, organizations, multi-tenant RLS, the observability stack, the graph database, cloud analytics, and the edge/tunnel layer) have been removed, while **the data model is an intact subset of the Enterprise model**, so this appliance stays sync-compatible with the Central System.

**Architecture**: One PostgreSQL database (`healthv10`) on one machine, serving one household (~6 people). No Row-Level Security — tenant isolation is unnecessary on a single-tenant box, so per-user privacy is enforced **in the application** with explicit `user_id` predicates. `tenant_id` remains on every table (always `1`) purely to keep the schema identical to Central. The deployable stack is **three containers** — PostgreSQL+pgvector, UserApp (Flask), UserMCP — with Ollama running on the host for embeddings.

Date: 2026-06-12 17:30 PDT

> The running appliance's schema source of truth is `Infrastructure/init/docker-init-home/02-home_schema.sql`.

## Critical Rules

- **Household trust model**: This is a home appliance on a home network, not a multi-tenant PHI host. There is no HIPAA program here (personal health data on the household's own hardware). Privacy *between household members* is enforced at the **application level** via explicit per-user `user_id` checks on every query — never RLS (removed), and never blanket household transparency. Household-shared reads (e.g. the shared diet-code catalog) must be **deliberate, reviewed exceptions**. See "Household Trust Model" below.
- **Time Zone**: Store all timestamps in UTC (`TIMESTAMPTZ` columns, `datetime.now(pytz.utc)` in Python). Localize to the user's `home_timezone` for display.
- **Database Access**: Every query against a user-owned table MUST carry an explicit `user_id = %s` predicate (and `tenant_id`, always `1`). RLS no longer exists, so a missing predicate silently returns other household members' rows. Pre-auth lookups keyed by a globally-unique secret (session id, API-token hash) are the documented exception.
- **No psql CLI**: Access Postgres only through Python with the credentials file.
- **Database driver (Python)**: psycopg 3 end-to-end — `import psycopg`, `psycopg_pool.ConnectionPool`, `psycopg.rows.dict_row`, `psycopg.sql`. Imports of `psycopg2` (and `psycopg2-binary`/`psycopg2.extras`/`psycopg2.pool`) are banned by ruff (`TID251`, configured in `pyproject.toml`) — the rule fires in pre-commit and CI. Mentions in migration notes are fine as historical archaeology; only import statements get blocked.
- **Questions**: When asked "How do I...??" — respond with a recommendation and WAIT for approval before acting.
- **Python venv**: Use `.venv` in the repo root for all Python work.
- **HTTP client (Python)**: New code uses `httpx` (sync in WSGI/workers); migrate `requests` call sites when you touch them.
- **MD File Headers**: Include both date and timestamp.
- **MCP Servers**: UserMCP is Python only.
- **SQL**: Any SQL used to define tables or relations MUST be recorded in the DataModel3 documentation and MUST NOT be concealed in shell scripts.
- **Source vs Derived Tables**: External-source data lands in its own namespaced tables first (`hkit_*`, `garm_*`), then projects into `health_*`. Never write source data directly into `health_*`.
- **Python permanently**: Home Edition is Python-only by design — there is no TypeScript here. A vestigial TS workspace scaffold (`package.json`, `nx.json`, `pnpm-workspace.yaml`, and the pnpm/Nx/ESLint pre-commit gates) is left over from the enterprise repo. This repository will remain pure Python, if you want to tinker with the mobile app, that's in MinowaMobile, and the TypeScript web interface lives there.

## Household Trust Model

Home Edition replaces multi-tenant RLS with a much simpler model:

- **One household, one box.** Everyone with a login is a member of the same household (`tenant_id = 1`).
- **No RLS.** The schema ships with zero policies and zero RLS-enabled tables. The enterprise `app.current_*` session GUCs are gone.
- **Privacy is per-user, in the app.** Each request resolves the authenticated `user_id` and every query against a user-owned table filters on it explicitly. The repeatable audit for this is `scripts/user_scope_audit.py` (sqlglot-based) — run it after touching any data-access code.
- **Shared reads are explicit.** Where the household legitimately shares data (e.g. the diet-code catalog), the query is a deliberate, documented exception rather than an accident of missing scoping.

## Services

### UserApp — Household API (primary service)

- **Stack:** Python 3.12, Flask 3.1, Gunicorn
- **Port:** 80 (bound to the LAN; no tunnel)
- **Key files:** `UserApp/webapp/app.py` (entry point), `UserApp/webapp/routes/` (v1+v2 blueprints), `UserApp/webapp/auth.py` (sessions, 2FA), `UserApp/webapp/db_manager.py` (single app-role pool, `user_id` scoping), `UserApp/webapp/logging_config.py` (three-tier logging), `UserApp/webapp/ocr/` (in-process document OCR pipeline), `UserApp/admin.py` (user lifecycle CLI)
- **Schema:** `Infrastructure/init/docker-init-home/02-home_schema.sql` (running source of truth)
- **OCR:** runs **in-process** — uploads call `process_document_inline` on a `background.fire_and_forget` daemon thread, preserving the client's `ocr_status` polling contract. No RabbitMQ, no separate worker.
- **Status:** Active, primary service.

### UserMCP — MCP Server for the Household

- **Python:** `UserMCP/` — streamable HTTP (`/mcp`) + legacy SSE transport, port 13282
- **Key files:** `UserMCP/mcp_server.py` (Starlette ASGI + MCP bootstrap), `UserMCP/tools/` (23 task-oriented tools: time_context, date_math, profile, regimen, stacks, clinical_history, vitals, labs, wearables, garmin_detail, garmin_sync, sleep_events, observations_detail, activity, adherence, acquisitions, nutrition, search, documents, chat_summary, episode_report, episode_report_list, feedback)
- **Architecture:** Stateless HTTP proxy → UserApp `/api/v1/*` → PostgreSQL (app-level `user_id` scoping). Per-request bearer token auth. Claude Desktop connects via `npx supergateway`.
- **Status:** Active.

### Ollama — Embeddings (host process)

- Runs on the **host** (not a container), providing `nomic-embed-text-v2-moe` for pgvector semantic search.
- Embedding is **best-effort**: an unreachable Ollama never blocks a write — documents and records still complete, just without a vector until re-embedded.

## Data Stores

### PostgreSQL 18 + pgVector

- **Database:** `healthv10` (single database, single app role `healthv10_app`, no RLS)
- **Container:** `pgvector/pgvector:pg18`
- **Port:** 5432
- **Schema source of truth (running appliance):** `Infrastructure/init/docker-init-home/02-home_schema.sql`; role + grants + indexes in `Infrastructure/init/docker-init-home/role/app-role-setup.sql`. Schema version marker: `11.0.0-home`.
- **Embeddings:** pgvector with **`nomic-embed-text-v2-moe`** via host Ollama (`EMBEDDING_MODEL=nomic-embed-text-v2-moe:latest`, `OLLAMA_URL=http://host.docker.internal:11434`), 768 dimensions, cosine distance, IVFFlat indexes (`lists=100`). On-device embedding is **not** supported — v2-moe doesn't fit on phone hardware, and a smaller on-device model would produce vectors in a different space (meaningless cosine similarity within one column). If the model ever changes, every embedding must be regenerated; column dimension stays at 768 so the schema is unaffected.

## Repository Layout

| Directory | Description |
|-----------|-------------|
| **Services** | |
| `UserApp/` | Household-facing Flask API + Docker Compose (includes in-process OCR under `webapp/ocr/`) |
| `UserMCP/` | User MCP server (Python, HTTP/SSE) |
| **Infrastructure** | |
| `Infrastructure/` | Shared Docker Compose: pgvector, network, home-schema init scripts |
| **Documentation** | |
| `DataModel3/` | Schema docs (ERD, per-table reference) + database audit tooling |
| `APIDocumentation/` | REST API docs, OpenAPI contract, conventions |
| `HowToDeploy/` | Appliance setup + local Mac dev stack |
| `MCP/` | UserMCP planning / reference docs |
| `UserProcess/` | UserApp process / workflow specs |
| `DesignMCP/` | MCP design tooling |
| **Support** | |
| `scripts/` | Setup, schema-generation, and audit scripts |
| `tests/` | Contract tests (`tests/contracts/`) and others |
| `UserApp/webapp/routes/` | Flask blueprints (v1 + v2 API routes) |
| `Infrastructure/init/docker-init-home/` | Database init: home schema, app role, grants |

## Authentication

- **Web Sessions**: 24-hour TTL, stored in the PostgreSQL `sessions` table.
- **Bearer Tokens**: For API clients and HealthKit sync.
- **2FA**: Optional TOTP with backup codes (PyOTP) — available, off by default; recommended for a LAN box.
- **Password reset / account provisioning**: **CLI-only**, via `admin.py provision-user` / `admin.py reset-password`, run by whoever has shell access to the box. There are no email flows (Mailgun removed). The authenticated in-app `change-password` (requires the current password) also works fully offline.
- **LAN access filter**: `source_ip_filter()` refuses any request whose source IP is outside an explicit allowlist — RFC1918, CGNAT/Tailscale (`100.64.0.0/10`), and loopback — on **every** route, API included (not just the web UI). The allowlist is enumerated explicitly rather than via `ipaddress.is_private` (whose membership varies by Python version). There is no Cloudflare Access, no edge auth, and `X-Forwarded-For` is not trusted — the real peer (`request.remote_addr`) is what's matched.

## Data Model

Running schema source of truth: `Infrastructure/init/docker-init-home/02-home_schema.sql`. `tenant_id` is present on every user table and is always `1`. Provider, delegation, organization, and care-thread tables were dropped; their feature code is removed but kept-table columns are intact.

| Domain | Key Tables | Notes |
|--------|-----------|-------|
| Users & Auth | `users`, `sessions`, `tenants` | `tenant_id` always 1; per-user app-level scoping |
| Health Tracking | `health_inputs`, `stacks`, `stack_inputs`, `timeframes`, `health_input_log`, `reminders` | Meds, supplements, intake logs, reminders |
| Dietary | `dietary_settings` | Diet preferences with history tracking |
| Vitals & Metrics | `health_blood_pressure_readings`, `health_metrics` | Generic: weight, temp, steps, HR, sleep, nutrition |
| Food | `health_food_itemsv2`, `health_food_logv2`, `meals`, `meal_items` | Nutritional tracking |
| Clinical History | `health_conditions`, `health_allergies`, `health_family_history`, `health_social_history`, `health_surgical_history` | Personal medical history |
| Documents | `documents`, `document_pages`, `document_annotations` | In-process OCR pipeline, pgvector embeddings. Fax was removed 2026-06-20 (expected to return); the `Fax` system folder and `documents.source` value `fax_inbound` remain as its seam |
| Contacts | `user_provider_contacts` | Personal contact book (all columns kept; no NPI verification software) |
| Wearables | `garm_*`, `garmin_credentials`, `garmin_sync_jobs` | Garmin integration (high-volume tables) |
| HealthKit | `hkit_*`, `healthkit_import_jobs` | Mobile health data via Apple HealthKit |
| Other | `api_tokens`, `user_devices`, `health_vaccinations`, `mobile_events`, `audit_log` | Auth tokens, device registry, simplified audit log |

### Inclusivity

The `users` table supports `biological_sex` (female, male, intersex, not_specified), `gender_identity` (free text), `pronouns` (free text), and `track_energy_spoons` (chronic illness "spoons theory" support).

## API Surface

- **UserApp:** `APIDocumentation/UserAPI.md` (human reference) + `APIDocumentation/openapi.yaml` (machine-readable contract). v1+v2 blueprints under `UserApp/webapp/routes/`; v2 blueprints accept optional embedding vectors on create/update.
- **Cross-cutting:** `APIDocumentation/Authentication.md`, `PaginationStandard.md`, `DateFiltering-API.md`, `Conventions.md`.
- **Mobile boundary:** the UserApp endpoint contract (`openapi.yaml` + `tests/contracts/test_endpoint_drift.py`) is what the mobile client validates against. Provider endpoints (`/api/v1/providers*`) were **removed** in Home Edition — the mobile client feature-gates by server edition.

## Logging

Logs are JSON to stdout (read with `docker compose logs`). There is no Loki/Prometheus/Grafana stack.

Two log-level env vars, kept distinct so the Flask three-tier vocabulary never collides with Python stdlib levels:

- `FLASK_LOG_LEVEL` — Flask three-tier: `BASIC` (1 line/request) → `STANDARD` (+ query timing) → `DEBUG` (+ full SQL, headers, bodies). Read by UserApp. Config: `UserApp/webapp/logging_config.py`.
- `UVICORN_LOG_LEVEL` — Python stdlib levels (`critical | error | warning | info | debug | trace`). Read by UserMCP.

## Deployment

Home Edition deploys as **one Docker Compose stack on one machine**. There is one canonical compose file — `HowToDeploy/docker-compose.local.yml` (pgvector + webapp + UserMCP) — driven by `local.env`. Local install and VM/appliance install are the **same file**; they differ only by `BIND_ADDR` in `local.env` (`127.0.0.1` loopback default for local; `0.0.0.0` to reach the appliance from the LAN). There is no VPS pair, no Cloudflare tunnel, no multi-account shell model, and no production/testing DNS split — those were enterprise concerns.

```bash
# Bring the whole stack up (run everything from the repo root)
docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env up -d

# Rebuild one service after code changes — convenience wrappers that target
# the canonical stack from anywhere in the repo:
cd UserApp && ./regen.sh      # rebuild webapp
cd UserMCP && ./regen.sh      # rebuild usermcp
cd UserApp && ./update.sh     # git pull + rebuild webapp (appliance update)

# Account / user management (CLI is the only provisioning path)
cd UserApp && ./admin.py provision-user alice
./admin.py reset-password alice
./admin.py list-users

# Logging
docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env logs -f webapp
```

See `HowToDeploy/` for the full appliance setup guide, and `PostgresAccess.md` for opening the Postgres port to an external tool / Postgres MCP.

## UI Vocabulary

When discussing the web app interface, use these terms consistently.

- **Workbench** — The entire application window after login
- **Perspective** — Layout configuration for the household member's view
- **Activity Bar** — Vertical icon strip on the left edge; switches main content area (Meds, Food, Vitals, Contacts)
- **Side Bar** — Collapsible panel adjacent to Activity Bar (explorer/navigator, search, filters, list view)
- **Editor** — Interactive panel for creating/modifying content (stack editor, food logger)
- **Status Bar** — Bottom strip for system status, current context info, quick actions

## Technology Stack

| Layer | Technology |
|-------|------------|
| **Backend (Python)** | Python 3.12, Flask 3.1, Gunicorn |
| **Database** | PostgreSQL 18 + pgVector extension |
| **Embeddings** | Ollama (`nomic-embed-text-v2-moe`), host process |
| **Auth** | Argon2id, PyOTP (2FA) |
| **Containers** | Docker, Docker Compose |
| **OCR** | Tesseract, pdf2image, Pillow (in-process) |
| **Mobile** | React Native + SQLite3 (contract boundary with Central) |
| **Web UI** | Vanilla HTML/CSS/JS (multiple themes) |

<!-- optivault-protocol -->
