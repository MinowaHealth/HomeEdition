"""Food Items and Meals routes — v2 (embedding-aware).

POST and PUT for food_items accept optional embedding vectors.
All other routes proxy to v1 implementations unchanged.
"""
from flask import Blueprint, request, jsonify, g, current_app
from datetime import datetime
import pytz
import uuid

from utils import require_auth, get_db_connection, get_user_id

from .embedding_helpers import embed_field
from .food import (
    normalize_food_payload_v4,
    get_food_items,
    delete_food_item,
    get_meals,
    create_meal,
    update_meal,
    delete_meal,
)

bp = Blueprint('food_v2', __name__, url_prefix='/api/v2')

# ==================== PROXIED FROM V1 ====================

bp.add_url_rule('/food-items', 'get_food_items', get_food_items, methods=['GET'])
bp.add_url_rule('/food-items/<item_id>', 'delete_food_item', delete_food_item, methods=['DELETE'])

bp.add_url_rule('/meals', 'get_meals', get_meals, methods=['GET'])
bp.add_url_rule('/meals', 'create_meal', create_meal, methods=['POST'])
bp.add_url_rule('/meals/<meal_id>', 'update_meal', update_meal, methods=['PUT'])
bp.add_url_rule('/meals/<meal_id>', 'delete_meal', delete_meal, methods=['DELETE'])


# ==================== V2 EMBEDDING-AWARE ====================

@bp.route('/food-items', methods=['POST'])
@require_auth
def create_food_item_v2():
    """Create a new food item with optional embedding.

    Accepts the same payload as v1 (with v3→v4 normalization), plus:
    - embedding: list of 768 floats (pre-computed vector from device)

    If no embedding provided, server generates one from the name field.
    Embedding failure never blocks the create operation.
    """
    data = normalize_food_payload_v4(request.json)
    if not data or 'name' not in data:
        return jsonify({'error': 'Missing required field: name'}), 400

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    item_id = uuid.uuid4()
    now = datetime.now(pytz.utc)

    cur.execute("""
        INSERT INTO health_food_itemsv2
        (tenant_id, id, user_id, name, brand, barcode, calories, protein_g, carbs_g, fat_g,
         is_favorite, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        tenant_id, item_id, user_id, data['name'], data.get('brand'), data.get('barcode'),
        data.get('calories'), data.get('protein_g'), data.get('carbs_g'), data.get('fat_g'),
        data.get('is_favorite', False),
        now, now
    ))

    result = cur.fetchone()
    conn.commit()
    cur.close()

    # Inline embedding — accepts client vector or generates server-side
    embedded_by = embed_field(
        conn, tenant_id, result['id'],
        'health_food_itemsv2', 'embedding_name',
        data['name'], data.get('embedding'),
    )

    conn.close()

    response = {'id': str(result['id']), 'message': 'Food item created'}
    if embedded_by:
        response['embedded_by'] = embedded_by
    return jsonify(response), 201


@bp.route('/food-items/<item_id>', methods=['PUT'])
@require_auth
def update_food_item_v2(item_id):
    """Update a food item with optional re-embedding.

    Same payload as v1, plus optional embedding field.
    Re-embeds when the name changes.
    """
    data = normalize_food_payload_v4(request.json)
    tenant_id = g.user.get('tenant_id', 1)
    user_id = get_user_id()

    conn = get_db_connection()
    cur = conn.cursor()

    now = datetime.now(pytz.utc)

    cur.execute("""
        UPDATE health_food_itemsv2
        SET name = %s, brand = %s, barcode = %s, calories = %s,
            protein_g = %s, carbs_g = %s, fat_g = %s, is_favorite = %s, updated_at = %s
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (
        data['name'], data.get('brand'), data.get('barcode'),
        data.get('calories'), data.get('protein_g'), data.get('carbs_g'), data.get('fat_g'),
        data.get('is_favorite', False),
        now, tenant_id, user_id, uuid.UUID(item_id)
    ))

    conn.commit()
    cur.close()

    # Re-embed on name change
    embedded_by = embed_field(
        conn, tenant_id, uuid.UUID(item_id),
        'health_food_itemsv2', 'embedding_name',
        data['name'], data.get('embedding'),
    )

    conn.close()

    response = {'message': 'Food item updated'}
    if embedded_by:
        response['embedded_by'] = embedded_by
    return jsonify(response)
