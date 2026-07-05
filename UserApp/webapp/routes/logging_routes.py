"""
Activity Logging routes.

Blueprint for logging meals, stacks, health inputs, food items, and viewing logs.
"""
from flask import Blueprint, request, jsonify, g, current_app
import db_driver
from db_driver import sql
from datetime import datetime, timedelta
import pytz
import uuid
import json

from utils import (
    require_auth,
    get_db_connection,
    get_user_id,
    local_to_utc,
    table_has_column,
    parse_pagination_params,
    parse_date_range_params,
    paginated_response,
)
import db_manager
import analytics
from logging_config import is_level, BASIC, DEBUG

bp = Blueprint('logging', __name__, url_prefix='/api/v1')


def parse_uuid(value):
    try:
        return uuid.UUID(str(value))
    except Exception:
        return None


def normalize_food_log_payload_v4(data):
    """Normalize legacy v3 food-log fields to v4 canonical names."""
    payload = dict(data or {})
    if 'servings' not in payload and payload.get('quantity') is not None:
        payload['servings'] = payload.get('quantity')
    if 'carbs_g' not in payload and payload.get('carbs_total_g') is not None:
        payload['carbs_g'] = payload.get('carbs_total_g')
    if 'fat_g' not in payload and payload.get('fat_total_g') is not None:
        payload['fat_g'] = payload.get('fat_total_g')
    return payload


def parse_food_notes_payload(data):
    """Build a normalized notes payload for optional nutrition overrides."""
    payload = {}
    raw_notes = (data or {}).get('notes')
    if isinstance(raw_notes, dict):
        payload.update(raw_notes)
    elif isinstance(raw_notes, str) and raw_notes.strip():
        try:
            parsed = json.loads(raw_notes)
            if isinstance(parsed, dict):
                payload.update(parsed)
            else:
                payload['notes_text'] = raw_notes
        except Exception:
            payload['notes_text'] = raw_notes

    for key in ['calories', 'protein_g', 'carbs_g', 'fat_g', 'fiber_g', 'sugar_g', 'sodium_mg', 'potassium_mg', 'meal']:
        if (data or {}).get(key) is not None:
            payload[key] = (data or {}).get(key)
    return payload


def validate_health_input_log_payload(data):
    if not data:
        return 'JSON body required'
    if not data.get('timestamp'):
        return 'timestamp is required'
    if not data.get('input_id') and not data.get('free_text'):
        return 'input_id or free_text is required'
    return None


def validate_food_log_payload(data):
    if not data:
        return 'JSON body required'
    if not data.get('timestamp'):
        return 'timestamp is required'
    if not data.get('food_item_id') and not data.get('free_text'):
        return 'food_item_id or free_text is required'
    return None


def resolve_time_column(conn, table_name, preferred='logged_at', legacy='timestamp'):
    """Pick the timestamp column used by the current schema."""
    if table_has_column(conn, table_name, preferred):
        return preferred
    return legacy


@bp.route('/log-meal', methods=['POST'])
@require_auth
def log_meal():
    """Log a meal consumption with diagnostics"""
    data = request.json
    if not data or 'meal_id' not in data or 'timestamp' not in data:
        current_app.logger.warning("log-meal: Missing required fields in request")
        return jsonify({'error': 'Missing required fields: meal_id, timestamp'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    meal_uuid = parse_uuid(data['meal_id'])
    if not meal_uuid:
        return jsonify({'error': 'Invalid meal_id'}), 400
    timestamp = local_to_utc(data['timestamp'])

    current_app.logger.info(
        "log-meal: Starting meal_id=%s tenant_id=%s user_id=%s",
        str(meal_uuid)[:8], tenant_id, str(user_id)[:8] if user_id else None
    )

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id FROM meals WHERE tenant_id = %s AND user_id = %s AND id = %s", (tenant_id, user_id, meal_uuid))
    meal_exists = cur.fetchone() is not None
    if not meal_exists:
        cur.close()
        conn.close()
        return jsonify({'error': 'Meal not found'}), 404

    cur.execute("""
        SELECT food_item_id, servings
        FROM meal_items
        WHERE user_id = %s AND meal_id = %s
    """, (user_id, meal_uuid,))
    meal_items = cur.fetchall()
    items_count = len(meal_items)

    current_app.logger.info("log-meal: Found %d items for meal_id=%s", items_count, str(meal_uuid)[:8])

    if items_count == 0:
        current_app.logger.warning(
            "log-meal: NO ITEMS FOUND for meal_id=%s tenant_id=%s",
            str(meal_uuid)[:8], tenant_id
        )
        cur.close()
        conn.close()
        return jsonify({
            'error': 'Meal has no items',
            'meal_id': str(meal_uuid),
            'items_found': 0,
            'items_logged': 0
        }), 404

    from nutrition_projection import project_nutrition_for_food_log
    inserted = 0
    projected = 0
    for item in meal_items:
        log_id = uuid.uuid4()
        try:
            cur.execute("""
                INSERT INTO health_food_logv2 (tenant_id, id, user_id, logged_at, food_item_id, servings, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (tenant_id, log_id, user_id, timestamp, item['food_item_id'], item['servings'], timestamp))
            inserted += 1
            # Project nutrition into health_metrics so /health-query and
            # /nutrition/today see the day's totals. Skip silently if the
            # food item lacks calorie data — see nutrition_projection.py.
            if project_nutrition_for_food_log(conn, tenant_id, log_id, user_id):
                projected += 1
        except Exception as e:
            current_app.logger.error("log-meal: INSERT FAILED for food_item_id=%s: %s", item['food_item_id'], e)

    conn.commit()
    cur.close()
    conn.close()
    if projected:
        current_app.logger.info("log-meal: projected %d/%d items into health_metrics", projected, inserted)

    current_app.logger.info(
        "log-meal: Completed meal_id=%s items_found=%d items_logged=%d",
        str(meal_uuid)[:8], items_count, inserted
    )

    analytics.capture('meal_logged', {'meal_id': str(meal_uuid), 'item_count': inserted})

    return jsonify({
        'message': 'Meal logged successfully',
        'meal_id': str(meal_uuid),
        'items_found': items_count,
        'items_logged': inserted
    }), 201


@bp.route('/log-stack', methods=['POST'])
@require_auth
def log_stack():
    """Log a stack consumption with detailed diagnostics"""
    data = request.json
    if not data or 'stack_id' not in data or 'timestamp' not in data:
        current_app.logger.warning("log-stack: Missing required fields in request")
        return jsonify({'error': 'Missing required fields: stack_id, timestamp'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    stack_uuid = parse_uuid(data['stack_id'])
    if not stack_uuid:
        return jsonify({'error': 'Invalid stack_id'}), 400
    timestamp = local_to_utc(data['timestamp'])

    current_app.logger.info(
        "log-stack: Starting stack_id=%s tenant_id=%s user_id=%s",
        str(stack_uuid)[:8], tenant_id, str(user_id)[:8] if user_id else None
    )

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id FROM stacks WHERE tenant_id = %s AND user_id = %s AND id = %s", (tenant_id, user_id, stack_uuid))
    stack_exists = cur.fetchone() is not None
    if not stack_exists:
        cur.close()
        conn.close()
        return jsonify({'error': 'Stack not found'}), 404

    # DIAGNOSTIC: First verify the stack exists
    if is_level(DEBUG):
        cur.execute("""
            SELECT id, name, user_id as stack_owner_id
            FROM stacks
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (tenant_id, user_id, stack_uuid,))
        stack_info = cur.fetchone()
        if stack_info:
            current_app.logger.debug(
                "log-stack: Stack found: name=%s owner=%s",
                stack_info['name'], str(stack_info['stack_owner_id'])[:8] if stack_info['stack_owner_id'] else 'None'
            )
        else:
            current_app.logger.warning("log-stack: Stack NOT FOUND")

    # Fetch stack inputs
    cur.execute("""
        SELECT health_input_id, dosage_override
        FROM stack_inputs
        WHERE user_id = %s AND stack_id = %s
    """, (user_id, stack_uuid,))
    stack_inputs = cur.fetchall()
    inputs_count = len(stack_inputs)

    current_app.logger.info("log-stack: Found %d inputs for stack_id=%s", inputs_count, str(stack_uuid)[:8])

    if inputs_count == 0:
        # This is the likely failure case - stack has no inputs
        current_app.logger.warning(
            "log-stack: NO INPUTS FOUND for stack_id=%s tenant_id=%s - check stack_inputs data",
            str(stack_uuid)[:8], tenant_id
        )

        # DEBUG: Try to see if inputs exist for this user
        if is_level(DEBUG):
            # Check raw counts for this user/stack
            cur.execute("SELECT COUNT(*) as cnt FROM stack_inputs WHERE user_id = %s AND stack_id = %s", (user_id, stack_uuid,))
            raw_count = cur.fetchone()
            current_app.logger.debug(
                "log-stack: Raw stack_inputs count (same query): %s",
                raw_count['cnt'] if raw_count else 0
            )

        cur.close()
        conn.close()
        return jsonify({
            'error': 'Stack has no inputs',
            'stack_id': str(stack_uuid),
            'inputs_found': 0,
            'inputs_logged': 0
        }), 400

    # Insert log entries for each input
    inserted = 0
    for item in stack_inputs:
        log_id = uuid.uuid4()
        try:
            cur.execute("""
                INSERT INTO health_input_log (tenant_id, id, user_id, logged_at, input_id, dosage_taken, stack_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (tenant_id, log_id, user_id, timestamp, item['health_input_id'], item['dosage_override'], stack_uuid, timestamp))
            inserted += 1

            if is_level(DEBUG):
                current_app.logger.debug(
                    "log-stack: Inserted log_id=%s for input_id=%s dosage=%s",
                    str(log_id)[:8], str(item['health_input_id'])[:8], item['dosage_override']
                )
        except Exception as e:
            current_app.logger.error(
                "log-stack: INSERT FAILED for input_id=%s: %s",
                str(item['health_input_id'])[:8], e
            )

    conn.commit()
    cur.close()
    conn.close()

    current_app.logger.info(
        "log-stack: Completed stack_id=%s inputs_found=%d inputs_logged=%d",
        str(stack_uuid)[:8], inputs_count, inserted
    )

    analytics.capture('stack_logged', {'stack_id': str(stack_uuid), 'item_count': inserted})

    return jsonify({
        'message': 'Stack logged successfully',
        'stack_id': str(stack_uuid),
        'inputs_found': inputs_count,
        'inputs_logged': inserted
    }), 201


@bp.route('/log-health-input', methods=['POST'])
@require_auth
def log_health_input():
    """Log a single health input (catalog or freeform)"""
    data = request.get_json(silent=True) or {}
    validation_error = validate_health_input_log_payload(data)
    if validation_error:
        return jsonify({'error': validation_error}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    timestamp = local_to_utc(data['timestamp'])
    log_id = uuid.uuid4()

    conn = get_db_connection()
    cur = conn.cursor()
    has_free_text = table_has_column(conn, 'health_input_log', 'free_text')
    has_free_dosage = table_has_column(conn, 'health_input_log', 'free_dosage')

    try:
        # Support both catalog (input_id + dosage) and freeform (free_text + free_dosage)
        input_id = parse_uuid(data.get('input_id')) if data.get('input_id') else None
        if data.get('input_id') and not input_id:
            return jsonify({'error': 'Invalid input_id'}), 400
        free_text = data.get('free_text')
        free_dosage = data.get('free_dosage')
        dosage_taken = data.get('dosage')  # backward compat

        if (free_text or free_dosage) and not (has_free_text and has_free_dosage):
            return jsonify({'error': 'freeform logging not supported by schema'}), 400
        if not input_id and not free_text:
            return jsonify({'error': 'input_id or free_text is required'}), 400

        if has_free_text and has_free_dosage:
            cur.execute("""
                INSERT INTO health_input_log
                    (tenant_id, id, user_id, logged_at, input_id, dosage_taken, free_text, free_dosage, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (tenant_id, log_id, user_id, timestamp, input_id, dosage_taken, free_text, free_dosage, timestamp))
        else:
            cur.execute("""
                INSERT INTO health_input_log
                    (tenant_id, id, user_id, logged_at, input_id, dosage_taken, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (tenant_id, log_id, user_id, timestamp, input_id, dosage_taken, timestamp))

        conn.commit()
        current_app.logger.info(
            "log-health-input: logged_id=%s input_id=%s freeform=%s",
            str(log_id)[:8], str(input_id)[:8] if input_id else 'None', bool(free_text)
        )
    except Exception as e:
        conn.rollback()
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/log-health-input', str(g.user.get('user_id', 'anon')))
            cur.close()
            conn.close()
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("log-health-input: INSERT FAILED: %s", e)
        cur.close()
        conn.close()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()

    analytics.capture('health_input_logged', {'log_id': str(log_id)})

    return jsonify({'id': str(log_id), 'message': 'Health input logged successfully'}), 201


@bp.route('/log-food-item', methods=['POST'])
@require_auth
def log_food_item():
    """Log a single food item (catalog or freeform)"""
    data = normalize_food_log_payload_v4(request.get_json(silent=True) or {})
    validation_error = validate_food_log_payload(data)
    if validation_error:
        return jsonify({'error': validation_error}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    timestamp = local_to_utc(data['timestamp'])
    log_id = uuid.uuid4()

    conn = get_db_connection()
    cur = conn.cursor()
    has_unit = table_has_column(conn, 'health_food_logv2', 'unit')
    has_notes = table_has_column(conn, 'health_food_logv2', 'notes')
    has_free_text = table_has_column(conn, 'health_food_logv2', 'free_text')
    has_photo_url = table_has_column(conn, 'health_food_logv2', 'photo_url')

    try:
        # Support both catalog (food_item_id + servings) and freeform (free_text + photo_url)
        food_item_id = parse_uuid(data.get('food_item_id')) if data.get('food_item_id') else None
        if data.get('food_item_id') and not food_item_id:
            return jsonify({'error': 'Invalid food_item_id'}), 400
        free_text = data.get('free_text')
        photo_url = data.get('photo_url')
        servings = data.get('servings', 1)
        unit = data.get('unit')
        notes_payload = parse_food_notes_payload(data)
        notes = json.dumps(notes_payload) if notes_payload else None

        if free_text and not has_free_text:
            return jsonify({'error': 'freeform food logging not supported by schema'}), 400
        if not food_item_id and not free_text:
            return jsonify({'error': 'food_item_id or free_text is required'}), 400

        columns = ["tenant_id", "id", "user_id", "logged_at", "food_item_id", "servings", "created_at"]
        params = [tenant_id, log_id, user_id, timestamp, food_item_id, servings, timestamp]
        if has_unit:
            columns.insert(-1, "unit")
            params.insert(-1, unit)
        if has_notes:
            columns.insert(-1, "notes")
            params.insert(-1, notes)
        if has_free_text:
            columns.insert(-1, "free_text")
            params.insert(-1, free_text)
        if has_photo_url:
            columns.insert(-1, "photo_url")
            params.insert(-1, photo_url)

        insert_query = sql.SQL(
            "INSERT INTO health_food_logv2 ({cols}) VALUES ({vals})"
        ).format(
            cols=sql.SQL(', ').join(sql.Identifier(c) for c in columns),
            vals=sql.SQL(', ').join(sql.Placeholder() * len(columns)),
        )
        cur.execute(insert_query, tuple(params))

        # Project nutrition before the commit so the projection lives or
        # dies with the log row. Returns None for freeform entries; that's
        # fine — they have no nutrition to project.
        from nutrition_projection import project_nutrition_for_food_log
        projected_metric_id = project_nutrition_for_food_log(
            conn, tenant_id, log_id, user_id,
        )

        conn.commit()
        current_app.logger.info(
            "log-food-item: logged_id=%s food_item_id=%s freeform=%s nutrition_projected=%s",
            str(log_id)[:8], str(food_item_id)[:8] if food_item_id else 'None',
            bool(free_text), bool(projected_metric_id),
        )
    except Exception as e:
        conn.rollback()
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/log-food-item', str(g.user.get('user_id', 'anon')))
            cur.close()
            conn.close()
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("log-food-item: INSERT FAILED: %s", e)
        cur.close()
        conn.close()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()

    analytics.capture('food_item_logged', {'log_id': str(log_id)})

    return jsonify({'id': str(log_id), 'message': 'Food item logged successfully'}), 201


@bp.route('/health-input-log', methods=['GET'])
@require_auth
def get_health_input_log():
    """Get a paginated list of health input log entries (including freeform).

    Optional query params:
        start_date, end_date (YYYY-MM-DD) for date filtering.
        input_id (UUID) to show only logs for a specific health input.
        input_type: 'medication' | 'supplement' | 'alternative' | 'treatment'
                    filters to logs whose joined health_input has that type.
                    Freeform logs (no input_id) are excluded when this is set.
        limit, offset for pagination (default 50, max 200).
    """
    from utils import parse_date_range_params
    start_date, end_date, err = parse_date_range_params()
    if err:
        return err

    ALLOWED_INPUT_TYPES = {'medication', 'supplement', 'alternative', 'treatment'}
    input_type_filter = request.args.get('input_type')
    if input_type_filter is not None:
        input_type_filter = input_type_filter.strip().lower() or None
    if input_type_filter and input_type_filter not in ALLOWED_INPUT_TYPES:
        return jsonify({
            'error': f"input_type must be one of {sorted(ALLOWED_INPUT_TYPES)}"
        }), 400

    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    hil_time_col = resolve_time_column(conn, 'health_input_log', preferred='logged_at', legacy='timestamp')
    has_free_text = table_has_column(conn, 'health_input_log', 'free_text')
    has_free_dosage = table_has_column(conn, 'health_input_log', 'free_dosage')
    has_promoted_at = table_has_column(conn, 'health_input_log', 'promoted_at')
    has_hil_is_deleted = table_has_column(conn, 'health_input_log', 'is_deleted')
    free_text_select = "hil.free_text" if has_free_text else "NULL::text AS free_text"
    free_dosage_select = "hil.free_dosage" if has_free_dosage else "NULL::text AS free_dosage"
    promoted_select = "hil.promoted_at" if has_promoted_at else "NULL::timestamp with time zone AS promoted_at"

    # Build WHERE clause
    conditions = [sql.SQL("hil.tenant_id = %s AND hil.user_id = %s")]
    params = [g.user.get('tenant_id', 1), get_user_id()]
    if has_hil_is_deleted:
        conditions.append(sql.SQL("hil.is_deleted = 0"))
    if start_date:
        conditions.append(sql.SQL("hil.{} >= %s").format(sql.Identifier(hil_time_col)))
        params.append(start_date)
    if end_date:
        conditions.append(sql.SQL("hil.{} < %s + INTERVAL '1 day'").format(sql.Identifier(hil_time_col)))
        params.append(end_date)
    input_id_filter = parse_uuid(request.args.get('input_id'))
    if input_id_filter:
        conditions.append(sql.SQL("hil.input_id = %s"))
        params.append(input_id_filter)
    if input_type_filter:
        # Requires a resolvable health_inputs row — freeform entries are excluded.
        conditions.append(sql.SQL("hi.input_type = %s"))
        params.append(input_type_filter)

    where_clause = sql.SQL("WHERE ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("")

    log_query = sql.SQL("""
        SELECT count(*) OVER() AS _total,
               hil.id, hil.{time_col} AS logged_at, hil.dosage_taken, {free_text_select}, {free_dosage_select}, {promoted_select},
               hi.name as input_name, hi.default_unit, hi.input_type,
               s.name as stack_name
        FROM health_input_log hil
        LEFT JOIN health_inputs hi ON hil.input_id = hi.id
        LEFT JOIN stacks s ON hil.stack_id = s.id
        {where_clause}
        ORDER BY logged_at DESC
        LIMIT %s OFFSET %s
    """).format(
        time_col=sql.Identifier(hil_time_col),
        free_text_select=sql.SQL(free_text_select),
        free_dosage_select=sql.SQL(free_dosage_select),
        promoted_select=sql.SQL(promoted_select),
        where_clause=where_clause,
    )
    cur.execute(log_query, params + [limit, offset])

    logs = cur.fetchall()
    cur.close()
    conn.close()

    total = logs[0]['_total'] if logs else 0
    for log in logs:
        log.pop('_total', None)
        log['id'] = str(log['id'])
        if log.get('logged_at'):
            log['timestamp'] = log.pop('logged_at').isoformat()
        # Compute is_freeform: true if input_id is null and free_text is not null
        log['is_freeform'] = log.get('free_text') is not None and log.get('promoted_at') is None

    has_more = offset + len(logs) < total
    resp = jsonify(paginated_response(logs, total, limit, offset, key='entries'))
    if has_more:
        resp.headers['X-Truncated'] = 'true'
    return resp


@bp.route('/all-logs', methods=['GET'])
@require_auth
def get_all_logs():
    """Get recent log entries from all sources, merged and sorted by time.

    Returns the standard {entries, pagination} envelope. Implementation runs
    six per-source SELECTs (each LIMIT 100), merges in Python, and slices the
    requested page. Total rows fetched is bounded at ~600; total in the
    envelope reflects the merged set size, not the underlying source counts.
    A UNION-as-CTE rewrite is deferred; see UserAPIPagination plan Option A.
    """
    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    all_logs = []
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    hil_time_col = resolve_time_column(conn, 'health_input_log', preferred='logged_at', legacy='timestamp')
    hfl_time_col = resolve_time_column(conn, 'health_food_logv2', preferred='logged_at', legacy='timestamp')
    hfl_qty_col = 'servings' if table_has_column(conn, 'health_food_logv2', 'servings') else 'quantity'
    hm_time_col = 'recorded_at' if table_has_column(conn, 'health_metrics', 'recorded_at') else 'timestamp'
    bp_time_col = 'measured_at' if table_has_column(conn, 'health_blood_pressure_readings', 'measured_at') else 'timestamp'
    has_hil_is_deleted = table_has_column(conn, 'health_input_log', 'is_deleted')
    has_hfl_is_deleted = table_has_column(conn, 'health_food_logv2', 'is_deleted')
    has_hm_is_deleted = table_has_column(conn, 'health_metrics', 'is_deleted')
    has_bp_is_deleted = table_has_column(conn, 'health_blood_pressure_readings', 'is_deleted')
    has_hil_free_text = table_has_column(conn, 'health_input_log', 'free_text')
    has_hil_free_dosage = table_has_column(conn, 'health_input_log', 'free_dosage')
    has_hfl_unit = table_has_column(conn, 'health_food_logv2', 'unit')
    has_hfl_free_text = table_has_column(conn, 'health_food_logv2', 'free_text')
    join_food_on_tenant = table_has_column(conn, 'health_food_logv2', 'tenant_id') and table_has_column(conn, 'health_food_itemsv2', 'tenant_id')
    food_join = "fl.tenant_id = fi.tenant_id AND fl.food_item_id = fi.id" if join_food_on_tenant else "fl.food_item_id = fi.id"
    hil_deleted_filter = "AND hil.is_deleted = 0" if has_hil_is_deleted else ""
    bp_deleted_filter = "AND is_deleted = 0" if has_bp_is_deleted else ""
    hm_deleted_prefix = "AND is_deleted = 0" if has_hm_is_deleted else ""
    hfl_deleted_filter = "AND fl.is_deleted = 0" if has_hfl_is_deleted else ""

    # Get health input logs (including freeform)
    cur.execute(sql.SQL("""
        SELECT hil.id, hil.{time_col} AS logged_at, hil.dosage_taken,
               {free_text_select},
               {free_dosage_select},
               hi.name as input_name, hi.default_unit,
               s.name as stack_name
        FROM health_input_log hil
        LEFT JOIN health_inputs hi ON hil.input_id = hi.id
        LEFT JOIN stacks s ON hil.stack_id = s.id
        WHERE hil.tenant_id = %s AND hil.user_id = %s {deleted_filter}
        ORDER BY logged_at DESC
        LIMIT 100
    """).format(
        time_col=sql.Identifier(hil_time_col),
        free_text_select=sql.SQL('hil.free_text' if has_hil_free_text else 'NULL::text AS free_text'),
        free_dosage_select=sql.SQL('hil.free_dosage' if has_hil_free_dosage else 'NULL::text AS free_dosage'),
        deleted_filter=sql.SQL(hil_deleted_filter),
    ), (tenant_id, user_id))
    health_logs = cur.fetchall()
    for log in health_logs:
        # Use freeform text if input not found, else use catalog name
        display_name = log['input_name'] or log['free_text'] or 'Unknown medication'
        dosage = log['dosage_taken'] or log['free_dosage'] or ''
        default_unit = log['default_unit'] or ''
        all_logs.append({
            'id': str(log['id']),
            'timestamp': log['logged_at'].isoformat(),
            'type': 'health_input',
            'description': f"{display_name} - {dosage} {default_unit}".strip(),
            'stack': log['stack_name'] or 'PRN',
            'input_name': display_name,
            'dosage_taken': dosage,
            'default_unit': default_unit,
            'is_freeform': log['free_text'] is not None
        })

    # Get blood pressure readings
    cur.execute(sql.SQL("""
        SELECT id, {time_col} AS measured_at, systolic, diastolic, pulse
        FROM health_blood_pressure_readings
        WHERE tenant_id = %s AND user_id = %s {deleted_filter}
        ORDER BY measured_at DESC
        LIMIT 100
    """).format(
        time_col=sql.Identifier(bp_time_col),
        deleted_filter=sql.SQL(bp_deleted_filter),
    ), (tenant_id, user_id))
    bp_logs = cur.fetchall()
    for log in bp_logs:
        pulse_str = f", HR: {log['pulse']}" if log['pulse'] else ""
        all_logs.append({
            'id': str(log['id']),
            'timestamp': log['measured_at'].isoformat(),
            'type': 'blood_pressure',
            'description': f"BP: {log['systolic']}/{log['diastolic']}{pulse_str}",
            'stack': None
        })

    # Get temperature readings
    cur.execute(sql.SQL("""
        SELECT id, {time_col} AS recorded_at, value, unit
        FROM health_metrics
        WHERE tenant_id = %s AND user_id = %s AND metric_type = 'temperature'
        {deleted_prefix}
        ORDER BY recorded_at DESC
        LIMIT 100
    """).format(
        time_col=sql.Identifier(hm_time_col),
        deleted_prefix=sql.SQL(hm_deleted_prefix),
    ), (tenant_id, user_id))
    temp_logs = cur.fetchall()
    for log in temp_logs:
        all_logs.append({
            'id': str(log['id']),
            'timestamp': log['recorded_at'].isoformat(),
            'type': 'temperature',
            'description': f"Temperature: {log['value']}°{log['unit']}",
            'stack': None
        })

    # Get weight readings
    cur.execute(sql.SQL("""
        SELECT id, {time_col} AS recorded_at, value, unit
        FROM health_metrics
        WHERE tenant_id = %s AND user_id = %s AND metric_type = 'weight'
        {deleted_prefix}
        ORDER BY recorded_at DESC
        LIMIT 100
    """).format(
        time_col=sql.Identifier(hm_time_col),
        deleted_prefix=sql.SQL(hm_deleted_prefix),
    ), (tenant_id, user_id))
    weight_logs = cur.fetchall()
    for log in weight_logs:
        all_logs.append({
            'id': str(log['id']),
            'timestamp': log['recorded_at'].isoformat(),
            'type': 'weight',
            'description': f"Weight: {log['value']} {log['unit']}",
            'stack': None
        })

    # Get blood glucose readings
    cur.execute(sql.SQL("""
        SELECT id, {time_col} AS recorded_at, value, unit
        FROM health_metrics
        WHERE tenant_id = %s AND user_id = %s AND metric_type = 'blood_glucose'
        {deleted_prefix}
        ORDER BY recorded_at DESC
        LIMIT 100
    """).format(
        time_col=sql.Identifier(hm_time_col),
        deleted_prefix=sql.SQL(hm_deleted_prefix),
    ), (tenant_id, user_id))
    glucose_logs = cur.fetchall()
    for log in glucose_logs:
        all_logs.append({
            'id': str(log['id']),
            'timestamp': log['recorded_at'].isoformat(),
            'type': 'blood_glucose',
            'description': f"Blood Glucose: {log['value']} {log['unit']}",
            'stack': None
        })

    # Get synced health metrics — exclude high-frequency telemetry (steps, heart_rate)
    # which can produce 1,000+ rows/day and drown out user-entered logs.
    # Steps/HR belong in the Analysis views, not the activity timeline.
    cur.execute(sql.SQL("""
        SELECT id, {time_col} AS recorded_at, metric_type, value, unit, notes, source
        FROM health_metrics
        WHERE tenant_id = %s AND user_id = %s AND metric_type IN ('sleep','nutrition')
        {deleted_prefix}
        ORDER BY recorded_at DESC
        LIMIT 50
    """).format(
        time_col=sql.Identifier(hm_time_col),
        deleted_prefix=sql.SQL(hm_deleted_prefix),
    ), (tenant_id, user_id))
    metric_logs = cur.fetchall()
    for log in metric_logs:
        if log['metric_type'] == 'sleep':
            desc = f"Sleep: {log['value']} {log['unit'] or 'hours'}"
        elif log['metric_type'] == 'nutrition':
            desc = f"Calories: {log['value']} {log['unit'] or 'kcal'}"
        else:
            desc = f"{log['metric_type']}: {log['value']}"

        all_logs.append({
            'id': str(log['id']),
            'timestamp': log['recorded_at'].isoformat(),
            'type': log['metric_type'],
            'description': desc,
            'stack': None,
            'source': log.get('source')
        })

    # Get medication metrics
    cur.execute(sql.SQL("""
        SELECT id, {time_col} AS recorded_at, metric_type, value, unit, notes, source
        FROM health_metrics
        WHERE tenant_id = %s AND user_id = %s AND metric_type = 'medication'
        {deleted_prefix}
        ORDER BY recorded_at DESC
        LIMIT 100
    """).format(
        time_col=sql.Identifier(hm_time_col),
        deleted_prefix=sql.SQL(hm_deleted_prefix),
    ), (tenant_id, user_id))
    med_logs = cur.fetchall()
    for log in med_logs:
        name = None
        status = None
        dosage = None
        try:
            raw = log.get('notes')
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
                or (log.get('unit') if log.get('unit') not in (None, '', 'dose') else None)
            )
        except Exception:
            pass
        parts = [p for p in [name, dosage, status] if p]
        desc = "Medication: " + (", ".join(parts) if parts else str(log['value'] or 'taken'))
        all_logs.append({
            'id': str(log['id']),
            'timestamp': log['recorded_at'].isoformat(),
            'type': log['metric_type'],
            'description': desc,
            'stack': None,
            'source': log.get('source'),
            'medication_name': name,
            'medication_status': status,
            'medication_dosage': dosage
        })

    # Get food logs (including freeform)
    cur.execute(sql.SQL("""
        SELECT fl.id, fl.{time_col} AS logged_at, fl.{qty_col} AS servings,
               {unit_select},
               fl.notes, fl.food_item_id,
               {free_text_select},
               fi.name as food_name
        FROM health_food_logv2 fl
        LEFT JOIN health_food_itemsv2 fi ON {food_join}
        WHERE fl.tenant_id = %s AND fl.user_id = %s {deleted_filter}
        ORDER BY logged_at DESC
        LIMIT 100
    """).format(
        time_col=sql.Identifier(hfl_time_col),
        qty_col=sql.Identifier(hfl_qty_col),
        unit_select=sql.SQL('fl.unit' if has_hfl_unit else 'NULL::text AS unit'),
        free_text_select=sql.SQL('fl.free_text' if has_hfl_free_text else 'NULL::text AS free_text'),
        food_join=sql.SQL(food_join),
        deleted_filter=sql.SQL(hfl_deleted_filter),
    ), (tenant_id, user_id))
    food_logs = cur.fetchall()
    for log in food_logs:
        # Use freeform text if food item not found, else use catalog name
        display_name = log['food_name'] or log['free_text'] or 'Unknown food'
        notes = log.get('notes')
        notes_obj = {}
        if notes:
            try:
                notes_obj = json.loads(notes) if isinstance(notes, str) else (notes if isinstance(notes, dict) else {})
            except Exception:
                notes_obj = {}
        all_logs.append({
            'id': str(log['id']),
            'timestamp': log['logged_at'].isoformat(),
            'type': 'food',
            'description': f"{display_name} x{log['servings']}",
            'stack': None,
            'food_item_id': str(log['food_item_id']) if log['food_item_id'] else None,
            'food_name': display_name,
            'servings': float(log['servings']) if log['servings'] else 1,
            'unit': log.get('unit'),
            'calories': notes_obj.get('calories') if isinstance(notes_obj, dict) else None,
            'protein_g': notes_obj.get('protein_g') if isinstance(notes_obj, dict) else None,
            'carbs_g': notes_obj.get('carbs_g') if isinstance(notes_obj, dict) else None,
            'fat_g': notes_obj.get('fat_g') if isinstance(notes_obj, dict) else None,
            'fiber_g': notes_obj.get('fiber_g') if isinstance(notes_obj, dict) else None,
            'sugar_g': notes_obj.get('sugar_g') if isinstance(notes_obj, dict) else None,
            'sodium_mg': notes_obj.get('sodium_mg') if isinstance(notes_obj, dict) else None,
            'potassium_mg': notes_obj.get('potassium_mg') if isinstance(notes_obj, dict) else None,
            'meal': notes_obj.get('meal') if isinstance(notes_obj, dict) else None,
            'is_freeform': log['free_text'] is not None
        })

    cur.close()
    conn.close()

    # Sort all logs by timestamp (most recent first)
    all_logs.sort(key=lambda x: x['timestamp'], reverse=True)

    total = len(all_logs)
    sliced = all_logs[offset:offset + limit]
    return jsonify(paginated_response(sliced, total, limit, offset, key='entries'))


@bp.route('/health-input-log/<log_id>', methods=['PUT'])
@require_auth
def update_health_input_log(log_id):
    """Update a health input log entry (including promotion)"""
    data = request.get_json(silent=True) or {}
    tenant_id = g.user.get('tenant_id', 1)
    log_id_uuid = parse_uuid(log_id)
    if not log_id_uuid:
        return jsonify({'error': 'Invalid log id'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    has_free_text = table_has_column(conn, 'health_input_log', 'free_text')
    has_free_dosage = table_has_column(conn, 'health_input_log', 'free_dosage')

    try:
        logged_at = local_to_utc(data['timestamp']) if data.get('timestamp') else datetime.now(pytz.utc)
        input_id = parse_uuid(data.get('input_id')) if data.get('input_id') else None
        if data.get('input_id') and not input_id:
            return jsonify({'error': 'Invalid input_id'}), 400

        # Dynamic SET clause - only update fields that are provided
        updates = [sql.SQL('logged_at = %s')]
        params = [logged_at]

        if 'dosage' in data:
            updates.append(sql.SQL('dosage_taken = %s'))
            params.append(data['dosage'])
        if has_free_text and 'free_text' in data:
            updates.append(sql.SQL('free_text = %s'))
            params.append(data.get('free_text'))
        if has_free_dosage and 'free_dosage' in data:
            updates.append(sql.SQL('free_dosage = %s'))
            params.append(data.get('free_dosage'))

        # On promotion (input_id supplied): set promoted_at if not already set
        if input_id:
            updates.append(sql.SQL('input_id = %s'))
            params.append(input_id)
            if table_has_column(conn, 'health_input_log', 'promoted_at'):
                updates.append(sql.SQL('promoted_at = COALESCE(promoted_at, %s)'))
                params.append(logged_at)

        params.extend([tenant_id, log_id_uuid])
        cur.execute(sql.SQL("""
            UPDATE health_input_log
            SET {set_clause}
            WHERE tenant_id = %s AND id = %s
        """).format(set_clause=sql.SQL(', ').join(updates)), params)
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({'error': 'Log entry not found'}), 404

        conn.commit()
        current_app.logger.info("health-input-log PUT: log_id=%s promoted=%s", str(log_id_uuid)[:8], bool(input_id))
    except Exception as e:
        conn.rollback()
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/health-input-log', str(g.user.get('user_id', 'anon')))
            cur.close()
            conn.close()
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("health-input-log PUT FAILED: %s", e)
        cur.close()
        conn.close()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()

    return jsonify({'message': 'Log entry updated'})


@bp.route('/health-input-log/<log_id>', methods=['DELETE'])
@require_auth
def delete_health_input_log(log_id):
    """Delete a health input log entry"""
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    log_uuid = parse_uuid(log_id)
    if not log_uuid:
        return jsonify({'error': 'Invalid log id'}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM health_input_log
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, log_uuid))

    if cur.rowcount == 0:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({'error': 'Log entry not found'}), 404
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': 'Log entry deleted'})


@bp.route('/food-log', methods=['GET'])
@require_auth
def get_food_log():
    """Get a paginated list of food log entries.

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
    hfl_time_col = resolve_time_column(conn, 'health_food_logv2', preferred='logged_at', legacy='timestamp')
    hfl_qty_col = 'servings' if table_has_column(conn, 'health_food_logv2', 'servings') else 'quantity'
    has_food_free_text = table_has_column(conn, 'health_food_logv2', 'free_text')
    has_food_promoted = table_has_column(conn, 'health_food_logv2', 'promoted_at')
    has_hfl_is_deleted = table_has_column(conn, 'health_food_logv2', 'is_deleted')
    join_food_on_tenant = table_has_column(conn, 'health_food_logv2', 'tenant_id') and table_has_column(conn, 'health_food_itemsv2', 'tenant_id')
    food_join = "fl.tenant_id = fi.tenant_id AND fl.food_item_id = fi.id" if join_food_on_tenant else "fl.food_item_id = fi.id"

    # Build WHERE clause
    conditions = [sql.SQL("fl.tenant_id = %s AND fl.user_id = %s")]
    params = [g.user.get('tenant_id', 1), get_user_id()]
    if has_hfl_is_deleted:
        conditions.append(sql.SQL("fl.is_deleted = 0"))
    if start_date:
        conditions.append(sql.SQL("fl.{} >= %s").format(sql.Identifier(hfl_time_col)))
        params.append(start_date)
    if end_date:
        conditions.append(sql.SQL("fl.{} < %s + INTERVAL '1 day'").format(sql.Identifier(hfl_time_col)))
        params.append(end_date)

    where_clause = sql.SQL("WHERE ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("")

    food_log_query = sql.SQL("""
        SELECT count(*) OVER() AS _total,
               fl.id, fl.{time_col} AS logged_at, fl.{qty_col} AS servings, fl.food_item_id,
               {free_text_select},
               {promoted_select},
               fi.name as food_name
        FROM health_food_logv2 fl
        LEFT JOIN health_food_itemsv2 fi ON {food_join}
        {where_clause}
        ORDER BY logged_at DESC
        LIMIT %s OFFSET %s
    """).format(
        time_col=sql.Identifier(hfl_time_col),
        qty_col=sql.Identifier(hfl_qty_col),
        free_text_select=sql.SQL('fl.free_text' if has_food_free_text else 'NULL::text AS free_text'),
        promoted_select=sql.SQL('fl.promoted_at' if has_food_promoted else 'NULL::timestamp with time zone AS promoted_at'),
        food_join=sql.SQL(food_join),
        where_clause=where_clause,
    )
    cur.execute(food_log_query, params + [limit, offset])

    logs = cur.fetchall()
    cur.close()
    conn.close()

    total = logs[0]['_total'] if logs else 0
    for log in logs:
        log.pop('_total', None)
        log['id'] = str(log['id'])
        log['food_item_id'] = str(log['food_item_id']) if log.get('food_item_id') else None
        if log.get('logged_at'):
            log['timestamp'] = log.pop('logged_at').isoformat()
        log['is_freeform'] = bool(log.get('free_text')) and not bool(log.get('promoted_at'))

    has_more = offset + len(logs) < total
    resp = jsonify(paginated_response(logs, total, limit, offset, key='entries'))
    if has_more:
        resp.headers['X-Truncated'] = 'true'
    return resp


@bp.route('/food-log/<log_id>', methods=['PUT'])
@require_auth
def update_food_log(log_id):
    """Update a food log entry (including promotion)"""
    data = normalize_food_log_payload_v4(request.get_json(silent=True) or {})
    tenant_id = g.user.get('tenant_id', 1)
    log_id_uuid = parse_uuid(log_id)
    if not log_id_uuid:
        return jsonify({'error': 'Invalid log id'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    has_unit = table_has_column(conn, 'health_food_logv2', 'unit')
    has_notes = table_has_column(conn, 'health_food_logv2', 'notes')
    has_free_text = table_has_column(conn, 'health_food_logv2', 'free_text')
    has_photo_url = table_has_column(conn, 'health_food_logv2', 'photo_url')
    has_promoted = table_has_column(conn, 'health_food_logv2', 'promoted_at')

    try:
        now = datetime.now(pytz.utc)
        timestamp = local_to_utc(data['timestamp']) if data.get('timestamp') else now
        food_item_id = parse_uuid(data.get('food_item_id')) if data.get('food_item_id') else None
        if data.get('food_item_id') and not food_item_id:
            return jsonify({'error': 'Invalid food_item_id'}), 400
        unit = data.get('unit')
        notes_payload = parse_food_notes_payload(data)
        notes = json.dumps(notes_payload) if notes_payload else None

        # Dynamic SET clause - only update fields that are provided
        updates = [sql.SQL('logged_at = %s')]
        params = [timestamp]

        if 'servings' in data:
            updates.append(sql.SQL('servings = %s'))
            params.append(data.get('servings', 1))
        if has_unit and unit is not None:
            updates.append(sql.SQL('unit = %s'))
            params.append(unit)
        if has_notes and notes is not None:
            updates.append(sql.SQL('notes = %s'))
            params.append(notes)
        if has_free_text and 'free_text' in data:
            updates.append(sql.SQL('free_text = %s'))
            params.append(data.get('free_text'))
        if has_photo_url and 'photo_url' in data:
            updates.append(sql.SQL('photo_url = %s'))
            params.append(data.get('photo_url'))

        # On promotion (food_item_id supplied): set promoted_at if not already set
        if food_item_id:
            updates.append(sql.SQL('food_item_id = %s'))
            params.append(food_item_id)
            if has_promoted:
                updates.append(sql.SQL('promoted_at = COALESCE(promoted_at, %s)'))
                params.append(timestamp)

        params.extend([tenant_id, log_id_uuid])
        cur.execute(sql.SQL("""
            UPDATE health_food_logv2
            SET {set_clause}
            WHERE tenant_id = %s AND id = %s
        """).format(set_clause=sql.SQL(', ').join(updates)), params)
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({'error': 'Food log entry not found'}), 404

        conn.commit()
        current_app.logger.info("food-log PUT: log_id=%s promoted=%s", str(log_id_uuid)[:8], bool(food_item_id))
    except Exception as e:
        conn.rollback()
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/food-log', str(g.user.get('user_id', 'anon')))
            cur.close()
            conn.close()
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("food-log PUT FAILED: %s", e)
        cur.close()
        conn.close()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()

    return jsonify({'message': 'Food log entry updated'})


@bp.route('/food-log/<log_id>', methods=['DELETE'])
@require_auth
def delete_food_log(log_id):
    """Delete a food log entry"""
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    log_uuid = parse_uuid(log_id)
    if not log_uuid:
        return jsonify({'error': 'Invalid log id'}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM health_food_logv2
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, log_uuid))

    if cur.rowcount == 0:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({'error': 'Food log entry not found'}), 404
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': 'Food log entry deleted'})


# ============================================================================
# LOG PROMOTIONS (AI/fuzzy-match suggestions)
# ============================================================================

@bp.route('/log-promotions', methods=['GET'])
@require_auth
def get_log_promotions():
    """List log promotion suggestions, optionally filtered by status"""
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    status = request.args.get('status')  # Optional filter: pending, accepted, dismissed, auto_linked
    limit, offset = parse_pagination_params(default_limit=50, max_limit=200)

    conn = get_db_connection()
    cur = conn.cursor()
    if not table_has_column(conn, 'log_promotions', 'id'):
        cur.close()
        conn.close()
        return jsonify(paginated_response([], 0, limit, offset, key='entries'))

    has_tenant_id = table_has_column(conn, 'log_promotions', 'tenant_id')
    has_user_id = table_has_column(conn, 'log_promotions', 'user_id')
    has_is_deleted = table_has_column(conn, 'log_promotions', 'is_deleted')

    query = """
        SELECT count(*) OVER() AS _total,
               id, source_table, source_log_id, suggested_catalog_table, suggested_catalog_id,
               free_text_original, match_confidence, match_method, status, resolved_at, created_at
        FROM log_promotions
        WHERE 1=1
    """
    params = []

    if has_tenant_id:
        query += " AND tenant_id = %s"
        params.append(tenant_id)
    if has_user_id:
        query += " AND user_id = %s"
        params.append(user_id)
    if has_is_deleted:
        query += " AND is_deleted = 0"

    if status:
        query += " AND status = %s"
        params.append(status)

    query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    cur.execute(query, params)
    promotions = cur.fetchall()
    cur.close()
    conn.close()

    total = promotions[0]['_total'] if promotions else 0
    for promo in promotions:
        promo.pop('_total', None)
        promo['id'] = str(promo['id'])
        promo['source_log_id'] = str(promo['source_log_id'])
        if promo.get('suggested_catalog_id'):
            promo['suggested_catalog_id'] = str(promo['suggested_catalog_id'])

    return jsonify(paginated_response(promotions, total, limit, offset, key='entries'))


@bp.route('/log-promotions', methods=['POST'])
@require_auth
def create_log_promotion():
    """Create a new log promotion suggestion"""
    data = request.get_json(silent=True) or {}
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    promo_id = uuid.uuid4()

    source_table = data.get('source_table')  # 'health_input_log' or 'health_food_logv2'
    valid_source_tables = {'health_input_log', 'health_food_logv2'}
    if source_table not in valid_source_tables:
        return jsonify({'error': f'Invalid source_table. Must be one of: {sorted(valid_source_tables)}'}), 400

    source_log_id_raw = data.get('source_log_id')
    source_log_id = parse_uuid(source_log_id_raw)
    if not source_log_id_raw:
        return jsonify({'error': 'source_log_id is required'}), 400
    if source_log_id is None:
        return jsonify({'error': 'Invalid source_log_id'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    if not table_has_column(conn, 'log_promotions', 'id'):
        cur.close()
        conn.close()
        return jsonify({'error': 'log promotions unavailable'}), 404

    try:
        suggested_catalog_table = data.get('suggested_catalog_table')
        suggested_catalog_id = parse_uuid(data.get('suggested_catalog_id')) if data.get('suggested_catalog_id') else None
        free_text_original = data.get('free_text_original')
        match_confidence = float(data.get('match_confidence', 0.0))
        match_method = data.get('match_method')  # 'exact', 'fuzzy', 'ai', 'user'

        cur.execute("""
            INSERT INTO log_promotions
                (tenant_id, id, user_id, source_table, source_log_id, suggested_catalog_table,
                 suggested_catalog_id, free_text_original, match_confidence, match_method, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (tenant_id, promo_id, user_id, source_table, source_log_id, suggested_catalog_table,
              suggested_catalog_id, free_text_original, match_confidence, match_method, 'pending', datetime.now(pytz.utc)))

        conn.commit()
        current_app.logger.info("log-promotions POST: created promo_id=%s", str(promo_id)[:8])
    except Exception as e:
        conn.rollback()
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/log-promotions', str(g.user.get('user_id', 'anon')))
            cur.close()
            conn.close()
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("log-promotions POST FAILED: %s", e)
        cur.close()
        conn.close()
        if isinstance(e, db_driver.UndefinedTable):
            return jsonify({'error': 'log promotions unavailable'}), 404
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()

    return jsonify({'id': str(promo_id), 'message': 'Promotion suggestion created'}), 201


@bp.route('/log-promotions/<promo_id>', methods=['PUT'])
@require_auth
def update_log_promotion(promo_id):
    """Accept or dismiss a log promotion (on accept, backfill source log FK)"""
    data = request.get_json(silent=True) or {}
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    promo_id_uuid = parse_uuid(promo_id)
    if not promo_id_uuid:
        return jsonify({'error': 'Invalid promotion id'}), 400
    status = data.get('status')  # 'accepted', 'dismissed', 'auto_linked'

    conn = get_db_connection()
    cur = conn.cursor()
    if not table_has_column(conn, 'log_promotions', 'id'):
        cur.close()
        conn.close()
        return jsonify({'error': 'Promotion not found'}), 404

    try:
        # Fetch promotion to verify ownership and get details
        cur.execute("""
            SELECT source_table, source_log_id, suggested_catalog_id
            FROM log_promotions
            WHERE tenant_id = %s AND id = %s AND user_id = %s
        """, (tenant_id, promo_id_uuid, user_id))

        promo = cur.fetchone()
        if not promo:
            cur.close()
            conn.close()
            return jsonify({'error': 'Promotion not found'}), 404

        now = datetime.now(pytz.utc)

        # If accepting: backfill source log with FK + promoted_at in a transaction
        if status == 'accepted':
            source_table = promo['source_table']
            source_log_id = promo['source_log_id']
            suggested_catalog_id = promo['suggested_catalog_id']

            # Determine which FK column to update
            if source_table == 'health_input_log':
                fk_column = 'input_id'
            elif source_table == 'health_food_logv2':
                fk_column = 'food_item_id'
            else:
                cur.close()
                conn.close()
                return jsonify({'error': 'Invalid source_table'}), 400

            # Update source log: set FK + promoted_at
            cur.execute(sql.SQL("""
                UPDATE {table}
                SET {fk_col} = %s, promoted_at = COALESCE(promoted_at, %s)
                WHERE tenant_id = %s AND id = %s
            """).format(
                table=sql.Identifier(source_table),
                fk_col=sql.Identifier(fk_column),
            ), (suggested_catalog_id, now, tenant_id, source_log_id))

        # Update promotion: set status + resolved_at
        cur.execute("""
            UPDATE log_promotions
            SET status = %s, resolved_at = %s, updated_at = %s
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (status, now, now, tenant_id, user_id, promo_id_uuid))

        conn.commit()
        current_app.logger.info("log-promotions PUT: promo_id=%s status=%s", str(promo_id_uuid)[:8], status)
    except Exception as e:
        conn.rollback()
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/log-promotions', str(g.user.get('user_id', 'anon')))
            cur.close()
            conn.close()
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("log-promotions PUT FAILED: %s", e)
        cur.close()
        conn.close()
        if isinstance(e, db_driver.UndefinedTable):
            return jsonify({'error': 'Promotion not found'}), 404
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()

    return jsonify({'message': f'Promotion {status}'})


@bp.route('/log-promotions/<promo_id>', methods=['DELETE'])
@require_auth
def delete_log_promotion(promo_id):
    """Soft-delete a log promotion"""
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    promo_id_uuid = parse_uuid(promo_id)
    if not promo_id_uuid:
        return jsonify({'error': 'Invalid promotion id'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    if not table_has_column(conn, 'log_promotions', 'id'):
        cur.close()
        conn.close()
        return jsonify({'error': 'Promotion not found'}), 404

    try:
        cur.execute("""
            UPDATE log_promotions
            SET is_deleted = 1, updated_at = %s
            WHERE tenant_id = %s AND id = %s AND user_id = %s
        """, (datetime.now(pytz.utc), tenant_id, promo_id_uuid, user_id))

        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({'error': 'Promotion not found'}), 404
        conn.commit()
        current_app.logger.info("log-promotions DELETE: promo_id=%s", str(promo_id_uuid)[:8])
    except Exception as e:
        conn.rollback()
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/log-promotions', str(g.user.get('user_id', 'anon')))
            cur.close()
            conn.close()
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("log-promotions DELETE FAILED: %s", e)
        cur.close()
        conn.close()
        if isinstance(e, db_driver.UndefinedTable):
            return jsonify({'error': 'Promotion not found'}), 404
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close()
        conn.close()

    return jsonify({'message': 'Promotion deleted'})


# ============================================================================
# ADHERENCE REPORT
# ============================================================================

@bp.route('/adherence', methods=['GET'])
@require_auth
def get_adherence():
    """Per-input adherence report: scheduled vs. logged doses.

    Query params:
        start_date, end_date (YYYY-MM-DD) — date range. Defaults to the
                               last 30 days. Capped at 90 days by
                               parse_date_range_params.
        input_ids (comma-separated UUIDs, optional) — restrict to these inputs.

    Response:
        {
          "window": {"from": "...", "to": "...", "days": N},
          "inputs": [
            {
              "input_id": "...", "name": "...", "input_type": "medication",
              "doses_per_day": 2, "usage_type": "scheduled",
              "scheduled_doses": 60, "logged_doses": 48,
              "pct_adherence": 80.0,
              "missed_windows": [{"date": "2026-04-05", "expected": 2, "logged": 0}],
              "timeframes": [{"id": "...", "name": "Wake Time"}]
            },
            ...
          ],
          "excluded_prn": [{"input_id": "...", "name": "..."}],
          "excluded_unspecified": [{"input_id": "...", "name": "..."}]
        }

    PRN inputs (doses_per_day = -1) and unspecified (NULL doses_per_day) are
    excluded from the `inputs` list and surfaced in the `excluded_*` fields
    so callers can note why adherence isn't reported. See plan Risk #2.
    """
    start_date, end_date, err = parse_date_range_params()
    if err:
        return err

    today = datetime.now(pytz.utc).date()
    if start_date is None and end_date is None:
        end_date = today
        start_date = today - timedelta(days=29)
    elif start_date is None:
        assert end_date is not None
        start_date = end_date - timedelta(days=29)
    elif end_date is None:
        end_date = min(today, start_date + timedelta(days=29))
    assert start_date is not None and end_date is not None

    days = (end_date - start_date).days + 1
    if days <= 0:
        return jsonify({'error': 'end_date must be on or after start_date'}), 400

    input_ids_param = request.args.get('input_ids')
    input_id_filter = None
    if input_ids_param:
        parts = [p.strip() for p in input_ids_param.split(',') if p.strip()]
        parsed = [parse_uuid(p) for p in parts]
        if any(x is None for x in parsed):
            return jsonify({'error': 'input_ids must be comma-separated UUIDs'}), 400
        input_id_filter = parsed

    user_id = get_user_id()
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        where_clause = sql.SQL("WHERE hi.is_active = true")
        params = []
        if input_id_filter:
            where_clause = sql.SQL("{base} AND hi.id = ANY(%s::uuid[])").format(base=where_clause)
            params.append(input_id_filter)

        cur.execute(sql.SQL("""
            SELECT hi.id, hi.name, hi.input_type, hi.default_unit,
                   hi.doses_per_day, hi.timeframe_id AS direct_timeframe_id,
                   COALESCE(
                       json_agg(
                           DISTINCT jsonb_build_object(
                               'id', tf.id,
                               'name', tf.name,
                               'time_of_day', to_char(tf.time_of_day, 'HH24:MI'),
                               'frequency', tf.frequency,
                               'source', CASE
                                   WHEN tf.id = hi.timeframe_id THEN 'direct'
                                   ELSE 'stack'
                               END
                           )
                       ) FILTER (WHERE tf.id IS NOT NULL),
                       '[]'::json
                   ) AS timeframes
            FROM health_inputs hi
            LEFT JOIN stack_inputs si
                   ON si.tenant_id = hi.tenant_id AND si.health_input_id = hi.id
            LEFT JOIN stacks s
                   ON s.tenant_id = si.tenant_id AND s.id = si.stack_id
                  AND s.is_active = true
            LEFT JOIN timeframes tf
                   ON (tf.id = s.timeframe_id OR tf.id = hi.timeframe_id)
                  AND tf.tenant_id = hi.tenant_id
                  AND tf.is_active = true
            {where}
            GROUP BY hi.id, hi.name, hi.input_type, hi.default_unit,
                     hi.doses_per_day, hi.timeframe_id
            ORDER BY hi.name
        """).format(where=where_clause), params)
        input_rows = cur.fetchall()

        scheduled_rows = [r for r in input_rows if r['doses_per_day'] and r['doses_per_day'] > 0]
        excluded_prn = [{'input_id': str(r['id']), 'name': r['name']} for r in input_rows if r['doses_per_day'] == -1]
        excluded_unspecified = [{'input_id': str(r['id']), 'name': r['name']} for r in input_rows if r['doses_per_day'] is None]

        results = []
        if scheduled_rows:
            scheduled_ids = [r['id'] for r in scheduled_rows]
            cur.execute("""
                SELECT input_id,
                       (logged_at AT TIME ZONE 'UTC')::date AS log_date,
                       COUNT(*) AS log_count
                FROM health_input_log
                WHERE user_id = %s
                  AND input_id = ANY(%s::uuid[])
                  AND logged_at >= %s
                  AND logged_at < %s::date + INTERVAL '1 day'
                GROUP BY input_id, log_date
            """, (user_id, scheduled_ids, start_date, end_date))
            log_rows = cur.fetchall()

            # (input_id, date) -> count
            logs_by_input_day: dict = {}
            # input_id -> total count
            logs_by_input: dict = {}
            for lr in log_rows:
                key = (lr['input_id'], lr['log_date'])
                logs_by_input_day[key] = int(lr['log_count'])
                logs_by_input[lr['input_id']] = logs_by_input.get(lr['input_id'], 0) + int(lr['log_count'])

            for row in scheduled_rows:
                dpd = row['doses_per_day']
                scheduled_doses = dpd * days
                logged_doses = logs_by_input.get(row['id'], 0)
                pct = round(min(100.0, (logged_doses / scheduled_doses) * 100.0), 1) if scheduled_doses else 0.0

                missed_windows = []
                for day_offset in range(days):
                    d = start_date + timedelta(days=day_offset)
                    logged_that_day = logs_by_input_day.get((row['id'], d), 0)
                    if logged_that_day < dpd:
                        missed_windows.append({
                            'date': d.isoformat(),
                            'expected': dpd,
                            'logged': logged_that_day,
                        })

                results.append({
                    'input_id': str(row['id']),
                    'name': row['name'],
                    'input_type': row['input_type'],
                    'default_unit': row['default_unit'],
                    'doses_per_day': dpd,
                    'usage_type': 'scheduled',
                    'scheduled_doses': scheduled_doses,
                    'logged_doses': logged_doses,
                    'pct_adherence': pct,
                    'missed_windows': missed_windows,
                    'timeframes': row['timeframes'] or [],
                })

        return jsonify({
            'window': {
                'from': start_date.isoformat(),
                'to': end_date.isoformat(),
                'days': days,
            },
            'inputs': results,
            'excluded_prn': excluded_prn,
            'excluded_unspecified': excluded_unspecified,
        })
    except Exception as e:
        current_app.logger.error("adherence GET FAILED: %s", e)
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/adherence', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()
