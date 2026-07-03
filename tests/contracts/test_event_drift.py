"""Asserts the client-emitted analytics event set matches backend-declared events.

Until the backend ships a structured server-side event registry, this test
only validates artifact shape + applies the client-only overlay. The backend-vs-
client comparison activates once `_load_backend_declared_events` returns a real set.
"""

import pytest

from tests.contracts.conftest import CONTRACTS_DIR


def _load_backend_declared_events() -> set[str] | None:
    """Return the set of event names declared by the backend, or None if no
    structured registry exists yet.

    When the backend gains a structured event registry (JSON/YAML), wire this
    function to read it.
    """
    return None


def test_event_artifact_shape(load_json_artifact):
    artifact = load_json_artifact("client-emitted-events.json")
    assert "events" in artifact, "artifact missing top-level `events` array"
    for event in artifact["events"]:
        assert "name" in event, f"event missing `name`: {event!r}"
        assert isinstance(event.get("properties", []), list), (
            f"event `properties` must be a list: {event!r}"
        )


def test_event_drift(load_json_artifact, load_yaml_overlay):
    artifact = load_json_artifact("client-emitted-events.json")
    overlay = load_yaml_overlay("allowed-event-deltas.yaml")

    client_events = {e["name"] for e in artifact["events"]}
    backend_events = _load_backend_declared_events()
    if backend_events is None:
        pytest.skip(
            "Backend event registry not yet structured. "
            "Add a structured event registry and wire "
            "_load_backend_declared_events() in this file."
        )

    allowed_backend_only = {e["name"] for e in overlay["backend_only"]}
    allowed_client_only = {e["name"] for e in overlay["client_only"]}

    backend_only = (backend_events - client_events) - allowed_backend_only
    client_only = (client_events - backend_events) - allowed_client_only

    sections = []
    if backend_only:
        sections.append(
            "Backend-declared events with no client emitter "
            "(add to allowed-event-deltas.yaml `backend_only` if server-fired):\n"
            + "\n".join(f"  {n}" for n in sorted(backend_only))
        )
    if client_only:
        sections.append(
            "Client-emitted events with no backend declaration "
            "(declare server-side, or add to allowed-event-deltas.yaml `client_only`):\n"
            + "\n".join(f"  {n}" for n in sorted(client_only))
        )

    assert not sections, "\n\n".join(sections)
