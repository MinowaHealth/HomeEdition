"""Health Inputs, Stacks, and Timeframes routes — v2 (embedding-aware).

POST and PUT for health_inputs accept optional embedding vectors.
All other routes proxy to v1 implementations unchanged.
"""
from flask import Blueprint, request, jsonify, g, current_app
from datetime import datetime
import pytz
import uuid
import json

from utils import require_auth, get_db_connection, get_user_id, parse_bool
import db_manager
from units import normalize_unit

from .embedding_helpers import embed_field
from .health_inputs import (
    get_health_inputs,
    delete_health_input,
    get_stacks,
    create_stack,
    update_stack,
    delete_stack,
    get_timeframes,
    create_timeframe,
    update_timeframe,
    delete_timeframe,
)

bp = Blueprint('health_inputs_v2', __name__, url_prefix='/api/v2')

# ==================== PROXIED FROM V1 ====================

bp.add_url_rule('/health-inputs', 'get_health_inputs', get_health_inputs, methods=['GET'])
bp.add_url_rule('/health-inputs/<input_id>', 'delete_health_input', delete_health_input, methods=['DELETE'])

bp.add_url_rule('/stacks', 'get_stacks', get_stacks, methods=['GET'])
bp.add_url_rule('/stacks', 'create_stack', create_stack, methods=['POST'])
bp.add_url_rule('/stacks/<stack_id>', 'update_stack', update_stack, methods=['PUT'])
bp.add_url_rule('/stacks/<stack_id>', 'delete_stack', delete_stack, methods=['DELETE'])

bp.add_url_rule('/timeframes', 'get_timeframes', get_timeframes, methods=['GET'])
bp.add_url_rule('/timeframes', 'create_timeframe', create_timeframe, methods=['POST'])
bp.add_url_rule('/timeframes/<timeframe_id>', 'update_timeframe', update_timeframe, methods=['PUT'])
bp.add_url_rule('/timeframes/<timeframe_id>', 'delete_timeframe', delete_timeframe, methods=['DELETE'])


# ==================== V2 EMBEDDING-AWARE ====================

@bp.route('/health-inputs', methods=['POST'])
@require_auth
def create_health_input_v2():
    """Create a new health input with optional embedding.

    Accepts the same payload as v1, plus optional fields:
    - embedding: list of 768 floats (pre-computed vector from device)
    - device_capabilities: dict with can_embed, embed_model, etc.

    If no embedding provided, server generates one from the name field.
    Embedding failure never blocks the create operation.
    """
    data = request.json
    current_app.logger.info("POST /v2/health-inputs: received data keys=%s",
                            list(data.keys()) if data else None)

    try:
        user_id = get_user_id()
        tenant_id = g.user.get('tenant_id', 1)

        if not data:
            return jsonify({'error': 'No data provided'}), 400
        if 'name' not in data:
            return jsonify({'error': 'Missing required field: name'}), 400
        if 'input_type' not in data:
            return jsonify({'error': 'Missing required field: input_type'}), 400

        try:
            default_unit = normalize_unit(data.get('default_unit'))
        except ValueError as e:
            return jsonify({'error': str(e), 'code': 'INVALID_UNIT'}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        input_id = uuid.uuid4()
        now = datetime.now(pytz.utc)

        custom_fields = json.dumps({
            'category': data.get('category'),
        })

        # Validate frequent_status if provided
        freq_status = data.get('frequent_status')
        if freq_status is not None and freq_status not in ('detected', 'sticky'):
            return jsonify({'error': 'frequent_status must be null, "detected", or "sticky"'}), 400

        # Validate doses_per_day if provided
        doses_per_day = data.get('doses_per_day')
        if doses_per_day is not None:
            if not isinstance(doses_per_day, int) or doses_per_day not in (-1, 1, 2, 3, 4):
                return jsonify({'error': 'doses_per_day must be null, -1, 1, 2, 3, or 4'}), 400

        cur.execute("""
            INSERT INTO health_inputs
            (tenant_id, id, user_id, name, input_type, default_dosage, default_unit,
             brand, form, is_active, take_with_food, notes,
             custom_fields, doses_per_day, frequent_status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            tenant_id, input_id, user_id, data['name'], data['input_type'],
            data.get('default_dosage'), default_unit,
            data.get('brand'), data.get('form'),
            parse_bool(data.get('is_active'), default=True),
            parse_bool(data.get('take_with_food'), default=False), data.get('notes'),
            custom_fields, doses_per_day, freq_status, now, now
        ))

        result = cur.fetchone()
        conn.commit()
        cur.close()

        # Inline embedding — accepts client vector or generates server-side
        embedded_by = embed_field(
            conn, tenant_id, result['id'],
            'health_inputs', 'embedding_name',
            data['name'], data.get('embedding'),
        )

        conn.close()

        response = {'id': str(result['id']), 'message': 'Health input created'}
        if embedded_by:
            response['embedded_by'] = embedded_by
        return jsonify(response), 201

    except KeyError as e:
        return jsonify({'error': f'Missing required field: {e}'}), 400
    except Exception as e:
        if db_manager.is_unique_violation(e):
            return jsonify({
                'error': 'A health input with this name already exists',
                'code': 'DUPLICATE_NAME',
            }), 409
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/v2/health-inputs', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("POST /v2/health-inputs FAILED: %s", e, exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.route('/health-inputs/<input_id>', methods=['PUT'])
@require_auth
def update_health_input_v2(input_id):
    """Update a health input with optional re-embedding.

    Same payload as v1, plus optional embedding field.
    Re-embeds when the name changes.
    """
    data = request.json

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        now = datetime.now(pytz.utc)
        tenant_id = g.user.get('tenant_id', 1)
        user_id = get_user_id()

        # Fetch existing record so partial updates keep unchanged fields
        cur.execute("""
            SELECT name, input_type, default_dosage, default_unit, brand, form,
                   is_active, take_with_food, notes, custom_fields,
                   doses_per_day, frequent_status
            FROM health_inputs
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (tenant_id, user_id, uuid.UUID(input_id)))
        existing = cur.fetchone()
        if not existing:
            cur.close()
            conn.close()
            return jsonify({'error': 'Health input not found'}), 404

        # Merge: use incoming value if provided, else keep existing
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
        custom_fields = json.dumps({
            'category': data.get('category', (existing['custom_fields'] or {}).get('category')),
        })
        doses_per_day = data.get('doses_per_day', existing['doses_per_day'])
        freq_status = data.get('frequent_status', existing['frequent_status'])

        # Validate frequent_status if provided
        if freq_status is not None and freq_status not in ('detected', 'sticky'):
            return jsonify({'error': 'frequent_status must be null, "detected", or "sticky"'}), 400

        # Validate doses_per_day if provided
        if doses_per_day is not None:
            if not isinstance(doses_per_day, int) or doses_per_day not in (-1, 1, 2, 3, 4):
                return jsonify({'error': 'doses_per_day must be null, -1, 1, 2, 3, or 4'}), 400

        cur.execute("""
            UPDATE health_inputs
            SET name = %s, input_type = %s, default_dosage = %s, default_unit = %s,
                brand = %s, form = %s, is_active = %s, take_with_food = %s,
                notes = %s, custom_fields = %s,
                doses_per_day = %s, frequent_status = %s, updated_at = %s
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (
            name, input_type, default_dosage,
            default_unit, brand, form,
            is_active, take_with_food,
            notes, custom_fields, doses_per_day, freq_status,
            now, tenant_id, user_id, uuid.UUID(input_id)
        ))

        rows_affected = cur.rowcount
        conn.commit()
        cur.close()

        if rows_affected == 0:
            conn.close()
            return jsonify({'error': 'Health input not found'}), 404

        # Re-embed on name change
        embedded_by = embed_field(
            conn, tenant_id, uuid.UUID(input_id),
            'health_inputs', 'embedding_name',
            name, data.get('embedding'),
        )

        conn.close()

        response = {'message': 'Health input updated'}
        if embedded_by:
            response['embedded_by'] = embedded_by
        return jsonify(response)

    except Exception as e:
        if db_manager.is_unique_violation(e):
            return jsonify({
                'error': 'A health input with this name already exists',
                'code': 'DUPLICATE_NAME',
            }), 409
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/v2/health-inputs/{input_id}', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("PUT /v2/health-inputs/%s FAILED: %s", input_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500
