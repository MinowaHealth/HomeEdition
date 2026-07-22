"""
Shared utilities for Minowa webapp routes.

This module contains helpers used across multiple blueprints:
- Authentication decorators
- Database connection helpers
- Timezone conversion utilities
- Logging helpers
"""
from flask import request, jsonify, g, redirect, url_for, current_app
from functools import wraps
import os
import pytz
from datetime import datetime, timedelta
from threading import Lock

import auth
import db_manager
from logging_middleware import log_auth_context, get_request_id

# Timezone configuration — fallback for non-request contexts (CLI, cron)
DEFAULT_TIMEZONE = pytz.timezone(os.getenv('TIMEZONE', 'America/Los_Angeles'))
TIMEZONE = DEFAULT_TIMEZONE  # Backward compat alias


def get_user_timezone():
    """Get the current user's timezone from g.user, falling back to DEFAULT_TIMEZONE.

    Must be called within a request context where require_auth has run.
    """
    try:
        tz_name = g.user.get('home_timezone', 'America/Los_Angeles')
        return pytz.timezone(tz_name)
    except (AttributeError, pytz.exceptions.UnknownTimeZoneError):
        return DEFAULT_TIMEZONE

# Verbose logging flag (set by app.py)
VERBOSE = os.getenv('VERBOSE_LOGGING', 'false').lower() in ('true', '1', 'yes')
_TABLE_COLUMNS_CACHE = {}
_TABLE_COLUMNS_CACHE_LOCK = Lock()


def escape_like(value: str) -> str:
    """
    Escape special characters for SQL LIKE patterns.

    Prevents LIKE injection where users could use % or _ to match unintended data.
    Use this when building LIKE/ILIKE patterns from user input.

    Example:
        search_pattern = f"%{escape_like(user_input)}%"
        cur.execute("SELECT * FROM items WHERE name ILIKE %s", (search_pattern,))
    """
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def vlog(msg, *args, **kwargs):
    """Log only when VERBOSE is enabled. Use for high-frequency debug info."""
    if VERBOSE:
        current_app.logger.debug(msg, *args, **kwargs)


def parse_bool(value, default=True):
    """Accept bools and legacy 0/1-style values from clients."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ('1', 'true', 'yes', 'on'):
            return True
        if normalized in ('0', 'false', 'no', 'off'):
            return False
    return default


def require_auth(f):
    """
    Decorator to require authentication for routes.

    Auth priority: HEALTHKIT_SYNC_TOKEN > bearer session > cookie session > redirect/401.
    This is the single source of truth — used by both app.py and all blueprints.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import session as flask_session

        session_id = flask_session.get('session_id')
        bearer = None
        auth_header = request.headers.get('Authorization')
        vlog("require_auth: path=%s session_id=%s auth_header=%s request_id=%s",
             request.path, session_id, bool(auth_header), get_request_id())
        current_app.logger.info(
            "auth_check path=%s auth_header=%s has_session_cookie=%s request_id=%s",
            request.path,
            bool(auth_header),
            bool(session_id),
            get_request_id(),
        )
        if auth_header and auth_header.lower().startswith('bearer '):
            bearer = auth_header.split(' ', 1)[1].strip()

        # Token-based auth (for background sync)
        token = os.getenv('HEALTHKIT_SYNC_TOKEN')
        token_username = os.getenv('HEALTHKIT_SYNC_USERNAME')
        if bearer and token and bearer == token:
            vlog("require_auth: using HEALTHKIT_SYNC_TOKEN auth")
            user_record = None
            if token_username:
                user_record = auth.get_user_by_username(token_username)
            if not user_record:
                fallback = get_first_user_record()
                if fallback:
                    user_record = {
                        'id': fallback['id'],
                        'username': fallback['username'],
                        'database_name': fallback.get('database_name'),
                    }
            if user_record:
                g.user = {
                    'session_id': None,
                    'user_id': user_record['id'],
                    'username': user_record.get('username'),
                    'database_name': user_record.get('database_name')
                }
                log_auth_context(g.user)
                vlog("require_auth: token auth success, g.user=%s", g.user)
                return f(*args, **kwargs)

        # Allow bearer to be a session token
        if bearer:
            vlog("require_auth: trying bearer as session token")
            try:
                user_session = auth.get_session(bearer)
            except Exception:
                # Invalid token format (e.g. not a valid UUID) - treat as no session
                user_session = None
            if user_session:
                g.user = user_session
                log_auth_context(g.user)
                vlog("require_auth: bearer session auth success, g.user=%s", g.user)
                return f(*args, **kwargs)

        # API key auth (hbk_ prefixed long-lived tokens)
        if bearer and bearer.startswith('hbk_'):
            vlog("require_auth: trying bearer as API key")
            user_record = auth.lookup_api_key(bearer)
            if user_record:
                g.user = user_record
                log_auth_context(g.user)
                vlog("require_auth: API key auth success, g.user=%s", g.user)
                return f(*args, **kwargs)

        # Bearer provided but invalid - return 401 for API clients
        if bearer and (request.is_json or auth_header):
            return jsonify({'error': 'Invalid or expired token'}), 401

        def _unauth(msg='Not authenticated'):
            if request.path.startswith('/api/') or request.is_json:
                return jsonify({'error': msg}), 401
            return redirect(url_for('login'))

        if not session_id:
            vlog("require_auth: no session_id, redirecting to login")
            return _unauth('Not authenticated')

        # Validate session
        vlog("require_auth: validating session_id=%s", session_id)
        user_session = auth.get_session(session_id)
        if not user_session:
            vlog("require_auth: session invalid or expired")
            flask_session.clear()
            return _unauth('Session expired')

        # Store user info in request context
        g.user = user_session
        log_auth_context(g.user)
        vlog("require_auth: session auth success, g.user=%s", g.user)
        return f(*args, **kwargs)

    return decorated_function


def table_has_column(conn, table_name, column_name):
    """Check if a column exists on the given table for the current schema.

    Uses a process-local cache keyed by connection identity (host/db/user/port),
    schema name, and table to avoid repeated information_schema lookups.
    """
    dsn = conn.get_dsn_parameters() if hasattr(conn, 'get_dsn_parameters') else {}
    base_key = (
        dsn.get('host') or '',
        dsn.get('port') or '',
        dsn.get('dbname') or '',
        dsn.get('user') or '',
    )

    # Fast path: scan existing keys for this connection/table regardless of schema.
    with _TABLE_COLUMNS_CACHE_LOCK:
        for cache_key, cached_columns in _TABLE_COLUMNS_CACHE.items():
            if cache_key[:4] == base_key and cache_key[5] == table_name:
                return column_name in cached_columns

    cur = conn.cursor()
    try:
        cur.execute(
            """
            WITH schema_name AS (SELECT current_schema() AS schema_name)
            SELECT s.schema_name, c.column_name
            FROM schema_name s
            LEFT JOIN information_schema.columns c
              ON c.table_schema = s.schema_name
             AND c.table_name = %s
            """,
            (table_name,),
        )
        rows = cur.fetchall() or []
    except Exception as exc:
        # Never raise from schema introspection in request paths; callers rely
        # on a conservative false when metadata access is unavailable.
        current_app.logger.warning(
            "table_has_column failed table=%s column=%s error=%s request_id=%s",
            table_name,
            column_name,
            exc,
            get_request_id(),
        )
        rows = []
    finally:
        cur.close()

    schema_name = rows[0]['schema_name'] if rows else None
    cache_key = base_key + (schema_name, table_name)
    columns = {row['column_name'] for row in rows if row.get('column_name')}

    with _TABLE_COLUMNS_CACHE_LOCK:
        _TABLE_COLUMNS_CACHE[cache_key] = columns

    return column_name in columns


def get_user_db_connection():
    """
    Get database connection for the current logged-in user.

    Home Edition: no RLS — queries must scope by tenant_id and user_id
    at the application level.
    """
    vlog("get_user_db_connection: g.user=%s", g.user)

    user_id = g.user.get('user_id')
    tenant_id = g.user.get('tenant_id', 1)

    if not user_id:
        current_app.logger.error("get_user_db_connection: No user_id available in g.user")
        raise ValueError("No user_id available for current user context")

    conn = db_manager.get_direct_connection_for_user(user_id, tenant_id)
    vlog("get_user_db_connection: connected for tenant_id=%s, user_id=%s", tenant_id, user_id)
    return conn


def get_db_connection():
    """Create a database connection for the current user"""
    return get_user_db_connection()


def get_user_id():
    """
    Get the current user's ID from the session context.

    Home Edition: the user_id comes from the authenticated session context.
    """
    user_id = g.user.get('user_id')
    if not user_id:
        raise ValueError("No user_id available in session context")
    return user_id


def get_first_user_record():
    """
    Return the first active user's record (for token auth fallback).
    In v10, there's no database_name - we use the shared healthv10 database.
    """
    conn = db_manager.get_direct_admin_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, email, display_name
            FROM users
            WHERE is_active = true
            ORDER BY id
            LIMIT 1
            """
        )
        user = cur.fetchone()
        if user:
            user['username'] = user.get('email')
            user['database_name'] = 'healthv10'
        return user
    finally:
        cur.close()
        conn.close()


def utc_to_local(utc_dt, tz=None):
    """Convert UTC datetime to user's local timezone.

    Args:
        utc_dt: UTC datetime (naive or aware)
        tz: Optional pytz timezone override. If None, uses get_user_timezone().
    """
    if utc_dt is None:
        return None
    if utc_dt.tzinfo is None:
        utc_dt = pytz.utc.localize(utc_dt)
    user_tz = tz or get_user_timezone()
    return utc_dt.astimezone(user_tz)


def local_to_utc(local_dt_str, tz=None):
    """Convert local datetime string to UTC.

    Naive datetimes are assumed to be in the user's home_timezone.

    Args:
        local_dt_str: ISO 8601 datetime string (possibly naive)
        tz: Optional pytz timezone override. If None, uses get_user_timezone().
    """
    if local_dt_str.endswith('Z'):
        local_dt_str = local_dt_str[:-1] + '+00:00'
    local_dt = datetime.fromisoformat(local_dt_str)
    if local_dt.tzinfo is None:
        user_tz = tz or get_user_timezone()
        local_dt = user_tz.localize(local_dt)
    return local_dt.astimezone(pytz.utc)


def parse_date_range_params():
    """
    Parse optional start_date/end_date query parameters for listing endpoints.

    Returns:
        (start_date, end_date, error_response)
        If error_response is not None, return it immediately (400 status).
        Dates are returned as date objects or None if not provided.
    """
    import re
    DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

    start_str = request.args.get('start_date')
    end_str = request.args.get('end_date')

    start_date = None
    end_date = None

    if start_str:
        if not DATE_RE.match(start_str):
            return None, None, (jsonify({'error': 'Invalid start_date format. Use YYYY-MM-DD'}), 400)
        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
        except ValueError:
            return None, None, (jsonify({'error': 'Invalid start_date value'}), 400)

    if end_str:
        if not DATE_RE.match(end_str):
            return None, None, (jsonify({'error': 'Invalid end_date format. Use YYYY-MM-DD'}), 400)
        try:
            end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
        except ValueError:
            return None, None, (jsonify({'error': 'Invalid end_date value'}), 400)

    if start_date and end_date and start_date > end_date:
        return None, None, (jsonify({'error': 'start_date must be before or equal to end_date'}), 400)

    MAX_DATE_RANGE_DAYS = 90
    if start_date and end_date and (end_date - start_date).days > MAX_DATE_RANGE_DAYS:
        return None, None, (jsonify({
            'error': f'Date range cannot exceed {MAX_DATE_RANGE_DAYS} days'
        }), 400)

    return start_date, end_date, None


# Shared bounds for the point-in-time detail endpoints (minute-detail,
# sleep-events, observations/detail): ±60 min default half-width around `at`,
# widenable to ±720 min, or an explicit from/to span capped at 24 hours.
DETAIL_WINDOW_MINUTES = 60
DETAIL_WINDOW_MAX_MINUTES = 720
DETAIL_SPAN_MAX_HOURS = 24


def parse_detail_window():
    """
    Parse the shared at / window_minutes / from-to query params used by the
    point-in-time detail endpoints. Two invocation modes, same return shape:

        at (+ optional window_minutes) — window is [at − w, at + w]
        from / to — explicit bounds (both required together; `at` ignored)

    Timestamps follow the app convention: offset-aware ISO 8601 strings are
    honored; naive strings are read in the user's home timezone.

    Returns:
        (at_utc, start, end, error_response)
        If error_response is not None, return it immediately (400 status).
        at_utc is None in from/to mode.
    """
    at_str = request.args.get('at')
    from_str = request.args.get('from')
    to_str = request.args.get('to')

    if from_str or to_str:
        if not (from_str and to_str):
            return None, None, None, (jsonify({'error': 'from and to must be provided together'}), 400)
        try:
            start = local_to_utc(from_str)
            end = local_to_utc(to_str)
        except (ValueError, TypeError):
            return None, None, None, (jsonify({'error': 'Invalid from/to timestamp; use ISO 8601'}), 400)
        if end <= start:
            return None, None, None, (jsonify({'error': 'to must be after from'}), 400)
        if end - start > timedelta(hours=DETAIL_SPAN_MAX_HOURS):
            return None, None, None, (jsonify({'error': f'from/to span must be {DETAIL_SPAN_MAX_HOURS} hours or less'}), 400)
        return None, start, end, None

    if not at_str:
        return None, None, None, (jsonify({'error': 'at (ISO 8601 timestamp) or from/to is required'}), 400)
    try:
        at_utc = local_to_utc(at_str)
    except (ValueError, TypeError):
        return None, None, None, (jsonify({'error': 'Invalid at timestamp; use ISO 8601'}), 400)
    try:
        window_minutes = int(request.args.get('window_minutes', DETAIL_WINDOW_MINUTES))
    except (ValueError, TypeError):
        return None, None, None, (jsonify({'error': 'window_minutes must be an integer'}), 400)
    if not 1 <= window_minutes <= DETAIL_WINDOW_MAX_MINUTES:
        return None, None, None, (jsonify({'error': f'window_minutes must be between 1 and {DETAIL_WINDOW_MAX_MINUTES}'}), 400)
    window = timedelta(minutes=window_minutes)
    return at_utc, at_utc - window, at_utc + window, None


def parse_pagination_params(default_limit=50, max_limit=200):
    """
    Parse limit/offset query parameters for listing endpoints.

    Soft contract: non-integer input falls back silently to defaults instead
    of raising. limit is clamped to [1, max_limit]; offset is clamped to >= 0.

    Pair with paginated_response() and the count(*) OVER() SQL pattern:

        limit, offset = parse_pagination_params()
        cur.execute('''
            SELECT count(*) OVER() AS _total, t.*
            FROM some_table t
            WHERE ...
            ORDER BY ...
            LIMIT %s OFFSET %s
        ''', (..., limit, offset))
        rows = cur.fetchall()
        total = rows[0]['_total'] if rows else 0
        items = [_serialize({k: v for k, v in r.items() if k != '_total'}) for r in rows]
        return jsonify(paginated_response(items, total, limit, offset, key='collection_name'))

    Returns:
        (limit, offset) — both ints, both clamped.
    """
    try:
        limit = int(request.args.get('limit', default_limit))
    except (TypeError, ValueError):
        limit = default_limit
    try:
        offset = int(request.args.get('offset', 0))
    except (TypeError, ValueError):
        offset = 0
    limit = min(max(limit, 1), max_limit)
    offset = max(offset, 0)
    return limit, offset


def paginated_response(items, total, limit, offset, key='items'):
    """
    Build the standard paginated JSON envelope used by all UserApp list endpoints.

    Args:
        items: page of rows (already serialized to JSON-friendly dicts)
        total: unfiltered post-WHERE count, typically from count(*) OVER()
        limit: page size used for this query (after clamping)
        offset: page offset used for this query (after clamping)
        key: collection name (e.g. 'conditions', 'readings', 'food_items')

    Returns:
        dict with the collection under `key` plus a `pagination` sub-object
        containing total/limit/offset/has_more.

    has_more is computed from len(items) rather than `limit` so the last
    (partial) page reports correctly.
    """
    return {
        key: items,
        'pagination': {
            'total': total,
            'limit': limit,
            'offset': offset,
            'has_more': offset + len(items) < total,
        }
    }
