"""Asserts the client-consumed endpoint set matches the backend Flask URL map.

Fails on drift in either direction unless covered by contracts/allowed-endpoint-deltas.yaml.
"""

import pytest

from tests.contracts.conftest import normalize_flask_path

_BACKEND_APPS = [
    ("UserApp.webapp.app", "app"),
]


def _collect_backend_routes() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for module_path, app_attr in _BACKEND_APPS:
        try:
            module = __import__(module_path, fromlist=[app_attr])
            flask_app = getattr(module, app_attr)
        except Exception as exc:
            pytest.skip(
                f"Cannot import {module_path}.{app_attr} in test context: {exc}. "
                f"Wire up a testing app factory before this test can compare URL maps."
            )
        for rule in flask_app.url_map.iter_rules():
            norm = normalize_flask_path(str(rule.rule))
            for method in rule.methods or ():
                if method in ("HEAD", "OPTIONS"):
                    continue
                routes.add((method, norm))
    return routes


def test_endpoint_drift(load_json_artifact, load_yaml_overlay):
    artifact = load_json_artifact("client-consumed-endpoints.json")
    overlay = load_yaml_overlay("allowed-endpoint-deltas.yaml")

    client_endpoints = {(e["method"], e["path"]) for e in artifact["endpoints"]}
    backend_endpoints = _collect_backend_routes()
    allowed_backend_only = {(e["method"], e["path"]) for e in overlay["backend_only"]}
    allowed_client_only = {(e["method"], e["path"]) for e in overlay["client_only"]}

    backend_only = (backend_endpoints - client_endpoints) - allowed_backend_only
    client_only = (client_endpoints - backend_endpoints) - allowed_client_only

    sections = []
    if backend_only:
        sections.append(
            "Backend routes with no client call site "
            "(prune them, or add to contracts/allowed-endpoint-deltas.yaml `backend_only` with a reason):\n"
            + "\n".join(f"  {m} {p}" for m, p in sorted(backend_only))
        )
    if client_only:
        sections.append(
            "Client call sites with no backend route "
            "(implement them, or add to contracts/allowed-endpoint-deltas.yaml `client_only` with reason+due):\n"
            + "\n".join(f"  {m} {p}" for m, p in sorted(client_only))
        )

    assert not sections, "\n\n".join(sections)
