"""
Feedback routes.

Blueprint for user feedback during alpha testing.
"""
from flask import Blueprint, request, jsonify, g
import pytz
import uuid
import logging

from utils import (
    require_auth,
    parse_pagination_params,
    paginated_response,
)
import db_manager
import analytics

_feedback_logger = logging.getLogger(__name__)

bp = Blueprint('feedback', __name__, url_prefix='/api/v1')


# ==================== FEEDBACK (Alpha Testing) ====================

@bp.route('/feedback', methods=['GET'])
@require_auth
def get_feedback():
    """Get current user's feedback entries, optionally filtered by page_context"""
    user_id = g.user['user_id']
    limit, offset = parse_pagination_params(default_limit=50, max_limit=200)
    conn = db_manager.get_direct_admin_connection()
    cur = conn.cursor()

    page = request.args.get('screen') or request.args.get('page')

    if page:
        cur.execute("""
            SELECT count(*) OVER() AS _total,
                   id, feedback_type, content, page_context, app_version,
                   status, created_at
            FROM feedback
            WHERE user_id = %s AND page_context = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, (user_id, page, limit, offset))
    else:
        cur.execute("""
            SELECT count(*) OVER() AS _total,
                   id, feedback_type, content, page_context, app_version,
                   status, created_at
            FROM feedback
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, (user_id, limit, offset))

    entries = cur.fetchall()
    cur.close()
    conn.close()

    total = entries[0]['_total'] if entries else 0
    for entry in entries:
        entry.pop('_total', None)
        if entry.get('created_at'):
            entry['date'] = entry['created_at'].isoformat()
            del entry['created_at']

    return jsonify(paginated_response(entries, total, limit, offset, key='entries'))


@bp.route('/feedback', methods=['POST'])
@require_auth
def create_feedback():
    """Create a new feedback entry"""
    data = request.json
    user_id = g.user['user_id']
    tenant_id = g.user.get('tenant_id', 1)

    content = data.get('feedback') or data.get('content')
    if not content:
        return jsonify({'error': 'Feedback text is required (use "feedback" or "content" field)'}), 400

    # feedback_type must be one of: bug, feature, general, praise
    feedback_type = data.get('feedback_type') or data.get('category', 'general')
    valid_types = ['bug', 'feature', 'general', 'praise']
    if feedback_type not in valid_types:
        feedback_type = 'general'

    conn = db_manager.get_direct_admin_connection()
    cur = conn.cursor()

    feedback_id = uuid.uuid4()

    # Source page identifier — accept any of `page_context`, `page`, or
    # `screen`. The mobile client historically sent `screen` (matching the
    # GET filter param), so include it as a fallback to keep older builds
    # working alongside the canonical `page_context` field.
    page_context = data.get('page_context') or data.get('page') or data.get('screen') or ''

    try:
        cur.execute("""
            INSERT INTO feedback (tenant_id, id, user_id, feedback_type, content,
                                  page_context, app_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            tenant_id,
            feedback_id,
            user_id,
            feedback_type,
            content,
            page_context or None,
            data.get('app_version')
        ))

        result = cur.fetchone()

        conn.commit()

        _feedback_logger.info(
            "feedback_submitted type=%s page=%s user=%s",
            feedback_type, page_context, user_id,
        )

        analytics.capture('feedback_submitted', {
            'feedback_type': feedback_type,
            'screen': page_context,
        })

        return jsonify({'id': str(result['id']), 'message': 'Feedback created'}), 201
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


@bp.route('/feedback/<feedback_id>', methods=['PUT'])
@require_auth
def update_feedback(feedback_id):
    """Update a feedback entry (only own feedback)"""
    data = request.json
    user_id = g.user['user_id']
    tenant_id = g.user.get('tenant_id', 1)

    content = data.get('feedback') or data.get('content')
    if not content:
        return jsonify({'error': 'Feedback text is required'}), 400

    conn = db_manager.get_direct_admin_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE feedback
        SET content = %s, page_context = %s, app_version = %s
        WHERE tenant_id = %s AND id = %s AND user_id = %s
    """, (
        content,
        data.get('page_context') or data.get('page'),
        data.get('app_version'),
        tenant_id,
        uuid.UUID(feedback_id),
        user_id
    ))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': 'Feedback updated'})


@bp.route('/feedback/<feedback_id>', methods=['DELETE'])
@require_auth
def delete_feedback(feedback_id):
    """Delete a feedback entry (only own feedback)"""
    user_id = g.user['user_id']
    tenant_id = g.user.get('tenant_id', 1)

    conn = db_manager.get_direct_admin_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM feedback WHERE tenant_id = %s AND id = %s AND user_id = %s", (tenant_id, uuid.UUID(feedback_id), user_id))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': 'Feedback deleted'})
