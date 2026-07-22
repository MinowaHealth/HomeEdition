# Porting Code Between Editions — the RLS Boundary

**Date: 2026-07-22 09:30 PDT**

This document lives in `DataModel3/` in **both** repositories (HBbackend and
HomeEdition) and must be kept identical in both. It exists because code moves
between the editions regularly, and the single most dangerous difference
between them is invisible in a diff: **who enforces per-user isolation.**

| | HBbackend (Enterprise) | HomeEdition |
|---|---|---|
| Isolation enforced by | PostgreSQL Row-Level Security | The application, per query |
| Session identity | GUCs: `app.current_tenant_id`, `app.current_user_id` (set by `db_manager` on every connection) | Resolved `user_id` passed explicitly into every statement |
| `tenant_id` | Real, varies per tenant | Present on every table, always literal `1` |
| A query with no `user_id` predicate | Correct — RLS appends the filter invisibly | **A cross-user data leak** |
| A bare `UPDATE`/`DELETE` | Scoped by RLS `WITH CHECK` / `USING` | **Mutates every household member's rows** |

The same SQL string is *correct in one repo and a privacy defect in the
other*, while being syntactically clean, passing the test suite (mocks don't
check WHERE clauses unless a test pins them), and looking fine in review.
The July 2026 port of ~24 HB feature commits into HomeEdition surfaced
**thirteen** such defects, including a bare `UPDATE user_preferences` that
would have rewritten every household member's settings and detail-window
reads that returned all members' heart-rate and symptom data.

---

## Direction 1: HBbackend → HomeEdition (the dangerous direction)

HB code legitimately omits `user_id` because RLS supplies it. Ported
verbatim, it leaks. **Assume every arriving SQL hunk is unscoped until
proven otherwise.**

### Tells — grep every applied hunk for these

- `WHERE TRUE` followed by composed filters (the classic RLS-era feed query)
- `current_setting('app.current_tenant_id')` / `current_setting('app.current_user_id')` — these GUCs **do not exist** in HomeEdition; the statement fails at runtime if you're lucky, or is replaced with an unscoped literal if you're not careful
- The words "RLS", "RLS-scoped", "keyed by the RLS session identity" in docstrings or comments — the code below them omits the predicate on purpose
- Any `SELECT`/`UPDATE`/`DELETE` on a user-owned table whose WHERE carries only `tenant_id = %s AND id = %s` (id alone does not prove ownership)

### Required rewrites

1. Every statement against a user-owned table gets explicit
   `tenant_id = %s AND user_id = %s` predicates (INSERTs write both columns).
2. GUC references become the literal tenant (`1`) and the resolved `user_id`
   from the request context (`g.user['user_id']` / `get_user_id()`) or the
   worker's `user_id` argument.
3. Docstring/comment claims about RLS get reworded to state the household
   model ("explicit tenant_id/user_id predicates; no RLS on this box") — a
   stale RLS comment above a scoped query will mislead the next port.
4. Standalone scripts: HB scripts run "through an RLS-scoped connection";
   HomeEdition's `db_manager.get_direct_connection_for_user()` returns a
   plain app-role connection with **no** scoping — the script itself must
   carry the predicates.

### Verification — the test suite is not the catch mechanism

1. **Baseline-diff the scope audit.** Run
   `.venv/bin/python scripts/user_scope_audit.py`, then run it again in a
   clean worktree of pre-port HEAD
   (`git worktree add /tmp/baseline HEAD`), and diff the flag counts.
   *Any new flag is a defect or a false positive you must explain.* The
   absolute count is noise (12 documented pre-auth exceptions on
   sessions/api_tokens are permanent); the delta is the signal.
2. **Pin the scoping in ported tests.** HB tests assert param tuples like
   `(win_start, win_end)`; update them to
   `(1, TEST_USER_ID, win_start, win_end)` so the scoping is regression-
   locked, not just present.
3. **Dynamic WHERE clauses use psycopg composition, not f-strings.**
   `DataModel3/code_query_audit.py` (pre-commit gate) cannot see through a
   Python f-string and fails with a phantom `missing_column` error. Build
   optional filters with `sql.SQL("...{extra}...").format(extra=...)` and
   keep the `tenant_id = %s AND user_id = %s` base predicate in the static
   SQL text so the static analyzer can verify it.

## Direction 2: HomeEdition → HBbackend

The hazard is smaller but real; explicit predicates are harmless under RLS
(redundant filter, RLS still applies), so scoped reads port cleanly. What
breaks:

- **Literal `tenant_id = 1`** (predicates, INSERT values, script constants)
  is wrong in a multi-tenant database — restore
  `current_setting('app.current_tenant_id')::smallint` or the resolved
  tenant from the session context.
- **Connections opened without the GUC handshake.** HB's `db_manager` sets
  `app.current_tenant_id`/`app.current_user_id` on every connection; code
  ported from HomeEdition that opens its own connection will fail with
  `unrecognized configuration parameter "app.current_user_id"` (or worse,
  RLS silently returns zero rows). Route all access through HB's
  `db_manager` helpers.
- **Tests pinning `(1, TEST_USER_ID, ...)` param tuples** will fail against
  HB queries that drop the redundant predicates, or pass misleadingly if HB
  keeps them — decide per port whether the explicit predicates travel.
- Validate with HB's own tooling: `DataModel3/rls_audit.py` and the
  procedures in `DataModel3/RLSValidation.md`.

## Ground rules, both directions

- Port **by feature commit, never by wholesale file copy** — file diffs
  between the editions contain edition-specific deltas that must not travel.
- Every ported commit message names its origin
  (`Origin: HBbackend <sha>` or `Origin: HomeEdition <sha>`).
- Schema DDL travels through each edition's schema source of truth
  (`Infrastructure/init/docker-init-home/02-home_schema.sql` here,
  `Infrastructure/init/docker-init-v10/02-healthv10_schema.sql` there) plus
  the DataModel3 ERD/Report docs — never through ad-hoc shell scripts.
  HB `Infrastructure/deltas/*.sql` files include RLS policy blocks: strip
  them coming into HomeEdition; author them going out.

## Case law

The July 2026 port (HomeEdition commits `e592a6e..4f35615`, 2026-07-21) is
the reference example: every "Port HB <sha>" commit message there records
what arrived unscoped and how it was rewritten. Read
`26763d5` ("Scope detail/search reads that leaned on upstream RLS") for the
shape of the failure when it slips through a first review.
