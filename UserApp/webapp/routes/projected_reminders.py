"""
Projected reminders routes.

Blueprint for viewing and managing projected reminders. These reminders are
derived from timeframes when stacks or health_inputs are linked to timeframes.
They are read-mostly: creation and updates happen via the projection logic
when the source entities change.

Available operations:
- GET /projected-reminders: List all projected reminders for the user
- GET /projected-reminders/<id>: Get a single projected reminder
- POST /projected-reminders/<id>/snooze: Snooze a reminder
- PUT /projected-reminders/<id>/enable: Enable/disable a reminder
"""
from flask import Blueprint, request, jsonify, g, current_app
from db_driver import sql
from datetime import datetime, timedelta
import pytz
import uuid

from utils import (
    require_auth,
    get_db_connection,
    get_user_id,
    parse_pagination_params,
    paginated_response,
)

bp = Blueprint('projected_reminders', __name__, url_prefix='/api/v1')

VALID_FREQUENCIES = {'daily', 'weekly', 'monthly', 'annual', 'custom', 'once'}


def _parse_uuid(id_str):
    """Parse a UUID string, returning (uuid, None) or (None, error_response)."""
    try:
        return uuid.UUID(id_str), None
    except ValueError:
        return None, (jsonify({'error': 'Invalid projected reminder ID format'}), 400)


def _serialize_projected_reminder(row: dict) -> dict:
    """Convert a database row to JSON-serializable format."""
    result = dict(row)
    for field in ('id', 'user_id', 'stack_id', 'health_input_id', 'timeframe_id'):
        if result.get(field) is not None:
            result[field] = str(result[field])
    for field in ('scheduled_time',):
        if result.get(field) is not None:
            result[field] = result[field].strftime('%H:%M')
    for field in ('start_date',):
        if result.get(field) is not None:
            result[field] = result[field].isoformat()
    for field in ('snoozed_until', 'created_at', 'updated_at', 'synced_at'):
        if result.get(field) is not None:
            result[field] = result[field].isoformat()
    return result


@bp.route('/projected-reminders', methods=['GET'])
@require_auth
def get_projected_reminders():
    """
    Get a paginated list of projected reminders.

    Query parameters:
    - enabled: Filter by enabled status (true/false)
    - frequency: Filter by frequency (daily, weekly, etc.)
    - stack_id: Filter by source stack
    - health_input_id: Filter by source health_input
    """
    enabled = request.args.get('enabled')
    frequency = request.args.get('frequency')
    stack_id = request.args.get('stack_id')
    health_input_id = request.args.get('health_input_id')
    limit, offset = parse_pagination_params()

    current_app.logger.info("GET /projected-reminders: user_id=%s", g.user.get('user_id'))

    if frequency and frequency not in VALID_FREQUENCIES:
        return jsonify({'error': f'Invalid frequency. Must be one of: {", ".join(sorted(VALID_FREQUENCIES))}'}), 400

    conn = None
    try:
        tenant_id = g.user.get('tenant_id', 1)
        conn = get_db_connection()
        cur = conn.cursor()

        where_clauses = [sql.SQL("pr.tenant_id = %s")]
        params = [tenant_id]

        if enabled is not None:
            where_clauses.append(sql.SQL("pr.enabled = %s"))
            params.append(enabled.lower() == 'true')

        if frequency:
            where_clauses.append(sql.SQL("pr.frequency = %s"))
            params.append(frequency)

        if stack_id:
            try:
                where_clauses.append(sql.SQL("pr.stack_id = %s"))
                params.append(uuid.UUID(stack_id))
            except ValueError:
                return jsonify({'error': 'Invalid stack_id format'}), 400

        if health_input_id:
            try:
                where_clauses.append(sql.SQL("pr.health_input_id = %s"))
                params.append(uuid.UUID(health_input_id))
            except ValueError:
                return jsonify({'error': 'Invalid health_input_id format'}), 400

        params.extend([limit, offset])

        # Join with stacks/health_inputs/timeframes for context
        pr_query = sql.SQL("""
            SELECT pr.*,
                   s.name AS stack_name,
                   hi.name AS health_input_name,
                   tf.name AS timeframe_name,
                   count(*) OVER() AS _total
            FROM projected_reminders pr
            LEFT JOIN stacks s ON pr.stack_id = s.id AND pr.tenant_id = s.tenant_id
            LEFT JOIN health_inputs hi ON pr.health_input_id = hi.id AND pr.tenant_id = hi.tenant_id
            LEFT JOIN timeframes tf ON pr.timeframe_id = tf.id AND pr.tenant_id = tf.tenant_id
            WHERE {where}
            ORDER BY pr.scheduled_time
            LIMIT %s OFFSET %s
        """).format(where=sql.SQL(" AND ").join(where_clauses))
        cur.execute(pr_query, params)

        reminders = cur.fetchall()
        cur.close()

        total = reminders[0]['_total'] if reminders else 0
        results = []
        for row in reminders:
            row.pop('_total', None)
            results.append(_serialize_projected_reminder(row))

        current_app.logger.info("GET /projected-reminders: returning %d items (total=%d)",
                                len(results), total)
        return jsonify(paginated_response(results, total, limit, offset, key='entries'))

    except Exception as e:
        current_app.logger.error("GET /projected-reminders FAILED: %s", e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@bp.route('/projected-reminders/<id>', methods=['GET'])
@require_auth
def get_projected_reminder(id):
    """Get a single projected reminder by ID."""
    pr_uuid, err = _parse_uuid(id)
    if err:
        return err

    current_app.logger.info("GET /projected-reminders/%s: request received", id)

    conn = None
    try:
        tenant_id = g.user.get('tenant_id', 1)
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT pr.*,
                   s.name AS stack_name,
                   hi.name AS health_input_name,
                   tf.name AS timeframe_name
            FROM projected_reminders pr
            LEFT JOIN stacks s ON pr.stack_id = s.id AND pr.tenant_id = s.tenant_id
            LEFT JOIN health_inputs hi ON pr.health_input_id = hi.id AND pr.tenant_id = hi.tenant_id
            LEFT JOIN timeframes tf ON pr.timeframe_id = tf.id AND pr.tenant_id = tf.tenant_id
            WHERE pr.tenant_id = %s AND pr.user_id = %s AND pr.id = %s
        """, (tenant_id, get_user_id(), pr_uuid))

        reminder = cur.fetchone()
        cur.close()

        if not reminder:
            return jsonify({'error': 'Projected reminder not found'}), 404

        return jsonify(_serialize_projected_reminder(reminder))

    except Exception as e:
        current_app.logger.error("GET /projected-reminders/%s FAILED: %s", id, e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@bp.route('/projected-reminders/<id>/snooze', methods=['POST'])
@require_auth
def snooze_projected_reminder(id):
    """
    Snooze a projected reminder.

    Body:
    - minutes: Number of minutes to snooze (1-1440, default 10)
    """
    pr_uuid, err = _parse_uuid(id)
    if err:
        return err

    data = request.json or {}
    current_app.logger.info("POST /projected-reminders/%s/snooze: data=%s", id, data)

    minutes = data.get('minutes', 10)
    if not isinstance(minutes, (int, float)) or minutes < 1 or minutes > 1440:
        return jsonify({'error': 'minutes must be a number between 1 and 1440'}), 400
    minutes = int(minutes)

    conn = None
    try:
        tenant_id = g.user.get('tenant_id', 1)
        conn = get_db_connection()
        cur = conn.cursor()

        now = datetime.now(pytz.utc)
        snoozed_until = now + timedelta(minutes=minutes)

        cur.execute("""
            UPDATE projected_reminders
            SET snoozed_until = %s, updated_at = %s
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (snoozed_until, now, tenant_id, get_user_id(), pr_uuid))

        rows_affected = cur.rowcount
        conn.commit()
        cur.close()

        if rows_affected == 0:
            return jsonify({'error': 'Projected reminder not found'}), 404

        current_app.logger.info("POST /projected-reminders/%s/snooze: snoozed until %s",
                                id, snoozed_until.isoformat())
        return jsonify({'message': 'Reminder snoozed', 'snoozed_until': snoozed_until.isoformat()})

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        current_app.logger.error("POST /projected-reminders/%s/snooze FAILED: %s", id, e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@bp.route('/projected-reminders/<id>/enable', methods=['PUT'])
@require_auth
def toggle_projected_reminder(id):
    """
    Enable or disable a projected reminder.

    Body:
    - enabled: Boolean indicating whether the reminder should be enabled
    """
    pr_uuid, err = _parse_uuid(id)
    if err:
        return err

    data = request.json
    current_app.logger.info("PUT /projected-reminders/%s/enable: data=%s", id, data)

    if not data or 'enabled' not in data:
        return jsonify({'error': 'Missing required field: enabled'}), 400

    enabled = data['enabled']
    if not isinstance(enabled, bool):
        return jsonify({'error': 'enabled must be a boolean'}), 400

    conn = None
    try:
        tenant_id = g.user.get('tenant_id', 1)
        conn = get_db_connection()
        cur = conn.cursor()

        now = datetime.now(pytz.utc)

        cur.execute("""
            UPDATE projected_reminders
            SET enabled = %s, updated_at = %s
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (enabled, now, tenant_id, get_user_id(), pr_uuid))

        rows_affected = cur.rowcount
        conn.commit()
        cur.close()

        if rows_affected == 0:
            return jsonify({'error': 'Projected reminder not found'}), 404

        status = 'enabled' if enabled else 'disabled'
        current_app.logger.info("PUT /projected-reminders/%s/enable: %s", id, status)
        return jsonify({'message': f'Reminder {status}'})

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        current_app.logger.error("PUT /projected-reminders/%s/enable FAILED: %s", id, e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
