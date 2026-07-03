"""Vitals routes — v2 (embedding-aware).

POST and PUT for observations accept optional embedding vectors
(replaces server-only _embed_observation with embed_field that also
accepts client-side pre-computed vectors).

All other routes proxy to v1 implementations unchanged.
"""
from flask import Blueprint, request, jsonify, g, current_app
from datetime import datetime
import pytz
import uuid

from utils import require_auth, get_db_connection, get_user_id, local_to_utc

from .embedding_helpers import embed_field
from .vitals import (
    get_blood_pressure,
    log_blood_pressure,
    delete_blood_pressure,
    get_temperature,
    log_temperature,
    get_weight,
    log_weight,
    delete_health_metric,
    get_observations,
    patch_observation,
    delete_observation,
)

bp = Blueprint('vitals_v2', __name__, url_prefix='/api/v2')

# ==================== PROXIED FROM V1 ====================

bp.add_url_rule('/blood-pressure', 'get_blood_pressure', get_blood_pressure, methods=['GET'])
bp.add_url_rule('/blood-pressure', 'log_blood_pressure', log_blood_pressure, methods=['POST'])
bp.add_url_rule('/blood-pressure/<reading_id>', 'delete_blood_pressure', delete_blood_pressure, methods=['DELETE'])

bp.add_url_rule('/temperature', 'get_temperature', get_temperature, methods=['GET'])
bp.add_url_rule('/temperature', 'log_temperature', log_temperature, methods=['POST'])

bp.add_url_rule('/weight', 'get_weight', get_weight, methods=['GET'])
bp.add_url_rule('/weight', 'log_weight', log_weight, methods=['POST'])

bp.add_url_rule('/health-metrics/<metric_id>', 'delete_health_metric', delete_health_metric, methods=['DELETE'])

bp.add_url_rule('/observations', 'get_observations', get_observations, methods=['GET'])
bp.add_url_rule('/observations/<obs_id>', 'patch_observation', patch_observation, methods=['PATCH'])
bp.add_url_rule('/observations/<obs_id>', 'delete_observation', delete_observation, methods=['DELETE'])


# ==================== V2 EMBEDDING-AWARE ====================

@bp.route('/observations', methods=['POST'])
@require_auth
def create_observation_v2():
    """Create a new observation with optional embedding.

    Accepts the same payload as v1, plus:
    - embedding: list of 768 floats (pre-computed vector from device)

    If device provides a valid embedding, it is stored directly (no server
    Ollama call). Otherwise, the server generates one via host Ollama
    (best-effort). Embedding failure never blocks the create operation.
    """
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

    # Inline embedding — accepts client vector or generates server-side
    embedded_by = embed_field(
        conn, tenant_id, result['id'],
        'health_observations', 'embedding_content',
        data['observation'], data.get('embedding'),
    )

    conn.close()

    response = {'id': str(result['id']), 'message': 'Observation created'}
    if embedded_by:
        response['embedded_by'] = embedded_by
    return jsonify(response), 201


@bp.route('/observations/<obs_id>', methods=['PUT'])
@require_auth
def update_observation_v2(obs_id):
    """Update an observation with optional re-embedding.

    Same payload as v1, plus optional embedding field.
    Re-embeds when content changes.
    """
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

    # Re-embed on content change — accepts client vector or generates server-side
    embedded_by = embed_field(
        conn, tenant_id, uuid.UUID(obs_id),
        'health_observations', 'embedding_content',
        data['observation'], data.get('embedding'),
    )

    conn.close()

    response = {'message': 'Observation updated'}
    if embedded_by:
        response['embedded_by'] = embedded_by
    return jsonify(response)
