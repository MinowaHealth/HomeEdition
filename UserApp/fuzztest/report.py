#!/usr/bin/env python3
"""Parse Schemathesis NDJSON reports into a markdown campaign summary.

Reads every `*.ndjson` file under the given report directory tree, groups
failures by (operation_id, check_name, status_code), splits 503-QUERY_TIMEOUT
from generic 5xx, and emits a markdown summary.

Usage:
    python report.py fuzz-reports/                  # walk a tree
    python report.py fuzz-reports/ring1/G/          # one profile
    python report.py fuzz-reports/ --out report.md  # explicit output path

The NDJSON format is Schemathesis v4's structured-events stream; each line
is a JSON object. We rely on the `type` discriminator and on a small set
of fields that v4.18 emits. If the format shifts in a later release, this
script's parsing degrades gracefully — unrecognized lines are ignored, not
crashed on.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Failure:
    operation_id: str
    method: str
    path: str
    check_name: str
    status_code: int | None
    server_header: str | None
    message: str
    request_body: str | None = None
    response_body: str | None = None


@dataclass
class ProfileSummary:
    profile: str
    ring: str
    total_requests: int = 0
    total_responses: int = 0
    failures: list[Failure] = field(default_factory=list)
    status_codes: Counter = field(default_factory=Counter)
    server_headers: Counter = field(default_factory=Counter)


def _safe_get(d: Any, *path: str, default: Any = None) -> Any:
    """Defensive nested dict access — Schemathesis NDJSON shape is fluid."""
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def parse_ndjson(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def derive_profile_meta(path: Path, root: Path) -> tuple[str, str]:
    """Infer (ring, profile) from path structure like fuzz-reports/ring1/G/foo.ndjson."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    parts = rel.parts
    ring = "unknown"
    profile = "unknown"
    for part in parts:
        if part.startswith("ring") and part[4:].isdigit():
            ring = part
        elif len(part) == 1 and part.isalpha() and part.upper() in "ABCDEFG":
            profile = part.upper()
    return ring, profile


def classify(status_code: int | None, server_header: str | None) -> str:
    """One-line classification used in the grouped failure table."""
    if status_code is None:
        return "no response"
    if 200 <= status_code < 300:
        return f"{status_code} OK"
    if status_code == 401:
        return "401 unauthorized"
    if status_code == 403:
        return "403 forbidden"
    if status_code == 404:
        return "404 not found"
    if status_code == 422:
        return "422 unprocessable"
    if status_code == 429:
        return "429 too many requests"
    if status_code == 503:
        return "503 (likely QUERY_TIMEOUT)"
    if 400 <= status_code < 500:
        return f"{status_code} client error"
    if status_code >= 500:
        return f"{status_code} SERVER ERROR"
    return f"{status_code}"


def extract_failure(event: dict[str, Any]) -> Failure | None:
    """Pull a Failure out of one NDJSON event. Returns None if not a failure event.

    Schemathesis v4 emits an event hierarchy; the check-failed events carry
    enough context for our report. We probe several known shapes — older
    snapshots used `check`/`failure`, newer ones use `result.checks`.
    """
    etype = event.get("type", "")
    if etype not in ("check_failed", "test_failure", "failure", "check"):
        # Not a failure event. Check whether this is a 'response' event we
        # should count toward status_codes — separate path below.
        return None

    op_id = (
        _safe_get(event, "operation_id")
        or _safe_get(event, "operation", "operation_id")
        or _safe_get(event, "operation", "id", default="")
        or ""
    )
    method = (
        _safe_get(event, "method")
        or _safe_get(event, "operation", "method", default="")
        or ""
    ).upper()
    path = (
        _safe_get(event, "path")
        or _safe_get(event, "operation", "path", default="")
        or ""
    )
    check_name = (
        _safe_get(event, "check_name")
        or _safe_get(event, "check", "name")
        or event.get("name", "unknown_check")
    )
    status_code = (
        _safe_get(event, "status_code")
        or _safe_get(event, "response", "status_code")
    )
    if isinstance(status_code, str) and status_code.isdigit():
        status_code = int(status_code)

    headers = _safe_get(event, "response", "headers", default={}) or {}
    # Headers may be a flat dict or list of [name, value] pairs.
    server_header: str | None = None
    if isinstance(headers, dict):
        for k, v in headers.items():
            if k.lower() == "server":
                server_header = v if isinstance(v, str) else (v[0] if v else None)
                break
    elif isinstance(headers, list):
        for pair in headers:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2 and pair[0].lower() == "server":
                server_header = pair[1]
                break

    message = (
        event.get("message")
        or _safe_get(event, "check", "message")
        or event.get("title")
        or ""
    )

    req_body = _safe_get(event, "request", "body")
    resp_body = _safe_get(event, "response", "body")
    if isinstance(req_body, (dict, list)):
        req_body = json.dumps(req_body)[:500]
    if isinstance(resp_body, (dict, list)):
        resp_body = json.dumps(resp_body)[:500]

    return Failure(
        operation_id=str(op_id),
        method=str(method),
        path=str(path),
        check_name=str(check_name),
        status_code=status_code,
        server_header=server_header,
        message=str(message)[:500],
        request_body=str(req_body)[:500] if req_body else None,
        response_body=str(resp_body)[:500] if resp_body else None,
    )


def accumulate(summary: ProfileSummary, event: dict[str, Any]) -> None:
    etype = event.get("type", "")
    if etype in ("after_call", "response", "request_response"):
        summary.total_responses += 1
        sc = _safe_get(event, "response", "status_code")
        if isinstance(sc, int):
            summary.status_codes[sc] += 1
        headers = _safe_get(event, "response", "headers", default={})
        server_val: str | None = None
        if isinstance(headers, dict):
            for k, v in headers.items():
                if k.lower() == "server":
                    server_val = v if isinstance(v, str) else (v[0] if v else None)
                    break
        if server_val:
            summary.server_headers[server_val] += 1
    if etype in ("before_call", "request"):
        summary.total_requests += 1
    fail = extract_failure(event)
    if fail:
        summary.failures.append(fail)


def emit_markdown(summaries: list[ProfileSummary]) -> str:
    lines: list[str] = []
    lines.append("# Schemathesis Campaign Report\n")

    if not summaries:
        lines.append("_No NDJSON reports found._\n")
        return "\n".join(lines)

    # Top-level overview table.
    lines.append("## Per-profile overview\n")
    lines.append("| Ring | Profile | Requests | Responses | Failures | 5xx | 4xx | 429s |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for s in summaries:
        five_xx = sum(c for sc, c in s.status_codes.items() if sc >= 500)
        four_xx = sum(c for sc, c in s.status_codes.items() if 400 <= sc < 500 and sc != 429)
        # The app does no rate limiting, so a 429 here would be unexpected —
        # surfaced as its own column rather than folded into the 4xx bucket.
        count_429 = s.status_codes.get(429, 0)
        lines.append(
            f"| {s.ring} | {s.profile} | {s.total_requests} | {s.total_responses} | "
            f"{len(s.failures)} | {five_xx} | {four_xx} | {count_429} |"
        )
    lines.append("")

    # Grouped failures.
    lines.append("## Failures grouped by operation × check × status\n")
    grouped: dict[tuple[str, str, str, str], list[Failure]] = defaultdict(list)
    for s in summaries:
        for f in s.failures:
            key = (s.ring, f.operation_id or f.path, f.check_name, classify(f.status_code, f.server_header))
            grouped[key].append(f)

    if not grouped:
        lines.append("_No failures across the entire campaign — clean run or all checks suppressed._\n")
    else:
        lines.append("| Ring | Operation | Check | Status | Count | First message |")
        lines.append("|---|---|---|---|---:|---|")
        for (ring, op, check, status), fails in sorted(grouped.items(), key=lambda x: -len(x[1])):
            sample_msg = fails[0].message.replace("|", "\\|").replace("\n", " ")[:120]
            lines.append(f"| {ring} | `{op}` | {check} | {status} | {len(fails)} | {sample_msg} |")
        lines.append("")

    # Server-header breakdown (edge vs app).
    lines.append("## Server header breakdown\n")
    lines.append("Origin-app `Server:` header tally — sanity check that responses came from the appliance.\n")
    lines.append("| Ring | Profile | Server header | Count |")
    lines.append("|---|---|---|---:|")
    for s in summaries:
        for server, count in s.server_headers.most_common():
            lines.append(f"| {s.ring} | {s.profile} | `{server}` | {count} |")
    lines.append("")

    # Failure-mode pointers (ring-1 only, by convention).
    has_ring1 = any(s.ring == "ring1" for s in summaries)
    if has_ring1:
        lines.append("## Failure-mode cross-reference\n")
        lines.append(
            "Pair this report with the corresponding `fuzz-failure-modes/<ts>/` "
            "monitor output. The failure-mode catalog lists 11 specific "
            "modes; the monitor's SUMMARY.md will name which ones were observed.\n"
        )

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Schemathesis NDJSON → markdown report.")
    p.add_argument("root", type=Path, help="Directory tree of fuzz reports.")
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output markdown path (default: <root>/CAMPAIGN_REPORT.md).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if not args.root.exists():
        print(f"ERROR: {args.root} does not exist", file=sys.stderr)
        return 2

    ndjson_files = sorted(args.root.rglob("*.ndjson"))
    if not ndjson_files:
        print(f"WARNING: no .ndjson files under {args.root}", file=sys.stderr)

    # Group by (ring, profile) directory.
    by_meta: dict[tuple[str, str], ProfileSummary] = {}
    for path in ndjson_files:
        ring, profile = derive_profile_meta(path, args.root)
        key = (ring, profile)
        summary = by_meta.setdefault(key, ProfileSummary(profile=profile, ring=ring))
        for event in parse_ndjson(path):
            accumulate(summary, event)

    summaries = sorted(by_meta.values(), key=lambda s: (s.ring, s.profile))
    md = emit_markdown(summaries)

    out = args.out or (args.root / "CAMPAIGN_REPORT.md")
    out.write_text(md)
    print(f"Wrote {out}")
    print(f"  profiles: {len(summaries)}")
    print(f"  total failures: {sum(len(s.failures) for s in summaries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
