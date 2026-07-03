"""
Food Items and Meals routes.

Blueprint for managing food items and meal templates.
"""
from flask import Blueprint, request, jsonify, g
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
import analytics

bp = Blueprint('food', __name__, url_prefix='/api/v1')


def normalize_food_payload_v4(data):
    """Normalize legacy v3 food fields to v4 canonical names."""
    payload = dict(data or {})
    if 'carbs_g' not in payload and payload.get('carbs_total_g') is not None:
        payload['carbs_g'] = payload.get('carbs_total_g')
    if 'fat_g' not in payload and payload.get('fat_total_g') is not None:
        payload['fat_g'] = payload.get('fat_total_g')
    return payload


def normalize_meal_payload_v4(data):
    """Normalize legacy v3 meal fields to v4 canonical names."""
    payload = dict(data or {})
    if 'is_favorite' not in payload and payload.get('is_template') is not None:
        payload['is_favorite'] = bool(payload.get('is_template'))

    normalized_items = []
    for item in payload.get('items') or []:
        normalized = dict(item or {})
        if 'servings' not in normalized and normalized.get('quantity') is not None:
            normalized['servings'] = normalized.get('quantity')
        normalized_items.append(normalized)
    payload['items'] = normalized_items
    return payload


# ==================== FOOD ITEMS ====================

@bp.route('/food-items', methods=['GET'])
@require_auth
def get_food_items():
    """Get a paginated list of food items.

    Default page size is 100, max 500 — food catalog browsing is inherently
    page-heavy, so the limits are looser than the standard 50/200.
    """
    limit, offset = parse_pagination_params(default_limit=100, max_limit=500)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT count(*) OVER() AS _total,
               id, name, brand, barcode, calories, protein_g, carbs_g, fat_g,
               is_favorite, created_at, updated_at
        FROM health_food_itemsv2
        WHERE user_id = %s
        ORDER BY name
        LIMIT %s OFFSET %s
    """, (get_user_id(), limit, offset))
    items = cur.fetchall()
    cur.close()
    conn.close()

    total = items[0]['_total'] if items else 0
    for item in items:
        item.pop('_total', None)
        item['id'] = str(item['id'])
        if item.get('created_at'):
            item['created_at'] = item['created_at'].isoformat()
        if item.get('updated_at'):
            item['updated_at'] = item['updated_at'].isoformat()

    return jsonify(paginated_response(items, total, limit, offset, key='entries'))


@bp.route('/food-items', methods=['POST'])
@require_auth
def create_food_item():
    """Create a new food item.

    ``fdc_id`` may be supplied to record a link to a USDA FoodData Central
    record (resolved client-side); it is stored verbatim. Home Edition does
    no server-side USDA lookup — the column is kept for sync compatibility
    with Central.
    """
    data = normalize_food_payload_v4(request.json)
    if not data:
        return jsonify({'error': 'Missing request body'}), 400

    fdc_id = data.get('fdc_id')
    if fdc_id is not None:
        try:
            fdc_id = int(fdc_id)
        except (TypeError, ValueError):
            return jsonify({'error': 'fdc_id must be an integer'}), 400

    if 'name' not in data:
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
         is_favorite, fdc_id, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        tenant_id, item_id, user_id, data['name'], data.get('brand'), data.get('barcode'),
        data.get('calories'), data.get('protein_g'), data.get('carbs_g'), data.get('fat_g'),
        data.get('is_favorite', False), fdc_id,
        now, now
    ))

    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'id': str(result['id']), 'message': 'Food item created'}), 201


@bp.route('/food-items/<item_id>', methods=['PUT'])
@require_auth
def update_food_item(item_id):
    """Update a food item"""
    data = normalize_food_payload_v4(request.json)
    tenant_id = g.user.get('tenant_id', 1)

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
        now, tenant_id, get_user_id(), uuid.UUID(item_id)
    ))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': 'Food item updated'})


@bp.route('/food-items/<item_id>', methods=['DELETE'])
@require_auth
def delete_food_item(item_id):
    """Delete a food item"""
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM health_food_itemsv2
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (tenant_id, get_user_id(), uuid.UUID(item_id),))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'message': 'Food item deleted'})


# ==================== MEALS ====================

@bp.route('/meals', methods=['GET'])
@require_auth
def get_meals():
    """Get a paginated list of meals with their items.

    Ordered by meal name (resource collection, not a time series).
    """
    limit, offset = parse_pagination_params()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT count(*) OVER() AS _total,
               m.id, m.name, m.description, m.is_favorite,
               json_agg(
                   json_build_object(
                       'food_item_id', mi.food_item_id,
                       'food_name', f.name,
                       'servings', mi.servings
                   )
               ) FILTER (WHERE mi.id IS NOT NULL) as items
        FROM meals m
        LEFT JOIN meal_items mi ON m.id = mi.meal_id
        LEFT JOIN health_food_itemsv2 f ON mi.food_item_id = f.id
        WHERE m.user_id = %s
        GROUP BY m.id, m.name, m.description, m.is_favorite
        ORDER BY m.name
        LIMIT %s OFFSET %s
    """, (get_user_id(), limit, offset))

    meals = cur.fetchall()
    cur.close()
    conn.close()

    total = meals[0]['_total'] if meals else 0
    for meal in meals:
        meal.pop('_total', None)
        meal['id'] = str(meal['id'])
        if meal['items'] and meal['items'][0] is not None:
            for item in meal['items']:
                if item.get('food_item_id'):
                    item['food_item_id'] = str(item['food_item_id'])
        else:
            meal['items'] = []

    return jsonify(paginated_response(meals, total, limit, offset, key='entries'))


@bp.route('/meals', methods=['POST'])
@require_auth
def create_meal():
    """Create a new meal"""
    data = normalize_meal_payload_v4(request.json)
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    meal_id = uuid.uuid4()
    now = datetime.now(pytz.utc)

    cur.execute("""
        INSERT INTO meals (tenant_id, id, user_id, name, description, is_favorite, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (tenant_id, meal_id, user_id, data['name'], data.get('description'),
          data.get('is_favorite', False), now, now))

    result = cur.fetchone()

    # Add meal items
    if data.get('items'):
        for item in data['items']:
            item_id = uuid.uuid4()
            cur.execute("""
                INSERT INTO meal_items (tenant_id, id, user_id, meal_id, food_item_id, servings, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (tenant_id, item_id, user_id, meal_id, uuid.UUID(item['food_item_id']),
                  item.get('servings', 1), now))

    conn.commit()
    cur.close()
    conn.close()

    analytics.capture('meal_created', {
        'meal_id': str(meal_id),
        'item_count': len(data.get('items') or []),
    })

    return jsonify({'id': str(result['id']), 'message': 'Meal created'}), 201


@bp.route('/meals/<meal_id>', methods=['PUT'])
@require_auth
def update_meal(meal_id):
    """Update a meal"""
    data = normalize_meal_payload_v4(request.json)
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    now = datetime.now(pytz.utc)
    meal_uuid = uuid.UUID(meal_id)

    cur.execute("""
        UPDATE meals
        SET name = %s, description = %s, is_favorite = %s, updated_at = %s
        WHERE tenant_id = %s AND user_id = %s AND id = %s
    """, (data['name'], data.get('description'), data.get('is_favorite', False), now, tenant_id, user_id, meal_uuid))

    # Delete existing meal items
    cur.execute("DELETE FROM meal_items WHERE tenant_id = %s AND user_id = %s AND meal_id = %s", (tenant_id, user_id, meal_uuid,))

    # Add new meal items
    if data.get('items'):
        for item in data['items']:
            item_id = uuid.uuid4()
            cur.execute("""
                INSERT INTO meal_items (tenant_id, id, user_id, meal_id, food_item_id, servings, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (tenant_id, item_id, user_id, meal_uuid, uuid.UUID(item['food_item_id']),
                  item.get('servings', 1), now))

    conn.commit()
    cur.close()
    conn.close()

    analytics.capture('meal_updated', {
        'meal_id': str(meal_uuid),
        'item_count': len(data.get('items') or []),
    })

    return jsonify({'message': 'Meal updated'})


@bp.route('/meals/<meal_id>', methods=['DELETE'])
@require_auth
def delete_meal(meal_id):
    """Delete a meal and its items"""
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_db_connection()
    cur = conn.cursor()

    meal_uuid = uuid.UUID(meal_id)

    # Delete meal items first (FK constraint)
    cur.execute("DELETE FROM meal_items WHERE tenant_id = %s AND user_id = %s AND meal_id = %s", (tenant_id, get_user_id(), meal_uuid,))
    cur.execute("DELETE FROM meals WHERE tenant_id = %s AND user_id = %s AND id = %s", (tenant_id, get_user_id(), meal_uuid,))

    conn.commit()
    cur.close()
    conn.close()

    analytics.capture('meal_deleted', {'meal_id': str(meal_uuid)})

    return jsonify({'message': 'Meal deleted'})
