"""
Appointments routes.

Blueprint for managing one-time medical appointments with lead-time reminders.
Unlike projected_reminders (derived from timeframes), appointments store their
own datetime and reminder configuration.
"""
from flask import Blueprint, request, jsonify, g, current_app
from db_driver import sql
from datetime import datetime
import pytz
import uuid

from utils import (
    require_auth,
    get_db_connection,
    get_user_id,
    parse_pagination_params,
    paginated_response,
)

bp = Blueprint('appointments', __name__, url_prefix='/api/v1')

VALID_STATUSES = {'scheduled', 'completed', 'cancelled', 'no_show'}


def _parse_uuid(id_str):
    """Parse a UUID string, returning (uuid, None) or (None, error_response)."""
    try:
        return uuid.UUID(id_str), None
    except ValueError:
        return None, (jsonify({'error': 'Invalid appointment ID format'}), 400)


def _serialize_appointment(row: dict) -> dict:
    """Convert a database row to JSON-serializable format."""
    result = dict(row)
    for field in ('id', 'user_id', 'provider_id'):
        if result.get(field) is not None:
            result[field] = str(result[field])
    for field in ('appointment_datetime', 'completed_at', 'created_at', 'updated_at', 'synced_at'):
        if result.get(field) is not None:
            result[field] = result[field].isoformat()
    return result


@bp.route('/appointments', methods=['GET'])
@require_auth
def get_appointments():
    """Get a paginated list of appointments, optionally filtered by status."""
    status = request.args.get('status')
    upcoming_only = request.args.get('upcoming', '').lower() == 'true'
    limit, offset = parse_pagination_params()

    current_app.logger.info("GET /appointments: user_id=%s status=%s upcoming=%s",
                            g.user.get('user_id'), status, upcoming_only)

    if status and status not in VALID_STATUSES:
        return jsonify({'error': f'Invalid status. Must be one of: {", ".join(sorted(VALID_STATUSES))}'}), 400

    conn = None
    try:
        tenant_id = g.user.get('tenant_id', 1)
        conn = get_db_connection()
        cur = conn.cursor()

        where_clauses = [sql.SQL("tenant_id = %s")]
        params = [tenant_id]

        if status:
            where_clauses.append(sql.SQL("status = %s"))
            params.append(status)

        if upcoming_only:
            where_clauses.append(sql.SQL("appointment_datetime >= NOW()"))
            where_clauses.append(sql.SQL("status = 'scheduled'"))

        params.extend([limit, offset])

        query = sql.SQL("""
            SELECT *, count(*) OVER() AS _total
            FROM appointments
            WHERE {where}
            ORDER BY appointment_datetime
            LIMIT %s OFFSET %s
        """).format(where=sql.SQL(" AND ").join(where_clauses))
        cur.execute(query, params)

        appointments = cur.fetchall()
        cur.close()

        total = appointments[0]['_total'] if appointments else 0
        results = []
        for row in appointments:
            row.pop('_total', None)
            results.append(_serialize_appointment(row))

        current_app.logger.info("GET /appointments: returning %d items (total=%d)", len(results), total)
        return jsonify(paginated_response(results, total, limit, offset, key='entries'))

    except Exception as e:
        current_app.logger.error("GET /appointments FAILED: %s", e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@bp.route('/appointments', methods=['POST'])
@require_auth
def create_appointment():
    """Create a new appointment."""
    data = request.json
    current_app.logger.info("POST /appointments: received data keys=%s", list(data.keys()) if data else None)

    conn = None
    try:
        user_id = get_user_id()
        tenant_id = g.user.get('tenant_id', 1)

        if not data:
            return jsonify({'error': 'No data provided'}), 400

        if 'title' not in data:
            return jsonify({'error': 'Missing required field: title'}), 400

        if 'appointment_datetime' not in data:
            return jsonify({'error': 'Missing required field: appointment_datetime'}), 400

        # Parse appointment_datetime
        try:
            appt_dt = datetime.fromisoformat(data['appointment_datetime'].replace('Z', '+00:00'))
            if appt_dt.tzinfo is None:
                appt_dt = pytz.utc.localize(appt_dt)
        except (ValueError, AttributeError):
            return jsonify({'error': 'Invalid appointment_datetime format. Use ISO 8601.'}), 400

        status = data.get('status', 'scheduled')
        if status not in VALID_STATUSES:
            return jsonify({'error': f'Invalid status. Must be one of: {", ".join(sorted(VALID_STATUSES))}'}), 400

        # Validate provider_id if provided
        provider_id = None
        if data.get('provider_id'):
            try:
                provider_id = uuid.UUID(data['provider_id'])
            except ValueError:
                return jsonify({'error': 'Invalid provider_id format'}), 400

        # Validate reminder_lead_times if provided
        lead_times = data.get('reminder_lead_times', [1440, 60])
        if not isinstance(lead_times, list) or not all(isinstance(x, int) and x > 0 for x in lead_times):
            return jsonify({'error': 'reminder_lead_times must be a list of positive integers (minutes)'}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        appt_id = uuid.uuid4()
        now = datetime.now(pytz.utc)

        cur.execute("""
            INSERT INTO appointments
            (tenant_id, id, user_id, title, appointment_datetime, duration_minutes,
             location, provider_id, notes, reminder_lead_times, reminder_enabled,
             status, sqlite_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            tenant_id, appt_id, user_id,
            data['title'],
            appt_dt,
            data.get('duration_minutes'),
            data.get('location'),
            provider_id,
            data.get('notes'),
            lead_times,
            data.get('reminder_enabled', True),
            status,
            data.get('sqlite_id'),
            now, now
        ))

        result = cur.fetchone()
        conn.commit()
        cur.close()

        current_app.logger.info("POST /appointments: created id=%s", result['id'])
        return jsonify({'id': str(result['id']), 'message': 'Created'}), 201

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        current_app.logger.error("POST /appointments FAILED: %s", e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@bp.route('/appointments/<id>', methods=['GET'])
@require_auth
def get_appointment(id):
    """Get a single appointment by ID."""
    appt_uuid, err = _parse_uuid(id)
    if err:
        return err

    current_app.logger.info("GET /appointments/%s: request received", id)

    conn = None
    try:
        tenant_id = g.user.get('tenant_id', 1)
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT * FROM appointments
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (tenant_id, get_user_id(), appt_uuid))

        appointment = cur.fetchone()
        cur.close()

        if not appointment:
            return jsonify({'error': 'Appointment not found'}), 404

        return jsonify(_serialize_appointment(appointment))

    except Exception as e:
        current_app.logger.error("GET /appointments/%s FAILED: %s", id, e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@bp.route('/appointments/<id>', methods=['PUT'])
@require_auth
def update_appointment(id):
    """Update an appointment (partial update)."""
    appt_uuid, err = _parse_uuid(id)
    if err:
        return err

    data = request.json
    current_app.logger.info("PUT /appointments/%s: received data keys=%s", id, list(data.keys()) if data else None)

    conn = None
    try:
        tenant_id = g.user.get('tenant_id', 1)

        if not data:
            return jsonify({'error': 'No data provided'}), 400

        if 'status' in data and data['status'] not in VALID_STATUSES:
            return jsonify({'error': f'Invalid status. Must be one of: {", ".join(sorted(VALID_STATUSES))}'}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        now = datetime.now(pytz.utc)

        updatable_fields = [
            'title', 'appointment_datetime', 'duration_minutes', 'location',
            'provider_id', 'notes', 'reminder_lead_times', 'reminder_enabled',
            'status', 'completed_at', 'sqlite_id'
        ]
        set_parts = []
        values = []

        for field in updatable_fields:
            if field in data:
                value = data[field]
                # Handle datetime parsing
                if field == 'appointment_datetime' and value:
                    try:
                        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
                        if value.tzinfo is None:
                            value = pytz.utc.localize(value)
                    except (ValueError, AttributeError):
                        return jsonify({'error': 'Invalid appointment_datetime format. Use ISO 8601.'}), 400
                if field == 'completed_at' and value:
                    try:
                        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
                        if value.tzinfo is None:
                            value = pytz.utc.localize(value)
                    except (ValueError, AttributeError):
                        return jsonify({'error': 'Invalid completed_at format. Use ISO 8601.'}), 400
                if field == 'provider_id' and value:
                    try:
                        value = uuid.UUID(value)
                    except ValueError:
                        return jsonify({'error': 'Invalid provider_id format'}), 400

                set_parts.append(sql.SQL("{} = %s").format(sql.Identifier(field)))
                values.append(value)

        if not set_parts:
            return jsonify({'error': 'No updatable fields provided'}), 400

        set_parts.append(sql.SQL("updated_at = %s"))
        values.append(now)

        values.append(tenant_id)
        values.append(get_user_id())
        values.append(appt_uuid)

        update_query = sql.SQL(
            "UPDATE appointments SET {set_clause} "
            "WHERE tenant_id = %s AND user_id = %s AND id = %s"
        ).format(set_clause=sql.SQL(", ").join(set_parts))
        cur.execute(update_query, values)

        rows_affected = cur.rowcount
        conn.commit()
        cur.close()

        if rows_affected == 0:
            return jsonify({'error': 'Appointment not found'}), 404

        current_app.logger.info("PUT /appointments/%s: updated", id)
        return jsonify({'message': 'Appointment updated'})

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        current_app.logger.error("PUT /appointments/%s FAILED: %s", id, e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@bp.route('/appointments/<id>', methods=['DELETE'])
@require_auth
def delete_appointment(id):
    """Delete an appointment."""
    appt_uuid, err = _parse_uuid(id)
    if err:
        return err

    current_app.logger.info("DELETE /appointments/%s: request received", id)

    conn = None
    try:
        tenant_id = g.user.get('tenant_id', 1)
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM appointments
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (tenant_id, get_user_id(), appt_uuid))

        rows_affected = cur.rowcount
        conn.commit()
        cur.close()

        if rows_affected == 0:
            return jsonify({'error': 'Appointment not found'}), 404

        current_app.logger.info("DELETE /appointments/%s: deleted", id)
        return jsonify({'message': 'Appointment deleted'})

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        current_app.logger.error("DELETE /appointments/%s FAILED: %s", id, e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@bp.route('/appointments/<id>/complete', methods=['POST'])
@require_auth
def complete_appointment(id):
    """Mark an appointment as completed."""
    appt_uuid, err = _parse_uuid(id)
    if err:
        return err

    current_app.logger.info("POST /appointments/%s/complete: request received", id)

    conn = None
    try:
        tenant_id = g.user.get('tenant_id', 1)
        conn = get_db_connection()
        cur = conn.cursor()

        now = datetime.now(pytz.utc)

        cur.execute("""
            UPDATE appointments
            SET status = 'completed', completed_at = %s, updated_at = %s
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (now, now, tenant_id, get_user_id(), appt_uuid))

        rows_affected = cur.rowcount
        conn.commit()
        cur.close()

        if rows_affected == 0:
            return jsonify({'error': 'Appointment not found'}), 404

        current_app.logger.info("POST /appointments/%s/complete: completed", id)
        return jsonify({'message': 'Appointment completed'})

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        current_app.logger.error("POST /appointments/%s/complete FAILED: %s", id, e, exc_info=True)
        return jsonify({'error': 'An internal error occurred'}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
