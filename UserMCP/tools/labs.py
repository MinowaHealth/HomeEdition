"""
get_lab_history — latest + trend-grouped lab results.

Replaces the flat `get_lab_results` tool: instead of one-row-per-test, this
groups historical values under each test so the LLM can state "LDL has been
stable at 105 ± 5" rather than needing to diff rows itself. The underlying
endpoint still returns the latest-per-test; when ProviderApp exposes a
full-history endpoint we'll switch over — today we flag `trend_available:
false` so the caller knows not to promise trends from a single value.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from mcp.types import Tool

from tools._envelope import build_envelope
from tools._shape import as_dict, as_list
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)


def schema() -> Tool:
    return Tool(
        name="get_lab_history",
        description=(
            "Return the user's latest lab results, grouped by test. Each group "
            "includes the latest value, unit, reference range, and interpretation "
            "flag. Historical trend data is not yet available — `trend_available` "
            "on each group says whether multi-point trends can be computed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "loinc_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional LOINC codes to filter to.",
                },
            },
        },
    )


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    loinc_filter = set(arguments.get("loinc_codes") or [])

    try:
        resp = await client.call_api("/lab-results", method="GET")
    except Exception as exc:
        logger.error(f"labs: {exc}")
        return build_envelope(
            {"groups": []},
            coverage={
                "counts": {"rows": 0, "sources_represented": []},
                "gaps": [{"reason": str(exc)}],
                "truncated": False,
            },
            sources=await fetch_sources(client),
        )

    rows: List[Dict[str, Any]] = as_list(
        as_dict(resp, where="labs.resp").get("results"),
        where="labs.results",
    )

    if loinc_filter:
        rows = [r for r in rows if (r.get("loinc_code") or "") in loinc_filter]

    # One row per test from the endpoint, so each group has exactly one
    # historical sample today. When multi-history lands this reshapes
    # naturally: append rather than overwrite.
    groups = []
    undated = 0
    for r in rows:
        test_key = r.get("loinc_code") or r.get("name") or "unknown"
        date = r.get("date")
        received = r.get("received_date")
        if not date:
            undated += 1
        groups.append({
            "test": test_key,
            "name": r.get("name") or test_key,
            "loinc_code": r.get("loinc_code"),
            "latest": {
                "date": date,
                # Import-received date, present only when the clinical draw
                # date didn't survive import. Labeled distinctly so the caller
                # never presents it as the collection date (Bug 4).
                "received_date": received,
                "date_is_received_fallback": bool(received and not date),
                "value": r.get("value"),
                "unit": r.get("unit"),
                "reference_range": r.get("reference_range"),
                "interpretation": r.get("interpretation"),
            },
            "history": [],  # will populate when endpoint supports multi-history
            "trend_available": False,
        })

    sources = await fetch_sources(client)
    gaps = []
    if not groups:
        gaps.append({"reason": "no lab results recorded"})
    elif undated:
        gaps.append({"reason": (
            f"{undated} of {len(groups)} tests have no clinical draw date "
            "(not captured at import); latest.received_date shows the "
            "import date as a fallback where available"
        )})
    coverage = {
        "counts": {
            "rows": len(groups),
            "sources_represented": ["healthkit"] if groups else [],
        },
        "gaps": gaps,
        "truncated": False,
    }

    return build_envelope({"groups": groups}, coverage=coverage, sources=sources)
