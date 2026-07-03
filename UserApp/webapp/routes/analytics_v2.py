"""Analytics routes — v2 passthrough.

All routes proxy to v1 implementations unchanged.
"""
from flask import Blueprint

from .analytics import (
    get_your_week,
    get_sleep_heatmap,
    get_stress_heatmap,
    get_lab_results,
    get_table_counts,
    health_query,
)

bp = Blueprint('analytics_v2', __name__, url_prefix='/api/v2')

bp.add_url_rule('/your-week', 'get_your_week', get_your_week)
bp.add_url_rule('/sleep-heatmap', 'get_sleep_heatmap', get_sleep_heatmap)
bp.add_url_rule('/stress-heatmap', 'get_stress_heatmap', get_stress_heatmap)
bp.add_url_rule('/lab-results', 'get_lab_results', get_lab_results)
bp.add_url_rule('/diagnostics/table-counts', 'get_table_counts', get_table_counts)
bp.add_url_rule('/health-query', 'health_query', health_query, methods=['POST'])
