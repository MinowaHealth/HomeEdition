# Testing & Local Development

Date: 2026-06-20 23:50 PDT

This is the single entry point for testing Minowa.ai Home Edition. **`.github/workflows/ci.yml` and `contract-test.yml` are the source of truth for what must pass** — this document mirrors them so you can run every gate locally before pushing. If the two ever disagree, CI wins; please fix this doc.

The fast loop is: `ruff` + `pyright` + `pytest` (all run in seconds, no containers). The full loop adds the live integration tests, which need the Docker stack up.

---

## Prerequisites

- **Docker + Docker Compose** — for the database and the live/integration tests.
- **Python 3.12** — the pinned interpreter (`PYTHON_VERSION` in CI). Other 3.x versions may pass locally but are not what CI runs.
- **Ollama (optional)** — only needed if you exercise embedding generation. Embedding is best-effort; an unreachable Ollama never blocks a write. See the root `CLAUDE.md`.

---

## One-time setup

**Shortcut:** `./setup.sh` from the repo root does all of the below in one shot (venv + deps + git hooks). The manual steps are spelled out here for reference and for when you also need the MCP server's deps.

```bash
# From the repo root
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip

# Runtime deps for the service you're working on, plus the dev tooling
.venv/bin/pip install -r UserApp/webapp/requirements.txt -r requirements-dev.txt
# (working on the MCP server? also: -r UserMCP/requirements.txt)

# Install the git pre-commit hooks (ruff, pyright, gitleaks, the audits)
.venv/bin/pre-commit install
```

`requirements-dev.txt` pins `pyright==1.1.408` to match CI. The pre-commit `pyright` hook calls `.venv/bin/pyright`, so it **must** be installed in the venv — this is the step that makes that true.

---

## The fast loop (no containers)

| Gate | Command | CI job |
|------|---------|--------|
| **Lint** | `.venv/bin/ruff check .` | `lint` |
| **Type check** | `.venv/bin/pyright` | `type-check` |
| **Unit tests** | `cd UserApp && ../.venv/bin/pytest --cov=webapp` | `test` |

### Lint (ruff)

```bash
.venv/bin/ruff check .          # add --fix to auto-fix
```

Ruff config (including the `psycopg2` import ban, `TID251`) lives in `pyproject.toml`.

### Type check (pyright)

```bash
.venv/bin/pyright               # whole repo, exactly what CI runs
```

`pyrightconfig.json` sets `typeCheckingMode: basic`. **The gate fails only on errors; warnings are tolerated** (current baseline: 0 errors, ~87 warnings). If you see "pyright not found", you skipped the dev-deps install above. If your local error count differs from CI, check your version — it must be `1.1.408` (`.venv/bin/pyright --version`).

### Unit tests (pytest)

```bash
cd UserApp
../.venv/bin/pytest --cov=webapp        # ~485 tests, all mocked — no DB needed
```

The `webapp/tests/` suite mocks the database, so it runs without a live Postgres. CI additionally runs it against a real `pgvector/pgvector:pg18` service with `DB_HOST=localhost` etc.; that only matters for the handful of tests that touch a DB. The coverage gate is currently `--cov-fail-under=0` (honest-floor mode; see the comment in `ci.yml`).

---

## The repeatable audits

These are guard scripts; the first two also run as pre-commit hooks and CI jobs.

| Audit | Command | What it guards |
|-------|---------|----------------|
| **SQL ↔ schema** | `DataModel3/run_code_query_audit.sh` | every SQL string is legal against `02-home_schema.sql` |
| **Route ↔ OpenAPI drift** | `.venv/bin/python APIDocumentation/route_spec_audit.py` | every route is in `openapi.yaml` or the allowlist |
| **Per-user scoping** | `.venv/bin/python scripts/user_scope_audit.py` | every user-owned-table query carries an explicit `user_id` predicate (Home Edition has no RLS) |

Run `user_scope_audit.py` after touching any data-access code — it is the repeatable check behind the household privacy model. Note its known blind spot: dynamically-composed SQL (`sql.SQL(...).format(...)`) is not statically verified.

### Pre-commit (the local subset)

```bash
.venv/bin/pre-commit run --all-files     # ruff, pyright, gitleaks, SQL + route audits, bandit
```

The `pyright` and route/SQL audit hooks are scoped to fire only on relevant files; CI re-runs them whole-repo so nothing slips through between commits that don't touch the trigger files.

---

## The full loop — live integration tests

These run real HTTP requests against a running stack and verify persistence by querying Postgres directly. They catch what mocked tests can't (e.g. silent write loss).

### 1. Bring up the stack

```bash
# From the repo root
docker compose --project-directory . -f HowToDeploy/docker-compose.local.yml --env-file local.env up -d --build
```

This starts `pgvector` (port 5432), `webapp` (port 80), and `usermcp` (port 13282). The database initializes from `Infrastructure/init/docker-init-home/02-home_schema.sql` on a fresh volume and seeds `test@example.com` / `Password2026` via `scripts/local-init-db.sh`. To re-initialize from scratch, wipe the volumes first: `docker compose ... down -v`.

### 2. UserApp live tests

```bash
cd UserApp
../.venv/bin/python -m livetest.run_all --base-url http://localhost:80
```

Preconditions and per-flow details: **`UserApp/livetest/README.md`**. Needs `UserApp/.env.livetest` (see `.env.livetest.example`) and the `test@example.com` user.

### 3. UserMCP live tests

```bash
cd UserMCP
../.venv/bin/python -m livetest.run_all     # see UserMCP/livetest/README.md
```

### 4. Contract tests (mobile boundary)

The UserApp endpoint contract is what the mobile client validates against. Mirrors `contract-test.yml`:

```bash
.venv/bin/python -m pytest tests/contracts/         # endpoint + event drift
openapi-spec-validator APIDocumentation/openapi.yaml # spec lint
```

Cadence and rationale: `APIDocumentation/ContractTestingCadence.md`.

### 5. Fuzz tests (optional)

API fuzzing harness: `UserApp/fuzztest/README.md`.

---

## What CI runs (the mirror)

`.github/workflows/ci.yml`:

| CI job | Local equivalent |
|--------|------------------|
| `lint` | `.venv/bin/ruff check .` |
| `type-check` | `.venv/bin/pyright` |
| `test` | `cd UserApp && pytest --cov=webapp` |
| `schema-audit` | `DataModel3/run_code_query_audit.sh` |
| `dependency-audit` | `.venv/bin/pip-audit -r UserApp/webapp/requirements.txt` |
| `secret-scan` | `.venv/bin/pre-commit run gitleaks --all-files` |
| `build` | `docker compose ... build` |

`.github/workflows/contract-test.yml`: OpenAPI spec lint, route↔spec drift, and a full-stack smoke profile.

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `pyright: command not found` | Dev deps not installed — run the one-time setup. |
| Local pyright error count ≠ CI | Version mismatch — `.venv/bin/pyright --version` must be `1.1.408`. |
| `extension "vector" has no installation script for version X` | The schema must not pin a pgvector micro-version; `CREATE EXTENSION vector` (no `VERSION`) installs whatever the image ships. |
| `401 Unauthorized` at livetest login | `test@example.com` missing — `cd UserApp && ./admin.py provision-user test@example.com password 'Test User'`. |
| `delta 0, expected 1` in livetest | Silent write loss — the bug class the live harness exists to catch. |
