"""
Reminders routes.

Blueprint for managing user reminders with CRUD operations, completion, and snooze.
"""
from flask import Blueprint, request, jsonify, g, current_app
from db_driver import sql
from datetime import datetime, timedelta
import pytz
import re
import uuid

from utils import (
    require_auth,
    get_db_connection,
    get_user_id,
    parse_pagination_params,
    paginated_response,
)

bp = Blueprint('reminders', __name__, url_prefix='/api/v1')

VALID_CATEGORIES = {'medication', 'health-check', 'activity', 'hydration', 'appointment'}
VALID_FREQUENCIES = {'daily', 'weekly', 'monthly', 'custom', 'once'}
TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')


def _parse_uuid(id_str):
    """Parse a UUID string, returning (uuid, None) or (None, error_response)."""
    try:
        return uuid.UUID(id_str), None
    except ValueError:
        return None, (jsonify({'error': 'Invalid reminder ID format'}), 400)


# ==================== REMINDERS ====================

@bp.route('/reminders', methods=['GET'])
@require_auth
def get_reminders():
    """Get a paginated list of reminders, optionally filtered by category."""
    category = request.args.get('category')
    limit, offset = parse_pagination_params()
    current_app.logger.info("GET /reminders: user_id=%s tenant_id=%s category=%s",
                            g.user.get('user_id'), g.user.get('tenant_id'), category)

    if category and category not in VALID_CATEGORIES:
        return jsonify({'error': f'Invalid category. Must be one of: {", ".join(sorted(VALID_CATEGORIES))}'}), 400

    conn = None
    try:
        user_id = get_user_id()
        tenant_id = g.user.get('tenant_id', 1)
        conn = get_db_connection()
        cur = conn.cursor()

        if category:
            cur.execute("""
                SELECT *, count(*) OVER() AS _total
                FROM reminders
                WHERE tenant_id = %s AND user_id = %s AND category = %s
                ORDER BY time
                LIMIT %s OFFSET %s
            """, (tenant_id, user_id, category, limit, offset))
        else:
            cur.execute("""
                SELECT *, count(*) OVER() AS _total
                FROM reminders
                WHERE tenant_id = %s AND user_id = %s
                ORDER BY time
                LIMIT %s OFFSET %s
            """, (tenant_id, user_id, limit, offset))

        reminders = cur.fetchall()
        current_app.logger.debug("GET /reminders: fetched %d rows", len(reminders))
        cur.close()

        total = reminders[0]['_total'] if reminders else 0
        for item in reminders:
            item.pop('_total', None)
            if item.get('id') is not None:
                item['id'] = str(item['id'])
            if item.get('user_id') is not None:
                item['user_id'] = str(item['user_id'])
            if item.get('health_input_id') is not None:
                item['health_input_id'] = str(item['health_input_id'])

        current_app.logger.info("GET /reminders: returning %d items (total=%d)", len(reminders), total)
        return jsonify(paginated_response(reminders, total, limit, offset, key='entries'))

    except Exception as e:
        current_app.logger.error("GET /reminders FAILED: %s", e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception as close_err:
                current_app.logger.debug("conn.close() failed: %s", close_err)


@bp.route('/reminders', methods=['POST'])
@require_auth
def create_reminder():
    """Create a new reminder"""
    data = request.json
    current_app.logger.info("POST /reminders: received data=%s", data)

    conn = None
    try:
        user_id = get_user_id()
        tenant_id = g.user.get('tenant_id', 1)

        if not data:
            return jsonify({'error': 'No data provided'}), 400

        if 'title' not in data:
            return jsonify({'error': 'Missing required field: title'}), 400

        if 'time' not in data:
            return jsonify({'error': 'Missing required field: time'}), 400

        if not TIME_RE.match(data['time']):
            return jsonify({'error': 'Invalid time format. Use HH:mm (e.g. 08:30)'}), 400

        category = data.get('category', 'medication')
        if category not in VALID_CATEGORIES:
            return jsonify({'error': f'Invalid category. Must be one of: {", ".join(sorted(VALID_CATEGORIES))}'}), 400

        frequency = data.get('frequency', 'daily')
        if frequency not in VALID_FREQUENCIES:
            return jsonify({'error': f'Invalid frequency. Must be one of: {", ".join(sorted(VALID_FREQUENCIES))}'}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        reminder_id = uuid.uuid4()
        now = datetime.now(pytz.utc)

        current_app.logger.debug("POST /reminders: inserting id=%s title=%s", reminder_id, data['title'])

        cur.execute("""
            INSERT INTO reminders
            (tenant_id, id, user_id, title, time, category, frequency, custom_days,
             timezone, snooze_minutes, privacy_level, notes, enabled, health_input_id,
             completed, completed_at, snoozed_until, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            tenant_id, reminder_id, user_id,
            data['title'],
            data['time'],
            category,
            frequency,
            data.get('custom_days'),
            data.get('timezone'),
            data.get('snooze_minutes'),
            data.get('privacy_level', 'normal'),
            data.get('notes'),
            data.get('enabled', True),
            data.get('health_input_id'),
            data.get('completed', False),
            data.get('completed_at'),
            data.get('snoozed_until'),
            now, now
        ))

        result = cur.fetchone()
        conn.commit()
        current_app.logger.info("POST /reminders: created id=%s", result['id'])
        cur.close()

        return jsonify({'id': str(result['id']), 'message': 'Created'}), 201

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception as rollback_err:
                current_app.logger.debug("conn.rollback() failed: %s", rollback_err)
        current_app.logger.error("POST /reminders FAILED: %s", e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception as close_err:
                current_app.logger.debug("conn.close() failed: %s", close_err)


@bp.route('/reminders/<id>', methods=['PUT'])
@require_auth
def update_reminder(id):
    """Update a reminder (partial update -- only provided fields are changed)"""
    reminder_uuid, err = _parse_uuid(id)
    if err:
        return err

    data = request.json
    current_app.logger.info("PUT /reminders/%s: received data=%s", id, data)

    conn = None
    try:
        tenant_id = g.user.get('tenant_id', 1)

        if not data:
            return jsonify({'error': 'No data provided'}), 400

        if 'category' in data and data['category'] not in VALID_CATEGORIES:
            return jsonify({'error': f'Invalid category. Must be one of: {", ".join(sorted(VALID_CATEGORIES))}'}), 400

        if 'frequency' in data and data['frequency'] not in VALID_FREQUENCIES:
            return jsonify({'error': f'Invalid frequency. Must be one of: {", ".join(sorted(VALID_FREQUENCIES))}'}), 400

        if 'time' in data and not TIME_RE.match(data['time']):
            return jsonify({'error': 'Invalid time format. Use HH:mm (e.g. 08:30)'}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        now = datetime.now(pytz.utc)

        updatable_fields = [
            'title', 'time', 'category', 'frequency', 'custom_days',
            'timezone', 'snooze_minutes', 'privacy_level', 'notes', 'enabled',
            'health_input_id', 'completed', 'completed_at', 'snoozed_until'
        ]
        set_parts: list = []
        values: list = []
        for field in updatable_fields:
            if field in data:
                set_parts.append(
                    sql.SQL("{} = %s").format(sql.Identifier(field))
                )
                values.append(data[field])

        if not set_parts:
            return jsonify({'error': 'No updatable fields provided'}), 400

        set_parts.append(sql.SQL("updated_at = %s"))
        values.append(now)

        values.append(tenant_id)
        values.append(get_user_id())
        values.append(reminder_uuid)

        query = sql.SQL(
            "UPDATE reminders SET {sets} WHERE tenant_id = %s AND user_id = %s AND id = %s"
        ).format(sets=sql.SQL(", ").join(set_parts))
        cur.execute(query, values)

        rows_affected = cur.rowcount
        conn.commit()
        current_app.logger.info("PUT /reminders/%s: rows_affected=%d", id, rows_affected)
        cur.close()

        if rows_affected == 0:
            return jsonify({'error': 'Reminder not found'}), 404

        return jsonify({'message': 'Reminder updated'})

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception as rollback_err:
                current_app.logger.debug("conn.rollback() failed: %s", rollback_err)
        current_app.logger.error("PUT /reminders/%s FAILED: %s", id, e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception as close_err:
                current_app.logger.debug("conn.close() failed: %s", close_err)


@bp.route('/reminders/<id>', methods=['DELETE'])
@require_auth
def delete_reminder(id):
    """Delete a reminder"""
    reminder_uuid, err = _parse_uuid(id)
    if err:
        return err

    current_app.logger.info("DELETE /reminders/%s: request received", id)

    conn = None
    try:
        user_id = get_user_id()
        tenant_id = g.user.get('tenant_id', 1)
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM reminders
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (tenant_id, user_id, reminder_uuid))

        rows_affected = cur.rowcount
        conn.commit()
        current_app.logger.info("DELETE /reminders/%s: rows_affected=%d", id, rows_affected)
        cur.close()

        if rows_affected == 0:
            return jsonify({'error': 'Reminder not found'}), 404

        return jsonify({'message': 'Reminder deleted'})

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception as rollback_err:
                current_app.logger.debug("conn.rollback() failed: %s", rollback_err)
        current_app.logger.error("DELETE /reminders/%s FAILED: %s", id, e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception as close_err:
                current_app.logger.debug("conn.close() failed: %s", close_err)


@bp.route('/reminders/<id>/complete', methods=['POST'])
@require_auth
def complete_reminder(id):
    """Mark a reminder as completed"""
    reminder_uuid, err = _parse_uuid(id)
    if err:
        return err

    current_app.logger.info("POST /reminders/%s/complete: request received", id)

    conn = None
    try:
        user_id = get_user_id()
        tenant_id = g.user.get('tenant_id', 1)
        conn = get_db_connection()
        cur = conn.cursor()

        now = datetime.now(pytz.utc)

        cur.execute("""
            UPDATE reminders
            SET completed = true, completed_at = %s, updated_at = %s
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (now, now, tenant_id, user_id, reminder_uuid))

        rows_affected = cur.rowcount
        conn.commit()
        current_app.logger.info("POST /reminders/%s/complete: rows_affected=%d", id, rows_affected)
        cur.close()

        if rows_affected == 0:
            return jsonify({'error': 'Reminder not found'}), 404

        return jsonify({'message': 'Reminder completed'})

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception as rollback_err:
                current_app.logger.debug("conn.rollback() failed: %s", rollback_err)
        current_app.logger.error("POST /reminders/%s/complete FAILED: %s", id, e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception as close_err:
                current_app.logger.debug("conn.close() failed: %s", close_err)


@bp.route('/reminders/<id>/snooze', methods=['POST'])
@require_auth
def snooze_reminder(id):
    """Snooze a reminder by a given number of minutes"""
    reminder_uuid, err = _parse_uuid(id)
    if err:
        return err

    data = request.json or {}
    current_app.logger.info("POST /reminders/%s/snooze: received data=%s", id, data)

    minutes = data.get('minutes', 10)
    if not isinstance(minutes, (int, float)) or minutes < 1 or minutes > 1440:
        return jsonify({'error': 'minutes must be a number between 1 and 1440'}), 400
    minutes = int(minutes)

    conn = None
    try:
        user_id = get_user_id()
        tenant_id = g.user.get('tenant_id', 1)
        conn = get_db_connection()
        cur = conn.cursor()

        now = datetime.now(pytz.utc)
        snoozed_until = now + timedelta(minutes=minutes)

        cur.execute("""
            UPDATE reminders
            SET snoozed_until = %s, updated_at = %s
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (snoozed_until, now, tenant_id, user_id, reminder_uuid))

        rows_affected = cur.rowcount
        conn.commit()
        current_app.logger.info("POST /reminders/%s/snooze: rows_affected=%d", id, rows_affected)
        cur.close()

        if rows_affected == 0:
            return jsonify({'error': 'Reminder not found'}), 404

        return jsonify({'message': 'Reminder snoozed'})

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception as rollback_err:
                current_app.logger.debug("conn.rollback() failed: %s", rollback_err)
        current_app.logger.error("POST /reminders/%s/snooze FAILED: %s", id, e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception as close_err:
                current_app.logger.debug("conn.close() failed: %s", close_err)
