"""Feedback routes — v2 passthrough.

All routes proxy to v1 implementations unchanged.
"""
from flask import Blueprint

from .feedback import (
    get_feedback,
    create_feedback,
    update_feedback,
    delete_feedback,
)

bp = Blueprint('feedback_v2', __name__, url_prefix='/api/v2')

bp.add_url_rule('/feedback', 'get_feedback', get_feedback, methods=['GET'])
bp.add_url_rule('/feedback', 'create_feedback', create_feedback, methods=['POST'])
bp.add_url_rule('/feedback/<feedback_id>', 'update_feedback', update_feedback, methods=['PUT'])
bp.add_url_rule('/feedback/<feedback_id>', 'delete_feedback', delete_feedback, methods=['DELETE'])
