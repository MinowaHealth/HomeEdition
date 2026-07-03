# Route Audit Allowlist

**Date**: 2026-06-13 18:00 PDT

This file is **audit plumbing**, not a compliance program. `DataModel3/code_query_audit.py` reads it to waive specific, reviewed findings of its route rules (SecurityHardening.md). Each waiver names a `Scope` and a `Reason`. The enterprise `Compliance/` HIPAA/SOC2 program documents were removed in the Home Edition conversion; this directory now holds only the two small files the static audit gate needs to stay green ([sensitive-write-sites.md](sensitive-write-sites.md) is the other).

Scope forms understood by the parser:
- `func:<repo-relative-file>:<function_name>` — one function
- `file:<repo-relative-file>` — any match in that file
- `dir:<repo-relative-dir>/` — any match under that directory

## Rule 2 — `send_from_directory('.', ...)`

| Scope | Reason | Tracked in |
|-------|--------|------------|
| `file:UserApp/webapp/app.py` | The web UI / SPA static-serving handlers intentionally serve from the app working tree on a single-household LAN box. No untrusted multi-tenant path traversal surface in Home Edition. | SecurityHardening.md F6 |
