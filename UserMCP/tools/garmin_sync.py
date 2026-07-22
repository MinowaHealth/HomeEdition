"""
sync_garmin_data — queue a one-week Garmin sync via `POST /api/v1/garmin/sync`.

Pure proxy: all sync logic (job row, worker queue, date math) lives in UserApp.
The MCP path sends source=mcp, which makes the sync quiet (no /all-logs entry)
and returns the watch's last upload time to Garmin Connect. If that upload is
more than 15 minutes old — or unknown — the envelope carries a device_alert
the model must relay: our server can only pull what the watch has uploaded.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from mcp.types import Tool

from tools._envelope import build_envelope

logger = logging.getLogger(__name__)

DEVICE_STALE_SECONDS = 15 * 60


def schema() -> Tool:
    return Tool(
        name="sync_garmin_data",
        description=(
            "Queue a background sync of the past week of Garmin wearable data "
            "(sleep, heart rate, stress, respiration, daily summaries). Use "
            "this when a wearable tool shows Garmin data is stale or missing "
            "for recent days, then re-query after a minute or two. If the "
            "response contains device_alert, relay it to the user verbatim — "
            "it means their watch itself hasn't uploaded to Garmin Connect "
            "recently, so the sync cannot bring in newer data."
        ),
        inputSchema={"type": "object", "properties": {}},
    )


def _device_alert(device_last_sync: Optional[str]) -> Optional[str]:
    """Alert text when the watch's last Garmin Connect upload is stale/unknown."""
    if not device_last_sync:
        return (
            "Could not determine when your watch last uploaded to Garmin "
            "Connect. If recent data looks missing, open the Garmin Connect "
            "app to sync your watch."
        )
    try:
        ts = datetime.fromisoformat(device_last_sync)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age <= DEVICE_STALE_SECONDS:
        return None
    minutes = int(age // 60)
    return (
        f"Your watch last uploaded to Garmin Connect {minutes} minutes ago "
        f"({device_last_sync}). Data newer than that isn't available to sync — "
        "open the Garmin Connect app on your phone to sync your watch first."
    )


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    try:
        job = await client.call_api(
            "/garmin/sync", method="POST",
            json={"range": "week", "source": "mcp"},
        )
    except Exception as exc:
        logger.error(f"sync_garmin_data: {exc}")
        return build_envelope({"queued": False, "error": str(exc)})

    job = job if isinstance(job, dict) else {}
    device_last_sync = job.get("device_last_sync")
    return build_envelope({
        "queued": True,
        "job_id": job.get("job_id"),
        "sync_from": job.get("sync_from"),
        "sync_to": job.get("sync_to"),
        "device_last_sync": device_last_sync,
        "device_alert": _device_alert(device_last_sync),
        "message": "One-week Garmin sync queued. Data typically lands within "
                   "a couple of minutes — re-run the wearable query then.",
    })
