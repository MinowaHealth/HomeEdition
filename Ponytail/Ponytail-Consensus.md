# Ponytail Audit — Four-Model Consensus

Date: 2026-07-01 23:15 PDT (original audit 2026-06-20; reworded pre-release)
Mode: over-engineering only (no bugs, security, perf)

Four models (Opus, GLM-5.2, DeepFlash, Kimi-K2.7) audited the same repo independently for over-engineering. This document merges their findings by agreement strength, reconciles conflicts by reading the code, and — now that the release policy is settled — records what was applied versus what Home Edition keeps **on purpose**. It is published as an example of how we do quality control on this codebase.

## Read this first: why "delete the stub" often loses here

The original audit graded the repo as a closed appliance, where an abstraction with one implementation is dead weight. Home Edition is not a closed appliance. It is a platform we expect hobbyist contributors to extend, its schema deliberately mirrors the enterprise Central system for a future sync path, and features that were stripped for this release (fax, HealthKit, Garmin) are expected to return. Under that policy, several findings below flip from "delete" to "keep deliberately":

- **Extension seams stay.** A one-implementation module whose call-site API matches the enterprise build (e.g. `analytics.py`) is a seam contributors and returning features plug into, not debt.
- **The client contract stays.** The v2 blueprints — including the thin passthroughs — are the API surface the MinowaMobile client and future editions validate against (`openapi.yaml` + `tests/contracts/`). Route files are cheap; contract drift is not.
- **The schema stays fat.** Kept tables retain all enterprise columns even where Home Edition leaves them empty, with an eye on later sync to Central. Unused columns are not cruft here.
- **Safety nets are never cut.** The `user_scope_audit.py` / `code_query_audit.py` scripts and the psycopg2 ruff ban are mandated guard rails under the no-RLS model.

## Applied (verified in-repo)

| Finding | Agreement | Outcome |
|---|---|---|
| `object_store/` ABC with one backend | unanimous | `provider.py` deleted, `LocalStore` kept as the implementation |
| Dead DB/auth aliases (`get_pool`, `get_master_connection`, `get_admin_pool`, `get_user_email_from_database`) | unanimous | Deleted — zero external callers |
| `_PoolAdapter` legacy pool shim | 2 of 4 | Deleted; native psycopg3 pool used directly |
| Fax provider stub (`FaxProvider` ABC + all-`NotImplementedError` SignalWire impl) | 3 of 4 | Went further than the audit asked: the whole fax feature was removed for this release. The `Fax` system folder and `documents.source` enum value remain as the seam for its planned return. |

The fax finding is worth the anecdote: two models confidently defended the abstraction as a legitimate two-implementation split; two flagged it as a stub. The code proved the stub. Rigor-of-tone is not rigor-of-fact — this is what running four models bought.

## Kept deliberately (policy, not oversight)

- **v2 passthrough blueprints** (`analytics_v2`, `embeddings_v2`, `feedback_v2`, `integrations_v2`, `logging_routes_v2`, ~140 lines) — the mobile/client contract surface. PRs against the API are cherry-picked so Home Edition does not drift from the enterprise backend; see CONTRIBUTING.md.
- **`analytics.py`** — product-event logging with the enterprise call-site API (`capture`/`identify`); events go to the local app log. Kept so route code is identical across editions.
- **UserMCP `_envelope`/`_pagination`/`_sources`/`_shape` tier** (~627 lines) — shared by 12 tools; inlining would *add* duplication.
- **`TestData/` seeder + fixtures** — builds the 6-user demo household; deployment scaffolding, not dead code.
- **`HealthBuddyJSONProvider`** — Flask's default JSON does not serialize `Decimal`/`UUID`; it exists because it must.
- **`parse_bool()` / `table_has_column()` cache** — the audit's own tie-breaks: callers and semantics justify both.
- **Audit scripts + psycopg2 ban** — mandated safety nets; ponytail never cuts security checks.

## Still open (honest housekeeping, post-release)

Small cleanups the audit found that remain valid and unglamorous: `StdoutLogHandler` (wraps a `StreamHandler` for no reason), the duplicate `vlog()` definitions in `utils.py` and `app.py`, tracked `.npmrc`/`.prettierrc.json` in a Python-only repo, `pytz` → stdlib `zoneinfo`, the `get_db_connection` alias, and the `apispec` dependencies alongside a static OpenAPI file. None changes behavior; none blocks release.

One audit item was upgraded rather than closed: the proposed `@scoped_user`/`@handle_db_errors` decorator pair is no longer mere deduplication. On a no-RLS box that invites new contributors, making the safe query pattern the *easy* pattern is security infrastructure, and it will land as its own test-backed PR.

## What the four-model exercise actually bought

1. **It caught a confident-but-wrong finding** (the fax stub, above).
2. **It separated signal tiers.** Findings all four models reached independently were the safe ones; single-model long tails earned a second look before cutting.
3. **It converged on scope discipline.** Three of four warned against a proposed -3,200-line route rewrite and against cutting the mandated audit scripts. The consensus was: delete the dead aliases and one-impl ABCs, add one decorator, leave the safety nets alone.
