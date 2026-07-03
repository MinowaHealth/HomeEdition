# Contract Testing

_2026-06-16 — Home Edition. Living document; update when the tooling changes._

The OpenAPI contract at [`openapi.yaml`](openapi.yaml) is a load-bearing artifact: it gates the route↔spec audit, drives the Schemathesis fuzz profiles, and is the source of truth for the mobile client and any external integration. Drift between the spec and the implementation is a defect, not a documentation gap.

Home Edition runs on one box, so there is no promotion pipeline, no staging/production split, and no on-call rotation — just two things a tinkerer modifying the backend should know about.

## Route ↔ spec drift audit (automatic)

[`route_spec_audit.py`](route_spec_audit.py) walks the Flask routes and `openapi.yaml` and fails if any v1 path exists in code but not in the spec or [`route_audit_allowlist.txt`](route_audit_allowlist.txt). It runs as a **pre-commit hook** — fired by changes to `UserApp/webapp/app.py`, `auth.py`, `routes/*.py`, `openapi.yaml`, or the allowlist — and again in CI if you push to a GitHub fork.

When it fails, do one of:
- Declare the new path in `openapi.yaml` (preferred — spec it).
- Add it to `route_audit_allowlist.txt` under the appropriate group with a one-line reason.
- Remove the route if it shouldn't have landed.

## Schemathesis fuzz profiles (optional, local)

If you're changing response shapes or status codes, run the fuzz profiles against a **throwaway instance** — never your live household database, since the fuzzer sends deliberately malformed input. The profiles live in [`UserApp/fuzztest/`](../UserApp/fuzztest/); see its [`README.md`](../UserApp/fuzztest/README.md) for the bring-up sequence, `profiles.sh` to run them, and `report.py` for the summary.

The lightest useful pass is **Profile A** (GETs only, unauthenticated, ~50 examples per operation), which catches the common drift:
- `response_schema_conformance` — the actual response doesn't match the declared schema. Fix the spec or the code.
- `not_a_server_error` — a 500 where a 4xx is correct. Real bug.
- `status_code_conformance` — a status not in the spec's `responses:` map. Add it, or fix the response.

## Failure tiers

- **Critical** — 5xx where 4xx is correct; auth bypass; cross-user data leak (a query missing its `user_id` predicate); a previously-documented P0 confirmed.
- **High** — spec drift that would break the client.
- **Medium** — response-shape drift on non-load-bearing fields; 422-vs-400 inconsistencies.
- **Low** — documentation tidy-ups.

A failing pre-commit hook or CI job blocks the change via its status; there is no out-of-band alert (Home Edition has no Slack or notification integration). Read the failure in the hook output, the GitHub Actions run, or the uploaded fuzz-report artifact.
