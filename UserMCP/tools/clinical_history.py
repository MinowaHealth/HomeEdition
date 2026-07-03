"""
get_my_clinical_history — conditions, allergies, family/surgical history, vaccinations.

This is the "what's in my chart" tool. Returns each category as its own
list so a caller can focus without scanning the whole payload, and
surfaces any potential medication-allergy overlaps in `alerts` so the
LLM flags contraindications without being asked.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from mcp.types import Tool

from tools._envelope import build_envelope
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)


def schema() -> Tool:
    return Tool(
        name="get_my_clinical_history",
        description=(
            "Return the user's clinical history: conditions, allergies, "
            "family history, surgical history, and vaccinations. Also "
            "returns `alerts` flagging any active medications that match "
            "known allergies (by allergen name)."
        ),
        inputSchema={"type": "object", "properties": {}},
    )


async def _safe_call(client: Any, path: str, **kwargs) -> Any:
    try:
        return await client.call_api(path, **kwargs)
    except Exception as exc:
        logger.warning(f"clinical_history: {path} failed: {exc}")
        return {"_error": str(exc)}


def _extract_list(resp: Any, *keys: str) -> list:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for k in keys:
            v = resp.get(k)
            if isinstance(v, list):
                return v
    return []


def _med_allergy_alerts(
    active_meds: List[Dict[str, Any]],
    allergies: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return simple case-insensitive substring match alerts.

    This is deliberately a conservative flag — it surfaces *possible*
    overlap for the LLM to caveat to the user, not a pharmacological
    contraindication check. The user's provider is still the authority.
    """
    alerts = []
    allergen_names = [
        (a.get("allergen") or a.get("name") or "").strip().lower()
        for a in allergies
    ]
    allergen_names = [a for a in allergen_names if a]
    for med in active_meds:
        med_name = (med.get("name") or "").strip().lower()
        if not med_name:
            continue
        for allergen in allergen_names:
            if allergen in med_name or med_name in allergen:
                alerts.append({
                    "kind": "allergy_med_overlap",
                    "medication": med.get("name"),
                    "medication_id": med.get("id"),
                    "allergen": allergen,
                    "severity": "possible",
                    "why": (
                        f"Active medication '{med.get('name')}' name overlaps "
                        f"with reported allergen '{allergen}'. Confirm with the "
                        f"user's provider before assuming these are unrelated."
                    ),
                })
                break
    return alerts


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    (
        conditions_r, allergies_r, family_r,
        surgical_r, vaccinations_r, social_r,
        inputs_r, sources,
    ) = await asyncio.gather(
        _safe_call(client, "/conditions", method="GET"),
        _safe_call(client, "/allergies", method="GET"),
        _safe_call(client, "/family-history", method="GET"),
        _safe_call(client, "/surgical-history", method="GET"),
        _safe_call(client, "/vaccinations", method="GET"),
        _safe_call(client, "/social-history", method="GET"),
        _safe_call(client, "/health-inputs", method="GET", params={"is_active": "true"}),
        fetch_sources(client),
    )

    gaps = []
    for label, r in (
        ("conditions", conditions_r),
        ("allergies", allergies_r),
        ("family_history", family_r),
        ("surgical_history", surgical_r),
        ("vaccinations", vaccinations_r),
        ("social_history", social_r),
    ):
        if isinstance(r, dict) and r.get("_error"):
            gaps.append({"source": label, "reason": r["_error"]})

    conditions = _extract_list(conditions_r, "conditions", "entries", "results")
    allergies = _extract_list(allergies_r, "allergies", "entries", "results")
    family = _extract_list(family_r, "family_history", "entries", "results")
    surgical = _extract_list(surgical_r, "surgical_history", "entries", "results")
    vaccinations = _extract_list(vaccinations_r, "vaccinations", "entries", "results")
    social = _extract_list(social_r, "social_history", "entries", "results")

    active_meds = [
        i for i in _extract_list(inputs_r, "inputs", "entries", "results")
        if i.get("input_type") in ("medication", "supplement")
    ]

    alerts = _med_allergy_alerts(active_meds, allergies)

    data = {
        "conditions": conditions,
        "allergies": allergies,
        "family_history": family,
        "surgical_history": surgical,
        "vaccinations": vaccinations,
        "social_history": social,
        "alerts": alerts,
    }

    counts = {
        "rows": (
            len(conditions) + len(allergies) + len(family)
            + len(surgical) + len(vaccinations) + len(social)
        ),
        "conditions": len(conditions),
        "allergies": len(allergies),
        "family_history": len(family),
        "surgical_history": len(surgical),
        "vaccinations": len(vaccinations),
        "social_history": len(social),
        "alerts": len(alerts),
        "sources_represented": ["manual", "healthkit"] if conditions or allergies else ["manual"],
    }

    next_actions = []
    if alerts:
        next_actions.append({
            "tool": "get_my_active_regimen",
            "args": {},
            "why": (
                f"{len(alerts)} potential allergen/medication overlap(s) flagged — "
                "review with the user."
            ),
        })

    coverage = {"counts": counts, "gaps": gaps, "truncated": False}
    return build_envelope(data, coverage=coverage, sources=sources, next_actions=next_actions)
