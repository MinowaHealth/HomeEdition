#!/usr/bin/env python3
"""api-probe — read /api/v1/healthz from one or more Minowa API bases.

Examples
--------
Probe production and testing side-by-side, exit 0 only if both are healthy:

    python scripts/api-probe.py \\
        --base https://localhost \\
        --base https://localhost

Probe a single base and dump the full response body:

    python scripts/api-probe.py --base https://localhost --verbose

Use as a healthcheck (exits non-zero on any 503 or unreachable target):

    python scripts/api-probe.py --base https://localhost && echo OK || echo BROKEN

Exit codes
----------
    0   every probed base returned 200 with all subsystem checks green
    1   at least one base returned 503 (endpoint reachable, dependency unhealthy)
    2   at least one base was unreachable, timed out, or returned an unexpected shape

This is the operator-side companion to the "API Health" button in the HealthAI
mobile app developer settings — same endpoint, same response contract.

Spec: APIDocumentation/openapi.yaml (operationId: healthz)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

try:
    import httpx
except ImportError:
    sys.stderr.write(
        "api-probe: httpx not installed. Install with: pip install httpx\n"
    )
    sys.exit(2)


HEALTHZ_PATH = '/api/v1/healthz'


def _probe_one(base: str, timeout: float) -> tuple[int, dict[str, Any] | None, str | None]:
    """Probe a single base URL. Returns (status_code_or_-1, body_or_None, error_or_None)."""
    url = base.rstrip('/') + HEALTHZ_PATH
    try:
        resp = httpx.get(url, timeout=timeout)
    except httpx.TimeoutException:
        return -1, None, f'timeout after {timeout}s'
    except httpx.ConnectError as exc:
        return -1, None, f'connect error: {exc}'
    except httpx.HTTPError as exc:
        return -1, None, f'http error: {exc}'

    try:
        body = resp.json()
    except ValueError:
        return resp.status_code, None, f'non-JSON body (first 80 chars): {resp.text[:80]!r}'

    return resp.status_code, body, None


def _format_check(name: str, check: dict[str, Any]) -> str:
    ok = check.get('ok')
    marker = '✓' if ok else '✗'
    parts = [f'  {marker} {name:<22}']
    if not ok and 'error' in check:
        parts.append(f'error="{check["error"]}"')
    extras = []
    for key in ('latency_ms', 'rows', 'last_success', 'queue_depth', 'stale_seconds'):
        if key in check:
            extras.append(f'{key}={check[key]}')
    if extras:
        parts.append('  '.join(extras))
    return '  '.join(parts)


def _summary_line(base: str, status: int, body: dict[str, Any] | None, error: str | None) -> str:
    if error is not None:
        return f'{base}  UNREACHABLE  {error}'
    if body is None:
        return f'{base}  HTTP {status}  (no JSON body)'
    svc = body.get('service', '?')
    ver = body.get('version', '?')
    env = body.get('environment', '?')
    label = 'healthy' if status == 200 else 'degraded'
    return f'{base}  HTTP {status} {label}  service={svc} version={ver} environment={env}'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Probe /api/v1/healthz on one or more Minowa API bases.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--base', action='append', required=True, metavar='URL',
        help='Base URL (without /api/v1/healthz). May be passed multiple times to probe side-by-side.',
    )
    parser.add_argument(
        '--timeout', type=float, default=5.0,
        help='HTTP timeout in seconds for each probe (default: 5).',
    )
    parser.add_argument(
        '--verbose', action='store_true',
        help='Print the full JSON response body for each base.',
    )
    parser.add_argument(
        '--quiet', action='store_true',
        help='Print only failures.',
    )
    args = parser.parse_args(argv)

    worst_exit = 0
    for base in args.base:
        status, body, error = _probe_one(base, args.timeout)

        if error is not None:
            worst_exit = max(worst_exit, 2)
        elif body is None:
            worst_exit = max(worst_exit, 2)
        elif status == 503:
            worst_exit = max(worst_exit, 1)
        elif status != 200:
            worst_exit = max(worst_exit, 2)

        if args.quiet and status == 200:
            continue

        print(_summary_line(base, status, body, error))
        if body and 'checks' in body:
            for check_name, check in body['checks'].items():
                print(_format_check(check_name, check))
        if args.verbose and body is not None:
            print(json.dumps(body, indent=2))

    return worst_exit


if __name__ == '__main__':
    sys.exit(main())
