"""
Per-source metric routes.

Three read-only list endpoints over `health_metrics`, one per `metric_type`
bucket that the mobile app needs alongside the existing per-source
endpoints (/blood-pressure, /temperature, /weight):

    /api/v1/sleep               → metric_type = 'sleep'
    /api/v1/nutrition           → metric_type = 'nutrition'
    /api/v1/medication-metrics  → metric_type = 'medication'

Rows are scoped to the authenticated user at the application level with an
explicit `tenant_id = %s AND user_id = %s` predicate (same pattern
/temperature and /weight use — they share the health_metrics table). Home
Edition has no RLS, so the predicate is mandatory. All three endpoints
return the standard {entries, pagination} envelope.
"""
import json

from flask import Blueprint, jsonify, g
from db_driver import sql

from utils import (
    require_auth,
    get_db_connection,
    get_user_id,
    parse_date_range_params,
    parse_pagination_params,
    paginated_response,
)

bp = Blueprint('health_metrics', __name__, url_prefix='/api/v1')


def _serialize_row(row):
    """Shared row projection for health_metrics per-source endpoints."""
    row.pop('_total', None)
    row['id'] = str(row['id'])
    if row.get('recorded_at'):
        row['timestamp'] = row.pop('recorded_at').isoformat()
    if row.get('value') is not None:
        row['value'] = float(row['value'])
    return row


def _page_health_metrics(metric_types, extra_project=None):
    """Shared handler for the three per-source metric endpoints.

    Args:
        metric_types: list of metric_type values to include (typically one).
        extra_project: optional callable(row) → row for per-endpoint enrichment.
    """
    start_date, end_date, err = parse_date_range_params()
    if err:
        return err

    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    conditions = [
        sql.SQL("tenant_id = %s AND user_id = %s"),
        sql.SQL("metric_type = ANY(%s::text[])"),
    ]
    params = [g.user.get('tenant_id', 1), get_user_id(), list(metric_types)]
    if start_date:
        conditions.append(sql.SQL("recorded_at >= %s"))
        params.append(start_date)
    if end_date:
        conditions.append(sql.SQL("recorded_at < %s + INTERVAL '1 day'"))
        params.append(end_date)

    metrics_query = sql.SQL("""
        SELECT count(*) OVER() AS _total,
               id, recorded_at, metric_type, value, unit, notes, source
        FROM health_metrics
        WHERE {where}
        ORDER BY recorded_at DESC
        LIMIT %s OFFSET %s
    """).format(where=sql.SQL(" AND ").join(conditions))
    cur.execute(metrics_query, params + [limit, offset])

    rows = cur.fetchall()
    cur.close()
    conn.close()

    total = rows[0]['_total'] if rows else 0
    projected = []
    for row in rows:
        row = _serialize_row(row)
        if extra_project:
            row = extra_project(row)
        projected.append(row)

    return jsonify(paginated_response(projected, total, limit, offset, key='entries'))


@bp.route('/sleep', methods=['GET'])
@require_auth
def get_sleep():
    """Get a paginated list of sleep metric entries.

    Optional query params: start_date, end_date (YYYY-MM-DD); limit, offset.
    """
    return _page_health_metrics(['sleep'])


@bp.route('/nutrition', methods=['GET'])
@require_auth
def get_nutrition():
    """Get a paginated list of nutrition metric entries.

    Optional query params: start_date, end_date (YYYY-MM-DD); limit, offset.
    """
    return _page_health_metrics(['nutrition'])


def _unpack_medication_notes(row):
    """Pull medication_name, medication_status, medication_dosage out of notes.

    Mirrors the projection in /all-logs so clients see the same flat fields.
    """
    raw = row.get('notes')
    try:
        if raw and isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        elif not raw:
            raw = {}
        meta = raw.get('metadata') or raw
        med_obj = meta.get('medication') if isinstance(meta.get('medication'), dict) else None
        lookup = meta or {}
        name = (
            lookup.get('medication_name')
            or lookup.get('medicationName')
            or (med_obj.get('name') if med_obj else None)
            or lookup.get('name')
        )
        status = lookup.get('status') or (med_obj.get('status') if med_obj else None)
        dosage = (
            lookup.get('dosage')
            or lookup.get('dose')
            or (med_obj.get('dosage') if med_obj else None)
            or (row.get('unit') if row.get('unit') not in (None, '', 'dose') else None)
        )
    except Exception:
        name = status = dosage = None

    row['medication_name'] = name
    row['medication_status'] = status
    row['medication_dosage'] = dosage
    return row


@bp.route('/medication-metrics', methods=['GET'])
@require_auth
def get_medication_metrics():
    """Get a paginated list of medication metric entries.

    Unpacks medication_name / medication_status / medication_dosage from the
    notes JSON field so mobile clients don't re-parse it.

    Optional query params: start_date, end_date (YYYY-MM-DD); limit, offset.
    """
    return _page_health_metrics(['medication'], extra_project=_unpack_medication_notes)
