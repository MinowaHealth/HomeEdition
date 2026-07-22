"""
Analytics routes.

Blueprint for dashboards, heatmaps, and reports.
"""
from flask import Blueprint, request, jsonify, g, current_app
from db_driver import sql
from datetime import datetime, timedelta
from decimal import Decimal
import pytz
import json

from utils import require_auth, get_db_connection, get_user_db_connection, get_user_timezone, get_user_id, table_has_column, local_to_utc
from unit_conversion import CANONICAL_UNITS, to_display
import db_manager

bp = Blueprint('analytics', __name__, url_prefix='/api/v1')


# ==================== YOUR WEEK DASHBOARD ====================

@bp.route('/your-week')
@require_auth
def get_your_week():
    """Get combined weekly heatmap data for sleep, steps, stress, and heart rate"""
    user_tz = get_user_timezone()
    user_id = get_user_id()
    conn = get_user_db_connection()
    cur = conn.cursor()

    today = datetime.now(user_tz).date()
    seven_days_ago = today - timedelta(days=6)

    try:
        result = {
            'today': today.isoformat(),
            'days': []
        }

        for i in range(7):
            day = today - timedelta(days=6-i)
            result['days'].append({
                'date': day.isoformat(),
                'day_name': day.strftime('%a'),
                'steps': None,
                'sleep_hours': {},
                'stress_hours': {},
                'hr_hours': {}
            })

        # 1. Get steps from daily summary
        cur.execute("""
            SELECT calendar_date, total_steps
            FROM garm_daily_summ
            WHERE user_id = %s AND calendar_date >= %s AND calendar_date <= %s
            ORDER BY calendar_date
        """, (user_id, seven_days_ago, today))

        steps_records = cur.fetchall()
        for rec in steps_records:
            day_str = rec['calendar_date'].isoformat()
            for d in result['days']:
                if d['date'] == day_str:
                    d['steps'] = rec['total_steps']
                    break

        # 2. Get sleep events (aggregated by hour)
        cur.execute("""
            SELECT start_time, end_time, sleep_type
            FROM garm_sleep_events
            WHERE user_id = %s AND start_time >= %s AND start_time < %s
            ORDER BY start_time
        """, (user_id, seven_days_ago, today + timedelta(days=1)))

        sleep_records = cur.fetchall()
        for rec in sleep_records:
            ts = rec['start_time']
            sleep_type = rec['sleep_type']
            end_time = rec['end_time']

            if not ts or not sleep_type or (sleep_type and 'awake' in sleep_type.lower()):
                continue

            if ts.tzinfo is None:
                ts = pytz.UTC.localize(ts)
            local_ts = ts.astimezone(user_tz)

            date_str = local_ts.date().isoformat()
            hour = local_ts.hour

            # Calculate duration from start_time and end_time
            try:
                if end_time and ts:
                    duration_mins = (end_time - rec['start_time']).total_seconds() / 60
                else:
                    duration_mins = 0
            except:
                duration_mins = 0

            for d in result['days']:
                if d['date'] == date_str:
                    if hour not in d['sleep_hours']:
                        d['sleep_hours'][hour] = 0
                    d['sleep_hours'][hour] += duration_mins
                    break

        # 3. Get stress (aggregated by hour)
        cur.execute("""
            SELECT timestamp, garm_stress
            FROM garm_stress
            WHERE user_id = %s AND timestamp >= %s AND timestamp < %s
            ORDER BY timestamp
        """, (user_id, seven_days_ago, today + timedelta(days=1)))

        stress_records = cur.fetchall()
        stress_hourly = {}

        for rec in stress_records:
            ts = rec['timestamp']
            stress = rec['garm_stress']

            if not ts or stress is None or stress < 0:
                continue

            if ts.tzinfo is None:
                ts = pytz.UTC.localize(ts)
            local_ts = ts.astimezone(user_tz)

            date_str = local_ts.date().isoformat()
            hour = local_ts.hour

            if date_str not in stress_hourly:
                stress_hourly[date_str] = {}
            if hour not in stress_hourly[date_str]:
                stress_hourly[date_str][hour] = {'total': 0, 'count': 0}

            stress_hourly[date_str][hour]['total'] += float(stress)
            stress_hourly[date_str][hour]['count'] += 1

        for d in result['days']:
            if d['date'] in stress_hourly:
                for hour, data in stress_hourly[d['date']].items():
                    if data['count'] > 0:
                        d['stress_hours'][hour] = round(data['total'] / data['count'], 1)

        # 4. Get heart rate (aggregated by hour)
        cur.execute("""
            SELECT timestamp, heart_rate
            FROM garm_hr
            WHERE user_id = %s AND timestamp >= %s AND timestamp < %s
            ORDER BY timestamp
        """, (user_id, seven_days_ago, today + timedelta(days=1)))

        hr_records = cur.fetchall()
        hr_hourly = {}

        for rec in hr_records:
            ts = rec['timestamp']
            hr = rec['heart_rate']

            if not ts or hr is None:
                continue

            if ts.tzinfo is None:
                ts = pytz.UTC.localize(ts)
            local_ts = ts.astimezone(user_tz)

            date_str = local_ts.date().isoformat()
            hour = local_ts.hour

            if date_str not in hr_hourly:
                hr_hourly[date_str] = {}
            if hour not in hr_hourly[date_str]:
                hr_hourly[date_str][hour] = {'total': 0, 'count': 0}

            hr_hourly[date_str][hour]['total'] += float(hr)
            hr_hourly[date_str][hour]['count'] += 1

        for d in result['days']:
            if d['date'] in hr_hourly:
                for hour, data in hr_hourly[d['date']].items():
                    if data['count'] > 0:
                        d['hr_hours'][hour] = round(data['total'] / data['count'], 0)

        return jsonify(result)

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/your-week', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        import traceback
        current_app.logger.error(f"your-week error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ==================== SLEEP HEATMAP ====================

@bp.route('/sleep-heatmap')
@require_auth
def get_sleep_heatmap():
    """Get sleep heatmap data for the past 28 days (4 weeks for comparison)"""
    user_tz = get_user_timezone()
    user_id = get_user_id()
    conn = get_user_db_connection()
    cur = conn.cursor()

    today = datetime.now(user_tz).date()
    four_weeks_ago = today - timedelta(days=27)

    try:
        cur.execute("""
            SELECT start_time, end_time, sleep_type
            FROM garm_sleep_events
            WHERE user_id = %s AND start_time >= %s AND start_time <= %s
            ORDER BY start_time
        """, (user_id, four_weeks_ago, today + timedelta(days=1)))

        sleep_events = cur.fetchall()

        daily_data = {}

        for event in sleep_events:
            event_start = event['start_time']
            event_end = event['end_time']
            sleep_type = event.get('sleep_type', '')

            if not event_start or not event_end:
                continue

            if sleep_type and 'awake' in sleep_type.lower():
                continue

            # Calculate duration from start and end times
            duration_minutes = (event_end - event_start).total_seconds() / 60

            if event_start.tzinfo is None:
                event_start = pytz.utc.localize(event_start)
            start_time = event_start.astimezone(user_tz)
            end_time = start_time + timedelta(minutes=duration_minutes)

            current = start_time
            while current < end_time:
                hour_start = current.replace(minute=0, second=0, microsecond=0)
                hour_end = hour_start + timedelta(hours=1)

                overlap_start = max(current, hour_start)
                overlap_end = min(end_time, hour_end)

                if overlap_end > overlap_start:
                    minutes_in_hour = (overlap_end - overlap_start).total_seconds() / 60.0

                    slot_date = hour_start.date()
                    date_str = slot_date.isoformat()
                    hour = hour_start.hour

                    if date_str not in daily_data:
                        daily_data[date_str] = {}
                    if hour not in daily_data[date_str]:
                        daily_data[date_str][hour] = 0
                    daily_data[date_str][hour] += minutes_in_hour

                current = hour_end

        weeks = []
        week_labels = ['This Week', 'Week 2', 'Week 3', 'Week 4']

        for week_num in range(4):
            week_data = []
            for i in range(7):
                day_offset = (week_num * 7) + (6 - i)
                day = today - timedelta(days=day_offset)
                date_str = day.isoformat()

                week_data.append({
                    'date': date_str,
                    'day_name': day.strftime('%a'),
                    'hours': daily_data.get(date_str, {})
                })

            weeks.append({
                'label': week_labels[week_num],
                'data': week_data
            })

        response_data = {
            'weeks': weeks,
            'today': today.isoformat(),
            'debug': {
                'daily_data_keys': list(daily_data.keys())[:10],
                'server_now': datetime.now(pytz.utc).isoformat(),
                'local_now': datetime.now(user_tz).isoformat()
            }
        }

        try:
            current_app.logger.info(
                "sleep_heatmap response user=%s events=%s days=%s weeks_with_hours=%s",
                g.user.get('username', 'unknown'),
                len(sleep_events),
                len(daily_data.keys()),
                sum(1 for week in weeks for day in week['data'] if day['hours'])
            )
        except Exception as log_err:
            current_app.logger.warning("sleep_heatmap logging failed: %s", log_err)

        return jsonify(response_data)

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/sleep-heatmap', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ==================== STRESS HEATMAP ====================

@bp.route('/stress-heatmap')
@require_auth
def get_stress_heatmap():
    """Get stress heatmap data - average stress per hour for past 28 days"""
    user_tz = get_user_timezone()
    user_id = get_user_id()
    conn = get_user_db_connection()
    cur = conn.cursor()

    today = datetime.now(user_tz).date()
    four_weeks_ago = today - timedelta(days=27)

    try:
        cur.execute("""
            SELECT timestamp, garm_stress
            FROM garm_stress
            WHERE user_id = %s AND timestamp >= %s AND timestamp <= %s
            ORDER BY timestamp
        """, (user_id, four_weeks_ago, today + timedelta(days=1)))

        stress_records = cur.fetchall()

        hourly_data = {}

        for record in stress_records:
            ts = record['timestamp']
            stress = record['garm_stress']

            if not ts or stress is None:
                continue

            if isinstance(stress, str):
                try:
                    stress = float(stress)
                except ValueError:
                    continue
            elif isinstance(stress, Decimal):
                stress = float(stress)

            if ts.tzinfo is None:
                ts = pytz.UTC.localize(ts)
            local_ts = ts.astimezone(user_tz)

            date_str = local_ts.date().isoformat()
            hour = local_ts.hour

            if date_str not in hourly_data:
                hourly_data[date_str] = {}
            if hour not in hourly_data[date_str]:
                hourly_data[date_str][hour] = {'total': 0, 'count': 0}

            hourly_data[date_str][hour]['total'] += stress
            hourly_data[date_str][hour]['count'] += 1

        daily_data = {}
        for date_str, hours in hourly_data.items():
            daily_data[date_str] = {}
            for hour, data in hours.items():
                if data['count'] > 0:
                    daily_data[date_str][hour] = round(data['total'] / data['count'], 1)

        weeks = []
        week_labels = ['This Week', 'Week 2', 'Week 3', 'Week 4']

        for week_num in range(4):
            week_data = []
            for i in range(7):
                day_offset = (week_num * 7) + (6 - i)
                day = today - timedelta(days=day_offset)
                date_str = day.isoformat()

                week_data.append({
                    'date': date_str,
                    'day_name': day.strftime('%a'),
                    'hours': daily_data.get(date_str, {})
                })

            weeks.append({
                'label': week_labels[week_num],
                'data': week_data
            })

        return jsonify({
            'weeks': weeks,
            'today': today.isoformat()
        })

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/activity-by-week', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ==================== LAB RESULTS ====================

@bp.route('/lab-results')
@require_auth
def get_lab_results():
    """Get latest lab results for each test type"""
    conn = get_db_connection()
    cur = conn.cursor()
    user_id = get_user_id()

    try:
        # A single test may have several observation rows across imports; some
        # rows carry a name/date, siblings in the same LOINC group do not
        # (raw_fhir was dropped on import — minowa-mcp-bug-report.md Bug 4).
        # rn=1 gives the latest reading's *value*; name and dates are pulled
        # from the whole group so a NULL on the latest row borrows from a
        # sibling. received_date (import-received, from the parent clinical
        # record) is a labeled fallback when no clinical draw date survived —
        # it is NOT relabeled as the collection date.
        cur.execute("""
            WITH obs AS (
                SELECT
                    lo.id, lo.loinc_code, lo.display_name, lo.effective_date,
                    lo.value_quantity, lo.value_unit, lo.value_string,
                    lo.reference_range, lo.interpretation,
                    cr.received_date,
                    COALESCE(lo.loinc_code, lo.display_name) AS grp
                FROM hkit_lab_observations lo
                LEFT JOIN hkit_clinical_records cr
                    ON cr.tenant_id = lo.tenant_id
                   AND cr.id = lo.clinical_record_id
                WHERE lo.user_id = %s AND lo.value_quantity IS NOT NULL
            ),
            ranked AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY grp
                        -- NULLS LAST: DESC alone sorts NULLs first, so a
                        -- NULL-dated row would shadow a dated sibling.
                        ORDER BY effective_date DESC NULLS LAST, id DESC
                    ) AS rn,
                    MAX(display_name) FILTER (
                        WHERE display_name IS NOT NULL
                          AND display_name <> COALESCE(loinc_code, '')
                    ) OVER (PARTITION BY grp) AS grp_name,
                    MAX(effective_date) OVER (PARTITION BY grp) AS grp_date,
                    MAX(received_date) OVER (PARTITION BY grp) AS grp_received
                FROM obs
            )
            SELECT id, loinc_code,
                   COALESCE(grp_name, display_name, loinc_code) AS display_name,
                   COALESCE(grp_date, effective_date) AS effective_date,
                   grp_received AS received_date,
                   value_quantity, value_unit, value_string,
                   reference_range, interpretation
            FROM ranked WHERE rn = 1
            ORDER BY COALESCE(grp_date, grp_received) DESC NULLS LAST
        """, (user_id,))

        results = cur.fetchall()

        formatted = []
        for r in results:
            formatted.append({
                'id': r['id'],
                'name': r['display_name'] or r['loinc_code'] or 'Unknown Test',
                'loinc_code': r['loinc_code'],
                'value': float(r['value_quantity']) if r['value_quantity'] else None,
                'unit': r['value_unit'] or '',
                'reference_range': r['reference_range'],
                'interpretation': r['interpretation'],
                'date': r['effective_date'].isoformat() if r['effective_date'] else None,
                # Labeled fallback — the date the record was imported, shown
                # only so a dateless lab still anchors in time. Not the draw date.
                'received_date': r['received_date'].isoformat() if r['received_date'] else None,
            })

        return jsonify({
            'results': formatted,
            'count': len(formatted)
        })

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/observations', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT', 'results': []}), 503
        return jsonify({'error': str(e), 'results': []}), 500
    finally:
        cur.close()
        conn.close()


# ==================== DIAGNOSTICS ====================

@bp.route('/diagnostics/table-counts')
@require_auth
def get_table_counts():
    """Get record counts for all tables in the database"""
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        tables = cur.fetchall()

        result = []
        for table in tables:
            table_name = table['table_name']
            # Savepoint per table — without this, first failing COUNT poisons the
            # whole TX and every subsequent query returns "current transaction is
            # aborted". Savepoint lets us rollback just the failing statement.
            cur.execute("SAVEPOINT count_table")
            try:
                cur.execute(sql.SQL('SELECT COUNT(*) as count FROM {}').format(
                    sql.Identifier(table_name)
                ))
                count = cur.fetchone()['count']
                cur.execute("RELEASE SAVEPOINT count_table")
                result.append({
                    'table': table_name,
                    'count': count
                })
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT count_table")
                result.append({
                    'table': table_name,
                    'count': -1,
                    'error': str(e)
                })

        return jsonify({
            'tables': result,
            'total_tables': len(result),
            'database': g.user_database if hasattr(g, 'user_database') else 'unknown'
        })

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/diagnostics/table-counts', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ==================== HEALTH QUERY ====================

# Metric types supported by health_metrics table.
HEALTH_METRIC_TYPES = {
    'heart_rate',
    'resting_heart_rate',
    'sleep',
    'nutrition',
    'active_energy_burned',
    'basal_energy_burned',
    'distance_walking_running',
    'workout',
    'workout_route',
    'floors_climbed',
    'wheelchair_pushes',
    'hydration',
    'heart_rate_variability',
    'respiratory_rate',
    'weight',
    'body_temperature',
    'basal_body_temperature',
    'temperature',
    'height',
    'body_fat_percentage',
    'lean_body_mass',
    'blood_glucose',
    'oxygen_saturation',
    'vo2_max',
    'allergy_record',
    'condition_record',
    'immunization_record',
    'lab_result_record',
    'medication_record',
    'procedure_record',
    'vital_sign_record',
    'blood_oxygen',
}
HEALTH_KIND_ALIASES = {
    # Canonicalize query kinds used by synced data.
    'resting_heart_rate': 'heart_rate',
    'body_temperature': 'temperature',
    'basal_body_temperature': 'temperature',
    'oxygen_saturation': 'blood_oxygen',
}


def parse_food_notes(notes):
    """Parse optional food log notes JSON safely."""
    if not notes:
        return {}
    try:
        if isinstance(notes, str):
            parsed = json.loads(notes)
            return parsed if isinstance(parsed, dict) else {}
        if isinstance(notes, dict):
            return notes
    except Exception:
        return {}
    return {}



@bp.route('/health-query', methods=['POST'])
@require_auth
def health_query():
    """
    Query health data by metric type and timestamp range.

    POST body:
        kind: str - Type of data (steps, heart_rate, sleep, weight, temperature,
                    blood_oxygen, respiratory_rate, blood_pressure, food)
        start: str - Start datetime (ISO format)
        end: str - End datetime (ISO format)

    Returns array of records matching the query.
    """
    payload = request.get_json(silent=True) or {}
    kind = (payload.get('kind') or '').strip()
    start = payload.get('start')
    end = payload.get('end')

    if not kind or not start or not end:
        return jsonify({'error': 'kind, start, and end are required'}), 400

    try:
        start_utc = local_to_utc(start)
        end_utc = local_to_utc(end)
    except Exception as exc:
        return jsonify({'error': f'invalid date range: {exc}'}), 400

    user_id = get_user_id()
    conn = get_user_db_connection()
    cur = conn.cursor()
    results = []

    try:
        query_kind = HEALTH_KIND_ALIASES.get(kind, kind)

        # Steps: use pre-aggregated daily totals from garm_daily_summ
        if query_kind == 'steps':
            cur.execute(
                """
                SELECT calendar_date, total_steps, daily_step_goal
                FROM garm_daily_summ
                WHERE user_id = %s AND calendar_date >= %s::date AND calendar_date <= %s::date
                ORDER BY calendar_date ASC
                """,
                (user_id, start_utc, end_utc),
            )
            rows = cur.fetchall()
            for row in rows:
                day_str = row['calendar_date'].isoformat()
                results.append({
                    'kind': 'steps',
                    'start_time': day_str,
                    'end_time': day_str,
                    'value': row['total_steps'],
                    'unit': 'steps',
                    'source': 'garmin',
                    'goal': row.get('daily_step_goal'),
                })
            return jsonify(results)

        # Medication: use health_input_log with JOINs for names and dosages
        if query_kind == 'medication':
            cur.execute(
                """
                SELECT hil.logged_at, hil.dosage_taken, hil.free_text,
                       hi.name AS input_name, hi.default_unit AS default_unit,
                       hi.input_type, s.name AS stack_name
                FROM health_input_log hil
                LEFT JOIN health_inputs hi
                    ON hi.tenant_id = hil.tenant_id AND hi.id = hil.input_id
                LEFT JOIN stacks s
                    ON s.tenant_id = hil.tenant_id AND s.id = hil.stack_id
                WHERE hil.user_id = %s
                  AND hil.logged_at >= %s
                  AND hil.logged_at <= %s
                ORDER BY hil.logged_at ASC
                """,
                (user_id, start_utc, end_utc),
            )
            rows = cur.fetchall()
            for row in rows:
                timestamp = row['logged_at'].isoformat() if row.get('logged_at') else None
                results.append({
                    'kind': 'medication',
                    'start_time': timestamp,
                    'end_time': timestamp,
                    'input_name': row.get('input_name'),
                    'dosage_taken': row.get('dosage_taken'),
                    'unit': row.get('default_unit'),
                    'input_type': row.get('input_type'),
                    'stack_name': row.get('stack_name'),
                    'free_text': row.get('free_text'),
                })
            return jsonify(results)

        # Query health_metrics for standard metric types
        if query_kind in HEALTH_METRIC_TYPES:
            cur.execute(
                """
                SELECT recorded_at, metric_type, value, unit, source, notes
                FROM health_metrics
                WHERE user_id = %s
                  AND metric_type = %s
                  AND recorded_at >= %s
                  AND recorded_at <= %s
                ORDER BY recorded_at ASC
                """,
                (user_id, query_kind, start_utc, end_utc),
            )
            rows = cur.fetchall()
            # For nutrition rows the projector packs the per-macro breakdown
            # into the `notes` JSON column (food_log → health_metrics path,
            # see nutrition_projection.py). Surface those keys as structured
            # fields so the client doesn't have to parse the JSON string.
            from nutrition_projection import parse_nutrition_notes
            for row in rows:
                timestamp = row['recorded_at'].isoformat() if row.get('recorded_at') else None
                entry = {
                    'kind': kind if kind in HEALTH_KIND_ALIASES else (row.get('metric_type') or query_kind),
                    'start_time': timestamp,
                    'end_time': timestamp,
                    'value': float(row['value']) if row.get('value') is not None else None,
                    'unit': row.get('unit'),
                    'source': row.get('source'),
                }
                if query_kind == 'nutrition':
                    macros = parse_nutrition_notes(row.get('notes'))
                    # Promote the macro keys into the response. Numeric values
                    # are already floats from json.loads; dicts/strings pass
                    # through (food_name comes through as-is).
                    for key, val in macros.items():
                        entry[key] = val
                results.append(entry)
            return jsonify(results)

        # Query blood pressure readings
        if kind == 'blood_pressure':
            cur.execute(
                """
                SELECT measured_at, systolic, diastolic, pulse
                FROM health_blood_pressure_readings
                WHERE user_id = %s
                  AND measured_at >= %s
                  AND measured_at <= %s
                ORDER BY measured_at ASC
                """,
                (user_id, start_utc, end_utc),
            )
            rows = cur.fetchall()
            for row in rows:
                timestamp = row['measured_at'].isoformat() if row.get('measured_at') else None
                results.append({
                    'kind': 'blood_pressure',
                    'start_time': timestamp,
                    'end_time': timestamp,
                    'systolic': row.get('systolic'),
                    'diastolic': row.get('diastolic'),
                    'value': row.get('systolic'),
                    'heart_rate': row.get('pulse'),
                })
            return jsonify(results)

        # Query food log
        if kind == 'food':
            food_time_col = 'logged_at' if table_has_column(conn, 'health_food_logv2', 'logged_at') else 'timestamp'
            food_qty_col = 'servings' if table_has_column(conn, 'health_food_logv2', 'servings') else 'quantity'
            has_food_unit = table_has_column(conn, 'health_food_logv2', 'unit')
            has_food_free_text = table_has_column(conn, 'health_food_logv2', 'free_text')
            has_food_photo = table_has_column(conn, 'health_food_logv2', 'photo_url')
            has_food_promoted_at = table_has_column(conn, 'health_food_logv2', 'promoted_at')
            has_food_is_deleted = table_has_column(conn, 'health_food_logv2', 'is_deleted')
            join_food_on_tenant = table_has_column(conn, 'health_food_logv2', 'tenant_id') and table_has_column(conn, 'health_food_itemsv2', 'tenant_id')
            unit_select = "fl.unit" if has_food_unit else "NULL::text as unit"
            free_text_select = "fl.free_text" if has_food_free_text else "NULL::text as free_text"
            photo_select = "fl.photo_url" if has_food_photo else "NULL::text as photo_url"
            promoted_select = "fl.promoted_at" if has_food_promoted_at else "NULL::timestamp with time zone as promoted_at"
            deleted_filter = "AND fl.is_deleted = 0" if has_food_is_deleted else ""
            food_join = "fl.food_item_id = fi.id AND fl.tenant_id = fi.tenant_id" if join_food_on_tenant else "fl.food_item_id = fi.id"
            food_query = sql.SQL("""
                SELECT fl.{time_col} AS logged_at, fl.{qty_col} AS servings, {unit_select}, fl.meal_type, fl.notes, fl.food_item_id,
                       {free_text_select}, {photo_select}, {promoted_select},
                       fi.name AS food_name, fi.calories,
                       fi.protein_g, fi.carbs_g, fi.fat_g, fi.fiber_g, fi.sugar_g,
                       fi.sodium_mg, fi.potassium_mg
                FROM health_food_logv2 fl
                LEFT JOIN health_food_itemsv2 fi ON {food_join}
                WHERE fl.{time_col} >= %s
                  AND fl.{time_col} <= %s
                  {deleted_filter}
                ORDER BY logged_at ASC
            """).format(
                time_col=sql.Identifier(food_time_col),
                qty_col=sql.Identifier(food_qty_col),
                unit_select=sql.SQL(unit_select),
                free_text_select=sql.SQL(free_text_select),
                photo_select=sql.SQL(photo_select),
                promoted_select=sql.SQL(promoted_select),
                food_join=sql.SQL(food_join),
                deleted_filter=sql.SQL(deleted_filter),
            )
            cur.execute(food_query, (start_utc, end_utc))
            rows = cur.fetchall()
            for row in rows:
                timestamp = row['logged_at'].isoformat() if row.get('logged_at') else None
                notes_obj = parse_food_notes(row.get('notes'))
                carbs = notes_obj.get('carbs_g')
                fat = notes_obj.get('fat_g')
                # Backward compatibility for older note payloads.
                if carbs is None:
                    carbs = notes_obj.get('carbs_total_g')
                if fat is None:
                    fat = notes_obj.get('fat_total_g')
                food_name = row.get('food_name') or row.get('free_text') or 'Unknown food'
                is_freeform = row.get('free_text') is not None and not row.get('promoted_at')
                results.append({
                    'kind': 'food',
                    'start_time': timestamp,
                    'end_time': timestamp,
                    'value': float(row['servings']) if row.get('servings') is not None else None,
                    'unit': row.get('unit') or 'servings',
                    'servings': float(row['servings']) if row.get('servings') is not None else None,
                    'food_item_id': str(row.get('food_item_id')) if row.get('food_item_id') else None,
                    'food_name': food_name,
                    'meal_type': row.get('meal_type'),
                    'calories': notes_obj.get('calories') if notes_obj.get('calories') is not None else (float(row['calories']) if row.get('calories') is not None else None),
                    'protein_g': notes_obj.get('protein_g') if notes_obj.get('protein_g') is not None else (float(row['protein_g']) if row.get('protein_g') is not None else None),
                    'carbs_g': carbs if carbs is not None else (float(row['carbs_g']) if row.get('carbs_g') is not None else None),
                    'fat_g': fat if fat is not None else (float(row['fat_g']) if row.get('fat_g') is not None else None),
                    'fiber_g': notes_obj.get('fiber_g') if notes_obj.get('fiber_g') is not None else (float(row['fiber_g']) if row.get('fiber_g') is not None else None),
                    'sugar_g': notes_obj.get('sugar_g') if notes_obj.get('sugar_g') is not None else (float(row['sugar_g']) if row.get('sugar_g') is not None else None),
                    'sodium_mg': notes_obj.get('sodium_mg') if notes_obj.get('sodium_mg') is not None else (float(row['sodium_mg']) if row.get('sodium_mg') is not None else None),
                    'potassium_mg': notes_obj.get('potassium_mg') if notes_obj.get('potassium_mg') is not None else (float(row['potassium_mg']) if row.get('potassium_mg') is not None else None),
                    'meal': notes_obj.get('meal'),
                    'free_text': row.get('free_text'),
                    'photo_url': row.get('photo_url'),
                    'is_freeform': is_freeform,
                })
            return jsonify(results)

        return jsonify({
            'error': 'unsupported kind',
            'supported': sorted(list(HEALTH_METRIC_TYPES | set(HEALTH_KIND_ALIASES.keys())) + ['blood_pressure', 'food']),
        }), 400

    finally:
        cur.close()
        conn.close()


# ==================== NUTRITION TODAY ====================

# Macro keys aggregated for the today summary. Keep aligned with
# nutrition_projection._NUTRIENT_COLS — every nutrient the projector writes
# should be summable here, otherwise the totals understate what the user logged.
_NUTRITION_MACRO_KEYS = (
    'calories',
    'protein_g',
    'carbs_g',
    'fat_g',
    'fiber_g',
    'sugar_g',
    'sodium_mg',
)


@bp.route('/nutrition/today', methods=['GET'])
@require_auth
def get_nutrition_today():
    """Aggregated nutrition totals for the user's local "today".

    Reads the projected nutrition rows in health_metrics (written by the
    food_log → metrics projector — see nutrition_projection.py) and rolls them
    up into a single-day summary plus a per-entry breakdown the client can
    show without further parsing.

    The day boundary is the user's local calendar day, not server UTC, so
    a meal logged at 11pm in Pacific time appears in *that day's* total even
    though the underlying recorded_at is past midnight UTC.

    Response:
        {
          "date": "YYYY-MM-DD",          # the user-tz date treated as "today"
          "timezone": "America/Los_Angeles",
          "totals": {"calories": X, "protein_g": Y, ...},
          "entries": [
              {"recorded_at": "...", "food_name": "...", "servings": N,
               "calories": ..., "protein_g": ..., ...},
              ...
          ],
          "entry_count": N
        }
    """
    user_tz = get_user_timezone()
    today_local = datetime.now(user_tz).date()

    # Build local-midnight to next-local-midnight, then convert to UTC.
    start_local = user_tz.localize(datetime.combine(today_local, datetime.min.time()))
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(pytz.utc)
    end_utc = end_local.astimezone(pytz.utc)

    user_id = get_user_id()
    conn = get_user_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT recorded_at, value, unit, source, notes, source_log_id
            FROM health_metrics
            WHERE user_id = %s
              AND metric_type = 'nutrition'
              AND recorded_at >= %s
              AND recorded_at < %s
            ORDER BY recorded_at ASC
            """,
            (user_id, start_utc, end_utc),
        )
        rows = cur.fetchall()

        from nutrition_projection import parse_nutrition_notes

        totals: dict[str, float] = {key: 0.0 for key in _NUTRITION_MACRO_KEYS}
        entries = []
        for row in rows:
            macros = parse_nutrition_notes(row.get('notes'))
            entry = {
                'recorded_at': row['recorded_at'].isoformat() if row.get('recorded_at') else None,
                'value': float(row['value']) if row.get('value') is not None else None,
                'unit': row.get('unit'),
                'source': row.get('source'),
                'source_log_id': str(row['source_log_id']) if row.get('source_log_id') else None,
                'food_name': macros.get('food_name'),
                'servings': macros.get('servings'),
            }
            for key in _NUTRITION_MACRO_KEYS:
                val = macros.get(key)
                if isinstance(val, (int, float)):
                    entry[key] = float(val)
                    totals[key] += float(val)
                else:
                    entry[key] = None
            entries.append(entry)

        return jsonify({
            'date': today_local.isoformat(),
            'timezone': str(user_tz),
            'totals': totals,
            'entries': entries,
            'entry_count': len(entries),
        })
    finally:
        cur.close()
        conn.close()


# ==================== DASHBOARD ====================

# Vitals metric types we roll up in the dashboard. Keep this list small — it
# represents the "at-a-glance" summary, not every metric_type the schema can
# hold. If a metric doesn't fit on a single dashboard tile, leave it out.
_DASHBOARD_METRIC_TYPES = (
    'weight',
    'body_temperature',
    'heart_rate',
    'resting_heart_rate',
    'oxygen_saturation',
    'respiratory_rate',
    'blood_glucose',
)

_MAX_DASHBOARD_DAYS = 90


@bp.route('/dashboard', methods=['GET'])
@require_auth
def get_dashboard():
    """Compact multi-section summary for the last N days.

    Query params:
        days (int, 1..90, default 30) — window size ending today (UTC).

    Response:
        {
          "window": {"from": "...", "to": "...", "days": N},
          "vitals": {
            "blood_pressure": {"count": N, "avg_systolic": X, "avg_diastolic": Y,
                               "min_systolic": ..., "max_systolic": ...,
                               "latest": {"measured_at": "...", "systolic": ..., "diastolic": ...}},
            "metrics": {
              "weight":        {"count": N, "avg": X, "min": ..., "max": ...,
                                "latest": {"recorded_at": "...", "value": ..., "unit": "..."}},
              ...
            }
          },
          "wearable": {
            "days_available": N,
            "total_steps": X, "avg_steps": Y,
            "avg_resting_hr": Z, "avg_stress": ..., "avg_spo2": ...
          },
          "adherence": {
            "scheduled_input_count": N,
            "total_scheduled_doses": X, "total_logged_doses": Y,
            "pct_overall": 0.0..100.0
          },
          "recent_events": [
            {"type": "blood_pressure" | "metric" | "input_log" | "observation",
             "date": "ISO8601", "summary": "..."},
            ...
          ]
        }

    This endpoint is READ-ONLY aggregate — it does not return raw rows, so
    the payload stays bounded even for 90-day windows on heavy users.
    """
    try:
        days = int(request.args.get('days', 30))
    except (TypeError, ValueError):
        return jsonify({'error': 'days must be an integer'}), 400
    if days < 1 or days > _MAX_DASHBOARD_DAYS:
        return jsonify({'error': f'days must be between 1 and {_MAX_DASHBOARD_DAYS}'}), 400

    end_date = datetime.now(pytz.utc).date()
    start_date = end_date - timedelta(days=days - 1)
    start_ts = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=pytz.utc)
    end_ts = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=pytz.utc)

    user_id = get_user_id()
    conn = get_user_db_connection()
    cur = conn.cursor()

    try:
        # -------- blood pressure rollup --------
        cur.execute("""
            SELECT COUNT(*)             AS count,
                   AVG(systolic)::float AS avg_systolic,
                   AVG(diastolic)::float AS avg_diastolic,
                   MIN(systolic)        AS min_systolic,
                   MAX(systolic)        AS max_systolic,
                   MIN(diastolic)       AS min_diastolic,
                   MAX(diastolic)       AS max_diastolic
            FROM health_blood_pressure_readings
            WHERE user_id = %s AND measured_at >= %s AND measured_at < %s
        """, (user_id, start_ts, end_ts))
        bp_row = cur.fetchone() or {}

        cur.execute("""
            SELECT measured_at, systolic, diastolic, pulse
            FROM health_blood_pressure_readings
            WHERE user_id = %s AND measured_at < %s
            ORDER BY measured_at DESC
            LIMIT 1
        """, (user_id, end_ts,))
        bp_latest_row = cur.fetchone()

        bp_summary = {
            'count': int(bp_row.get('count') or 0),
            'avg_systolic': round(bp_row['avg_systolic'], 1) if bp_row.get('avg_systolic') is not None else None,
            'avg_diastolic': round(bp_row['avg_diastolic'], 1) if bp_row.get('avg_diastolic') is not None else None,
            'min_systolic': bp_row.get('min_systolic'),
            'max_systolic': bp_row.get('max_systolic'),
            'min_diastolic': bp_row.get('min_diastolic'),
            'max_diastolic': bp_row.get('max_diastolic'),
            'latest': None,
        }
        if bp_latest_row:
            bp_summary['latest'] = {
                'measured_at': bp_latest_row['measured_at'].isoformat() if bp_latest_row.get('measured_at') else None,
                'systolic': bp_latest_row.get('systolic'),
                'diastolic': bp_latest_row.get('diastolic'),
                'pulse': bp_latest_row.get('pulse'),
            }

        # -------- health_metrics rollup (one row per metric_type) --------
        # Rows keep the unit they were entered in, so canonicalize (lbs / F /
        # mg/dL) before aggregating — otherwise mixed-unit history averages
        # into nonsense. CASE mirrors unit_conversion.CANONICAL_UNITS.
        # Manual entry stores 'temperature', HealthKit stores 'body_temperature'
        # — same vital, so fold both into the 'body_temperature' bucket the
        # response contract already exposes.
        cur.execute("""
            SELECT CASE WHEN metric_type = 'temperature'
                        THEN 'body_temperature' ELSE metric_type END AS metric_type,
                   COUNT(*)       AS count,
                   AVG(cv)::float AS avg_value,
                   MIN(cv)::float AS min_value,
                   MAX(cv)::float AS max_value
            FROM (
                SELECT metric_type,
                       CASE
                           WHEN metric_type = 'weight' AND unit = 'kg'
                               THEN value / 0.45359237
                           WHEN metric_type IN ('temperature', 'body_temperature') AND unit = 'C'
                               THEN value * 9.0 / 5.0 + 32
                           WHEN metric_type = 'blood_glucose' AND unit = 'mmol/L'
                               THEN value * 18.0182
                           ELSE value
                       END AS cv
                FROM health_metrics
                WHERE user_id = %s
                  AND metric_type = ANY(%s::text[])
                  AND recorded_at >= %s AND recorded_at < %s
                  AND value IS NOT NULL
            ) canonical
            GROUP BY 1
        """, (user_id, list(_DASHBOARD_METRIC_TYPES) + ['temperature'], start_ts, end_ts))
        metric_rollup = {r['metric_type']: r for r in cur.fetchall()}

        unit_system = g.user.get('unit_system', 'imperial')
        metrics_summary: dict = {}
        for mtype in _DASHBOARD_METRIC_TYPES:
            agg = metric_rollup.get(mtype)
            if not agg:
                metrics_summary[mtype] = {'count': 0}
                continue
            # Latest row per type for display ('body_temperature' spans both
            # stored spellings, matching the rollup above)
            spellings = ['temperature', 'body_temperature'] if mtype == 'body_temperature' else [mtype]
            cur.execute("""
                SELECT recorded_at, value, unit
                FROM health_metrics
                WHERE user_id = %s
                  AND metric_type = ANY(%s::text[])
                  AND recorded_at < %s
                ORDER BY recorded_at DESC
                LIMIT 1
            """, (user_id, spellings, end_ts))
            latest = cur.fetchone()
            latest_summary = None
            if latest:
                latest_val = float(latest['value']) if latest.get('value') is not None else None
                latest_unit = latest.get('unit')
                if latest_val is not None and latest_unit:
                    latest_val, latest_unit = to_display(mtype, latest_val, latest_unit, unit_system)
                latest_summary = {
                    'recorded_at': latest['recorded_at'].isoformat() if latest.get('recorded_at') else None,
                    'value': latest_val,
                    'unit': latest_unit,
                }
            entry = {
                'count': int(agg['count']),
                'avg': round(agg['avg_value'], 2) if agg['avg_value'] is not None else None,
                'min': round(agg['min_value'], 2) if agg['min_value'] is not None else None,
                'max': round(agg['max_value'], 2) if agg['max_value'] is not None else None,
                'latest': latest_summary,
            }
            # Aggregates are canonical; present them in the user's display unit.
            canonical = CANONICAL_UNITS.get(mtype)
            if canonical:
                for k in ('avg', 'min', 'max'):
                    if entry[k] is not None:
                        entry[k], entry['unit'] = to_display(mtype, entry[k], canonical, unit_system)
            metrics_summary[mtype] = entry

        # -------- Garmin daily summary rollup --------
        cur.execute("""
            SELECT COUNT(*)                  AS days_available,
                   SUM(total_steps)          AS total_steps,
                   AVG(total_steps)::float   AS avg_steps,
                   AVG(resting_heart_rate)::float AS avg_resting_hr,
                   AVG(avg_stress_level)::float   AS avg_stress,
                   AVG(spo2_avg)::float      AS avg_spo2,
                   SUM(sleeping_time_secs)   AS total_sleep_secs
            FROM garm_daily_summ
            WHERE user_id = %s AND calendar_date >= %s AND calendar_date <= %s
        """, (user_id, start_date, end_date))
        wr = cur.fetchone() or {}
        wearable_summary = {
            'days_available': int(wr.get('days_available') or 0),
            'total_steps': int(wr['total_steps']) if wr.get('total_steps') is not None else None,
            'avg_steps': round(wr['avg_steps'], 0) if wr.get('avg_steps') is not None else None,
            'avg_resting_hr': round(wr['avg_resting_hr'], 1) if wr.get('avg_resting_hr') is not None else None,
            'avg_stress': round(wr['avg_stress'], 1) if wr.get('avg_stress') is not None else None,
            'avg_spo2': round(wr['avg_spo2'], 1) if wr.get('avg_spo2') is not None else None,
            'total_sleep_hours': round(wr['total_sleep_secs'] / 3600.0, 1) if wr.get('total_sleep_secs') is not None else None,
        }

        # -------- adherence rollup (scheduled inputs only) --------
        cur.execute("""
            SELECT hi.id, hi.doses_per_day
            FROM health_inputs hi
            WHERE hi.user_id = %s
              AND hi.is_active = true
              AND hi.doses_per_day IS NOT NULL
              AND hi.doses_per_day > 0
        """, (user_id,))
        scheduled = cur.fetchall()

        adherence_summary: dict = {
            'scheduled_input_count': len(scheduled),
            'total_scheduled_doses': 0,
            'total_logged_doses': 0,
            'pct_overall': None,
        }
        if scheduled:
            scheduled_ids = [r['id'] for r in scheduled]
            total_scheduled = sum(int(r['doses_per_day']) * days for r in scheduled)

            cur.execute("""
                SELECT COUNT(*) AS log_count
                FROM health_input_log
                WHERE user_id = %s
                  AND input_id = ANY(%s::uuid[])
                  AND logged_at >= %s
                  AND logged_at < %s
            """, (user_id, scheduled_ids, start_ts, end_ts))
            logged = int((cur.fetchone() or {}).get('log_count') or 0)

            adherence_summary['total_scheduled_doses'] = total_scheduled
            adherence_summary['total_logged_doses'] = logged
            if total_scheduled > 0:
                adherence_summary['pct_overall'] = round(
                    min(100.0, (logged / total_scheduled) * 100.0), 1
                )

        # -------- recent events (union of BP + metrics + logs + observations) --------
        # Each branch is limited individually; we take top 20 overall by date.
        cur.execute("""
            SELECT measured_at AS event_at,
                   'blood_pressure' AS kind,
                   systolic, diastolic
            FROM health_blood_pressure_readings
            WHERE user_id = %s AND measured_at >= %s AND measured_at < %s
            ORDER BY measured_at DESC
            LIMIT 10
        """, (user_id, start_ts, end_ts))
        bp_events = [
            {
                'type': 'blood_pressure',
                'date': r['event_at'].isoformat() if r.get('event_at') else None,
                'summary': f"BP {r['systolic']}/{r['diastolic']}",
            }
            for r in cur.fetchall()
        ]

        cur.execute("""
            SELECT recorded_at, metric_type, value, unit
            FROM health_metrics
            WHERE user_id = %s
              AND recorded_at >= %s AND recorded_at < %s
              AND metric_type = ANY(%s::text[])
              AND value IS NOT NULL
            ORDER BY recorded_at DESC
            LIMIT 10
        """, (user_id, start_ts, end_ts, list(_DASHBOARD_METRIC_TYPES)))
        metric_events = [
            {
                'type': 'metric',
                'date': r['recorded_at'].isoformat() if r.get('recorded_at') else None,
                'summary': f"{r['metric_type']} = {float(r['value']):g}{(' ' + r['unit']) if r.get('unit') else ''}",
            }
            for r in cur.fetchall()
        ]

        cur.execute("""
            SELECT hil.logged_at, hi.name, hi.input_type, hil.dosage_taken, hi.default_unit AS unit
            FROM health_input_log hil
            JOIN health_inputs hi
              ON hi.tenant_id = hil.tenant_id AND hi.id = hil.input_id
            WHERE hil.user_id = %s
              AND hil.logged_at >= %s AND hil.logged_at < %s
              AND COALESCE(hil.skipped, false) = false
            ORDER BY hil.logged_at DESC
            LIMIT 10
        """, (user_id, start_ts, end_ts))
        log_events = [
            {
                'type': 'input_log',
                'date': r['logged_at'].isoformat() if r.get('logged_at') else None,
                'summary': (
                    f"Logged {r['name']}"
                    + (f" ({r['dosage_taken']}{' ' + r['unit'] if r.get('unit') else ''})"
                       if r.get('dosage_taken') is not None else '')
                ),
            }
            for r in cur.fetchall()
        ]

        cur.execute("""
            SELECT observed_at, category, content
            FROM health_observations
            WHERE user_id = %s AND observed_at >= %s AND observed_at < %s
            ORDER BY observed_at DESC
            LIMIT 10
        """, (user_id, start_ts, end_ts))
        obs_events = [
            {
                'type': 'observation',
                'date': r['observed_at'].isoformat() if r.get('observed_at') else None,
                'summary': (
                    f"{r['category'] or 'note'}: "
                    + ((r['content'] or '')[:80] + ('…' if r.get('content') and len(r['content']) > 80 else ''))
                ),
            }
            for r in cur.fetchall()
        ]

        recent = bp_events + metric_events + log_events + obs_events
        recent.sort(key=lambda e: e.get('date') or '', reverse=True)
        recent = recent[:20]

        return jsonify({
            'window': {
                'from': start_date.isoformat(),
                'to': end_date.isoformat(),
                'days': days,
            },
            'vitals': {
                'blood_pressure': bp_summary,
                'metrics': metrics_summary,
            },
            'wearable': wearable_summary,
            'adherence': adherence_summary,
            'recent_events': recent,
        })

    except Exception as e:
        current_app.logger.error("dashboard GET FAILED: %s", e)
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/dashboard', str(g.user.get('user_id', 'anon')))
            return jsonify({
                'error': 'Query took too long and was cancelled',
                'code': 'QUERY_TIMEOUT',
            }), 503
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()
