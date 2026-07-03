"""
MCP resources exposed by UserMCP.

Resources are immutable bundles of prose/config that Claude reads once per
conversation to frame its behavior. Unlike tools, they're not called
repeatedly — they're read up-front.

Three resources are published here:

  usermcp://profile         — runtime user profile snapshot
  usermcp://disclaimers     — long-form medical/data disclaimers
  usermcp://data-sources    — table-by-table "what lives here" index
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from mcp.types import Resource

from tools._shape import as_dict, as_list
from tools._sources import fetch_sources


# Long-form disclaimers. Kept here as a constant so updating them is a
# single-file edit reviewed like code — the short form in _envelope.py
# must also be updated if the first sentence changes.
DISCLAIMERS_MARKDOWN = """\
# Minowa — Disclaimers

## Not medical advice
This assistant summarizes the data the user has entered or synced into
Minowa. It is **not** a substitute for evaluation, diagnosis, or
treatment by a licensed clinician. Any actionable health decision —
starting, stopping, or changing medication; seeking emergency care —
must be made by the user in consultation with their provider.

## Data source reliability
- **Manual entries** reflect what the user typed; they may be incomplete,
  mis-timed, or mis-categorized.
- **HealthKit** is a best-effort mirror of Apple Health. Some data types
  (e.g. lab results) are user-entered through HealthKit and inherit the
  reliability of whoever entered them.
- **Garmin** reflects what the watch recorded; it may miss events the
  watch wasn't worn through.

## Reference ranges
Reference ranges for lab values are lab-specific and often population-
specific. A value flagged "high" by one lab's reference range may be
normal at another lab.

## Privacy
This MCP server runs on the household's own appliance; your health data
is not sent to any Minowa cloud service. Note that whatever a tool returns
is passed to the connected MCP client (e.g. Claude Desktop) and its model
provider — so treat a conversation like any other cloud AI chat, and avoid
pasting other people's medical information or unrelated secrets.
"""


DATA_SOURCES_MARKDOWN = """\
# Minowa — Data Source Map

Each tool response includes a `sources` block. This document explains
what each source means and what tables its data ends up in.

## manual
Anything the user typed into the web or mobile UI. Lands in:
- `health_inputs`, `stacks`, `timeframes`, `reminders`
- `health_input_log` (med/supplement logs)
- `health_blood_pressure_readings`, `health_metrics`, `health_observations`
- `health_conditions`, `health_allergies`, `health_family_history`,
  `health_surgical_history`, `health_social_history`, `health_vaccinations`
- `health_food_itemsv2`, `meals`, `meal_items`, `health_food_logv2`
- `documents`, `document_annotations`
- `dietary_settings`, `feedback`

## healthkit
Apple HealthKit export, either live sync from the iOS app or a
one-shot Health Export file import. Lands in `hkit_*` tables first
(preserving HealthKit's native shape), then projected into `health_*`.
- Raw: `hkit_records`, `hkit_workouts`, `hkit_activity_summaries`,
  `hkit_lab_observations`, `hkit_clinical_records`, `hkit_medications`,
  `hkit_allergies`, `hkit_immunizations`
- Derived: `health_metrics`, `health_blood_pressure_readings`,
  `health_conditions`, `health_allergies`, etc.

## garmin
Garmin Connect OAuth sync (requires Garmin account linkage). Lands in
`garm_*` tables, high-volume time series kept at their native cadence.
- `garm_daily_summ` — one row per day
- `garm_hr`, `garm_rr`, `garm_stress` — minute-to-second cadence
- `garm_sleep`, `garm_sleep_events`

## Freshness
Each `sources` entry carries a `last_sync` field. Interpret:
- `null` or missing: source never connected or never synced
- older than 24h for garmin/healthkit: the user may not have opened
  their watch/phone recently
"""


def _resource_list() -> List[Resource]:
    return [
        Resource(
            uri="usermcp://profile",
            name="User profile snapshot",
            description=(
                "Live user profile: identity, timezone, connected data sources "
                "with last-sync timestamps, active scheduled inputs. Useful at "
                "conversation start."
            ),
            mimeType="application/json",
        ),
        Resource(
            uri="usermcp://disclaimers",
            name="Medical + data disclaimers",
            description=(
                "Long-form disclaimers covering medical-advice scope, data "
                "source reliability, reference-range caveats, and privacy."
            ),
            mimeType="text/markdown",
        ),
        Resource(
            uri="usermcp://data-sources",
            name="Data source map",
            description=(
                "Table-by-table index of what lives in each data source "
                "(manual, HealthKit, Garmin) and how sources project into "
                "the `health_*` derived layer."
            ),
            mimeType="text/markdown",
        ),
    ]


async def _profile_snapshot(client: Any) -> Dict[str, Any]:
    """Fetch enough context for usermcp://profile in one shot.

    If the API is unavailable we return a best-effort shape with empty
    fields — the snapshot is never load-bearing for safety.
    """
    try:
        session = await client.call_api("/session", method="GET")
    except Exception:
        session = {}
    try:
        sources = await fetch_sources(client)
    except Exception:
        sources = []
    try:
        inputs = await client.call_api(
            "/health-inputs", method="GET", params={"is_active": "true"}
        )
        if isinstance(inputs, list):
            input_rows = inputs
        else:
            inputs_d = as_dict(inputs, where="resources.health-inputs")
            input_rows = inputs_d.get("inputs") or inputs_d.get("entries") or []
    except Exception:
        input_rows = []

    session_d = as_dict(session, where="resources.session")
    return {
        "profile": {
            "user_id": session_d.get("user_id"),
            "display_name": session_d.get("display_name") or session_d.get("username"),
            "home_timezone": session_d.get("home_timezone"),
            "pronouns": session_d.get("pronouns"),
        },
        "sources": sources,
        "active_inputs": [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "input_type": r.get("input_type"),
                "doses_per_day": r.get("doses_per_day"),
            }
            for r in input_rows
        ],
    }


async def _read_resource(uri: str, client: Any) -> str:
    """Return the payload for a given resource URI."""
    if uri == "usermcp://profile":
        return json.dumps(await _profile_snapshot(client), indent=2, default=str)
    if uri == "usermcp://disclaimers":
        return DISCLAIMERS_MARKDOWN
    if uri == "usermcp://data-sources":
        return DATA_SOURCES_MARKDOWN
    raise ValueError(f"Unknown resource: {uri}")


def all_resources() -> List[Resource]:
    """Entry point for `mcp_server.list_resources`."""
    return _resource_list()


async def read(uri: str, client: Any) -> str:
    """Entry point for `mcp_server.read_resource`."""
    return await _read_resource(uri, client)
