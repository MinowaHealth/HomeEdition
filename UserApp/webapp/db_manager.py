"""
Database connection manager for the healthv10 Home Edition single-household system

Home Edition design:
  - Single connection pool on APP_DB_USER (healthv10_app, non-superuser)
  - No RLS: privacy is enforced at the application level — every query
    must scope by tenant_id and user_id in its WHERE clause
  - tenant_id stays in the data model (always 1) so the schema matches
    the enterprise data model; DEFAULT_TENANT_ID covers callers that
    don't pass it
  - The former admin/app pool split is collapsed; the admin entry points
    below are kept as aliases so call sites didn't have to change

Driver: routed through ``db_driver`` shim (psycopg3 only since commit
ebc35a2). The shim persists ``RESET`` cleanup via an explicit commit and
re-exports the QueryCanceled error class.

Usage:
    with get_connection_for_user(user_id, tenant_id=1) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM health_inputs WHERE tenant_id = %s AND user_id = %s",
            (1, user_id),
        )
        ...
"""

from __future__ import annotations

import os
import logging
import threading
from contextlib import contextmanager
from typing import Any, Generator, cast

import db_driver

logger = logging.getLogger(__name__)

# Configuration
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'healthv10')

# App credentials (non-superuser)
# SECURITY: No fallback to superuser - APP_DB_USER must be explicitly set
APP_DB_USER = os.getenv('APP_DB_USER')
APP_DB_PASSWORD = os.getenv('APP_DB_PASSWORD')

if not APP_DB_USER:
    logger.warning("APP_DB_USER not set - defaulting to 'healthv10_app'")
    APP_DB_USER = 'healthv10_app'
if not APP_DB_PASSWORD:
    raise ValueError(
        "APP_DB_PASSWORD must be set. Refusing to start without app "
        "credentials to prevent running as superuser."
    )

# Default tenant for single-tenant deployments
DEFAULT_TENANT_ID = int(os.getenv('DEFAULT_TENANT_ID', '1'))

# Pool configuration
POOL_MIN_CONNECTIONS = int(os.getenv('POOL_MIN_CONNECTIONS', '5'))
POOL_MAX_CONNECTIONS = int(os.getenv('POOL_MAX_CONNECTIONS', '50'))

# Single connection pool on the app role (healthv10_app)
_app_pool = None
_pool_lock = threading.Lock()


def get_app_pool():
    """Get or create the app connection pool (healthv10_app role)."""
    global _app_pool
    if _app_pool is None:
        with _pool_lock:
            if _app_pool is None:
                logger.info(
                    "Creating APP pool: %s@%s:%s user=%s (min=%d, max=%d)",
                    DB_NAME, DB_HOST, DB_PORT, APP_DB_USER,
                    POOL_MIN_CONNECTIONS, POOL_MAX_CONNECTIONS,
                )
                _app_pool = db_driver.make_pool(
                    POOL_MIN_CONNECTIONS,
                    POOL_MAX_CONNECTIONS,
                    host=DB_HOST,
                    port=DB_PORT,
                    dbname=DB_NAME,
                    user=APP_DB_USER,
                    password=APP_DB_PASSWORD,
                    options="-c statement_timeout=30000",  # 30s guard for app queries
                )
    return _app_pool


@contextmanager
def get_connection_for_user(user_id: str, tenant_id: int | None = None) -> Generator[Any, None, None]:
    """
    Get a pooled connection for a user-scoped request.

    Home Edition: no RLS context is set on the connection. Privacy is
    enforced at the application level — callers must scope every query
    by tenant_id and user_id in the WHERE clause. The user_id/tenant_id
    arguments are kept so call sites and the handler convention stay
    unchanged.

    Args:
        user_id: UUID string of the user
        tenant_id: Integer tenant ID (defaults to DEFAULT_TENANT_ID)

    Yields:
        Pooled connection with a 30s statement_timeout

    Example:
        with get_connection_for_user(g.user['user_id'], g.user.get('tenant_id', 1)) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM health_inputs WHERE tenant_id = %s AND user_id = %s",
                (tenant_id, user_id),
            )
    """
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID

    pool = get_app_pool()
    conn = pool.getconn()

    try:
        cur = conn.cursor()
        cur.execute("SET statement_timeout = '30s'")
        cur.close()

        yield conn

    finally:
        # Rollback any uncommitted transaction before returning to pool
        try:
            conn.rollback()
        except Exception:
            pass

        # Reset the timeout before returning the connection to the pool.
        # commit_after_reset persists the RESET so the connection comes
        # back clean.
        reset_ok = False
        try:
            cur = conn.cursor()
            cur.execute("RESET statement_timeout")
            cur.close()
            db_driver.commit_after_reset(conn)
            reset_ok = True
        except Exception as e:
            logger.error("CRITICAL: Failed to reset connection state: %s", e)
            # Close the connection entirely instead of returning to pool
            try:
                conn.close()
            except Exception:
                pass

        if reset_ok:
            pool.putconn(conn)


@contextmanager
def get_admin_connection() -> Generator[Any, None, None]:
    """
    Get a pooled connection for auth/system operations.

    Use this for:
    - Authentication (querying users by email before user_id is known)
    - Admin operations (listing all users, audit log)
    - System operations (cleanup jobs)

    Home Edition: same pool and role as user connections — the only
    difference is the tighter 10s statement_timeout. Kept as a separate
    entry point so call sites written against the former admin/app
    split keep working.

    Yields:
        Pooled connection with a 10s statement_timeout
    """
    pool = get_app_pool()
    conn = pool.getconn()

    try:
        cur = conn.cursor()
        cur.execute("SET statement_timeout = '10s'")
        cur.close()

        yield conn
    finally:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            cur = conn.cursor()
            cur.execute("RESET statement_timeout")
            cur.close()
            db_driver.commit_after_reset(conn)
        except Exception:
            pass
        pool.putconn(conn)


def get_direct_connection_for_user(user_id: str, tenant_id: int | None = None):
    """
    Get a direct (non-pooled) connection for a user-scoped worker.

    Uses APP credentials (healthv10_app). No RLS context — callers must
    scope queries by tenant_id and user_id at the application level.
    Use for background workers that need long-lived connections.
    Caller is responsible for closing the connection.

    Args:
        user_id: UUID string of the user
        tenant_id: Integer tenant ID (defaults to DEFAULT_TENANT_ID)

    Returns:
        Connection with a 30s statement_timeout
    """
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID

    conn = db_driver.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=APP_DB_USER,
        password=APP_DB_PASSWORD,
    )

    cur = conn.cursor()
    cur.execute("SET statement_timeout = '30s'")
    cur.close()

    return conn


def get_direct_admin_connection():
    """
    Get a direct (non-pooled) connection for system operations.

    Home Edition: same APP credentials as user connections (single
    role). Use for background workers that need long-lived connections.
    Caller is responsible for closing the connection.

    Returns:
        Connection (no statement_timeout set)
    """
    return db_driver.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=APP_DB_USER,
        password=APP_DB_PASSWORD,
    )


def is_query_killed(exc: BaseException) -> bool:
    """Check if an exception is a statement_timeout kill.

    Use in route handler except blocks:
        except Exception as e:
            if db_manager.is_query_killed(e):
                return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
            return jsonify({'error': str(e)}), 500
    """
    return isinstance(exc, db_driver.QueryCanceled)


def is_unique_violation(exc: BaseException) -> bool:
    """Check if an exception is a Postgres UNIQUE constraint violation (SQLSTATE 23505).

    Use in route handler except blocks to surface duplicate-key collisions
    as 409 Conflict rather than a generic 500. The driver shim re-exports
    ``UniqueViolation`` from psycopg3.
    """
    return isinstance(exc, db_driver.UniqueViolation)


def log_and_count_query_kill(endpoint: str = 'unknown', user_id: str = 'anon',
                             pool_type: str = 'app'):
    """Log a structured warning for a killed query.

    Call this whenever a statement_timeout kill is detected, regardless of
    whether it was caught by a route handler or by the Flask error handler.
    The log line uses a fixed prefix (QUERY_KILLED) that is easy to grep in
    the JSON logs for alerting.
    """
    logger.warning(
        "QUERY_KILLED statement_timeout endpoint=%s user=%s pool=%s "
        "— possible slow query or DoS attempt",
        endpoint, user_id, pool_type,
    )


def close_pools() -> None:
    """Close the connection pool (for shutdown)."""
    global _app_pool
    with _pool_lock:
        if _app_pool is not None:
            pool_to_close = _app_pool
            _app_pool = None
            pool_to_close.close()
            logger.info("App connection pool closed")


def get_user_database_info(identifier: str, tenant_id: int | None = None) -> dict[str, Any] | None:
    """
    Look up user by identifier (email or user_id) within a tenant.

    Args:
        identifier: Email address or UUID string
        tenant_id: Integer tenant ID (defaults to DEFAULT_TENANT_ID)

    Returns dict with: tenant_id, id, email, display_name, or None if not found.
    """
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID

    conn = get_direct_admin_connection()
    cur = conn.cursor()

    try:
        identifier_str = str(identifier).lower().strip()

        if '@' in identifier_str:
            # Email lookup within tenant
            cur.execute("""
                SELECT tenant_id, id, email, display_name
                FROM users
                WHERE tenant_id = %s AND email = %s AND is_active = true
            """, (tenant_id, identifier_str,))
        elif '-' in identifier_str and len(identifier_str) == 36:
            # UUID lookup within tenant
            cur.execute("""
                SELECT tenant_id, id, email, display_name
                FROM users
                WHERE tenant_id = %s AND id = %s AND is_active = true
            """, (tenant_id, identifier_str,))
        else:
            return None

        return cast(dict[str, Any] | None, cur.fetchone())

    finally:
        conn.close()


def get_user_database_info_by_id(user_id: str, tenant_id: int | None = None) -> dict[str, Any] | None:
    """
    Look up user by UUID within a tenant.

    Args:
        user_id: UUID string of the user
        tenant_id: Integer tenant ID (defaults to DEFAULT_TENANT_ID)

    Returns dict with: tenant_id, id, email, display_name, or None if not found.
    """
    if tenant_id is None:
        tenant_id = DEFAULT_TENANT_ID

    conn = get_direct_admin_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT tenant_id, id, email, display_name
            FROM users
            WHERE tenant_id = %s AND id = %s AND is_active = true
        """, (tenant_id, str(user_id),))
        return cast(dict[str, Any] | None, cur.fetchone())
    finally:
        conn.close()
