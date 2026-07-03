# Environment Variable Reference — Minowa.ai Home Edition

**Date: 2026-07-03 03:45 PDT**

Home Edition runs as one Docker Compose stack — pgvector Postgres, UserApp (Flask, port 80), and UserMCP (port 13282) — defined in `HowToDeploy/docker-compose.local.yml` and driven entirely by `local.env` at the repo root. A local install and a VM/appliance install use the same file; the only difference is `BIND_ADDR` (`127.0.0.1` for loopback-only, `0.0.0.0` to reach the box from the LAN). Every variable below is read by shipping code or set in shipping config; the "Read by" column points at the exact file.

## Quick reference — what breaks without it

| Variable | Failure if missing |
|----------|-------------------|
| `SECRET_KEY` | `RuntimeError` at UserApp startup (unless `FLASK_ENV=testing`) |
| `APP_DB_PASSWORD` | Webapp cannot connect as the app role — pool creation fails |
| `POSTGRES_PASSWORD` | Superuser paths fail: container init, `admin.py` CLI |
| `HEALTHKIT_SYNC_TOKEN` without `HEALTHKIT_SYNC_USERNAME` | `webapp/validate_env.py` refuses to boot — the two are a paired contract |

Everything else has a working default.

## Compose / global

Set in `local.env`; consumed by `HowToDeploy/docker-compose.local.yml`.

| Variable | Default | What it does | Read by |
|----------|---------|--------------|---------|
| `BIND_ADDR` | `127.0.0.1` | Host interface for the web UI (:80) and UserMCP (:13282). `127.0.0.1` = local install, loopback only; `0.0.0.0` = appliance, reachable on the LAN. The only local-vs-appliance difference. | `HowToDeploy/docker-compose.local.yml` (port mappings) |
| `PG_BIND_ADDR` | `127.0.0.1` | Host interface for the Postgres port (:5432), independent of `BIND_ADDR`. A direct DB connection bypasses all app-level `user_id` scoping, so exposing Postgres is a deliberate opt-in — see `PostgresAccess.md`. | `HowToDeploy/docker-compose.local.yml` (pgvector port mapping) |
| `TZ` | `America/Los_Angeles` | Container timezone; also fed to Postgres as `PGTZ`. Storage is always UTC — this affects display/log formatting only. | compose → pgvector container env |
| `DEFAULT_TENANT_ID` | `1` | The fixed app-level scoping convention: every row carries `tenant_id = 1`, and this var supplies that constant. | `UserApp/webapp/db_manager.py`, `UserApp/webapp/auth.py`, `UserApp/admin.py` |

## PostgreSQL

| Variable | Default | What it does | Read by |
|----------|---------|--------------|---------|
| `POSTGRES_USER` | `postgres` | Superuser role name. Used for container init and maintenance only — the webapp never connects as superuser. | `UserApp/admin.py`, `UserApp/scripts/detect_frequent_inputs.py`, compose |
| `POSTGRES_PASSWORD` | — (set in `local.env`) | Superuser password. | same as above |
| `APP_DB_USER` | `healthv10_app` | Application role all services connect as (created by `Infrastructure/init/docker-init-home/role/app-role-setup.sql`). | `UserApp/webapp/db_manager.py`, `webapp/auth.py`, `webapp/ocr/db.py` |
| `APP_DB_PASSWORD` | — (required) | Application role password. Webapp will not start without it. | same as above |
| `DB_HOST` | `localhost` (code) / `hb-local-postgres` (compose) | Postgres hostname. Inside the stack this is the container name. | `webapp/db_manager.py`, `webapp/auth.py`, `webapp/ocr/db.py`, `admin.py` |
| `DB_PORT` | `5432` | Postgres port. | same as above |
| `DB_NAME` | `healthv10` | Database name. | same as above |
| `POOL_MIN_CONNECTIONS` | `5` | Connection pool floor (psycopg_pool). | `UserApp/webapp/db_manager.py` |
| `POOL_MAX_CONNECTIONS` | `50` | Connection pool ceiling. | `UserApp/webapp/db_manager.py` |

## UserApp (Flask)

| Variable | Default | What it does | Read by |
|----------|---------|--------------|---------|
| `SECRET_KEY` | — (required) | Flask session-signing key. Startup refuses a missing or placeholder value; generate with `python -c "import secrets; print(secrets.token_hex(32))"` (or let `setup.sh` do it). | `UserApp/webapp/app.py` |
| `FLASK_ENV` | unset | Environment label; `testing` relaxes the `SECRET_KEY` startup check for the test suite. `local.env` sets `production`. | `UserApp/webapp/app.py` |
| `FLASK_DEBUG` | `0` | Enables Flask debug mode in the bare `app.run()` dev path only — Gunicorn serves the container. | `UserApp/webapp/app.py` |
| `PORT` | `5000` | Listen port for the bare `app.run()` dev path only; the container listens on 80 via Gunicorn. | `UserApp/webapp/app.py` |
| `THEME` | `default` | Default web UI theme, exposed at the theme endpoint. | `UserApp/webapp/app.py` |
| `CORS_ORIGINS` | `http://localhost:80,http://127.0.0.1:80,http://10.0.2.2:80` | Comma-separated browser origins allowed to call `/api/*`. Browser-only (mobile clients ignore CORS). Never `*` — the CORS spec forbids wildcard with credentials. | `UserApp/webapp/app.py` |
| `SESSION_DURATION_HOURS` | `24` | Web session TTL (sessions live in the `sessions` table). | `UserApp/webapp/auth.py` |
| `SESSION_TIMEOUT_MINUTES` | unset | Minute-granularity override; when set it takes precedence over `SESSION_DURATION_HOURS`. | `UserApp/webapp/auth.py` |
| `MAX_API_KEYS_PER_USER` | `5` | Cap on active API tokens per household member. | `UserApp/webapp/auth.py` |
| `TIMEZONE` | `America/Los_Angeles` | Fallback display timezone when a user has no `home_timezone` set. Storage stays UTC. | `UserApp/webapp/utils.py` |
| `HEALTHKIT_SYNC_TOKEN` | unset | Bearer token for background HealthKit sync. Paired contract with `HEALTHKIT_SYNC_USERNAME` — set both or neither. | `UserApp/webapp/utils.py` |
| `HEALTHKIT_SYNC_USERNAME` | unset | Email of the user the sync token acts on behalf of. `webapp/validate_env.py` refuses to boot if the token is set without it. | `UserApp/webapp/utils.py` |
| `USERDOCS_STORAGE_PATH` | `/data/userdocs` | Filesystem root for uploaded documents (compose mounts the `hb-local-userdocs` volume there). | `UserApp/webapp/routes/documents.py` |
| `OCR_LANGUAGES` | `eng` | Tesseract language pack(s) for the in-process OCR pipeline. | `UserApp/webapp/ocr/engine.py` |
| `MCP_BASE_URL` | `http://localhost:13282` | UserMCP endpoint the webapp hands to clients via the MCP-config endpoint. | `UserApp/webapp/app.py` |
| `APP_VERSION` | `unknown` | Version string reported by `/healthz`. | `UserApp/webapp/routes/healthz.py` |
| `DEPLOY_ENV` | `pilot` | Environment label reported by `/healthz` and shown in logs; `.env.example` sets `home`. | `UserApp/webapp/routes/healthz.py` |

## UserMCP

| Variable | Default | What it does | Read by |
|----------|---------|--------------|---------|
| `API_BASE_URL` | `http://localhost` | UserApp API endpoint the MCP server proxies to. Compose sets `http://hb-local-webapp:80` (container-internal name). | `UserMCP/mcp_server.py` |
| `MCP_PORT` | `13282` | Listen port. | `UserMCP/mcp_server.py` |
| `MCP_HOST` | `127.0.0.1` | Bind address inside the container. Compose sets `0.0.0.0` so the host port mapping reaches the service; the code default would trap it in the container. | `UserMCP/mcp_server.py` |
| `MCP_TRACE` | unset | Truthy (`1`/`true`/`yes`) enables request/response tracing for debugging. | `UserMCP/mcp_server.py` |

## Embeddings / Ollama

Ollama runs on the **host**, not in a container; the stack reaches it via `host.docker.internal`. Embedding is best-effort — an unreachable or slow Ollama never blocks a write; the row is stored with a `NULL` vector and re-embedded later. See `DataModel3/EmbeddingDesign.md` for the model rules.

| Variable | Default | What it does | Read by |
|----------|---------|--------------|---------|
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Host Ollama endpoint. | `UserApp/webapp/embedding_utils.py`, `webapp/routes/healthz.py` |
| `EMBEDDING_MODEL` | `nomic-embed-text-v2-moe:latest` | Embedding model. Changing it invalidates every stored vector — re-embed everything (dimension stays 768, schema unaffected). | `UserApp/webapp/embedding_utils.py`, `webapp/routes/healthz.py` |
| `OLLAMA_TIMEOUT` | `10` (code) / `30` (compose) | HTTP timeout in seconds for Ollama calls. | `UserApp/webapp/embedding_utils.py` |
| `EMBEDDING_DEADLINE` | `20` | Overall per-embedding deadline in seconds; on expiry the write completes without a vector. | `UserApp/webapp/embedding_utils.py` |

## Logging

Logs are JSON to stdout — read them with `docker compose logs`. The two log-level vars use deliberately different vocabularies so the Flask three-tier scheme never collides with Python stdlib levels.

| Variable | Default | What it does | Read by |
|----------|---------|--------------|---------|
| `FLASK_LOG_LEVEL` | `BASIC` | UserApp three-tier verbosity: `BASIC` (1 line/request) → `STANDARD` (+ query timing) → `DEBUG` (+ full SQL, headers, bodies). | `UserApp/webapp/logging_config.py`, `webapp/app.py` |
| `UVICORN_LOG_LEVEL` | `info` | UserMCP verbosity, Python stdlib vocabulary: `critical` \| `error` \| `warning` \| `info` \| `debug` \| `trace`. | `UserMCP/mcp_server.py`, `UserMCP/logging_setup.py` |
| `SLOW_QUERY_THRESHOLD_MS` | `100` | Queries slower than this get flagged in `STANDARD`/`DEBUG` request logs. | `UserApp/webapp/logging_config.py` |
| `VERBOSE_LOGGING` | `false` | Legacy verbose flag, kept for backwards compatibility — prefer `FLASK_LOG_LEVEL`. | `UserApp/webapp/app.py`, `webapp/utils.py` |

## Test and seed variables (development only)

None of these are read by the running appliance — they configure the test suite (`UserApp/tests/`), livetests, and the `TestData/` seeders.

| Variable | Default | Used for | Read by |
|----------|---------|----------|---------|
| `RUN_INTEGRATION_TESTS` | unset | Gate: integration tests skip unless truthy. | `UserApp/tests/conftest.py`, `tests/test_migration.py` |
| `DB_USER`, `DB_PASSWORD` | — | Test-suite DB credentials. | `UserApp/tests/conftest.py` |
| `TEST_DB_HOST` / `TEST_DB_PORT` / `TEST_DB_NAME` / `TEST_DB_USER` / `TEST_DB_PASSWORD` | — | Migration-test DB target. | `UserApp/tests/test_migration.py` |
| `MCP_URL` | `http://localhost:13282` | UserMCP smoke-test target. | `UserMCP/livetest/mcp_smoke.py` |
| `MCP_API_KEY` | `""` | Bearer token for the MCP smoke test. | `UserMCP/livetest/mcp_smoke.py` |
| `SEED_DB_HOST` / `SEED_DB_PORT` / `SEED_DB_NAME` / `SEED_DB_USER` / `SEED_DB_PASSWORD` | `localhost` / `5432` / `healthv10` / — / — | Seeder DB connection (user/password required). | `TestData/seed_users.py`, `TestData/three_month_seed/config.py` |
| `SEED_TEST_DATA` | unset | Must be `true` for the temporal seeder to run — a guard against seeding a real household DB. | `TestData/three_month_seed/config.py` |
| `SEED_API_BASE_URL` | `http://localhost` | API endpoint the seeder posts through. | `TestData/three_month_seed/config.py` |
| `WINDOW_END` / `WINDOW_DAYS` | `2026-05-08` / `90` | Seeded time window. | `TestData/three_month_seed/config.py` |
| `SEED` / `SCALE` | `42` / `1.0` | RNG seed and data-volume multiplier. | `TestData/three_month_seed/config.py` |
| `SEED_POST_THROTTLE_SECS` / `SEED_LOGIN_THROTTLE_SECS` | `0.55` / `13` | Request pacing during seeding. | `TestData/three_month_seed/__main__.py` |
| `LOG_LEVEL` | `INFO` | Seeder log verbosity (stdlib vocabulary). | `TestData/three_month_seed/config.py` |
