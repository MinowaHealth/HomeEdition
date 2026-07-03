"""
Provider Contacts Routes

API endpoints for users to manage their personal provider address book.

Home Edition: contacts are plain address-book entries. The enterprise
NPI-verification pipeline (providers.verify queue + worker) is removed;
the verification columns remain in the data model but new contacts are
simply 'unverified'.
"""

from flask import Blueprint, request, jsonify, g, current_app
from db_driver import sql
import uuid

from utils import require_auth, get_db_connection
import db_manager
import analytics

bp = Blueprint('provider_contacts', __name__, url_prefix='/api/v1')

VALID_PRACTITIONER_TYPES = (
    'medical', 'dental', 'massage', 'acupuncture', 'chiropractic',
    'naturopathic', 'mental_health', 'physical_therapy', 'other',
)

VALID_RELATIONSHIP_TYPES = (
    'primary_care', 'specialist', 'therapist', 'caregiver',
    'family', 'dentist', 'other',
)

VALID_VERIFICATION_STATUSES = (
    'pending', 'verified', 'review', 'unverified', 'user_confirmed',
)


def _parse_uuid(value):
    try:
        return uuid.UUID(str(value))
    except Exception:
        return None


@bp.route('/provider-contacts', methods=['POST'])
@require_auth
def create_contact():
    """
    Add a provider contact to the user's personal address book.

    Request body:
    - display_name: string (required)
    - first_name, last_name: string (optional but recommended for NPI lookup)
    - phone, address_line1, address_line2, city, state, zip_code: string
    - portal_url: string (patient portal URL)
    - notes: string
    - practitioner_type: one of VALID_PRACTITIONER_TYPES (default: 'medical')
    - relationship_type: one of VALID_RELATIONSHIP_TYPES (default: 'primary_care')

    Returns 201 with the created contact.
    """
    conn = None
    try:
        data = request.get_json() or {}

        display_name = (data.get('display_name') or '').strip()
        if not display_name:
            return jsonify({'error': 'display_name is required'}), 400

        practitioner_type = data.get('practitioner_type', 'medical')
        if practitioner_type not in VALID_PRACTITIONER_TYPES:
            return jsonify({'error': f'Invalid practitioner_type. Must be one of: {list(VALID_PRACTITIONER_TYPES)}'}), 400

        relationship_type = data.get('relationship_type', 'primary_care')
        if relationship_type not in VALID_RELATIONSHIP_TYPES:
            return jsonify({'error': f'Invalid relationship_type. Must be one of: {list(VALID_RELATIONSHIP_TYPES)}'}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        user_id = g.user.get('user_id')
        tenant_id = g.user.get('tenant_id', 1)

        contact_id = str(uuid.uuid4())
        first_name = (data.get('first_name') or '').strip() or None
        last_name = (data.get('last_name') or '').strip() or None

        # Home Edition: no NPI verification pipeline — contacts are plain
        # address-book entries.
        verification_status = 'unverified'

        cur.execute("""
            INSERT INTO user_provider_contacts (
                tenant_id, id, user_id, display_name, first_name, last_name,
                phone, address_line1, address_line2, city, state, zip_code,
                portal_url, notes, practitioner_type, relationship_type,
                verification_status
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s
            )
            RETURNING *
        """, (
            tenant_id, contact_id, user_id, display_name, first_name, last_name,
            data.get('phone'), data.get('address_line1'), data.get('address_line2'),
            data.get('city'), data.get('state'), data.get('zip_code'),
            data.get('portal_url'), data.get('notes'),
            practitioner_type, relationship_type,
            verification_status,
        ))

        contact = cur.fetchone()
        conn.commit()

        current_app.logger.info(
            "User %s created provider contact %s (type=%s, verification=%s)",
            user_id, contact_id, practitioner_type, verification_status,
        )

        analytics.capture('provider_contact_created', {
            'contact_id': contact_id,
            'practitioner_type': practitioner_type,
            'verification_status': verification_status,
        })

        return jsonify(_serialize_contact(contact)), 201

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/provider-contacts', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error(f"Error creating provider contact: {e}")
        if conn:
            conn.rollback()
        return jsonify({'error': 'Failed to create provider contact'}), 500
    finally:
        if conn:
            conn.close()


@bp.route('/provider-contacts', methods=['GET'])
@require_auth
def list_contacts():
    """
    List the user's provider contacts.

    Query params:
    - status: filter by verification_status (optional)
    - type: filter by practitioner_type (optional)
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        status_filter = request.args.get('status')
        type_filter = request.args.get('type')

        user_id = g.user.get('user_id')

        query = "SELECT * FROM user_provider_contacts WHERE 1=1 AND user_id = %s"
        params = [user_id]

        if status_filter and status_filter in VALID_VERIFICATION_STATUSES:
            query += " AND verification_status = %s"
            params.append(status_filter)

        if type_filter and type_filter in VALID_PRACTITIONER_TYPES:
            query += " AND practitioner_type = %s"
            params.append(type_filter)

        query += " ORDER BY created_at DESC"

        cur.execute(query, params)
        contacts = cur.fetchall()

        return jsonify({
            'contacts': [_serialize_contact(c) for c in contacts],
            'count': len(contacts),
        })

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/provider-contacts', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error(f"Error listing provider contacts: {e}")
        return jsonify({'error': 'Failed to list provider contacts'}), 500
    finally:
        if conn:
            conn.close()


@bp.route('/provider-contacts/<contact_id>', methods=['GET'])
@require_auth
def get_contact(contact_id):
    """Get a single provider contact."""
    conn = None
    try:
        contact_uuid = _parse_uuid(contact_id)
        if not contact_uuid:
            return jsonify({'error': 'Invalid contact_id'}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        tenant_id = g.user.get('tenant_id', 1)
        user_id = g.user.get('user_id')

        cur.execute("""
            SELECT * FROM user_provider_contacts
            WHERE tenant_id = %s AND user_id = %s AND id = %s
        """, (tenant_id, user_id, contact_uuid))

        contact = cur.fetchone()
        if not contact:
            return jsonify({'error': 'Contact not found'}), 404

        return jsonify(_serialize_contact(contact))

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/provider-contacts', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error(f"Error getting provider contact: {e}")
        return jsonify({'error': 'Failed to get provider contact'}), 500
    finally:
        if conn:
            conn.close()


@bp.route('/provider-contacts/<contact_id>', methods=['PUT'])
@require_auth
def update_contact(contact_id):
    """
    Update a provider contact's info.

    Only user-entered fields can be updated (not verification fields).
    """
    conn = None
    try:
        contact_uuid = _parse_uuid(contact_id)
        if not contact_uuid:
            return jsonify({'error': 'Invalid contact_id'}), 400

        data = request.get_json() or {}

        # Updatable fields only — not verification_status, npi_*, linked_provider_id
        allowed = {
            'display_name', 'first_name', 'last_name', 'phone',
            'address_line1', 'address_line2', 'city', 'state', 'zip_code',
            'portal_url', 'notes', 'practitioner_type', 'relationship_type',
        }
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}

        if not updates:
            return jsonify({'error': 'No valid fields to update'}), 400

        if 'practitioner_type' in updates and updates['practitioner_type'] not in VALID_PRACTITIONER_TYPES:
            return jsonify({'error': f'Invalid practitioner_type. Must be one of: {list(VALID_PRACTITIONER_TYPES)}'}), 400

        if 'relationship_type' in updates and updates['relationship_type'] not in VALID_RELATIONSHIP_TYPES:
            return jsonify({'error': f'Invalid relationship_type. Must be one of: {list(VALID_RELATIONSHIP_TYPES)}'}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        tenant_id = g.user.get('tenant_id', 1)

        set_clauses = [sql.SQL("{} = %s").format(sql.Identifier(k)) for k in updates]
        set_clauses.append(sql.SQL("updated_at = now()"))
        values = list(updates.values())

        update_query = sql.SQL(
            "UPDATE user_provider_contacts SET {set_clause} "
            "WHERE tenant_id = %s AND user_id = %s AND id = %s RETURNING *"
        ).format(set_clause=sql.SQL(', ').join(set_clauses))
        cur.execute(update_query, values + [tenant_id, g.user.get('user_id'), contact_uuid])

        contact = cur.fetchone()
        if not contact:
            return jsonify({'error': 'Contact not found'}), 404

        conn.commit()
        return jsonify(_serialize_contact(contact))

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/provider-contacts', str(g.user.get('user_id', 'anon')))
            if conn:
                conn.rollback()
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error(f"Error updating provider contact: {e}")
        if conn:
            conn.rollback()
        return jsonify({'error': 'Failed to update provider contact'}), 500
    finally:
        if conn:
            conn.close()


@bp.route('/provider-contacts/<contact_id>', methods=['DELETE'])
@require_auth
def delete_contact(contact_id):
    """Delete a provider contact from the user's address book."""
    conn = None
    try:
        contact_uuid = _parse_uuid(contact_id)
        if not contact_uuid:
            return jsonify({'error': 'Invalid contact_id'}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        tenant_id = g.user.get('tenant_id', 1)
        user_id = g.user.get('user_id')

        cur.execute("""
            DELETE FROM user_provider_contacts
            WHERE tenant_id = %s AND user_id = %s AND id = %s
            RETURNING id
        """, (tenant_id, user_id, contact_uuid))

        deleted = cur.fetchone()
        if not deleted:
            return jsonify({'error': 'Contact not found'}), 404

        conn.commit()

        current_app.logger.info("User %s deleted provider contact %s", g.user.get('user_id'), contact_id)

        return jsonify({'success': True, 'deleted_id': str(deleted['id'])})

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/provider-contacts', str(g.user.get('user_id', 'anon')))
            if conn:
                conn.rollback()
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error(f"Error deleting provider contact: {e}")
        if conn:
            conn.rollback()
        return jsonify({'error': 'Failed to delete provider contact'}), 500
    finally:
        if conn:
            conn.close()


def _serialize_contact(row):
    """Convert a RealDictRow to a JSON-safe dict."""
    if not row:
        return None
    result = dict(row)
    for key, val in result.items():
        if isinstance(val, uuid.UUID):
            result[key] = str(val)
        elif hasattr(val, 'isoformat'):
            result[key] = val.isoformat()
    return result
