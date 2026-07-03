# Security Policy

Date: 2026-07-01 23:20 PDT

## Reporting a vulnerability

Email **security@minowa.ai**, or use GitHub's private vulnerability reporting ("Report a vulnerability" under this repo's Security tab). Please do **not** open a public issue for security problems — this software holds families' health data.

Include what you found, how to reproduce it, and what data it exposes. We'll acknowledge within a few days; this is a small team, not a SOC.

## What this system is — and is not

Minowa Home Edition is a **LAN appliance**. It is *not hardened for the public internet*, and we do not support exposing it there.

- Every route enforces a source-IP allowlist: RFC1918 private ranges, the CGNAT range `100.64.0.0/10` (Tailscale), and loopback. `X-Forwarded-For` is never trusted.
- **Do not port-forward this box, and do not publish it through a tunnel.** Any local tunnel or reverse proxy (Cloudflare Tunnel, ngrok, nginx on the same host) connects from a *permitted* local address — the allowlist cannot protect a box exposed that way. Reports of "I tunneled it to the internet and something got in" are configuration, not a vulnerability.
- The supported remote-access path is **Tailscale Personal**. Cloudflare Access can work, but only if you are already fluent with it; we don't provide support for that path.

## The household trust model

There is no PostgreSQL Row-Level Security in Home Edition. Privacy **between household members** is enforced in the application: every query against a user-owned table carries an explicit `user_id` predicate for the authenticated user.

That means:

- **In scope, highest severity:** any way for one authenticated household member to read or write another member's data. This is exactly the bug class we most want reported.
- **In scope:** authentication bypass, session/token weaknesses, SQL injection, path traversal in the document store.
- **Out of scope:** attacks requiring shell access to the box (whoever has shell owns the appliance — that's the admin), attacks from an internet exposure we tell you not to create, and physical access.

If you modify data-access code, run the repeatable audit before you trust your change: `scripts/user_scope_audit.py`. See CONTRIBUTING.md before touching the API or database layers.

## Updates

Security fixes ship on `main`. Appliances update with `UserApp/update.sh` (git pull + rebuild). There is no auto-update; subscribe to the repo's releases to hear about fixes.
