# Sensitive Write Sites

**Date**: 2026-06-13 18:00 PDT

This file is **audit plumbing**, not a compliance program. `DataModel3/code_query_audit.py` (Rule 4) treats it as the inventory of *approved* code sites that write to sensitive-named columns (password hashes, TOTP secrets, API-token hashes, encrypted credentials). A write from any function not listed here fails the audit — the point is that every such write is deliberate and reviewed. The enterprise `Compliance/` HIPAA/SOC2 program documents were removed in the Home Edition conversion; this is one of two small audit-support files this directory still carries (see [route-audit-allowlist.md](route-audit-allowlist.md)).

## Code Write Sites

| Function | File | Column(s) | Why it's legitimate |
|----------|------|-----------|---------------------|
| `provision_user` | `../UserApp/admin.py` | `password_hash` | CLI account provisioning — the only way accounts are created in Home Edition (no email signup). |
| `reset_password` | `../UserApp/admin.py` | `password_hash` | CLI password reset — the only reset path (no email flows). |
| `change_password` | `../UserApp/webapp/auth.py` | `password_hash` | Authenticated in-app password change; requires the current password. |
| `setup_2fa` | `../UserApp/webapp/auth.py` | `totp_secret` | Per-user TOTP enrolment (optional 2FA). |
| `create_api_key` | `../UserApp/webapp/auth.py` | `token_hash`, `token_type` | Issues bearer API tokens for clients / HealthKit sync. |
| `garmin_connect` | `../UserApp/webapp/routes/integrations.py` | `encrypted_password` | Stores the household member's Garmin credential (encrypted) for wearable sync. |
