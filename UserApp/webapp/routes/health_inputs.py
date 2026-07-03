"""
Health Inputs, Stacks, and Timeframes routes.

Blueprint for managing medications, supplements, stacks (bundles), and timeframes.
"""
from flask import Blueprint, request, jsonify, g, current_app
from datetime import datetime
import pytz
import uuid
import json

from utils import (
    require_auth,
    get_db_connection,
    get_user_id,
    parse_bool,
    parse_pagination_params,
    paginated_response,
)
import db_manager
from units import normalize_unit
from projection import (
    project_reminder_for_stack,
    project_reminder_for_health_input,
    update_projections_for_timeframe,
)

bp = Blueprint('health_inputs', __name__, url_prefix='/api/v1')


# ==================== HEALTH INPUTS ====================

@bp.route('/health-inputs', methods=['GET'])
@require_auth
def get_health_inputs():
    """Get all health inputs"""
    current_app.logger.info("GET /health-inputs: user_id=%s tenant_id=%s",
                            g.user.get('user_id'), g.user.get('tenant_id'))
    limit, offset = parse_pagination_params(default_limit=50, max_limit=200)
    user_id = get_user_id()
    try:
        conn = get_db_connection()
        current_app.logger.info("GET /health-inputs: database connection established")
        cur = conn.cursor()

        cur.execute("""
            SELECT count(*) OVER() AS _total,
                   id, name, input_type, default_dosage, default_unit, brand, form,
                   is_active, take_with_food, notes, instructions, custom_fields,
                   doses_per_day, frequent_status
            FROM health_inputs
            WHERE user_id = %s
            ORDER BY
                CASE WHEN frequent_status IS NOT NULL THEN 0 ELSE 1 END,
                CASE WHEN frequent_status = 'sticky' THEN 0 ELSE 1 END,
                name
            LIMIT %s OFFSET %s
        """, (user_id, limit, offset))

        inputs = cur.fetchall()
        current_app.logger.info("GET /health-inputs: fetched %d rows from database", len(inputs))
        cur.close()
        conn.close()

        total = inputs[0]['_total'] if inputs else 0
        for item in inputs:
            item.pop('_total', None)
            item['id'] = str(item['id'])
            custom = item.pop('custom_fields', None) or {}
            item['category'] = custom.get('category')

        current_app.logger.info("GET /health-inputs: returning %d items", len(inputs))
        return jsonify(paginated_response(inputs, total, limit, offset, key='entries'))

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/health-inputs', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("GET /health-inputs FAILED: %s", e, exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.route('/health-inputs', methods=['POST'])
@require_auth
def create_health_input():
    """Create a new health input"""
    data = request.json
    current_app.logger.info("POST /health-inputs: received data=%s", data)

    try:
        user_id = get_user_id()
        tenant_id = g.user.get('tenant_id', 1)
        current_app.logger.info("POST /health-inputs: user_id=%s tenant_id=%s", user_id, tenant_id)

        if not data:
            current_app.logger.error("POST /health-inputs: No JSON data received")
            return jsonify({'error': 'No data provided'}), 400

        if 'name' not in data:
            current_app.logger.error("POST /health-inputs: Missing required field 'name'")
            return jsonify({'error': 'Missing required field: name'}), 400

        if 'input_type' not in data:
            current_app.logger.error("POST /health-inputs: Missing required field 'input_type'")
            return jsonify({'error': 'Missing required field: input_type'}), 400

        try:
            default_unit = normalize_unit(data.get('default_unit'))
        except ValueError as e:
            return jsonify({'error': str(e), 'code': 'INVALID_UNIT'}), 400

        conn = get_db_connection()
        current_app.logger.info("POST /health-inputs: database connection established")
        cur = conn.cursor()

        input_id = uuid.uuid4()
        now = datetime.now(pytz.utc)

        custom_fields = json.dumps({
            'category': data.get('category'),
        })

        current_app.logger.info("POST /health-inputs: inserting id=%s name=%s type=%s",
                                input_id, data['name'], data['input_type'])

        # Validate frequent_status if provided
        freq_status = data.get('frequent_status')
        if freq_status is not None and freq_status not in ('detected', 'sticky'):
            return jsonify({'error': 'frequent_status must be null, "detected", or "sticky"'}), 400

        # Validate doses_per_day if provided
        doses_per_day = data.get('doses_per_day')
        if doses_per_day is not None:
            if not isinstance(doses_per_day, int) or doses_per_day not in (-1, 1, 2, 3, 4):
                return jsonify({'error': 'doses_per_day must be null, -1, 1, 2, 3, or 4'}), 400

        # Parse optional timeframe_id for standalone input reminders
        timeframe_id = uuid.UUID(data['timeframe_id']) if data.get('timeframe_id') else None

        cur.execute("""
            INSERT INTO health_inputs
            (tenant_id, id, user_id, name, input_type, default_dosage, default_unit,
             brand, form, is_active, take_with_food, notes,
             custom_fields, doses_per_day, frequent_status, timeframe_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            tenant_id, input_id, user_id, data['name'], data['input_type'],
            data.get('default_dosage'), default_unit,
            data.get('brand'), data.get('form'),
            parse_bool(data.get('is_active'), default=True),
            parse_bool(data.get('take_with_food'), default=False), data.get('notes'),
            custom_fields, doses_per_day, freq_status, timeframe_id, now, now
        ))

        result = cur.fetchone()

        # Project reminder if health_input has a timeframe
        if timeframe_id:
            project_reminder_for_health_input(conn, input_id, timeframe_id, user_id, tenant_id)

        conn.commit()
        current_app.logger.info("POST /health-inputs: SUCCESS created id=%s", result['id'])
        cur.close()
        conn.close()

        return jsonify({'id': str(result['id']), 'message': 'Health input created'}), 201

    except KeyError as e:
        current_app.logger.error("POST /health-inputs FAILED: missing field %s", e, exc_info=True)
        return jsonify({'error': f'Missing required field: {e}'}), 400
    except Exception as e:
        if db_manager.is_unique_violation(e):
            return jsonify({
                'error': 'A health input with this name already exists',
                'code': 'DUPLICATE_NAME',
            }), 409
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/health-inputs', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("POST /health-inputs FAILED: %s", e, exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.route('/health-inputs/<input_id>', methods=['PUT'])
@require_auth
def update_health_input(input_id):
    """Update a health input"""
    data = request.json
    current_app.logger.info("PUT /health-inputs/%s: received data=%s", input_id, data)

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    try:
        conn = get_db_connection()
        current_app.logger.info("PUT /health-inputs/%s: database connection established", input_id)
        cur = conn.cursor()

        now = datetime.now(pytz.utc)
        tenant_id = g.user.get('tenant_id', 1)
        user_id = get_user_id()

        # Fetch existing record so partial updates keep unchanged fields
        cur.execute("""
            SELECT name, input_type, default_dosage, default_unit, brand, form,
                   is_active, take_with_food, notes, instructions, custom_fields,
                   doses_per_day, frequent_status, timeframe_id
            FROM health_inputs
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (tenant_id, user_id, uuid.UUID(input_id)))
        existing = cur.fetchone()
        if not existing:
            cur.close()
            conn.close()
            return jsonify({'error': 'Health input not found'}), 404

        # Merge: use incoming value if provided, else keep existing.
        # db_manager connections default to RealDictCursor, so `existing` is a
        # dict-like RealDictRow — index by column name, not tuple position.
        name = data.get('name', existing['name'])
        input_type = data.get('input_type', existing['input_type'])
        default_dosage = data.get('default_dosage', existing['default_dosage'])
        # Normalize only when the key is present — a legacy row with a
        # non-canonical stored unit must stay updatable for other fields.
        if 'default_unit' in data:
            try:
                default_unit = normalize_unit(data['default_unit'])
            except ValueError as e:
                cur.close()
                conn.close()
                return jsonify({'error': str(e), 'code': 'INVALID_UNIT'}), 400
        else:
            default_unit = existing['default_unit']
        brand = data.get('brand', existing['brand'])
        form = data.get('form', existing['form'])
        is_active = parse_bool(data.get('is_active'), default=existing['is_active'])
        take_with_food = parse_bool(data.get('take_with_food'), default=existing['take_with_food'])
        notes = data.get('notes', existing['notes'])
        instructions = data.get('instructions', existing['instructions'])
        custom_fields = json.dumps({
            'category': data.get('category', (existing['custom_fields'] or {}).get('category')),
        })
        doses_per_day = data.get('doses_per_day', existing['doses_per_day'])
        freq_status = data.get('frequent_status', existing['frequent_status'])

        # Handle timeframe_id: explicit null clears it, missing key keeps existing
        if 'timeframe_id' in data:
            timeframe_id = uuid.UUID(data['timeframe_id']) if data['timeframe_id'] else None
        else:
            timeframe_id = existing['timeframe_id']

        # Validate frequent_status if provided
        if freq_status is not None and freq_status not in ('detected', 'sticky'):
            return jsonify({'error': 'frequent_status must be null, "detected", or "sticky"'}), 400

        # Validate doses_per_day if provided
        if doses_per_day is not None:
            if not isinstance(doses_per_day, int) or doses_per_day not in (-1, 1, 2, 3, 4):
                return jsonify({'error': 'doses_per_day must be null, -1, 1, 2, 3, or 4'}), 400

        current_app.logger.info("PUT /health-inputs/%s: executing UPDATE for name=%s type=%s",
                                input_id, name, input_type)

        input_uuid = uuid.UUID(input_id)

        cur.execute("""
            UPDATE health_inputs
            SET name = %s, input_type = %s, default_dosage = %s, default_unit = %s,
                brand = %s, form = %s, is_active = %s, take_with_food = %s,
                notes = %s, instructions = %s, custom_fields = %s,
                doses_per_day = %s, frequent_status = %s, timeframe_id = %s, updated_at = %s
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (
            name, input_type, default_dosage,
            default_unit, brand, form,
            is_active, take_with_food,
            notes, instructions, custom_fields, doses_per_day, freq_status, timeframe_id,
            now, tenant_id, user_id, input_uuid
        ))

        rows_affected = cur.rowcount

        # Re-project reminder if timeframe changed (deletes old, creates new if set)
        if 'timeframe_id' in data:
            project_reminder_for_health_input(conn, input_uuid, timeframe_id, user_id, tenant_id)

        conn.commit()
        current_app.logger.info("PUT /health-inputs/%s: SUCCESS rows_affected=%d", input_id, rows_affected)
        cur.close()
        conn.close()

        if rows_affected == 0:
            current_app.logger.warning("PUT /health-inputs/%s: no rows updated - ID may not exist", input_id)
            return jsonify({'error': 'Health input not found'}), 404

        return jsonify({'message': 'Health input updated'})

    except Exception as e:
        if db_manager.is_unique_violation(e):
            return jsonify({
                'error': 'A health input with this name already exists',
                'code': 'DUPLICATE_NAME',
            }), 409
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/health-inputs/{input_id}', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("PUT /health-inputs/%s FAILED: %s", input_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.route('/health-inputs/<input_id>', methods=['DELETE'])
@require_auth
def delete_health_input(input_id):
    """Delete a health input"""
    current_app.logger.info("DELETE /health-inputs/%s: request received", input_id)

    try:
        tenant_id = g.user.get('tenant_id', 1)
        user_id = get_user_id()
        conn = get_db_connection()
        current_app.logger.info("DELETE /health-inputs/%s: database connection established", input_id)
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM health_inputs
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (tenant_id, user_id, uuid.UUID(input_id),))

        rows_affected = cur.rowcount
        conn.commit()
        current_app.logger.info("DELETE /health-inputs/%s: SUCCESS rows_affected=%d", input_id, rows_affected)
        cur.close()
        conn.close()

        if rows_affected == 0:
            current_app.logger.warning("DELETE /health-inputs/%s: no rows deleted - ID may not exist", input_id)
            return jsonify({'error': 'Health input not found'}), 404

        return jsonify({'message': 'Health input deleted'})

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/health-inputs/{input_id}', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("DELETE /health-inputs/%s FAILED: %s", input_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== STACKS ====================

@bp.route('/stacks', methods=['GET'])
@require_auth
def get_stacks():
    """Get a paginated list of stacks with their inputs.

    Note: count(*) OVER() runs AFTER GROUP BY in PostgreSQL evaluation order,
    so _total counts the number of distinct stacks (post-aggregation rows),
    not the number of stack_inputs join rows. This is exactly what we want.
    """
    limit, offset = parse_pagination_params()
    user_id = get_user_id()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT count(*) OVER() AS _total,
               s.id, s.name, s.timeframe_id, t.name as timeframe_name, s.is_active,
               json_agg(
                   json_build_object(
                       'input_id', si.health_input_id,
                       'input_name', hi.name,
                       'dosage_override', si.dosage_override
                   )
               ) FILTER (WHERE si.id IS NOT NULL) as inputs
        FROM stacks s
        LEFT JOIN timeframes t ON s.timeframe_id = t.id
        LEFT JOIN stack_inputs si ON s.id = si.stack_id
        LEFT JOIN health_inputs hi ON si.health_input_id = hi.id
        WHERE s.user_id = %s
        GROUP BY s.id, s.name, s.timeframe_id, t.name, s.is_active
        ORDER BY s.name
        LIMIT %s OFFSET %s
    """, (user_id, limit, offset))

    stacks = cur.fetchall()
    cur.close()
    conn.close()

    total = stacks[0]['_total'] if stacks else 0
    for stack in stacks:
        stack.pop('_total', None)
        stack['id'] = str(stack['id'])
        if stack['timeframe_id']:
            stack['timeframe_id'] = str(stack['timeframe_id'])
        if stack['inputs'] and stack['inputs'][0] is not None:
            for item in stack['inputs']:
                if item.get('input_id'):
                    item['input_id'] = str(item['input_id'])
        else:
            stack['inputs'] = []

    return jsonify(paginated_response(stacks, total, limit, offset, key='entries'))


@bp.route('/stacks', methods=['POST'])
@require_auth
def create_stack():
    """Create a new stack"""
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Missing required field: name'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        stack_id = uuid.uuid4()
        now = datetime.now(pytz.utc)

        timeframe_id = uuid.UUID(data['timeframe_id']) if data.get('timeframe_id') else None

        cur.execute("""
            INSERT INTO stacks (tenant_id, id, user_id, name, timeframe_id, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (tenant_id, stack_id, user_id, name, timeframe_id, parse_bool(data.get('is_active'), default=True), now, now))

        result = cur.fetchone()

        # Add stack inputs
        if data.get('inputs'):
            for item in data['inputs']:
                item_id = uuid.uuid4()
                cur.execute("""
                    INSERT INTO stack_inputs (tenant_id, id, user_id, stack_id, health_input_id, dosage_override, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (tenant_id, item_id, user_id, stack_id, uuid.UUID(item['input_id']),
                      item.get('dosage_override'), now))

        # Project reminder if stack has a timeframe
        if timeframe_id:
            project_reminder_for_stack(conn, stack_id, timeframe_id, user_id, tenant_id)

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({'id': str(result['id']), 'message': 'Stack created'}), 201
    except Exception as e:
        if db_manager.is_unique_violation(e):
            return jsonify({
                'error': 'A stack with this name already exists',
                'code': 'DUPLICATE_NAME',
            }), 409
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/stacks', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("POST /stacks FAILED: %s", e, exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.route('/stacks/<stack_id>', methods=['PUT'])
@require_auth
def update_stack(stack_id):
    """Update a stack"""
    data = request.json
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        now = datetime.now(pytz.utc)
        stack_uuid = uuid.UUID(stack_id)
        timeframe_id = uuid.UUID(data['timeframe_id']) if data.get('timeframe_id') else None

        cur.execute("""
            UPDATE stacks
            SET name = %s, timeframe_id = %s, is_active = %s, updated_at = %s
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (data['name'], timeframe_id, parse_bool(data.get('is_active'), default=True), now, tenant_id, user_id, stack_uuid))

        # Delete existing stack inputs (hard delete to avoid unique constraint issues)
        cur.execute("DELETE FROM stack_inputs WHERE tenant_id = %s AND user_id = %s AND stack_id = %s", (tenant_id, user_id, stack_uuid,))

        # Add new stack inputs
        if data.get('inputs'):
            for item in data['inputs']:
                item_id = uuid.uuid4()
                cur.execute("""
                    INSERT INTO stack_inputs (tenant_id, id, user_id, stack_id, health_input_id, dosage_override, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (tenant_id, item_id, user_id, stack_uuid, uuid.UUID(item['input_id']),
                      item.get('dosage_override'), now))

        # Re-project reminder (deletes old, creates new if timeframe set)
        project_reminder_for_stack(conn, stack_uuid, timeframe_id, user_id, tenant_id)

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        if db_manager.is_unique_violation(e):
            return jsonify({
                'error': 'A stack with this name already exists',
                'code': 'DUPLICATE_NAME',
            }), 409
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/stacks/{stack_id}', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("PUT /stacks/%s FAILED: %s", stack_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500

    return jsonify({'message': 'Stack updated'})


@bp.route('/stacks/<stack_id>', methods=['DELETE'])
@require_auth
def delete_stack(stack_id):
    """Delete a stack and its inputs"""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    conn = get_db_connection()
    cur = conn.cursor()

    stack_uuid = uuid.UUID(stack_id)

    # Null out stack_id references in health_input_log BEFORE the stacks DELETE
    # so the FK cascade has nothing to do.
    #
    # Schema bug workaround: health_input_log has a composite FK
    # (tenant_id, stack_id) REFERENCES stacks(tenant_id, id) ON DELETE SET NULL.
    # On cascade, Postgres sets BOTH columns to NULL — including tenant_id,
    # which is NOT NULL. The cascade fails and DELETE stacks returns 500.
    #
    # Pre-nullifying stack_id (leaving tenant_id alone) removes the FK
    # reference before the cascade can fire. The same latent issue exists
    # on health_food_logv2.timeframe_id (line 1019 of the schema); both
    # should be fixed at the schema level in a single migration, at which
    # point this workaround can be removed.
    cur.execute(
        "UPDATE health_input_log SET stack_id = NULL "
        "WHERE tenant_id = %s AND user_id = %s AND stack_id = %s",
        (tenant_id, user_id, stack_uuid,)
    )

    # Delete stack inputs first (FK constraint)
    cur.execute("DELETE FROM stack_inputs WHERE tenant_id = %s AND user_id = %s AND stack_id = %s", (tenant_id, user_id, stack_uuid,))
    cur.execute("DELETE FROM stacks WHERE tenant_id = %s AND user_id = %s AND id = %s", (tenant_id, user_id, stack_uuid,))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': 'Stack deleted'})


# ==================== TIMEFRAMES ====================

@bp.route('/timeframes', methods=['GET'])
@require_auth
def get_timeframes():
    """Get all timeframes"""
    user_id = get_user_id()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, time_of_day, sort_order, is_active
        FROM timeframes
        WHERE user_id = %s
        ORDER BY sort_order, name
    """, (user_id,))

    timeframes = cur.fetchall()
    cur.close()
    conn.close()

    for item in timeframes:
        item['id'] = str(item['id'])
        time_of_day = item.get('time_of_day')
        if time_of_day is not None and hasattr(time_of_day, 'isoformat'):
            item['time_of_day'] = time_of_day.isoformat()[:5]

    return jsonify(timeframes)


@bp.route('/timeframes', methods=['POST'])
@require_auth
def create_timeframe():
    """Create a new timeframe"""
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Missing required field: name'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    # Validate frequency if provided
    frequency = data.get('frequency', 'daily')
    valid_frequencies = ('daily', 'weekly', 'monthly', 'annual', 'custom', 'once')
    if frequency not in valid_frequencies:
        return jsonify({'error': f'Invalid frequency. Must be one of: {", ".join(valid_frequencies)}'}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    timeframe_id = uuid.uuid4()
    now = datetime.now(pytz.utc)

    cur.execute("""
        INSERT INTO timeframes
        (tenant_id, id, user_id, name, time_of_day, sort_order, is_active,
         frequency, custom_days, start_date, notes, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        tenant_id, timeframe_id, user_id, name, data.get('time_of_day'),
        data.get('sort_order', 0), parse_bool(data.get('is_active'), default=True),
        frequency, data.get('custom_days'), data.get('start_date'),
        data.get('notes'), now, now
    ))

    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'id': str(result['id']), 'message': 'Timeframe created'}), 201


@bp.route('/timeframes/<timeframe_id>', methods=['PUT'])
@require_auth
def update_timeframe(timeframe_id):
    """Update a timeframe"""
    data = request.json or {}
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    now = datetime.now(pytz.utc)
    tf_uuid = uuid.UUID(timeframe_id)

    # Validate frequency if provided
    frequency = data.get('frequency', 'daily')
    valid_frequencies = ('daily', 'weekly', 'monthly', 'annual', 'custom', 'once')
    if frequency not in valid_frequencies:
        cur.close()
        conn.close()
        return jsonify({'error': f'Invalid frequency. Must be one of: {", ".join(valid_frequencies)}'}), 400

    cur.execute("""
        UPDATE timeframes
        SET name = %s, time_of_day = %s, sort_order = %s, is_active = %s,
            frequency = %s, custom_days = %s, start_date = %s, notes = %s,
            updated_at = %s
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (
        data['name'], data.get('time_of_day'), data.get('sort_order', 0),
        parse_bool(data.get('is_active'), default=True),
        frequency, data.get('custom_days'), data.get('start_date'),
        data.get('notes'), now, tenant_id, user_id, tf_uuid
    ))

    # Update all projected reminders linked to this timeframe
    update_projections_for_timeframe(conn, tf_uuid, get_user_id(), tenant_id)

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': 'Timeframe updated'})


@bp.route('/timeframes/<timeframe_id>', methods=['DELETE'])
@require_auth
def delete_timeframe(timeframe_id):
    """Delete a timeframe"""
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()
    tf_uuid = uuid.UUID(timeframe_id)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*) AS count FROM stacks
        WHERE tenant_id = %s AND user_id = %s AND timeframe_id = %s
    """, (tenant_id, user_id, tf_uuid))
    orphaned = cur.fetchone()['count']

    cur.execute("""
        DELETE FROM timeframes
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, user_id, tf_uuid))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': 'Timeframe deleted', 'stacks_orphaned': orphaned})
