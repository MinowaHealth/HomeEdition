"""
External Integrations routes.

Blueprint for HealthKit and Garmin Connect integrations.
"""
from flask import Blueprint, request, jsonify, g, current_app
from datetime import datetime, date, timedelta
import pytz
import tempfile
import uuid
import concurrent.futures
import zipfile
import shutil
from pathlib import Path

from utils import (
    require_auth,
    get_db_connection,
    get_user_db_connection,
    get_user_id,
    get_user_timezone,
    parse_pagination_params,
    paginated_response,
)
import db_manager
import analytics

bp = Blueprint('integrations', __name__, url_prefix='/api/v1')

HEALTHKIT_UPLOAD_DIR = Path(tempfile.gettempdir()) / 'healthkit-uploads'


# ==================== HEALTHKIT UPLOAD ====================

@bp.route('/healthkit/upload', methods=['POST'])
@require_auth
def upload_healthkit():
    """Upload HealthKit export ZIP for processing"""

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    if not file.filename.endswith('.zip'):
        return jsonify({'error': 'File must be a ZIP archive'}), 400

    # Early size check before saving to disk (defense-in-depth with MAX_CONTENT_LENGTH)
    max_size = 500 * 1024 * 1024
    if request.content_length and request.content_length > max_size:
        return jsonify({'error': f'File too large (max {max_size // 1024 // 1024}MB)'}), 413

    user_id = g.user['user_id']
    tenant_id = g.user.get('tenant_id', 1)
    job_id = f"hk-import-{uuid.uuid4().hex[:12]}"

    upload_dir = HEALTHKIT_UPLOAD_DIR / str(user_id) / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    zip_path = upload_dir / 'export.zip'
    extract_dir = upload_dir / 'extracted'

    try:
        file.save(zip_path)
        file_size = zip_path.stat().st_size

        max_size = 500 * 1024 * 1024
        if file_size > max_size:
            shutil.rmtree(upload_dir)
            return jsonify({'error': f'File too large (max {max_size // 1024 // 1024}MB)'}), 413

        with zipfile.ZipFile(zip_path, 'r') as zf:
            if 'apple_health_export/export.xml' not in zf.namelist():
                shutil.rmtree(upload_dir)
                return jsonify({'error': 'Invalid HealthKit export (missing export.xml)'}), 400

            # Validate every member path before extracting to prevent zip-slip
            # (absolute paths or .. traversal escaping extract_dir). zipfile has
            # no equivalent to tarfile's filter='data' kwarg, so we check manually.
            extract_dir.mkdir(parents=True, exist_ok=True)
            extract_root = extract_dir.resolve()
            for member in zf.infolist():
                member_path = (extract_root / member.filename).resolve()
                if not member_path.is_relative_to(extract_root):
                    shutil.rmtree(upload_dir)
                    return jsonify({'error': 'Unsafe path in ZIP archive'}), 400

            zf.extractall(extract_dir)

        conn = get_user_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO healthkit_import_jobs
                (tenant_id, user_id, status)
                VALUES (%s, %s, 'pending')
                RETURNING id
            """, (tenant_id, user_id))
            result = cur.fetchone()
            job_id = str(result['id'])
            conn.commit()
            cur.close()
        finally:
            conn.close()

        from healthkit_worker import queue_healthkit_import
        queue_healthkit_import(user_id, job_id, extract_dir / 'apple_health_export',
                               tenant_id=tenant_id)

        analytics.capture('healthkit_sync_completed', {'job_id': job_id, 'source': 'upload'})

        return jsonify({
            'job_id': job_id,
            'status': 'pending',
            'message': 'HealthKit import queued for processing',
            'estimated_time': '2-5 minutes'
        }), 202

    except zipfile.BadZipFile:
        if upload_dir.exists():
            shutil.rmtree(upload_dir)
        return jsonify({'error': 'Invalid ZIP file'}), 400
    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/healthkit/upload', str(g.user.get('user_id', 'anon')))
            if upload_dir.exists():
                shutil.rmtree(upload_dir)
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        if upload_dir.exists():
            shutil.rmtree(upload_dir)
        return jsonify({'error': str(e)}), 500


@bp.route('/healthkit/jobs/<job_id>', methods=['GET'])
@require_auth
def get_healthkit_job_status(job_id):
    """Get status of HealthKit import job"""

    user_id = g.user['user_id']
    conn = get_user_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, status, total_records, processed_records,
               error_message, started_at, completed_at, created_at
        FROM healthkit_import_jobs
        WHERE id = %s::uuid AND user_id = %s
    """, (job_id, user_id))

    job = cur.fetchone()
    cur.close()
    conn.close()

    if not job:
        return jsonify({'error': 'Job not found'}), 404

    return jsonify({
        'job_id': str(job['id']),
        'status': job['status'],
        'total_records': job['total_records'],
        'processed_records': job['processed_records'],
        'error_message': job['error_message'],
        'started_at': job['started_at'].isoformat() if job['started_at'] else None,
        'completed_at': job['completed_at'].isoformat() if job['completed_at'] else None,
        'created_at': job['created_at'].isoformat() if job['created_at'] else None
    })


@bp.route('/healthkit/jobs', methods=['GET'])
@require_auth
def list_healthkit_jobs():
    """List recent HealthKit import jobs for current user"""

    user_id = g.user['user_id']
    limit, offset = parse_pagination_params(default_limit=20, max_limit=100)
    conn = get_user_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT count(*) OVER() AS _total,
               id, status, total_records, processed_records,
               error_message, completed_at, created_at
        FROM healthkit_import_jobs
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """, (user_id, limit, offset))

    jobs = cur.fetchall()
    cur.close()
    conn.close()

    total = jobs[0]['_total'] if jobs else 0
    result = []
    for job in jobs:
        result.append({
            'job_id': str(job['id']),
            'status': job['status'],
            'total_records': job['total_records'],
            'processed_records': job['processed_records'],
            'error_message': job['error_message'],
            'completed_at': job['completed_at'].isoformat() if job['completed_at'] else None,
            'created_at': job['created_at'].isoformat() if job['created_at'] else None
        })

    return jsonify(paginated_response(result, total, limit, offset, key='entries'))


# ==================== GARMIN CONNECT ====================

@bp.route('/garmin/connect', methods=['POST'])
@require_auth
def garmin_connect():
    """Authenticate with Garmin Connect and store OAuth tokens"""
    from garminconnect import Garmin, GarminConnectAuthenticationError
    import base64

    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    def _do_garmin_login():
        g_client = Garmin(email=email, password=password)
        g_client.login()
        name = None
        try:
            name = g_client.get_full_name()
        except Exception:
            pass
        return g_client, name

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_do_garmin_login)
            garmin, display_name = future.result(timeout=15)

        session_data = garmin.garth.dumps()
        garth_session = base64.b64encode(session_data.encode()).decode()

        conn = get_user_db_connection()
        user_id = get_user_id()
        tenant_id = g.user.get('tenant_id', 1)

        cur = conn.cursor()

        # Store session in encrypted_password field (base64 encoded garth session)
        cur.execute("""
            INSERT INTO garmin_credentials
            (tenant_id, user_id, email, encrypted_password)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tenant_id, user_id) DO UPDATE SET
                email = EXCLUDED.email,
                encrypted_password = EXCLUDED.encrypted_password,
                updated_at = now()
        """, (tenant_id, user_id, email, garth_session))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': 'Connected to Garmin',
            'email': email,
            'display_name': display_name
        })

    except GarminConnectAuthenticationError as e:
        return jsonify({'error': f'Authentication failed: {str(e)}'}), 401
    except concurrent.futures.TimeoutError:
        return jsonify({'error': 'Garmin login timed out — try again later'}), 504
    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/garmin/connect', str(g.user.get('user_id', 'anon')))
            return jsonify({'error': 'Query took too long and was cancelled', 'code': 'QUERY_TIMEOUT'}), 503
        return jsonify({'error': f'Connection failed: {str(e)}'}), 500


@bp.route('/garmin/status', methods=['GET'])
@require_auth
def garmin_status():
    """Get Garmin connection status and last sync time"""
    conn = get_user_db_connection()
    user_id = get_user_id()

    cur = conn.cursor()

    cur.execute("""
        SELECT email, last_sync, created_at, updated_at
        FROM garmin_credentials
        WHERE user_id = %s
    """, (user_id,))

    creds = cur.fetchone()
    cur.close()
    conn.close()

    if not creds:
        return jsonify({
            'connected': False,
            'message': 'Not connected to Garmin'
        })

    for key in ['last_sync', 'created_at', 'updated_at']:
        if creds.get(key):
            creds[key] = creds[key].isoformat()

    return jsonify({
        'connected': True,
        'email': creds['email'],
        'last_sync': creds['last_sync'],
        'created_at': creds['created_at']
    })


@bp.route('/garmin/disconnect', methods=['POST'])
@require_auth
def garmin_disconnect():
    """Remove Garmin credentials"""
    tenant_id = g.user.get('tenant_id', 1)

    conn = get_user_db_connection()
    user_id = get_user_id()

    cur = conn.cursor()

    cur.execute("DELETE FROM garmin_credentials WHERE tenant_id = %s AND user_id = %s", (tenant_id, user_id,))
    deleted = cur.rowcount > 0

    conn.commit()
    cur.close()
    conn.close()

    if deleted:
        return jsonify({'success': True, 'message': 'Disconnected from Garmin'})
    else:
        return jsonify({'success': False, 'message': 'No Garmin connection found'}), 404


@bp.route('/garmin/sync', methods=['POST'])
@require_auth
def garmin_sync():
    """Trigger a Garmin data sync job"""
    conn = get_user_db_connection()
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    master_user_id = g.user.get('user_id')
    if not master_user_id:
        return jsonify({'error': 'User not found'}), 404

    cur = conn.cursor()

    cur.execute("SELECT encrypted_password, last_sync FROM garmin_credentials WHERE tenant_id = %s AND user_id = %s", (tenant_id, user_id,))
    creds = cur.fetchone()

    if not creds:
        cur.close()
        conn.close()
        return jsonify({'error': 'Not connected to Garmin. Please connect first.'}), 400

    data = request.get_json() or {}
    sync_from = data.get('from_date')
    sync_to = data.get('to_date')

    if not sync_from:
        if creds['last_sync']:
            sync_from = creds['last_sync'].date().isoformat()
        else:
            user_today = datetime.now(get_user_timezone()).date()
            sync_from = (user_today - timedelta(days=30)).isoformat()

    if not sync_to:
        sync_to = datetime.now(get_user_timezone()).date().isoformat()

    cur.execute("""
        INSERT INTO garmin_sync_jobs
        (tenant_id, user_id, job_type, status, start_date, end_date)
        VALUES (%s, %s, 'full_sync', 'pending', %s, %s)
        RETURNING id
    """, (tenant_id, user_id, sync_from, sync_to))

    result = cur.fetchone()
    job_id = str(result['id'])

    conn.commit()
    cur.close()
    conn.close()

    from garmin_worker import queue_garmin_sync
    queue_garmin_sync(master_user_id, job_id, creds['encrypted_password'], sync_from, sync_to)

    analytics.capture('garmin_sync_completed', {'job_id': job_id, 'source': 'manual'})

    return jsonify({
        'job_id': job_id,
        'status': 'pending',
        'sync_from': sync_from,
        'sync_to': sync_to,
        'message': 'Garmin sync job queued'
    }), 202


@bp.route('/garmin/jobs/<job_id>', methods=['GET'])
@require_auth
def get_garmin_job_status(job_id):
    """Get status of Garmin sync job"""
    conn = get_user_db_connection()
    user_id = get_user_id()

    cur = conn.cursor()

    cur.execute("""
        SELECT id, job_type, status, start_date, end_date,
               progress, error_message,
               started_at, completed_at, created_at
        FROM garmin_sync_jobs
        WHERE id = %s::uuid AND user_id = %s
    """, (job_id, user_id))

    job = cur.fetchone()
    cur.close()
    conn.close()

    if not job:
        return jsonify({'error': 'Job not found'}), 404

    # Map to API response format
    result = {
        'job_id': str(job['id']),
        'job_type': job['job_type'],
        'status': job['status'],
        'start_date': job['start_date'].isoformat() if job['start_date'] else None,
        'end_date': job['end_date'].isoformat() if job['end_date'] else None,
        'progress': job['progress'],
        'error_message': job['error_message'],
        'started_at': job['started_at'].isoformat() if job['started_at'] else None,
        'completed_at': job['completed_at'].isoformat() if job['completed_at'] else None,
        'created_at': job['created_at'].isoformat() if job['created_at'] else None
    }

    return jsonify(result)


@bp.route('/garmin/jobs', methods=['GET'])
@require_auth
def list_garmin_jobs():
    """List recent Garmin sync jobs for current user"""
    conn = get_user_db_connection()
    user_id = get_user_id()
    limit, offset = parse_pagination_params(default_limit=20, max_limit=100)

    cur = conn.cursor()

    cur.execute("""
        SELECT count(*) OVER() AS _total,
               id, job_type, status, start_date, end_date,
               progress, error_message, completed_at, created_at
        FROM garmin_sync_jobs
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """, (user_id, limit, offset))

    jobs = cur.fetchall()
    cur.close()
    conn.close()

    total = jobs[0]['_total'] if jobs else 0
    result = []
    for job in jobs:
        result.append({
            'job_id': str(job['id']),
            'job_type': job['job_type'],
            'status': job['status'],
            'start_date': job['start_date'].isoformat() if job['start_date'] else None,
            'end_date': job['end_date'].isoformat() if job['end_date'] else None,
            'progress': job['progress'],
            'error_message': job['error_message'],
            'completed_at': job['completed_at'].isoformat() if job['completed_at'] else None,
            'created_at': job['created_at'].isoformat() if job['created_at'] else None
        })

    return jsonify(paginated_response(result, total, limit, offset, key='entries'))


# ==================== DATA CORRECTIONS ====================

ALLOWED_CORRECTION_FIELDS = {'activityType', 'food_name'}


@bp.route('/healthkit/correct', methods=['PUT'])
@require_auth
def correct_health_record():
    """Apply a correction to a synced health record (exercise name, food name, etc.)."""
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body required'}), 400

    sample_id = data.get('sample_id')
    field = data.get('field')
    new_value = data.get('new_value')

    if not sample_id or not field or not new_value:
        return jsonify({'error': 'sample_id, field, and new_value are required'}), 400

    if field not in ALLOWED_CORRECTION_FIELDS:
        return jsonify({'error': f'field must be one of: {", ".join(sorted(ALLOWED_CORRECTION_FIELDS))}'}), 400

    record_type = 'workout' if field == 'activityType' else 'food'

    current_app.logger.info(
        "PUT /healthkit/correct: user_id=%s sample_id=%s field=%s",
        user_id, sample_id, field
    )

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Fetch original value for audit trail.
            # activityType lives on the canonical hkit_workouts.metadata jsonb;
            # food name lives on health_food_logv2.free_text (manual entries).
            original_value = None
            if field == 'activityType':
                cur.execute("""
                    SELECT COALESCE(metadata->>'activityType', '') AS original
                    FROM hkit_workouts
                    WHERE tenant_id = %s AND user_id = %s AND id = %s::bigint
                """, (tenant_id, user_id, sample_id))
                row = cur.fetchone()
                original_value = row['original'] if row else None
            elif field == 'food_name':
                cur.execute("""
                    SELECT free_text AS original
                    FROM health_food_logv2
                    WHERE tenant_id = %s AND user_id = %s AND id = %s::uuid
                """, (tenant_id, user_id, sample_id))
                row = cur.fetchone()
                original_value = row['original'] if row else None

            # Log the correction
            cur.execute("""
                INSERT INTO data_corrections (tenant_id, user_id, record_type, corrected_field, original_value, new_value)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (tenant_id, user_id, record_type, field, original_value, new_value))

            # Apply the correction to the canonical source table.
            if field == 'activityType':
                cur.execute("""
                    UPDATE hkit_workouts
                    SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('activityType', %s)
                    WHERE tenant_id = %s AND user_id = %s AND id = %s::bigint
                """, (new_value, tenant_id, user_id, sample_id))
            elif field == 'food_name':
                cur.execute("""
                    UPDATE health_food_logv2
                    SET free_text = %s
                    WHERE tenant_id = %s AND user_id = %s AND id = %s::uuid
                """, (new_value, tenant_id, user_id, sample_id))

            if cur.rowcount == 0:
                conn.rollback()
                return jsonify({'error': 'Record not found'}), 404

            conn.commit()

        analytics.capture('data_corrected', {
            'record_type': record_type,
            'corrected_field': field,
        })

        return jsonify({'ok': True})
    except Exception as e:
        conn.rollback()
        current_app.logger.error("PUT /healthkit/correct failed: %s", str(e))
        return jsonify({'error': 'Failed to apply correction'}), 500
    finally:
        conn.close()
