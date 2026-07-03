#!/usr/bin/env python3
"""Audit: Flask routes ↔ OpenAPI spec drift.

Catches the dominant contract-drift mode: a new endpoint lands in a route
file but the spec doesn't get updated (or a path is removed from code but
the spec still declares it).

How it works:

1. Walk `UserApp/webapp/app.py` and `UserApp/webapp/routes/*.py` with AST.
2. For each file:
   - If the file declares a Blueprint, capture its `url_prefix` kwarg.
   - For every `@app.route(PATH, ...)`, `@bp.route(PATH, ...)`, or
     `@<var>.route(PATH, ...)` decorator, capture (PATH, METHODS).
3. Prepend the blueprint's url_prefix to its routes.
4. Normalize Flask `<param>` / `<int:param>` / `<uuid:param>` to OpenAPI
   `{param}`.
5. Walk `paths:` in `APIDocumentation/openapi.yaml`.
6. Diff. Report:
   - Routes in code, missing from spec  — under-documented.
   - Paths in spec, missing from code   — over-promised contract.
7. Exit non-zero if either set is non-empty.

What is intentionally NOT flagged:
- Paths declared on the web UI surface (e.g. `/login` GET that returns
  HTML). The spec only covers JSON APIs. The audit excludes:
    * Routes whose path doesn't start with `/api/v1/` or `/api/v2/`
    * Static file serving routes like `/<path:filename>`
- Method differences. (A v0.5 of this audit could compare methods per
  path; the current version only checks path-set membership.)
- `/api/v2/*` aliases — v1 paths are the contract; v2 is a documented
  passthrough alias per the spec preamble.

Usage:
    python APIDocumentation/route_spec_audit.py
    python APIDocumentation/route_spec_audit.py --verbose

Exit codes:
    0 — sets agree.
    1 — drift detected (over-promised or under-documented endpoints).
    2 — usage error (file not found, parse failure).
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PY = REPO_ROOT / "UserApp" / "webapp" / "app.py"
ROUTES_DIR = REPO_ROOT / "UserApp" / "webapp" / "routes"
SPEC = REPO_ROOT / "APIDocumentation" / "openapi.yaml"
ALLOWLIST = REPO_ROOT / "APIDocumentation" / "route_audit_allowlist.txt"


@dataclass(frozen=True)
class Route:
    path: str
    source: str  # file path string


# Flask path-converter prefixes that get stripped during normalization.
# E.g. <int:user_id>, <uuid:reading_id>, <path:filename>.
_FLASK_PARAM = re.compile(r"<(?:[a-z]+:)?([^>]+)>")


def normalize_path(flask_path: str) -> str:
    """Convert Flask `<int:id>` → OpenAPI `{id}` so the sets are comparable."""
    return _FLASK_PARAM.sub(r"{\1}", flask_path)


def _extract_blueprint_prefix(tree: ast.Module) -> str:
    """Find a `Blueprint(..., url_prefix='X')` call and return X, or ''.

    Returns the first url_prefix found. Most route files declare exactly
    one blueprint at module top-level.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match `Blueprint(...)` or `flask.Blueprint(...)`
        is_blueprint = (
            (isinstance(func, ast.Name) and func.id == "Blueprint")
            or (isinstance(func, ast.Attribute) and func.attr == "Blueprint")
        )
        if not is_blueprint:
            continue
        for kw in node.keywords:
            if kw.arg == "url_prefix" and isinstance(kw.value, ast.Constant):
                value = kw.value.value
                if isinstance(value, str):
                    return value
    return ""


def _extract_route_decorators(tree: ast.Module) -> list[str]:
    """Find every `@<name>.route(PATH, ...)` decorator's literal PATH.

    Matches `@app.route(...)`, `@bp.route(...)`, `@<anything>.route(...)`.
    Non-literal paths (computed/interpolated) are silently skipped — the
    audit only enforces what's statically declared.
    """
    paths: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # Match `@<name>.route(...)` calls
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not (isinstance(func, ast.Attribute) and func.attr == "route"):
                continue
            if not dec.args:
                continue
            first = dec.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                paths.append(first.value)
    return paths


def routes_from_file(path: Path) -> set[Route]:
    """Parse a route file and return its declared API paths (prefix applied)."""
    src = path.read_text()
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        print(f"ERROR: parse failed for {path}: {e}", file=sys.stderr)
        sys.exit(2)

    prefix = _extract_blueprint_prefix(tree)
    raw_paths = _extract_route_decorators(tree)

    routes: set[Route] = set()
    for raw in raw_paths:
        joined = prefix + raw if prefix else raw
        # Skip non-API surfaces.
        if not (joined.startswith("/api/v1/") or joined.startswith("/api/v2/")):
            continue
        # Skip catch-all paths (these are static-file handlers).
        if "<path:" in joined:
            continue
        normalized = normalize_path(joined)
        routes.add(Route(path=normalized, source=str(path.relative_to(REPO_ROOT))))
    return routes


def all_code_routes() -> set[Route]:
    """Aggregate routes from app.py + every routes/*.py file."""
    routes: set[Route] = set()
    routes |= routes_from_file(APP_PY)
    for p in sorted(ROUTES_DIR.glob("*.py")):
        if p.name == "__init__.py":
            continue
        routes |= routes_from_file(p)
    return routes


def spec_paths() -> set[str]:
    """Read the OpenAPI spec and return the set of declared paths."""
    doc = yaml.safe_load(SPEC.read_text())
    return set((doc.get("paths") or {}).keys())


def load_allowlist(path: Path) -> set[str]:
    """Read the allowlist file: one path per line, '#' comments, blanks ignored."""
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit Flask routes ↔ openapi.yaml drift."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show full per-source breakdown of code-side routes.",
    )
    parser.add_argument(
        "--v1-only",
        action="store_true",
        help=(
            "Only audit /api/v1/* paths. v2 routes are passthrough aliases "
            "per the spec preamble and currently undeclared by design."
        ),
    )
    args = parser.parse_args()

    code = all_code_routes()
    spec = spec_paths()
    allowlist = load_allowlist(ALLOWLIST)

    # The spec is v1-only by doctrine. Compare like-to-like.
    code_v1 = {r for r in code if r.path.startswith("/api/v1/")}
    code_v2 = {r for r in code if r.path.startswith("/api/v2/")}

    code_v1_paths = {r.path for r in code_v1}

    # Three drift categories:
    #   NEW drift     — in code, in neither spec nor allowlist (fail)
    #   STALE allow   — in allowlist AND in spec (allowlist cleanup needed; fail)
    #   STALE allow²  — in allowlist but not in code (route deleted; fail)
    #   OVER-PROMISED — in spec but not in code (fail)
    in_code_not_spec_not_allowed = code_v1_paths - spec - allowlist
    in_allowlist_and_spec = allowlist & spec
    in_allowlist_not_code = allowlist - code_v1_paths
    in_spec_not_code = spec - code_v1_paths

    # Backwards-tolerated: paths in code & allowlist (the deferred backlog).
    tolerated = code_v1_paths & allowlist

    if args.verbose:
        print(f"[audit] {len(code_v1)} v1 routes declared in code")
        print(f"[audit] {len(code_v2)} v2 routes declared in code (aliases, not audited)")
        print(f"[audit] {len(spec)} paths declared in spec")
        print(f"[audit] {len(allowlist)} paths in allowlist (deferred scope)")
        print(f"[audit] {len(tolerated)} routes tolerated via allowlist")
        if code_v1:
            print()
            print("v1 routes in code:")
            for r in sorted(code_v1, key=lambda x: (x.source, x.path)):
                marker = "✓" if r.path in spec else ("·" if r.path in allowlist else "?")
                print(f"  {marker} {r.path:<58} ← {r.source}")
            print()

    drift = False

    if in_code_not_spec_not_allowed:
        drift = True
        print("NEW DRIFT — routes in code that aren't in the spec OR the allowlist:")
        path_to_source: dict[str, str] = {r.path: r.source for r in code_v1}
        for path in sorted(in_code_not_spec_not_allowed):
            print(f"  {path:<60} ← {path_to_source.get(path, '<?>')}")
        print()

    if in_spec_not_code:
        drift = True
        print("OVER-PROMISED — paths declared in openapi.yaml have no matching route:")
        for path in sorted(in_spec_not_code):
            print(f"  {path}")
        print()

    if in_allowlist_and_spec:
        drift = True
        print(
            "STALE ALLOWLIST — paths now in the spec but also still in the allowlist:"
        )
        print(
            "  (remove these from APIDocumentation/route_audit_allowlist.txt)"
        )
        for path in sorted(in_allowlist_and_spec):
            print(f"  {path}")
        print()

    if in_allowlist_not_code:
        drift = True
        print("STALE ALLOWLIST — allowlist entries that no longer match any code route:")
        print("  (remove these from APIDocumentation/route_audit_allowlist.txt)")
        for path in sorted(in_allowlist_not_code):
            print(f"  {path}")
        print()

    if drift:
        print(
            "Fix one of:\n"
            "  (a) Declare the path in APIDocumentation/openapi.yaml (preferred), OR\n"
            "  (b) Add it to APIDocumentation/route_audit_allowlist.txt with a one-line\n"
            "      comment explaining why it's intentionally deferred, OR\n"
            "  (c) Remove/rename the corresponding route in UserApp/webapp/, OR\n"
            "  (d) Remove the stale allowlist or spec entry.\n",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK — {len(spec)} spec paths + {len(tolerated)} allowlisted paths cover all "
        f"{len(code_v1_paths)} v1 routes."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
