#!/usr/bin/env python3
"""Mint a long-lived API key for a fuzz user. Idempotent across runs.

The raw key is returned exactly once by `POST /api/v1/api-keys` — the list
endpoint only exposes `key_prefix`. So "idempotent" here means:

1. On startup, list the user's active keys.
2. Revoke any key whose label matches the run's label (default: derived from
   --ring). This makes room under the `MAX_API_KEYS_PER_USER=5` cap and
   ensures the run starts with a known-clean key.
3. Mint a fresh key.
4. Print the raw key to STDOUT (and only the raw key — all status messages
   go to STDERR so the shell can capture stdout cleanly).

Usage:
    python mint_key.py --base-url http://localhost \\
                       --email fuzz@appliance.local \\
                       --password <pw> \\
                       --ring 1

    # Or as a one-shot revoker (post-crash cleanup):
    python mint_key.py --base-url ... --email ... --password ... --revoke-only

The script does NOT auto-revoke at exit — `profiles.sh` is responsible for
revoking via `--revoke-only` in its cleanup trap so the revoke runs even
after a non-zero exit from schemathesis.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx


def _err(msg: str) -> None:
    """Write a status line to stderr. Stdout is reserved for the raw key."""
    print(msg, file=sys.stderr)


def login(base_url: str, email: str, password: str) -> str:
    """Log in and return the session token. Raises on failure."""
    resp = httpx.post(
        f"{base_url}/api/v1/login",
        json={"email": email, "password": password},
        timeout=30,
        follow_redirects=True,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        if body.get("requires_2fa"):
            raise SystemExit(
                "Fuzz user has 2FA enabled. Disable it first: "
                "UPDATE users SET totp_enabled=false WHERE email=...;"
            )
        raise SystemExit(f"Login failed: {body}")
    token = body.get("token")
    if not token:
        raise SystemExit(f"Login succeeded but no token in response: {body}")
    return str(token)


def revoke_matching_keys(base_url: str, token: str, label: str) -> int:
    """Revoke every active API key whose `device_name` matches `label`.

    Returns the count revoked. The list endpoint returns metadata only; the
    label minted on POST is stored as `device_name` on the read side (see
    the spec's ApiKeyMetadata schema note).
    """
    headers = {"Authorization": f"Bearer {token}"}
    resp = httpx.get(
        f"{base_url}/api/v1/api-keys", headers=headers, timeout=30,
        follow_redirects=True,
    )
    resp.raise_for_status()
    keys: list[dict[str, Any]] = resp.json()
    revoked = 0
    for key in keys:
        if key.get("device_name") == label:
            key_id = key["id"]
            del_resp = httpx.delete(
                f"{base_url}/api/v1/api-keys/{key_id}",
                headers=headers,
                timeout=30,
                follow_redirects=True,
            )
            del_resp.raise_for_status()
            _err(f"  revoked existing key id={key_id} label={label!r}")
            revoked += 1
    return revoked


def mint_key(base_url: str, token: str, label: str) -> str:
    """Create a new API key and return the raw value."""
    headers = {"Authorization": f"Bearer {token}"}
    resp = httpx.post(
        f"{base_url}/api/v1/api-keys",
        json={"label": label},
        headers=headers,
        timeout=30,
        follow_redirects=True,
    )
    resp.raise_for_status()
    body = resp.json()
    if "key" not in body:
        raise SystemExit(f"Mint succeeded but no raw key in response: {body}")
    return str(body["key"])


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Idempotent fuzz-user API-key minter.")
    p.add_argument("--base-url", required=True, help="API base, e.g. http://localhost")
    p.add_argument("--email", required=True, help="Fuzz user email")
    p.add_argument("--password", required=True, help="Fuzz user password")
    p.add_argument(
        "--ring",
        type=int,
        choices=[1, 2],
        help="Ring number — derives the default label.",
    )
    p.add_argument(
        "--label",
        default=None,
        help="Explicit label override (default: fuzz-ring<N>).",
    )
    p.add_argument(
        "--revoke-only",
        action="store_true",
        help="Revoke matching keys and exit. Used by profiles.sh cleanup traps.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.label is None:
        if args.ring is None:
            _err("ERROR: --label or --ring required")
            return 2
        args.label = f"fuzz-ring{args.ring}"

    _err(f"[mint_key] target={args.base_url} email={args.email} label={args.label}")

    try:
        token = login(args.base_url, args.email, args.password)
    except httpx.HTTPError as e:
        _err(f"ERROR: login request failed: {e}")
        return 1

    try:
        revoked = revoke_matching_keys(args.base_url, token, args.label)
    except httpx.HTTPError as e:
        _err(f"ERROR: revoke step failed: {e}")
        return 1
    _err(f"[mint_key] revoked {revoked} pre-existing key(s) with label={args.label!r}")

    if args.revoke_only:
        _err("[mint_key] --revoke-only set; not minting.")
        return 0

    try:
        raw_key = mint_key(args.base_url, token, args.label)
    except httpx.HTTPStatusError as e:
        body = e.response.text if e.response is not None else ""
        _err(f"ERROR: mint failed: {e} body={body}")
        return 1
    except httpx.HTTPError as e:
        _err(f"ERROR: mint failed: {e}")
        return 1

    _err(f"[mint_key] minted new key prefix={raw_key[:12]}…")
    # ONLY the raw key on stdout, for shell capture.
    print(raw_key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
