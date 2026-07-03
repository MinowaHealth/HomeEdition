"""Nutrition projection: health_food_logv2 → health_metrics(metric_type='nutrition').

Plan: PilotFeedback.md § B4.

The food log table records *what* was eaten (food_item_id + servings); the
nutrition catalog (health_food_itemsv2) records the per-serving nutrient
content. Neither is what the read path queries — `analytics.py /health-query`
selects from health_metrics WHERE metric_type='nutrition'. This module is the
projector that runs synchronously after each food/meal log write to bridge
the two: it joins log×catalog, multiplies per-serving values by servings, and
writes one nutrition row per food log into health_metrics with `source_log_id`
pointing back at the log row.

Idempotent — uses NOT EXISTS via source_log_id so re-running on the same log
is a no-op. Safe to call from both /log-meal and /log-food-item write paths.

Skipped cases (returns None, no row written):
  - Freeform log entries (food_item_id IS NULL) — no catalog reference, no
    nutrition data to project. The mobile client should display these as
    "uncategorized" rather than as a nutrition gap.
  - Food items with NULL calories — incomplete catalog entries, treated the
    same way. Once the user fills in calories, the next log of that item
    will project; the prior log will need a one-off backfill (rare).

Doctrine alignment (CLAUDE.md): the food log is canonical/manual entry, not
external-source data. health_metrics is a *derived* table — projecting into
it from a manual write follows the canonical→derived rule even though the
source table isn't an `hkit_*` or `garm_*` namespace.
"""
from __future__ import annotations

import json
import logging
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)


# Per-serving nutrient columns to project. Order matters only for log readability.
_NUTRIENT_COLS = (
    'calories',
    'protein_g',
    'carbs_g',
    'fat_g',
    'fiber_g',
    'sugar_g',
    'sodium_mg',
)


def project_nutrition_for_food_log(
    conn,
    tenant_id: int,
    log_id: UUID,
    user_id: UUID,
) -> Optional[UUID]:
    """Project a single health_food_logv2 row into a health_metrics nutrition row.

    Args:
        conn: Active database connection (app-level user_id scoping; no RLS).
        tenant_id: Tenant scope for the projection.
        log_id: The health_food_logv2 row id to project from.
        user_id: The user who owns the log row (and the new metric row).

    Returns:
        UUID of the newly inserted health_metrics row, or None if the log
        is freeform (no food_item_id) or the food_item lacks nutrition data,
        or a prior projection already exists for this log_id.

    Caller is responsible for `conn.commit()` — this function does not
    commit so it can participate in the same transaction as the log INSERT.
    """
    cur = conn.cursor()
    try:
        # Idempotency: skip if we've already projected this log.
        cur.execute(
            """
            SELECT id FROM health_metrics
            WHERE tenant_id = %s AND user_id = %s AND source_log_id = %s
            LIMIT 1
            """,
            (tenant_id, user_id, log_id),
        )
        existing = cur.fetchone()
        if existing:
            logger.debug(
                "nutrition_projection: log %s already projected as metric %s; skipping",
                log_id, existing['id'],
            )
            return None

        # Fetch the log row + joined food_item nutrition. LEFT JOIN so we can
        # cleanly return None for freeform logs (food_item_id IS NULL).
        cur.execute(
            """
            SELECT fl.logged_at, fl.servings,
                   fi.name AS food_name,
                   fi.calories, fi.protein_g, fi.carbs_g, fi.fat_g,
                   fi.fiber_g, fi.sugar_g, fi.sodium_mg
            FROM health_food_logv2 fl
            LEFT JOIN health_food_itemsv2 fi
              ON fi.tenant_id = fl.tenant_id AND fi.id = fl.food_item_id
            WHERE fl.tenant_id = %s AND fl.user_id = %s AND fl.id = %s
            """,
            (tenant_id, user_id, log_id),
        )
        row = cur.fetchone()
        if not row:
            logger.warning("nutrition_projection: log %s not found", log_id)
            return None
        if row['calories'] is None:
            # Freeform log OR food item with no calorie data — skip.
            logger.debug(
                "nutrition_projection: log %s has no nutrition data (freeform or incomplete catalog); skipping",
                log_id,
            )
            return None

        servings = float(row['servings'] or 1)

        # Build the macros dict — multiply each non-null per-serving value by servings.
        macros: dict[str, object] = {'food_name': row['food_name'], 'servings': servings}
        for col in _NUTRIENT_COLS:
            per_serving = row[col]
            if per_serving is not None:
                macros[col] = float(per_serving) * servings

        total_calories = macros.get('calories', 0)

        cur.execute(
            """
            INSERT INTO health_metrics
                (tenant_id, user_id, metric_type, recorded_at,
                 value, unit, source, source_log_id, notes)
            VALUES (%s, %s, 'nutrition', %s,
                    %s, 'kcal', 'food_log', %s, %s)
            RETURNING id
            """,
            (tenant_id, user_id, row['logged_at'],
             total_calories, log_id, json.dumps(macros)),
        )
        result = cur.fetchone()
        metric_id = result['id']
        logger.info(
            "nutrition_projection: projected log %s → metric %s (calories=%s, food=%r)",
            log_id, metric_id, total_calories, row['food_name'],
        )
        return metric_id
    finally:
        cur.close()


def parse_nutrition_notes(notes: Optional[str]) -> dict[str, object]:
    """Decode the JSON macros payload stored in health_metrics.notes for nutrition rows.

    Returns an empty dict if notes is None or not valid JSON. Callers in
    the read path should treat all keys as optional — different food_items
    in the catalog have different completeness, so protein_g may be present
    on one row and missing on another within the same query result.
    """
    if not notes:
        return {}
    try:
        decoded = json.loads(notes)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    return decoded
