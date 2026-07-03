"""Fixtures and helpers for cross-repo contract drift tests.

Consumes artifacts mirrored from the HealthAI mobile client:
  contracts/client-consumed-endpoints.json
  contracts/client-emitted-events.json

Plus locally-edited overlays:
  contracts/allowed-endpoint-deltas.yaml
  contracts/allowed-event-deltas.yaml
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "contracts"

_FLASK_CONVERTER_RE = re.compile(r"<(?:(?P<conv>[^:>]+):)?(?P<name>[^>]+)>")


def normalize_flask_path(path: str) -> str:
    """Convert Flask url_map syntax to {param} form to match the client extractor."""
    return _FLASK_CONVERTER_RE.sub(lambda m: "{" + m.group("name") + "}", path)


@pytest.fixture
def load_json_artifact():
    def _load(name: str) -> dict:
        path = CONTRACTS_DIR / name
        if not path.exists():
            pytest.skip(
                f"Client artifact not yet committed: contracts/{name}. "
                f"Waiting on the HealthAI extractor to produce it."
            )
        return json.loads(path.read_text())

    return _load


@pytest.fixture
def load_yaml_overlay():
    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML not installed; cannot load allowed-delta overlays")

    def _load(name: str) -> dict:
        path = CONTRACTS_DIR / name
        if not path.exists():
            return {"backend_only": [], "client_only": []}
        data = yaml.safe_load(path.read_text())
        if not data:
            return {"backend_only": [], "client_only": []}
        data.setdefault("backend_only", [])
        data.setdefault("client_only", [])
        return data

    return _load