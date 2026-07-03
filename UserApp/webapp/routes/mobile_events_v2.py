"""Mobile event logging — v2.

Accepts generic in-app events from mobile clients with optional pre-computed
embedding vectors. Server falls back to Ollama embedding when none provided.
Embedding failure never blocks the write.

Route: POST /api/v2/mobile-events
"""
from datetime import datetime
import uuid

import pytz
from flask import Blueprint, g, jsonify, request, current_app

from utils import require_auth, get_db_connection, get_user_id
from .embedding_helpers import embed_field

bp = Blueprint('mobile_events_v2', __name__, url_prefix='/api/v2')


@bp.route('/mobile-events', methods=['POST'])
@require_auth
def log_mobile_event():
    data = request.json

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    event_text = (data.get('event_text') or '').strip()
    if not event_text:
        return jsonify({'error': 'Missing required field: event_text'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    event_id = uuid.uuid4()
    now = datetime.now(pytz.utc)

    try:
        cur.execute(
            """
            INSERT INTO mobile_events
                (tenant_id, id, user_id, device_type, screen, event_text,
                 duration_ms, status, error_code, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                tenant_id,
                event_id,
                user_id,
                data.get('device_type'),
                data.get('screen'),
                event_text,
                data.get('duration_ms'),
                data.get('status'),
                data.get('error_code'),
                now,
            ),
        )
        result = cur.fetchone()
        conn.commit()
        cur.close()

        # Structured log for observability (matches logs.sh stack filter)
        device = (data.get('device_type') or '').lower()
        platform = 'ios' if device.startswith('ios') else (
            'android' if device.startswith('android') else 'unknown')
        current_app.logger.info(
            "log-mobile-dbwrite screen=%s platform=%s duration_ms=%s status=%s",
            data.get('screen'), platform,
            data.get('duration_ms'), data.get('status'),
        )
    except Exception as exc:
        current_app.logger.error(
            "POST /v2/mobile-events INSERT failed: %s", exc, exc_info=True
        )
        conn.close()
        return jsonify({'error': 'Internal server error'}), 500

    # Inline embedding — called AFTER commit, per v2 route convention.
    embedded_by = embed_field(
        conn, tenant_id, result['id'],
        'mobile_events', 'embedding_event_text',
        event_text, data.get('embedding'),
    )

    conn.close()

    response = {'id': str(result['id']), 'message': 'Event logged'}
    if embedded_by:
        response['embedded_by'] = embedded_by
    return jsonify(response), 201
