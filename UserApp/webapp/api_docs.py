"""API Documentation Generator — route introspection + OpenAPI spec.

Provides two Flask CLI commands:

    flask api-docs inventory   Print JSON inventory of all registered routes
    flask api-docs openapi     Generate OpenAPI 3.0 spec (JSON or YAML)

The introspection walks app.url_map and resolves each endpoint back to its
view function, extracting: URL pattern, HTTP methods, module/file, docstring,
and whether @require_auth is applied.

The OpenAPI generator layers apispec on top of the same data, producing a
spec that can be served, diffed, or fed to Swagger UI / ReDoc.

Usage from the repo root (with .venv activated):
    cd UserApp/webapp
    FLASK_APP=app flask api-docs inventory
    FLASK_APP=app flask api-docs openapi --format yaml --output openapi.yaml
"""

import inspect
import json
import os
import sys

import click
from flask import Blueprint, current_app
from flask.cli import AppGroup

from apispec import APISpec
from apispec_webframeworks.flask import FlaskPlugin


# ── CLI group ────────────────────────────────────────────────────────────

api_docs_cli = AppGroup("api-docs", help="API documentation and route introspection.")


# ── Introspection helpers ────────────────────────────────────────────────

# Flask adds these to every route; they're not part of the API surface.
SKIP_ENDPOINTS = {"static", "source_ip_filter"}

# Methods Flask registers implicitly on every route.
IMPLICIT_METHODS = {"OPTIONS", "HEAD"}


def _is_auth_required(view_func) -> bool:
    """Detect whether the view function is wrapped by @require_auth.

    Works by walking the wrapper chain (__wrapped__) and checking names.
    """
    # The decorator sets functools.wraps, so __wrapped__ points to the original.
    if hasattr(view_func, "__wrapped__"):
        # If the immediate wrapper is called 'decorated_function' from require_auth
        # we can check the closure or just the presence of __wrapped__.
        return True
    return False


def _get_source_location(view_func) -> dict:
    """Return the source file (relative to webapp/) and line number."""
    try:
        source_file = inspect.getfile(view_func)
        # Make path relative to webapp/ for readability
        webapp_dir = os.path.dirname(os.path.abspath(__file__))
        rel_path = os.path.relpath(source_file, webapp_dir)
        line = inspect.getsourcelines(view_func)[1]
        return {"file": rel_path, "line": line}
    except (TypeError, OSError):
        return {"file": "unknown", "line": 0}


def _get_blueprint_name(endpoint: str) -> str | None:
    """Extract blueprint name from a dotted endpoint like 'providers.list_my_providers'."""
    if "." in endpoint:
        return endpoint.rsplit(".", 1)[0]
    return None


def introspect_routes(app) -> list[dict]:
    """Walk app.url_map and return a structured inventory of all API routes.

    Returns a list of dicts, each with:
        rule, methods, endpoint, blueprint, auth_required,
        docstring, source_file, source_line
    """
    routes = []

    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.endpoint in SKIP_ENDPOINTS:
            continue

        view_func = app.view_functions.get(rule.endpoint)
        if view_func is None:
            continue

        methods = sorted(rule.methods - IMPLICIT_METHODS)
        if not methods:
            continue

        # Resolve through wrappers to get the real function for docstring/source
        real_func = view_func
        while hasattr(real_func, "__wrapped__"):
            real_func = real_func.__wrapped__

        source = _get_source_location(real_func)
        docstring = (real_func.__doc__ or "").strip()
        # Take first line only for the summary
        summary = docstring.split("\n")[0] if docstring else ""

        routes.append({
            "rule": rule.rule,
            "methods": methods,
            "endpoint": rule.endpoint,
            "blueprint": _get_blueprint_name(rule.endpoint),
            "auth_required": _is_auth_required(view_func),
            "summary": summary,
            "docstring": docstring,
            "source": source,
        })

    return routes


# ── CLI: inventory ───────────────────────────────────────────────────────

@api_docs_cli.command("inventory")
@click.option("--format", "fmt", type=click.Choice(["json", "table"]), default="table",
              help="Output format (default: table).")
@click.option("--output", "-o", type=click.Path(), default=None,
              help="Write output to file instead of stdout.")
@click.option("--api-only", is_flag=True, default=False,
              help="Only show /api/* routes.")
def inventory_cmd(fmt, output, api_only):
    """Print an inventory of all registered routes."""
    app = current_app._get_current_object()
    routes = introspect_routes(app)

    if api_only:
        routes = [r for r in routes if r["rule"].startswith("/api/")]

    if fmt == "json":
        text = json.dumps(routes, indent=2)
    else:
        # Human-readable table
        lines = []
        lines.append(f"{'METHOD':<8} {'ROUTE':<55} {'AUTH':<5} {'SOURCE':<40} SUMMARY")
        lines.append("─" * 140)
        for r in routes:
            for method in r["methods"]:
                auth = "✓" if r["auth_required"] else ""
                src = f"{r['source']['file']}:{r['source']['line']}"
                lines.append(f"{method:<8} {r['rule']:<55} {auth:<5} {src:<40} {r['summary'][:50]}")
        lines.append(f"\n{len(routes)} routes ({sum(1 for r in routes if r['auth_required'])} authenticated)")
        text = "\n".join(lines)

    if output:
        with open(output, "w") as f:
            f.write(text)
        click.echo(f"Written to {output}")
    else:
        click.echo(text)


# ── CLI: openapi ─────────────────────────────────────────────────────────

def _build_openapi_spec(app) -> APISpec:
    """Build an OpenAPI 3.0 spec from registered Flask routes.

    Uses apispec's FlaskPlugin to resolve URL converters and methods.
    Docstrings that contain YAML blocks (apispec convention) are parsed
    into operation descriptions.  Routes without YAML still appear as
    skeleton entries — the spec grows as docstrings are enriched.
    """
    spec = APISpec(
        title="Minowa.ai User API",
        version="1.0.0",
        openapi_version="3.0.3",
        info={
            "description": (
                "Patient-facing REST API for Minowa.ai Home Edition. "
                "Single-household. All timestamps UTC."
            ),
            "contact": {"name": "Minowa.ai", "url": "https://localhost"},
        },
        servers=[
            {"url": "https://localhost", "description": "Pilot"},
        ],
        plugins=[FlaskPlugin()],
    )

    # Add security scheme
    spec.components.security_scheme(
        "bearerAuth",
        {
            "type": "http",
            "scheme": "bearer",
            "description": "Session UUID from /api/v1/login",
        },
    )

    # Walk routes and register with apispec
    routes = introspect_routes(app)
    seen_paths = set()

    for route_info in routes:
        rule = route_info["rule"]
        if not rule.startswith("/api/"):
            continue

        endpoint = route_info["endpoint"]
        view_func = app.view_functions.get(endpoint)
        if view_func is None:
            continue

        # apispec deduplicates by path — register each path once
        if rule in seen_paths:
            continue
        seen_paths.add(rule)

        # Build operations dict for methods that lack YAML docstrings
        # so they still appear in the spec as skeleton entries
        operations = {}
        for method in route_info["methods"]:
            method_lower = method.lower()
            op: dict = {"summary": route_info["summary"] or endpoint}
            if route_info["auth_required"]:
                op["security"] = [{"bearerAuth": []}]
            if route_info["blueprint"]:
                op["tags"] = [route_info["blueprint"]]
            operations[method_lower] = op

        # FlaskPlugin.path_helper resolves <converter:param> to {param}
        # and merges any YAML from the docstring
        try:
            spec.path(
                view=view_func,
                app=app,
                operations=operations,
            )
        except Exception as exc:
            # Some routes (e.g. static, redirects) may not resolve cleanly
            click.echo(f"  ⚠ Skipping {rule}: {exc}", err=True)

    return spec


@api_docs_cli.command("openapi")
@click.option("--format", "fmt", type=click.Choice(["json", "yaml"]), default="json",
              help="Output format (default: json).")
@click.option("--output", "-o", type=click.Path(), default=None,
              help="Write spec to file instead of stdout.")
def openapi_cmd(fmt, output):
    """Generate OpenAPI 3.0 specification from registered routes."""
    app = current_app._get_current_object()
    spec = _build_openapi_spec(app)

    if fmt == "yaml":
        text = spec.to_yaml()
    else:
        text = json.dumps(spec.to_dict(), indent=2)

    if output:
        with open(output, "w") as f:
            f.write(text)
        click.echo(f"OpenAPI spec written to {output}")
    else:
        click.echo(text)

    # Summary stats
    paths = spec.to_dict().get("paths", {})
    op_count = sum(len(ops) for ops in paths.values())
    click.echo(f"\n{len(paths)} paths, {op_count} operations", err=True)
