# Security Hardening Doctrine

**Date:** 2026-06-17 11:20 PDT

This is the security-hardening doctrine the Home Edition codebase enforces. It is the referent for the `SecurityHardening.md Track N` / `F<n>` citations scattered through the validator, the live tests, and the static audit gates. The doctrine predates the Home Edition conversion (it came out of a security review pass); the items below are the ones still wired into this repo.

**Threat model.** Home Edition is a single-household appliance on a home LAN (see the Household Trust Model in `CLAUDE.md`). There is no multi-tenant PHI host and no public internet exposure by default. These controls exist anyway, because the failure modes they catch — a permissive env default, a missing check, a forged request, an unreviewed write to a secret column — are real on a box that one household trusts with its own health data.

The defining principle: **promote runtime-silent defects to deploy-time-loud or test-time-loud.** A dangerous default that "just works" is worse than one that refuses to start.

---

## Tracks

Each Track is a class of control. A Track is enforced by some combination of a startup validator, a live test, and a static (AST) audit gate.

### Track 1 — Sensitive-write inventory

Every code site that writes a sensitive-named column (password hashes, TOTP secrets, API-token hashes, encrypted credentials) must appear in the approved inventory at [`Compliance/sensitive-write-sites.md`](Compliance/sensitive-write-sites.md). A write from any function not listed there fails the static audit. The point is that every such write is deliberate and reviewed. Enforced by `DataModel3/code_query_audit.py` (Rule 4).

### Track 2 — No silent fallback to a permissive default

Silent fallback-to-permissive-default is the headline vulnerability class: code that, when a config value is missing or empty, quietly does the *less* safe thing (grants access, skips a check, widens scope) instead of failing. The static audit flags the name/shape patterns (Patterns 1–4) that tend to hide these. **F3** (below) is the canonical instance. Enforced by `DataModel3/code_query_audit.py` (Track 2 patterns) and, at runtime, by Track 6.

### Track 3 — Flask-route AST rules

Static rules over the Flask route surface, checked without a database by `DataModel3/test_route_audit.py` / `code_query_audit.py`. Routes that intentionally diverge from a rule are waived, with a reason, in [`Compliance/route-audit-allowlist.md`](Compliance/route-audit-allowlist.md).

### Track 4a — Active-defense tests

The tests that prove the defenses actually repel forged/abusive requests:

- **Live flows** in `UserApp/livetest/flows/security_*.py` — fire real requests at a running server. Defenses not yet in place are asserted with `xfail="F<n>: ..."`: the assertion fails today (`xfail`, counts as pass) and flips to `xpass` (counts as **fail**) when the fix lands, forcing the marker to be removed.
- **Unit test** `UserApp/webapp/tests/test_security_defense.py` — asserts the runtime fallback *shape* for **F3** (easier to drive with synthetic env than a live server).

### Track 6 — Startup env validation

`UserApp/webapp/validate_env.py` refuses to start the app when env-var combinations are dangerous in ways the running code can't distinguish from a healthy config — the Track 2 failure class, caught at boot. Pure function; one rule per defect. **F3** is the rule that ships today.

### Track 7 — Schema crypto-column contract

Columns that store cryptographic material must declare their algorithm in the schema column comment; `DataModel3/code_query_audit.py` checks the comment contract. A secret column with an undeclared or `tbd` algorithm is a finding.

---

## Findings

Findings are concrete defects the Tracks watch for. Open findings ride as `xfail`/validator rules until fixed.

| ID | Finding | Where it's caught |
|----|---------|-------------------|
| **F1** | Session-auth mutating routes (POST/PUT/DELETE/PATCH) accept forged-`Origin` requests — `CSRFProtect` is not registered on any blueprint. | Track 4a flow `security_csrf.py`; audit `code_query_audit.py` |
| **F3** | `HEALTHKIT_SYNC_TOKEN` set while `HEALTHKIT_SYNC_USERNAME` is empty makes the token-auth fallback grant the token holder access to the lowest-ID active user — credential-free account takeover via a shipped-empty default. | Track 6 validator (refuses to boot); Track 4a unit test |
| **F6** | Static-file-serving handlers in `app.py` serve from the app working tree (`send_from_directory`) — a path-traversal surface accepted as safe on a single-household LAN box with no untrusted multi-tenant traffic. | Track 3 route audit; waived in `route-audit-allowlist.md` |

A finding is closed by landing the fix **and** removing its `xfail=`/waiver, at which point the Track 4a test flips to `xpass` and forces the cleanup.

---

## Notes for Home Edition

- This doc was reconstructed during the Home Edition conversion to resolve the `SecurityHardening.md` citations that survived in code after the original review docs were removed. The companion review docs `2ndOpinion.md` / `3rdOpinion.md` are likewise gone; their findings that still matter are folded into the Findings table above.
- Several citations live in `DataModel3/*`, which is **frozen** and rewritten in Phase 6. If this doctrine is ever renamed, those references (and the `validate_env.py` ↔ `test_validate_env.py` string assertion) must be updated in the same pass.
