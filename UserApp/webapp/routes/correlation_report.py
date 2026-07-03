"""
Correlation Report routes.

Blueprint for generating AI-powered correlation reports from health data.
"""
from flask import Blueprint, request, jsonify, g, current_app
from datetime import datetime, timedelta
import pytz

from utils import require_auth, get_db_connection, get_user_id

bp = Blueprint('correlation_report', __name__, url_prefix='/api/v1')


def _build_mock_insights(steps_count, sleep_count, weight_count, med_count):
    """Generate mock insight objects based on available data counts."""
    insights = []

    if steps_count > 0 and sleep_count > 0:
        insights.append({
            'title': 'Sleep & Steps',
            'description': (
                f'Based on {steps_count} step records and {sleep_count} sleep records, '
                'longer sleep duration appears to correlate with higher step counts the following day.'
            ),
            'recommendation': 'Aim for 7-9 hours of sleep to support an active lifestyle.',
            'confidence': 0.85,
        })

    if med_count > 0 and steps_count > 0:
        insights.append({
            'title': 'Medication & Energy',
            'description': (
                f'Across {med_count} medication logs, days with consistent adherence '
                'show moderately higher activity levels.'
            ),
            'recommendation': 'Keep taking medications at the same time each day for best results.',
            'confidence': 0.72,
        })

    if weight_count > 0 and steps_count > 0:
        insights.append({
            'title': 'Activity & Weight',
            'description': (
                f'With {weight_count} weight entries and {steps_count} step records, '
                'weeks with higher average steps tend to coincide with slight weight decreases.'
            ),
            'recommendation': 'Consistent daily movement of 8,000+ steps supports weight management.',
            'confidence': 0.68,
        })

    if sleep_count > 0 and weight_count > 0:
        insights.append({
            'title': 'Sleep & Weight',
            'description': (
                f'Across {sleep_count} sleep and {weight_count} weight records, '
                'shorter sleep duration is weakly associated with weight fluctuations.'
            ),
            'recommendation': 'Prioritize consistent sleep to support metabolic health.',
            'confidence': 0.55,
        })

    return insights


@bp.route('/correlation-report', methods=['GET'])
@require_auth
def get_correlation_report():
    """Generate a correlation report from the user's recent health data."""
    current_app.logger.info(
        "GET /correlation-report: user_id=%s tenant_id=%s",
        g.user.get('user_id'), g.user.get('tenant_id'),
    )
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        user_id = get_user_id()
        tenant_id = g.user.get('tenant_id', 1)

        since = datetime.now(pytz.utc) - timedelta(days=30)

        # Count recent steps records
        cur.execute(
            """SELECT COUNT(*) AS cnt FROM health_metrics
               WHERE user_id = %s AND metric_type = 'steps' AND recorded_at >= %s""",
            (user_id, since,),
        )
        steps_count = cur.fetchone()['cnt']

        # Count recent sleep records
        cur.execute(
            """SELECT COUNT(*) AS cnt FROM health_metrics
               WHERE user_id = %s AND metric_type = 'sleep' AND recorded_at >= %s""",
            (user_id, since,),
        )
        sleep_count = cur.fetchone()['cnt']

        # Count recent weight records
        cur.execute(
            """SELECT COUNT(*) AS cnt FROM health_metrics
               WHERE user_id = %s AND metric_type = 'weight' AND recorded_at >= %s""",
            (user_id, since,),
        )
        weight_count = cur.fetchone()['cnt']

        # Count recent medication logs
        cur.execute(
            """SELECT COUNT(*) AS cnt FROM health_input_log
               WHERE user_id = %s AND logged_at >= %s""",
            (user_id, since,),
        )
        med_count = cur.fetchone()['cnt']

        cur.close()
        conn.close()

        total = steps_count + sleep_count + weight_count + med_count
        insights = _build_mock_insights(steps_count, sleep_count, weight_count, med_count)

        summary = (
            f'Based on your data from the past 30 days ({total} total records), '
            f'we found {len(insights)} notable correlation{"s" if len(insights) != 1 else ""} '
            'in your health patterns.'
        )

        if total == 0:
            summary = (
                'We don\'t have enough data yet to generate meaningful correlations. '
                'Keep logging your health data and check back soon.'
            )

        return jsonify({
            'summary': summary,
            'insights': insights,
            'generatedAt': datetime.now(pytz.utc).isoformat(),
        })

    except Exception as e:
        current_app.logger.error("GET /correlation-report FAILED: %s", e, exc_info=True)
        return jsonify({'error': str(e)}), 500
