"""
get_my_profile — identity, timezone, dietary settings.

Replaces the old health_data "profile" passthrough by pulling from
identity-shaped endpoints in parallel and presenting one envelope. No
scheduled or vitals data here — just the "who am I and how should the
assistant speak to me?" context.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope
from tools._shape import as_list
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)


def schema() -> Tool:
    return Tool(
        name="get_my_profile",
        description=(
            "Return the user's identity profile: display name, timezone, "
            "and dietary settings. Use this for onboarding and "
            "personalizing responses."
        ),
        inputSchema={"type": "object", "properties": {}},
    )


async def _safe_call(client: Any, path: str, **kwargs) -> Dict[str, Any]:
    try:
        resp = await client.call_api(path, **kwargs)
        return resp if isinstance(resp, dict) else {"_raw": resp}
    except Exception as exc:
        logger.warning(f"profile: {path} failed: {exc}")
        return {"_error": str(exc)}


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    # Home Edition: no /providers endpoint (provider/delegation features
    # removed) — profile is identity + dietary only.
    session_r, dietary_r, sources = await asyncio.gather(
        _safe_call(client, "/session", method="GET"),
        _safe_call(client, "/dietary-settings", method="GET"),
        fetch_sources(client),
    )

    gaps = []
    for label, r in (
        ("session", session_r),
        ("dietary_settings", dietary_r),
    ):
        if isinstance(r, dict) and r.get("_error"):
            gaps.append({"source": label, "reason": r["_error"]})

    profile_block = {
        "user_id": session_r.get("user_id"),
        "tenant_id": session_r.get("tenant_id"),
        "username": session_r.get("username"),
        "display_name": session_r.get("display_name") or session_r.get("username"),
        "home_timezone": session_r.get("home_timezone"),
        "biological_sex": session_r.get("biological_sex"),
        "gender_identity": session_r.get("gender_identity"),
        "pronouns": session_r.get("pronouns"),
        "track_energy_spoons": session_r.get("track_energy_spoons"),
    }

    # /dietary-settings typically returns a list; we surface the most recent.
    dietary_list = as_list(
        dietary_r.get("settings") or dietary_r.get("results") or [],
        where="profile.dietary_list",
    )
    dietary_latest = dietary_list[0] if dietary_list else None

    data = {
        "profile": profile_block,
        "dietary_settings": dietary_latest,
    }

    row_count = (
        (1 if profile_block.get("user_id") else 0)
        + (1 if dietary_latest else 0)
    )
    coverage = {
        "counts": {
            "rows": row_count,
            "sources_represented": ["manual"],
        },
        "gaps": gaps,
        "truncated": False,
    }

    return build_envelope(data, coverage=coverage, sources=sources)
