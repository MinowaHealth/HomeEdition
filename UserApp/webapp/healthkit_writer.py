"""
HealthKit canonical writer.

Writes HealthKit samples, workouts, activity summaries, and user
characteristics into the ``hkit_*`` tables in a HealthKit-native,
source-faithful shape.

This module is the shared backend for both the export-file importer
(``healthkit_importer.py``) and the live sync endpoint
(``/api/v1/healthkit/sync`` in ``app.py``). It exists so both ingestion
paths land data in exactly the same shape, eliminating the "two disjoint
copies of the same Apple data" problem described in
``2026-04-10-HealthKitConsistencyPlan.md``.

Design rules
------------
* The caller owns the database connection, cursor, and transaction. Every
  function here takes an already-open cursor. All scoping is explicit:
  each statement binds ``ctx.tenant_id`` / ``ctx.user_id`` from the
  ``WriterContext``. The writer never issues ``COMMIT`` or ``ROLLBACK``.
* No global state. A ``WriterContext`` instance is created per request
  and carries the tenant/user plus per-request lookup caches for
  ``hkit_record_types`` and ``hkit_sources``.
* Every upsert uses an ``ON CONFLICT DO NOTHING`` or ``DO UPDATE`` clause
  that matches the natural dedup key of the target table, so replaying
  the same payload is a no-op.
* Canonical shape wins. Type identifiers are stored as the raw
  ``HKQuantityTypeIdentifierXxx`` / ``HKCategoryTypeIdentifierXxx``
  strings — no server-side enum rewriting, no alias collapsing.
  ``resting_heart_rate`` stays ``HKQuantityTypeIdentifierRestingHeartRate``
  in ``hkit_record_types`` so it is distinguishable from instantaneous
  heart rate samples at query time.

Usage
-----
::

    from healthkit_writer import WriterContext, upsert_sample, upsert_bp_correlation

    ctx = WriterContext(tenant_id=1, user_id=user_id)

    # Inside the caller's transaction; every write scopes by ctx.user_id (no RLS):
    for sample in payload['samples']:
        upsert_sample(cur, ctx, sample, source_id=source_map.get(sample.get('source_ref')))

Not covered (deferred)
----------------------
* Projection from ``hkit_*`` into ``health_*`` tables. That is a
  separate writer responsibility that will land when Phase C of the
  HealthKit Consistency Plan is implemented. This module writes only
  the canonical ``hkit_*`` layer.
* Clinical record ingestion via ``HKClinicalRecord``. The export
  importer handles that today; the sync path does not need it yet.
* Anchor-token incremental sync. Deferred until base sync is verified.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import pytz

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class WriterContext:
    """Per-request state carried through writer calls.

    Holds the tenant/user identity plus per-request lookup caches so
    repeated ``get_or_create_record_type`` / ``get_or_create_source``
    calls within one request only hit the database once per distinct
    key.

    Attributes:
        tenant_id: Integer tenant ID for the current request.
        user_id: UUID string of the user whose data is being written.
        record_type_cache: Maps ``type_identifier`` → ``hkit_record_types.id``.
        source_cache: Maps a deterministic source key tuple →
            ``hkit_sources.id``.
    """

    tenant_id: int
    user_id: str
    record_type_cache: dict[str, int] = field(default_factory=dict)
    source_cache: dict[tuple, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Lookup tables: hkit_record_types and hkit_sources
# ---------------------------------------------------------------------------


_CAMEL_BOUNDARY = re.compile(r"([A-Z])")


def _humanize_type_identifier(type_identifier: str) -> str:
    """Convert ``HKQuantityTypeIdentifierRestingHeartRate`` → ``Resting Heart Rate``."""
    stripped = type_identifier
    for prefix in (
        "HKQuantityTypeIdentifier",
        "HKCategoryTypeIdentifier",
        "HKCorrelationTypeIdentifier",
        "HKCharacteristicTypeIdentifier",
    ):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break
    return _CAMEL_BOUNDARY.sub(r" \1", stripped).strip()


def _category_for_type_identifier(type_identifier: str) -> str:
    """Return the ``hkit_record_types.category`` value for a given identifier."""
    if "Quantity" in type_identifier:
        return "quantity"
    if "Category" in type_identifier:
        return "category"
    if "Correlation" in type_identifier:
        return "correlation"
    return "unknown"


def get_or_create_record_type(cur, ctx: WriterContext, type_identifier: str) -> int:
    """Return the ``hkit_record_types.id`` for the given identifier.

    Creates a new row on first sight. Results are cached on ``ctx`` so
    repeated calls within the same request are free after the first
    lookup.

    Args:
        cur: An open database cursor (dict-row factory required).
        ctx: The active ``WriterContext``.
        type_identifier: The raw HealthKit type identifier, e.g.
            ``HKQuantityTypeIdentifierHeartRate``. Must be a non-empty
            string.

    Returns:
        The primary-key ``id`` of the ``hkit_record_types`` row for
        ``type_identifier``.

    Raises:
        ValueError: If ``type_identifier`` is empty or not a string.
    """
    if not isinstance(type_identifier, str) or not type_identifier:
        raise ValueError("type_identifier must be a non-empty string")

    cached = ctx.record_type_cache.get(type_identifier)
    if cached is not None:
        return cached

    cur.execute(
        "SELECT id FROM hkit_record_types WHERE type_identifier = %s",
        (type_identifier,),
    )
    row = cur.fetchone()
    if row:
        rt_id = row["id"]
        ctx.record_type_cache[type_identifier] = rt_id
        return rt_id

    display_name = _humanize_type_identifier(type_identifier)
    category = _category_for_type_identifier(type_identifier)
    cur.execute(
        """
        INSERT INTO hkit_record_types (type_identifier, category, display_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (type_identifier) DO UPDATE
            SET type_identifier = EXCLUDED.type_identifier
        RETURNING id
        """,
        (type_identifier, category, display_name),
    )
    rt_id = cur.fetchone()["id"]
    ctx.record_type_cache[type_identifier] = rt_id
    return rt_id


def _source_cache_key(source_info: dict) -> tuple:
    """Build a deterministic lookup key for a source descriptor."""
    return (
        source_info.get("source_name") or "",
        source_info.get("source_bundle_id") or "",
        source_info.get("source_version") or "",
        source_info.get("device_name") or "",
        source_info.get("device_model") or "",
    )


def get_or_create_source(cur, ctx: WriterContext, source_info: dict) -> int:
    """Return the ``hkit_sources.id`` for a full source/device descriptor.

    Unlike the existing ``healthkit_importer.get_or_create_source`` —
    which only takes ``source_name`` and ``source_version`` — this
    function preserves the full provenance HealthKit carries on each
    sample: the application bundle ID, the source version, the device
    name, and the device model. The ``UNIQUE (tenant_id, user_id,
    source_bundle_id)`` constraint on ``hkit_sources`` is used as the
    dedup key when ``source_bundle_id`` is present; otherwise we fall
    back to a ``source_name`` match.

    Args:
        cur: An open database cursor (dict-row factory required).
        ctx: The active ``WriterContext``.
        source_info: Dict with keys ``source_name`` (required),
            ``source_bundle_id``, ``source_version``, ``device_name``,
            ``device_model``.

    Returns:
        The primary-key ``id`` of the ``hkit_sources`` row for this
        (tenant, user, source) combination.

    Raises:
        ValueError: If ``source_info`` is not a dict or has no
            ``source_name``.
    """
    if not isinstance(source_info, dict):
        raise ValueError("source_info must be a dict")
    source_name = source_info.get("source_name")
    if not source_name:
        raise ValueError("source_info.source_name is required")

    cache_key = (ctx.tenant_id, ctx.user_id) + _source_cache_key(source_info)
    cached = ctx.source_cache.get(cache_key)
    if cached is not None:
        return cached

    source_bundle_id = source_info.get("source_bundle_id")
    source_version = source_info.get("source_version")
    device_name = source_info.get("device_name")
    device_model = source_info.get("device_model")

    # Prefer a dedup lookup on (tenant_id, user_id, source_bundle_id) which
    # is the natural UNIQUE constraint on hkit_sources. If no bundle ID is
    # given, fall back to matching on source_name + device_model so multiple
    # devices from the same vendor (e.g. two Omron cuffs) don't collide.
    if source_bundle_id:
        cur.execute(
            """
            SELECT id FROM hkit_sources
            WHERE tenant_id = %s AND user_id = %s AND source_bundle_id = %s
            """,
            (ctx.tenant_id, ctx.user_id, source_bundle_id),
        )
    else:
        cur.execute(
            """
            SELECT id FROM hkit_sources
            WHERE tenant_id = %s AND user_id = %s
              AND source_name = %s
              AND COALESCE(device_model, '') = COALESCE(%s, '')
            """,
            (ctx.tenant_id, ctx.user_id, source_name, device_model),
        )
    row = cur.fetchone()
    if row:
        src_id = row["id"]
        ctx.source_cache[cache_key] = src_id
        return src_id

    cur.execute(
        """
        INSERT INTO hkit_sources
            (tenant_id, user_id, source_name, source_bundle_id,
             source_version, device_name, device_model)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            ctx.tenant_id,
            ctx.user_id,
            source_name,
            source_bundle_id,
            source_version,
            device_name,
            device_model,
        ),
    )
    src_id = cur.fetchone()["id"]
    ctx.source_cache[cache_key] = src_id
    return src_id


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def _parse_iso_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string into a timezone-aware UTC datetime.

    Accepts:
        * ``datetime`` — returned as-is if timezone-aware, else marked UTC.
        * ``str`` — parsed via ``datetime.fromisoformat`` with a ``Z``
          suffix handled as UTC.
        * ``None`` — returned as ``None``.

    Returns:
        A timezone-aware ``datetime`` in UTC, or ``None`` if the input
        was ``None`` or could not be parsed.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return pytz.utc.localize(value)
        return value.astimezone(pytz.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # fromisoformat in Python 3.11+ handles trailing Z, but we normalize
    # anyway for forward compat with older interpreters.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.warning("healthkit_writer: unparseable timestamp %r", value)
        return None
    if parsed.tzinfo is None:
        return pytz.utc.localize(parsed)
    return parsed.astimezone(pytz.utc)


# ---------------------------------------------------------------------------
# Upsert functions
# ---------------------------------------------------------------------------


def upsert_characteristics(cur, ctx: WriterContext, characteristics: dict) -> None:
    """Upsert the user's HealthKit characteristics into ``hkit_user_profile``.

    These are the immutable (or very-slowly-changing) user facts Apple
    exposes: date of birth, biological sex, blood type, Fitzpatrick
    skin type, wheelchair use. Missing keys are stored as ``NULL``;
    passing an empty dict is a no-op.

    Args:
        cur: An open database cursor.
        ctx: The active ``WriterContext``.
        characteristics: Dict with any subset of:
            ``date_of_birth`` (YYYY-MM-DD string or date),
            ``biological_sex``,
            ``blood_type``,
            ``fitzpatrick_skin_type``,
            ``wheelchair_use`` (bool).

    Raises:
        ValueError: If ``characteristics`` is not a dict.
    """
    if not isinstance(characteristics, dict):
        raise ValueError("characteristics must be a dict")
    if not characteristics:
        return

    dob = characteristics.get("date_of_birth")
    sex = characteristics.get("biological_sex")
    blood_type = characteristics.get("blood_type")
    skin = characteristics.get("fitzpatrick_skin_type")
    wheelchair = characteristics.get("wheelchair_use")
    if wheelchair is not None and not isinstance(wheelchair, bool):
        wheelchair = bool(wheelchair)

    cur.execute(
        """
        INSERT INTO hkit_user_profile
            (tenant_id, user_id, date_of_birth, biological_sex,
             blood_type, fitzpatrick_skin_type, wheelchair_use, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (tenant_id, user_id) DO UPDATE SET
            date_of_birth         = COALESCE(EXCLUDED.date_of_birth,         hkit_user_profile.date_of_birth),
            biological_sex        = COALESCE(EXCLUDED.biological_sex,        hkit_user_profile.biological_sex),
            blood_type            = COALESCE(EXCLUDED.blood_type,            hkit_user_profile.blood_type),
            fitzpatrick_skin_type = COALESCE(EXCLUDED.fitzpatrick_skin_type, hkit_user_profile.fitzpatrick_skin_type),
            wheelchair_use        = COALESCE(EXCLUDED.wheelchair_use,        hkit_user_profile.wheelchair_use),
            updated_at            = now()
        """,
        (ctx.tenant_id, ctx.user_id, dob, sex, blood_type, skin, wheelchair),
    )


def upsert_activity_summary(cur, ctx: WriterContext, summary: dict) -> None:
    """Upsert a single daily activity-ring summary into ``hkit_activity_summaries``.

    Idempotent on ``(tenant_id, user_id, date)``. Replaying the same
    summary updates any fields that changed.

    Args:
        cur: An open database cursor.
        ctx: The active ``WriterContext``.
        summary: Dict with keys ``date`` (YYYY-MM-DD, required),
            ``active_energy_burned``, ``active_energy_burned_goal``,
            ``exercise_time``, ``exercise_time_goal``,
            ``stand_hours``, ``stand_hours_goal``,
            ``move_time``, ``move_time_goal``. All fields except
            ``date`` are optional.

    Raises:
        ValueError: If ``summary`` is not a dict or has no ``date``.
    """
    if not isinstance(summary, dict):
        raise ValueError("summary must be a dict")
    date_val = summary.get("date")
    if not date_val:
        raise ValueError("activity_summary.date is required")

    cur.execute(
        """
        INSERT INTO hkit_activity_summaries
            (tenant_id, user_id, date,
             active_energy_burned, active_energy_burned_goal,
             exercise_time, exercise_time_goal,
             stand_hours, stand_hours_goal,
             move_time, move_time_goal)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, user_id, date) DO UPDATE SET
            active_energy_burned      = EXCLUDED.active_energy_burned,
            active_energy_burned_goal = EXCLUDED.active_energy_burned_goal,
            exercise_time             = EXCLUDED.exercise_time,
            exercise_time_goal        = EXCLUDED.exercise_time_goal,
            stand_hours               = EXCLUDED.stand_hours,
            stand_hours_goal          = EXCLUDED.stand_hours_goal,
            move_time                 = EXCLUDED.move_time,
            move_time_goal            = EXCLUDED.move_time_goal
        """,
        (
            ctx.tenant_id,
            ctx.user_id,
            date_val,
            summary.get("active_energy_burned"),
            summary.get("active_energy_burned_goal"),
            summary.get("exercise_time"),
            summary.get("exercise_time_goal"),
            summary.get("stand_hours"),
            summary.get("stand_hours_goal"),
            summary.get("move_time"),
            summary.get("move_time_goal"),
        ),
    )


def upsert_workout(
    cur,
    ctx: WriterContext,
    workout: dict,
    source_id: Optional[int] = None,
) -> None:
    """Upsert a single HealthKit workout into ``hkit_workouts``.

    The workout route (if present) and any workout events are stashed
    inside the ``metadata`` jsonb column under the ``route`` and
    ``events`` keys. A dedicated ``hkit_workout_routes`` table is
    deferred until route-heavy query patterns emerge (per D6 of the
    HealthKit Consistency Plan).

    Args:
        cur: An open database cursor.
        ctx: The active ``WriterContext``.
        workout: Dict with keys ``workout_type`` (required),
            ``start``, ``end``, ``duration_seconds``, ``total_distance``,
            ``total_energy_burned``, ``metadata``, ``route``, ``events``.
        source_id: Optional ``hkit_sources.id`` produced by
            ``get_or_create_source``. May be ``None`` if the client did
            not provide a source reference.

    Raises:
        ValueError: If ``workout`` is not a dict or has no
            ``workout_type``.
    """
    if not isinstance(workout, dict):
        raise ValueError("workout must be a dict")
    workout_type = workout.get("workout_type")
    if not workout_type:
        raise ValueError("workout.workout_type is required")

    start = _parse_iso_timestamp(workout.get("start"))
    end = _parse_iso_timestamp(workout.get("end"))

    metadata = dict(workout.get("metadata") or {})
    route = workout.get("route")
    if isinstance(route, list) and route:
        metadata["route"] = route
    events = workout.get("events")
    if isinstance(events, list) and events:
        metadata["events"] = events
    # Preserve the unit information on distance/energy so we don't lose it.
    if workout.get("total_distance_unit"):
        metadata.setdefault("total_distance_unit", workout["total_distance_unit"])
    if workout.get("total_energy_burned_unit"):
        metadata.setdefault("total_energy_burned_unit", workout["total_energy_burned_unit"])

    cur.execute(
        """
        INSERT INTO hkit_workouts
            (tenant_id, user_id, workout_type, source_id,
             start_date, end_date, duration_seconds,
             total_distance, total_energy_burned, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            ctx.tenant_id,
            ctx.user_id,
            workout_type,
            source_id,
            start,
            end,
            workout.get("duration_seconds"),
            workout.get("total_distance"),
            workout.get("total_energy_burned"),
            json.dumps(metadata) if metadata else None,
        ),
    )


def _coerce_numeric(value: Any) -> Optional[float]:
    """Coerce an incoming value to ``float``, returning ``None`` on failure."""
    if value is None:
        return None
    if isinstance(value, bool):
        # bool is a subclass of int — refuse to treat it as a measurement.
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def upsert_sample(
    cur,
    ctx: WriterContext,
    sample: dict,
    source_id: Optional[int] = None,
) -> bool:
    """Upsert a single ``HKQuantitySample`` or ``HKCategorySample`` into ``hkit_records``.

    Dedup is on the unique index
    ``(tenant_id, user_id, record_type_id, source_id, start_date, end_date)``,
    so replaying the same sample is a no-op.

    Args:
        cur: An open database cursor.
        ctx: The active ``WriterContext``.
        sample: Dict with keys ``type_identifier`` (required),
            ``value`` (required, numeric or numeric string),
            ``unit``, ``start``, ``end``, ``metadata``.
        source_id: Optional ``hkit_sources.id``.

    Returns:
        ``True`` if the sample was accepted for write, ``False`` if it
        was rejected (missing type identifier, unparseable value, etc.).
        A return of ``True`` does not imply a new row was inserted —
        the unique index may have caused a no-op on replay.
    """
    if not isinstance(sample, dict):
        return False
    type_identifier = sample.get("type_identifier")
    if not isinstance(type_identifier, str) or not type_identifier:
        return False

    value = _coerce_numeric(sample.get("value"))
    if value is None:
        return False

    start = _parse_iso_timestamp(sample.get("start"))
    if start is None:
        return False
    end = _parse_iso_timestamp(sample.get("end")) or start

    unit = sample.get("unit")
    metadata = sample.get("metadata")
    metadata_json = json.dumps(metadata) if isinstance(metadata, dict) and metadata else None

    record_type_id = get_or_create_record_type(cur, ctx, type_identifier)

    cur.execute(
        """
        INSERT INTO hkit_records
            (tenant_id, user_id, record_type_id, source_id,
             value, unit, start_date, end_date, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (tenant_id, user_id, record_type_id, source_id, start_date, end_date)
        DO NOTHING
        """,
        (
            ctx.tenant_id,
            ctx.user_id,
            record_type_id,
            source_id,
            value,
            unit,
            start,
            end,
            metadata_json,
        ),
    )
    return True


def upsert_bp_correlation(
    cur,
    ctx: WriterContext,
    correlation: dict,
    source_id: Optional[int] = None,
) -> Optional[dict]:
    """Upsert an ``HKCorrelationTypeIdentifierBloodPressure`` correlation.

    Writes two rows to ``hkit_records`` — one for
    ``HKQuantityTypeIdentifierBloodPressureSystolic`` and one for
    ``HKQuantityTypeIdentifierBloodPressureDiastolic`` — that share a
    ``metadata.correlation_id`` so the pair can be reassembled at query
    time.

    The caller is responsible for any subsequent projection into
    ``health_blood_pressure_readings``; this writer only populates the
    canonical ``hkit_*`` layer.

    Args:
        cur: An open database cursor.
        ctx: The active ``WriterContext``.
        correlation: Dict with keys ``start``, ``end``, ``metadata``,
            and ``components`` (a list containing at least one systolic
            and one diastolic quantity sample).
        source_id: Optional ``hkit_sources.id``.

    Returns:
        On success, a dict ``{'systolic': float, 'diastolic': float,
        'unit': str, 'start': datetime, 'correlation_id': str}``
        summarising what was written. This is convenient for the
        caller when it also needs to do a legacy
        ``health_blood_pressure_readings`` write during the transition.
        Returns ``None`` if the correlation could not be reassembled
        (missing components, unparseable values, etc.).
    """
    if not isinstance(correlation, dict):
        return None
    components = correlation.get("components")
    if not isinstance(components, list):
        return None

    systolic: Optional[float] = None
    diastolic: Optional[float] = None
    unit: Optional[str] = None
    for component in components:
        if not isinstance(component, dict):
            continue
        c_type = component.get("type_identifier")
        c_value = _coerce_numeric(component.get("value"))
        if c_value is None:
            continue
        if c_type == "HKQuantityTypeIdentifierBloodPressureSystolic":
            systolic = c_value
            unit = component.get("unit") or unit
        elif c_type == "HKQuantityTypeIdentifierBloodPressureDiastolic":
            diastolic = c_value
            unit = component.get("unit") or unit

    if systolic is None or diastolic is None:
        return None

    start = _parse_iso_timestamp(correlation.get("start"))
    if start is None:
        return None
    end = _parse_iso_timestamp(correlation.get("end")) or start

    base_meta = dict(correlation.get("metadata") or {})
    external_uuid = base_meta.get("HKMetadataKeyExternalUUID")
    correlation_id = base_meta.get("correlation_id") or external_uuid or (
        f"bp-{ctx.user_id}-{start.isoformat()}"
    )
    base_meta["correlation_id"] = correlation_id

    systolic_meta = dict(base_meta)
    systolic_meta["component_role"] = "systolic"
    diastolic_meta = dict(base_meta)
    diastolic_meta["component_role"] = "diastolic"

    systolic_type_id = get_or_create_record_type(
        cur, ctx, "HKQuantityTypeIdentifierBloodPressureSystolic"
    )
    diastolic_type_id = get_or_create_record_type(
        cur, ctx, "HKQuantityTypeIdentifierBloodPressureDiastolic"
    )

    for rt_id, value, meta in (
        (systolic_type_id, systolic, systolic_meta),
        (diastolic_type_id, diastolic, diastolic_meta),
    ):
        cur.execute(
            """
            INSERT INTO hkit_records
                (tenant_id, user_id, record_type_id, source_id,
                 value, unit, start_date, end_date, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (tenant_id, user_id, record_type_id, source_id, start_date, end_date)
            DO NOTHING
            """,
            (
                ctx.tenant_id,
                ctx.user_id,
                rt_id,
                source_id,
                value,
                unit or "mmHg",
                start,
                end,
                json.dumps(meta),
            ),
        )

    return {
        "systolic": systolic,
        "diastolic": diastolic,
        "unit": unit or "mmHg",
        "start": start,
        "correlation_id": correlation_id,
    }


# ---------------------------------------------------------------------------
# Payload-level helper
# ---------------------------------------------------------------------------


@dataclass
class WriterCounts:
    """Summary of rows written during a single payload processing pass."""

    characteristics: int = 0
    activity_summaries: int = 0
    workouts: int = 0
    samples: int = 0
    bp_correlations: int = 0
    anchors: int = 0
    skipped: int = 0


def upsert_sync_anchor(
    cur,
    ctx: WriterContext,
    device_id: str,
    sample_type: str,
    anchor: str,
) -> None:
    """Persist an HKAnchoredObjectQuery anchor for one (device, sample_type).

    The anchor is opaque to the server — whatever string HealthKit hands
    the mobile client. Storing it server-side lets a reinstalled mobile
    app resume incremental sync without re-reading the full 5-year
    HealthKit window.
    """
    if not device_id:
        raise ValueError("device_id is required")
    if not sample_type:
        raise ValueError("sample_type is required")
    if not anchor:
        raise ValueError("anchor is required")

    cur.execute(
        """
        INSERT INTO hkit_sync_anchors
            (tenant_id, user_id, device_id, sample_type, anchor)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, user_id, device_id, sample_type)
        DO UPDATE SET anchor = EXCLUDED.anchor, updated_at = now()
        """,
        (ctx.tenant_id, ctx.user_id, device_id, sample_type, anchor),
    )


def get_sync_anchors(cur, ctx: WriterContext, device_id: str) -> dict[str, str]:
    """Return ``{sample_type: anchor}`` currently stored for this device."""
    if not device_id:
        raise ValueError("device_id is required")

    cur.execute(
        """
        SELECT sample_type, anchor
        FROM hkit_sync_anchors
        WHERE tenant_id = %s AND user_id = %s AND device_id = %s
        ORDER BY sample_type
        """,
        (ctx.tenant_id, ctx.user_id, device_id),
    )
    return {row["sample_type"]: row["anchor"] for row in cur.fetchall()}


def write_v2_payload(cur, ctx: WriterContext, payload: dict) -> tuple[WriterCounts, list[dict]]:
    """Process a ``payload_version=2`` HealthKit sync body end-to-end.

    This is the top-level entry point the sync endpoint calls after
    opening the transaction. It walks the payload's sections in order
    (characteristics → sources → activity_summaries → workouts →
    samples) and dispatches each entry to the appropriate upsert
    function.

    The function does **not** commit or roll back — the caller owns
    the transaction. On the first database exception, this function
    re-raises and leaves it to the caller to handle.

    Args:
        cur: An open database cursor inside the caller's transaction.
        ctx: The active ``WriterContext``.
        payload: The decoded JSON request body. Must have
            ``payload_version == 2``. Other top-level keys are
            optional.

    Returns:
        A tuple ``(counts, bp_summaries)`` where ``counts`` is a
        ``WriterCounts`` with per-section row counts accepted for
        write, and ``bp_summaries`` is the list of summary dicts
        returned by ``upsert_bp_correlation`` (one per successful BP
        correlation). The caller can use the BP summaries to drive a
        concurrent write to the legacy
        ``health_blood_pressure_readings`` table during the transition
        period.

    Raises:
        ValueError: If ``payload`` is not a dict or is not v2.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    if payload.get("payload_version") != 2:
        raise ValueError("write_v2_payload requires payload_version == 2")

    counts = WriterCounts()
    bp_summaries: list[dict] = []

    # 1. Characteristics — single upsert, immutable facts.
    characteristics = payload.get("characteristics")
    if isinstance(characteristics, dict) and characteristics:
        upsert_characteristics(cur, ctx, characteristics)
        counts.characteristics = 1

    # 2. Sources — build an index so samples/workouts can reference them by
    #    position. The v2 payload uses `source_ref: <int>` to point at the
    #    corresponding entry in the top-level `sources` array.
    source_map: dict[int, int] = {}
    for idx, src in enumerate(payload.get("sources") or []):
        if isinstance(src, dict):
            try:
                source_map[idx] = get_or_create_source(cur, ctx, src)
            except ValueError:
                counts.skipped += 1

    # 3. Activity summaries.
    for summary in payload.get("activity_summaries") or []:
        try:
            upsert_activity_summary(cur, ctx, summary)
            counts.activity_summaries += 1
        except (ValueError, TypeError):
            counts.skipped += 1

    # 4. Workouts.
    for workout in payload.get("workouts") or []:
        if not isinstance(workout, dict):
            counts.skipped += 1
            continue
        src_ref = workout.get("source_ref")
        src_id = source_map.get(src_ref) if isinstance(src_ref, int) else None
        try:
            upsert_workout(cur, ctx, workout, source_id=src_id)
            counts.workouts += 1
        except (ValueError, TypeError):
            counts.skipped += 1

    # 5. Samples (quantity/category) and BP correlations share the `samples`
    #    array. BP correlations are dispatched to their own handler; everything
    #    else goes through upsert_sample.
    for sample in payload.get("samples") or []:
        if not isinstance(sample, dict):
            counts.skipped += 1
            continue
        type_identifier = sample.get("type_identifier")
        src_ref = sample.get("source_ref")
        src_id = source_map.get(src_ref) if isinstance(src_ref, int) else None

        if type_identifier == "HKCorrelationTypeIdentifierBloodPressure":
            bp_summary = upsert_bp_correlation(cur, ctx, sample, source_id=src_id)
            if bp_summary is None:
                counts.skipped += 1
            else:
                counts.bp_correlations += 1
                bp_summaries.append(bp_summary)
            continue

        if upsert_sample(cur, ctx, sample, source_id=src_id):
            counts.samples += 1
        else:
            counts.skipped += 1

    # 6. Sync anchors — store whatever the client says is current.
    #    Requires a device_id; silently skipped if the caller omitted it.
    device_id = payload.get("device_id")
    anchors_in = payload.get("anchors") or {}
    if (
        isinstance(device_id, str)
        and device_id
        and isinstance(anchors_in, dict)
    ):
        for sample_type, anchor_value in anchors_in.items():
            if not (
                isinstance(sample_type, str)
                and sample_type
                and isinstance(anchor_value, str)
                and anchor_value
            ):
                continue
            upsert_sync_anchor(cur, ctx, device_id, sample_type, anchor_value)
            counts.anchors += 1

    return counts, bp_summaries
