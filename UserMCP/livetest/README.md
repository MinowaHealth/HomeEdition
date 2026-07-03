# UserMCP Livetest

**Date**: 2026-06-12 17:30 PDT

Pre-ship smoke test for UserMCP v0.5.0. Exercises all 12 tools against a
live server via the MCP SSE transport and classifies each response.

## What it checks

| Classification | Meaning |
|---|---|
| `OK` | Envelope has all 5 standard keys (`data`, `coverage`, `sources`, `disclaimer`, `next_actions`) and no degraded signals |
| `DEGRADED` | Envelope valid but `coverage.gaps` non-empty, or `sources` has a disconnected source (e.g. Garmin not synced, embedding service down) |
| `ERROR` | JSON-RPC error, missing envelope keys, HTTP ≥ 400, no response in timeout, or exception mid-call |

`send_feedback` is special-cased (its response shape is `{success, id}`,
not the standard envelope). `get_document` accepts a "not found" envelope
for an invented UUID as `OK` — we're testing the lookup plumbing, not
requiring a real document.

## Prerequisite: a test account

The livetest needs a real account on the appliance to log in and mint a
key against. There is no seeded persona — provision one with the admin
CLI on the box:

```bash
~/UserApp/admin.py provision-user smoketest
```

Pass that account's email (or username) to `smoke.sh`. Give it some data
first if you want non-`DEGRADED` results — an empty account will report
`coverage.gaps` on most tools.

## Usage

```bash
# Pass the CLI-provisioned account to test against
UserMCP/livetest/smoke.sh smoketest
```

Exit codes: `0` on all OK/DEGRADED, `1` if any tool ERRORs, `2` on setup
failure (login or key minting).

## Environment overrides

```bash
USERAPP_URL=http://<appliance-lan-ip> UserMCP/livetest/smoke.sh   # from another LAN device
MCP_PASSWORD=something UserMCP/livetest/smoke.sh             # account password
```

Default values assume the Mac-local stack per
[HowToDeploy/MacDeploy.md](../../HowToDeploy/MacDeploy.md) with
`--env-file local.env`.

## Implementation

- `smoke.sh` — curl-driven. Logs in, mints a permanent `hbk_*` API key,
  hands off to the Python helper, revokes the key on exit via a trap
  (even if the sweep fails).
- `mcp_smoke.py` — Python stdlib only. Opens one SSE session, drives 14
  JSON-RPC messages (initialize + initialized notification + 12 tool
  calls), classifies each response.

The key ceremony is in shell (curl territory). The MCP protocol dance
is in Python (SSE parsing in bash is a trap). Neither side pulls in any
test framework — this is a smoke test, not a test suite.

## Not covered

- **Field-level assertions**. We don't check that a BP reading
  has a systolic ≥ diastolic. Use `UserMCP/tests/` (pytest) for that.
- **Write paths other than `send_feedback`**. No data mutation.
- **Cross-tool sequencing**. Each call is independent.
- **Shape validation inside `data`**. An unexpected inner shape (e.g.
  the Cowork-reported `surgical_history` issue) passes as OK here —
  structural contract testing lives in the unit tests.
