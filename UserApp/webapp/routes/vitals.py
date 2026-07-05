"""
Vitals routes.

Blueprint for blood pressure, temperature, weight, blood glucose, observations, and metric deletions.
"""
from flask import Blueprint, request, jsonify, g, current_app
from db_driver import sql
from datetime import datetime
import pytz
import uuid
import threading

from utils import (
    require_auth,
    get_db_connection,
    get_user_id,
    local_to_utc,
    table_has_column,
    parse_pagination_params,
    paginated_response,
)
import analytics
import db_manager

bp = Blueprint('vitals', __name__, url_prefix='/api/v1')


# ==================== BLOOD PRESSURE ====================

@bp.route('/blood-pressure', methods=['GET'])
@require_auth
def get_blood_pressure():
    """Get a paginated list of blood pressure readings.

    Optional query params: start_date, end_date (YYYY-MM-DD) for date filtering;
    limit, offset for pagination.
    """
    from utils import parse_date_range_params
    start_date, end_date, err = parse_date_range_params()
    if err:
        return err

    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    conditions: list = [sql.SQL("tenant_id = %s AND user_id = %s")]
    params: list = [g.user.get('tenant_id', 1), get_user_id()]
    if start_date:
        conditions.append(sql.SQL("measured_at >= %s"))
        params.append(start_date)
    if end_date:
        conditions.append(sql.SQL("measured_at < %s + INTERVAL '1 day'"))
        params.append(end_date)

    where_sql = (
        sql.SQL("WHERE ") + sql.SQL(" AND ").join(conditions)
        if conditions
        else sql.SQL("")
    )

    query = sql.SQL("""
        SELECT count(*) OVER() AS _total,
               id, measured_at, systolic, diastolic, pulse
        FROM health_blood_pressure_readings
        {where}
        ORDER BY measured_at DESC
        LIMIT %s OFFSET %s
    """).format(where=where_sql)

    cur.execute(query, params + [limit, offset])

    readings = cur.fetchall()
    cur.close()
    conn.close()

    total = readings[0]['_total'] if readings else 0
    for reading in readings:
        reading.pop('_total', None)
        reading['id'] = str(reading['id'])
        if reading.get('measured_at'):
            reading['timestamp'] = reading.pop('measured_at').isoformat()

    has_more = offset + len(readings) < total
    resp = jsonify(paginated_response(readings, total, limit, offset, key='entries'))
    if has_more:
        # X-Truncated header preserved during transition for clients that
        # haven't yet adopted pagination.has_more. Now driven by the true
        # has_more signal rather than "len == hard cap".
        resp.headers['X-Truncated'] = 'true'
    return resp


@bp.route('/blood-pressure', methods=['POST'])
@require_auth
def log_blood_pressure():
    """Log a blood pressure reading"""
    data = request.json
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    measured_at = local_to_utc(data['timestamp'])
    now = datetime.now(pytz.utc)

    reading_id = uuid.uuid4()
    cur.execute("""
        INSERT INTO health_blood_pressure_readings
        (tenant_id, id, user_id, measured_at, systolic, diastolic, pulse, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        tenant_id,
        reading_id,
        user_id,
        measured_at,
        int(data['systolic']),
        int(data['diastolic']),
        int(data['heart_rate']) if data.get('heart_rate') else None,
        now
    ))

    conn.commit()
    cur.close()
    conn.close()

    analytics.capture('blood_pressure_recorded')

    return jsonify({'message': 'Blood pressure logged successfully'}), 201


@bp.route('/blood-pressure/<reading_id>', methods=['DELETE'])
@require_auth
def delete_blood_pressure(reading_id):
    """Delete a blood pressure reading"""
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM health_blood_pressure_readings
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, get_user_id(), uuid.UUID(reading_id),))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': 'Blood pressure reading deleted'})


# ==================== TEMPERATURE ====================

@bp.route('/temperature', methods=['GET'])
@require_auth
def get_temperature():
    """Get a paginated list of temperature readings.

    Optional query params: start_date, end_date (YYYY-MM-DD) for date filtering;
    limit, offset for pagination.
    """
    from utils import parse_date_range_params
    start_date, end_date, err = parse_date_range_params()
    if err:
        return err

    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    conditions: list = [sql.SQL("tenant_id = %s AND user_id = %s"),
                       sql.SQL("metric_type = 'temperature'")]
    params: list = [g.user.get('tenant_id', 1), get_user_id()]
    if start_date:
        conditions.append(sql.SQL("recorded_at >= %s"))
        params.append(start_date)
    if end_date:
        conditions.append(sql.SQL("recorded_at < %s + INTERVAL '1 day'"))
        params.append(end_date)

    query = sql.SQL("""
        SELECT count(*) OVER() AS _total,
               id, recorded_at, value, unit
        FROM health_metrics
        WHERE {where}
        ORDER BY recorded_at DESC
        LIMIT %s OFFSET %s
    """).format(where=sql.SQL(" AND ").join(conditions))

    cur.execute(query, params + [limit, offset])

    readings = cur.fetchall()
    cur.close()
    conn.close()

    total = readings[0]['_total'] if readings else 0
    for reading in readings:
        reading.pop('_total', None)
        reading['id'] = str(reading['id'])
        reading['temperature'] = float(reading.pop('value'))
        if reading.get('recorded_at'):
            reading['timestamp'] = reading.pop('recorded_at').isoformat()

    has_more = offset + len(readings) < total
    resp = jsonify(paginated_response(readings, total, limit, offset, key='entries'))
    if has_more:
        resp.headers['X-Truncated'] = 'true'
    return resp


@bp.route('/temperature', methods=['POST'])
@require_auth
def log_temperature():
    """Log a temperature reading"""
    data = request.json
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    recorded_at = local_to_utc(data['timestamp'])
    now = datetime.now(pytz.utc)

    metric_id = uuid.uuid4()
    cur.execute("""
        INSERT INTO health_metrics
        (tenant_id, id, user_id, recorded_at, metric_type, value, unit, created_at)
        VALUES (%s, %s, %s, %s, 'temperature', %s, %s, %s)
    """, (
        tenant_id,
        metric_id,
        user_id,
        recorded_at,
        float(data['temperature']),
        data.get('unit', 'F'),
        now
    ))

    conn.commit()
    cur.close()
    conn.close()

    analytics.capture('temperature_recorded')

    return jsonify({'message': 'Temperature logged successfully'}), 201


# ==================== BLOOD GLUCOSE ====================

@bp.route('/blood-glucose', methods=['GET'])
@require_auth
def get_blood_glucose():
    """Get a paginated list of blood glucose readings.

    Optional query params: start_date, end_date (YYYY-MM-DD) for date filtering;
    limit, offset for pagination.
    """
    from utils import parse_date_range_params
    start_date, end_date, err = parse_date_range_params()
    if err:
        return err

    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    conditions: list = [sql.SQL("tenant_id = %s AND user_id = %s"),
                       sql.SQL("metric_type = 'blood_glucose'")]
    params: list = [g.user.get('tenant_id', 1), get_user_id()]
    if start_date:
        conditions.append(sql.SQL("recorded_at >= %s"))
        params.append(start_date)
    if end_date:
        conditions.append(sql.SQL("recorded_at < %s + INTERVAL '1 day'"))
        params.append(end_date)

    query = sql.SQL("""
        SELECT count(*) OVER() AS _total,
               id, recorded_at, value, unit
        FROM health_metrics
        WHERE {where}
        ORDER BY recorded_at DESC
        LIMIT %s OFFSET %s
    """).format(where=sql.SQL(" AND ").join(conditions))

    cur.execute(query, params + [limit, offset])

    readings = cur.fetchall()
    cur.close()
    conn.close()

    total = readings[0]['_total'] if readings else 0
    for reading in readings:
        reading.pop('_total', None)
        reading['id'] = str(reading['id'])
        reading['blood_glucose'] = float(reading.pop('value'))
        if reading.get('recorded_at'):
            reading['timestamp'] = reading.pop('recorded_at').isoformat()

    has_more = offset + len(readings) < total
    resp = jsonify(paginated_response(readings, total, limit, offset, key='entries'))
    if has_more:
        resp.headers['X-Truncated'] = 'true'
    return resp


@bp.route('/blood-glucose', methods=['POST'])
@require_auth
def log_blood_glucose():
    """Log a blood glucose reading"""
    data = request.json
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    recorded_at = local_to_utc(data['timestamp'])
    now = datetime.now(pytz.utc)

    metric_id = uuid.uuid4()
    cur.execute("""
        INSERT INTO health_metrics
        (tenant_id, id, user_id, recorded_at, metric_type, value, unit, created_at)
        VALUES (%s, %s, %s, %s, 'blood_glucose', %s, %s, %s)
    """, (
        tenant_id,
        metric_id,
        user_id,
        recorded_at,
        float(data['blood_glucose']),
        data.get('unit', 'mg/dL'),
        now
    ))

    conn.commit()
    cur.close()
    conn.close()

    analytics.capture('blood_glucose_recorded')

    return jsonify({'message': 'Blood glucose logged successfully'}), 201


# ==================== WEIGHT ====================

@bp.route('/weight', methods=['GET'])
@require_auth
def get_weight():
    """Get a paginated list of weight readings.

    Optional query params: start_date, end_date (YYYY-MM-DD) for date filtering;
    limit, offset for pagination.
    """
    from utils import parse_date_range_params
    start_date, end_date, err = parse_date_range_params()
    if err:
        return err

    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    conditions = [sql.SQL("tenant_id = %s AND user_id = %s"),
                  sql.SQL("metric_type = 'weight'")]
    params: list = [g.user.get('tenant_id', 1), get_user_id()]
    if start_date:
        conditions.append(sql.SQL("recorded_at >= %s"))
        params.append(start_date)
    if end_date:
        conditions.append(sql.SQL("recorded_at < %s + INTERVAL '1 day'"))
        params.append(end_date)

    query = sql.SQL("""
        SELECT count(*) OVER() AS _total,
               id, recorded_at, value, unit
        FROM health_metrics
        WHERE {where}
        ORDER BY recorded_at DESC
        LIMIT %s OFFSET %s
    """).format(where=sql.SQL(" AND ").join(conditions))

    cur.execute(query, params + [limit, offset])

    readings = cur.fetchall()
    cur.close()
    conn.close()

    total = readings[0]['_total'] if readings else 0
    for reading in readings:
        reading.pop('_total', None)
        reading['id'] = str(reading['id'])
        reading['weight'] = float(reading.pop('value'))
        if reading.get('recorded_at'):
            reading['timestamp'] = reading.pop('recorded_at').isoformat()

    has_more = offset + len(readings) < total
    resp = jsonify(paginated_response(readings, total, limit, offset, key='entries'))
    if has_more:
        resp.headers['X-Truncated'] = 'true'
    return resp


@bp.route('/weight', methods=['POST'])
@require_auth
def log_weight():
    """Log a weight reading"""
    data = request.json
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    recorded_at = local_to_utc(data['timestamp'])
    now = datetime.now(pytz.utc)

    metric_id = uuid.uuid4()
    cur.execute("""
        INSERT INTO health_metrics
        (tenant_id, id, user_id, recorded_at, metric_type, value, unit, created_at)
        VALUES (%s, %s, %s, %s, 'weight', %s, %s, %s)
    """, (
        tenant_id,
        metric_id,
        user_id,
        recorded_at,
        float(data['weight']),
        data.get('unit', 'lbs'),
        now
    ))

    conn.commit()
    cur.close()
    conn.close()

    analytics.capture('weight_recorded')

    return jsonify({'message': 'Weight logged successfully'}), 201


@bp.route('/weight/<weight_id>', methods=['DELETE'])
@require_auth
def delete_weight(weight_id):
    """Delete a weight reading."""
    tenant_id = g.user.get('tenant_id', 1)
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM health_metrics
        WHERE tenant_id = %s AND user_id = %s AND id = %s AND metric_type = 'weight'
    """, (tenant_id, get_user_id(), uuid.UUID(weight_id),))

    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    if rows_affected == 0:
        return jsonify({'error': 'Weight reading not found'}), 404

    return jsonify({'message': 'Weight reading deleted'})


@bp.route('/health-metrics/<metric_id>', methods=['DELETE'])
@require_auth
def delete_health_metric(metric_id):
    """Delete a health metric (temperature or weight)"""
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM health_metrics
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, get_user_id(), uuid.UUID(metric_id),))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': 'Health metric deleted'})


# ==================== OBSERVATIONS ====================

def _embed_observation(conn, tenant_id, user_id, obs_id, content: str, logger=None):
    """Attempt server-side embedding for an observation. Fails silently.

    Called inline after observation create/update. The observation is already
    committed — if embedding fails, the row exists without a vector and can
    be backfilled later. Uses its own cursor to avoid poisoning the caller's.

    `logger` is the caller-provided logger so this function works from inside
    a daemon thread, where `current_app` has no application context and its
    proxy raises RuntimeError on attribute access. Callers running on the
    request thread can pass `current_app.logger`; thread callers must capture
    it in the parent and pass it in.
    """
    if logger is None:
        logger = current_app.logger
    try:
        if not table_has_column(conn, 'health_observations', 'embedding_content'):
            return  # Column not yet migrated

        from embedding_utils import get_embedding, register_pgvector
        register_pgvector(conn)

        embedding = get_embedding(content)
        if embedding:
            cur = conn.cursor()
            cur.execute(
                "UPDATE health_observations "
                "SET embedding_content = %s::vector "
                "WHERE tenant_id = %s AND user_id = %s AND id = %s",
                (str(embedding), tenant_id, user_id, obs_id),
            )
            cur.close()
            conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception as rollback_err:
            logger.debug(
                "embed_observation_rollback_failed obs_id=%s error=%s",
                obs_id, rollback_err,
            )
        logger.warning(
            "embed_observation_failed obs_id=%s error=%s", obs_id, exc,
        )


@bp.route('/observations', methods=['GET'])
@require_auth
def get_observations():
    """Get a paginated list of observations."""
    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT count(*) OVER() AS _total,
               id, content, observed_at, category, mental_health_flag
        FROM health_observations
        WHERE user_id = %s
        ORDER BY observed_at DESC
        LIMIT %s OFFSET %s
    """, (get_user_id(), limit, offset))

    observations = cur.fetchall()
    cur.close()
    conn.close()

    total = observations[0]['_total'] if observations else 0
    for obs in observations:
        obs.pop('_total', None)
        obs['id'] = str(obs['id'])
        obs['observation'] = obs.pop('content')
        obs['timestamp'] = obs.pop('observed_at').isoformat() if obs.get('observed_at') else None
        obs['source_type'] = obs.pop('category', 'text')
        obs['mental_health_flag'] = obs.get('mental_health_flag', False) or False

    return jsonify(paginated_response(observations, total, limit, offset, key='entries'))


@bp.route('/observations', methods=['POST'])
@require_auth
def create_observation():
    """Create a new observation"""
    data = request.json
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    obs_id = uuid.uuid4()
    now = datetime.now(pytz.utc)

    observed_at = local_to_utc(data['timestamp']) if data.get('timestamp') else now

    mental_health_flag = bool(data.get('mental_health_flag', False))

    cur.execute("""
        INSERT INTO health_observations (tenant_id, id, user_id, content, observed_at, category, mental_health_flag, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (tenant_id, obs_id, user_id, data['observation'], observed_at, data.get('source_type', 'text'), mental_health_flag, now, now))

    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    obs_id_val = result['id']
    obs_text = data['observation']

    # Daemon thread can't access current_app or g; capture everything we need now.
    logger = current_app.logger
    captured_user_id = user_id
    captured_tenant_id = tenant_id

    def _bg_embed():
        try:
            bg_conn = db_manager.get_direct_connection_for_user(captured_user_id, captured_tenant_id)
            _embed_observation(bg_conn, captured_tenant_id, captured_user_id, obs_id_val, obs_text, logger=logger)
            bg_conn.close()
        except Exception as exc:
            logger.warning(
                "bg_embed_create_failed obs_id=%s error=%s", obs_id_val, exc,
            )

    threading.Thread(target=_bg_embed, daemon=True).start()

    return jsonify({'id': str(obs_id_val), 'message': 'Observation created'}), 201


@bp.route('/observations/<obs_id>', methods=['PUT'])
@require_auth
def update_observation(obs_id):
    """Update an observation"""
    data = request.json
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    now = datetime.now(pytz.utc)
    observed_at = local_to_utc(data['timestamp']) if data.get('timestamp') else now

    cur.execute("""
        UPDATE health_observations
        SET content = %s, observed_at = %s, updated_at = %s
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (data['observation'], observed_at, now, tenant_id, get_user_id(), uuid.UUID(obs_id)))

    conn.commit()
    cur.close()
    conn.close()

    obs_uuid = uuid.UUID(obs_id)
    obs_text = data['observation']

    # Daemon thread can't access current_app or g; capture everything we need now.
    logger = current_app.logger
    captured_user_id = get_user_id()
    captured_tenant_id = tenant_id

    def _bg_embed():
        try:
            bg_conn = db_manager.get_direct_connection_for_user(captured_user_id, captured_tenant_id)
            _embed_observation(bg_conn, captured_tenant_id, captured_user_id, obs_uuid, obs_text, logger=logger)
            bg_conn.close()
        except Exception as exc:
            logger.warning(
                "bg_embed_update_failed obs_id=%s error=%s", obs_uuid, exc,
            )

    threading.Thread(target=_bg_embed, daemon=True).start()

    return jsonify({'message': 'Observation updated'})


@bp.route('/observations/<obs_id>', methods=['PATCH'])
@require_auth
def patch_observation(obs_id):
    """Patch an observation (update mental_health_flag)"""
    data = request.json
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    now = datetime.now(pytz.utc)
    mental_health_flag = bool(data.get('mental_health_flag', False))

    cur.execute("""
        UPDATE health_observations
        SET mental_health_flag = %s, updated_at = %s
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (mental_health_flag, now, tenant_id, get_user_id(), uuid.UUID(obs_id)))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': 'Observation updated'})


@bp.route('/observations/<obs_id>', methods=['DELETE'])
@require_auth
def delete_observation(obs_id):
    """Delete an observation"""
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM health_observations
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, get_user_id(), uuid.UUID(obs_id),))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': 'Observation deleted'})
