"""Activity Logging routes — v2 passthrough.

All routes proxy to v1 implementations unchanged.
No embedding columns on logging tables.
"""
from flask import Blueprint

from .logging_routes import (
    log_meal,
    log_stack,
    log_health_input,
    log_food_item,
    get_health_input_log,
    get_all_logs,
    update_health_input_log,
    delete_health_input_log,
    get_food_log,
    update_food_log,
    delete_food_log,
    get_log_promotions,
    create_log_promotion,
    update_log_promotion,
    delete_log_promotion,
)

bp = Blueprint('logging_v2', __name__, url_prefix='/api/v2')

# Meal and stack logging
bp.add_url_rule('/log-meal', 'log_meal', log_meal, methods=['POST'])
bp.add_url_rule('/log-stack', 'log_stack', log_stack, methods=['POST'])
bp.add_url_rule('/log-health-input', 'log_health_input', log_health_input, methods=['POST'])
bp.add_url_rule('/log-food-item', 'log_food_item', log_food_item, methods=['POST'])

# Health input log CRUD
bp.add_url_rule('/health-input-log', 'get_health_input_log', get_health_input_log, methods=['GET'])
bp.add_url_rule('/health-input-log/<log_id>', 'update_health_input_log', update_health_input_log, methods=['PUT'])
bp.add_url_rule('/health-input-log/<log_id>', 'delete_health_input_log', delete_health_input_log, methods=['DELETE'])

# Combined logs
bp.add_url_rule('/all-logs', 'get_all_logs', get_all_logs, methods=['GET'])

# Food log CRUD
bp.add_url_rule('/food-log', 'get_food_log', get_food_log, methods=['GET'])
bp.add_url_rule('/food-log/<log_id>', 'update_food_log', update_food_log, methods=['PUT'])
bp.add_url_rule('/food-log/<log_id>', 'delete_food_log', delete_food_log, methods=['DELETE'])

# Log promotions
bp.add_url_rule('/log-promotions', 'get_log_promotions', get_log_promotions, methods=['GET'])
bp.add_url_rule('/log-promotions', 'create_log_promotion', create_log_promotion, methods=['POST'])
bp.add_url_rule('/log-promotions/<promo_id>', 'update_log_promotion', update_log_promotion, methods=['PUT'])
bp.add_url_rule('/log-promotions/<promo_id>', 'delete_log_promotion', delete_log_promotion, methods=['DELETE'])
