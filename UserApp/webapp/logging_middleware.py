"""
Request/Response logging middleware for Flask.

Provides automatic logging of:
- Request start time and request_id generation
- Response status, duration, and payload size
- Request/response bodies at DEBUG level
- Error tracking
"""

import time
import uuid
import json
from functools import wraps
from flask import request, g, current_app
import analytics
from werkzeug.exceptions import HTTPException

from logging_config import (
    get_log_level, is_level, BASIC, STANDARD, DEBUG,
    REQUEST_BODY_TRUNCATE, RESPONSE_BODY_TRUNCATE,
    increment_stat
)


def _normalize_endpoint(path: str) -> str:
    """Normalize an endpoint path to reduce log-label cardinality.
    Replaces UUIDs and numeric IDs with a {id} placeholder.
    """
    import re
    path = re.sub(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
        '{id}', path, flags=re.IGNORECASE,
    )
    path = re.sub(r'/\d+(?=/|$)', '/{id}', path)
    return path


def setup_request_logging(app):
    """Register before/after request handlers for logging."""

    @app.before_request
    def log_request_start():
        """Capture request start time and generate request_id."""
        g.request_start = time.time()

        # Generate or extract request_id
        g.request_id = (
            request.headers.get('X-Request-Id') or
            request.headers.get('x-request-id') or
            str(uuid.uuid4())[:8]
        )

        # Pre-populate tenant/user for logging (will be updated by auth)
        g.tenant_id = '-'
        g.user_id = None

        # DEBUG: Log full request details
        if is_level(DEBUG):
            _log_request_debug()

    @app.after_request
    def log_response(response):
        """Log response details based on level."""
        duration_ms = (time.time() - getattr(g, 'request_start', time.time())) * 1000

        # Update stats
        increment_stat('requests')
        if response.status_code >= 500:
            increment_stat('errors')

        # Skip logging for health checks and metrics
        if request.path in ('/health', '/metrics', '/favicon.ico'):
            _add_response_headers(response)
            return response

        # BASIC: One-line request summary
        current_app.logger.info(
            "%s %s -> %d (%.1fms)",
            request.method, request.path, response.status_code, duration_ms
        )

        # STANDARD: Add payload size info
        if is_level(STANDARD):
            extra_info = []
            if response.content_length:
                extra_info.append(f"size={response.content_length}")
            if extra_info:
                current_app.logger.info("  %s", " ".join(extra_info))

        # DEBUG: Log response body (truncated)
        if is_level(DEBUG):
            _log_response_debug(response)

        _add_response_headers(response)
        return response

    @app.errorhandler(HTTPException)
    def log_http_exception(error):
        """Pass through expected HTTP exceptions (e.g. 401/404/409)."""
        return error

    # ── Statement-timeout kill handler ──────────────────────────────
    # Must be registered before the generic Exception handler so Flask
    # dispatches QueryCanceled here instead of the catch-all.
    try:
        from db_driver import QueryCanceled
    except ImportError:
        QueryCanceled = None

    if QueryCanceled is not None:
        @app.errorhandler(QueryCanceled)
        def handle_query_killed(error):
            """Handle queries killed by statement_timeout that bubble up to Flask."""
            from flask import jsonify
            import db_manager

            endpoint = _normalize_endpoint(request.path) if request else 'unknown'
            user_id = getattr(g, 'user_id', None) or 'anon'
            pool_type = 'admin' if '/login' in request.path else 'app'

            db_manager.log_and_count_query_kill(endpoint, str(user_id), pool_type)

            return jsonify({
                'error': 'Query took too long and was cancelled',
                'code': 'QUERY_TIMEOUT',
            }), 503

    @app.errorhandler(Exception)
    def log_exception(error):
        """Log unhandled exceptions with stack trace."""

        increment_stat('errors')
        current_app.logger.error(
            "Unhandled exception on %s %s: %s",
            request.method, request.path, error,
            exc_info=True
        )
        # Re-raise to let Flask handle the error response
        raise error


def _log_request_debug():
    """Log detailed request info at DEBUG level."""
    headers = dict(request.headers)
    # Sanitize sensitive headers
    for sensitive in ('Authorization', 'Cookie', 'X-Api-Key'):
        if sensitive in headers:
            headers[sensitive] = '[REDACTED]'

    body = None
    if request.is_json and request.content_length and request.content_length < 50000:
        try:
            body = request.get_json(silent=True)
            if body:
                body = _sanitize_body(body)
                body_str = json.dumps(body)
                if len(body_str) > REQUEST_BODY_TRUNCATE:
                    body_str = body_str[:REQUEST_BODY_TRUNCATE] + '...[truncated]'
                body = body_str
        except Exception:
            body = '[parse error]'

    current_app.logger.debug(
        "REQUEST %s %s headers=%s body=%s",
        request.method, request.path, headers, body
    )


def _log_response_debug(response):
    """Log response body at DEBUG level."""
    if not response.is_json:
        return

    try:
        body = response.get_json(silent=True)
        if body:
            body_str = json.dumps(body)
            if len(body_str) > RESPONSE_BODY_TRUNCATE:
                body_str = body_str[:RESPONSE_BODY_TRUNCATE] + '...[truncated]'
            current_app.logger.debug("RESPONSE body=%s", body_str)
    except Exception:
        pass


def _add_response_headers(response):
    """Add standard response headers."""
    if hasattr(g, 'request_id'):
        response.headers['X-Request-Id'] = g.request_id
    response.headers['Connection'] = 'close'
    return response


def _sanitize_body(body):
    """Remove sensitive fields from request body for logging."""
    if isinstance(body, dict):
        sanitized = {}
        sensitive_keys = {
            'password', 'current_password', 'new_password',
            'token', 'secret', 'api_key', 'authorization',
            'credit_card', 'ssn', 'encrypted_password'
        }
        for key, value in body.items():
            if key.lower() in sensitive_keys:
                sanitized[key] = '[REDACTED]'
            elif isinstance(value, dict):
                sanitized[key] = _sanitize_body(value)
            else:
                sanitized[key] = value
        return sanitized
    return body


def get_request_id():
    """Get the current request ID from Flask's g object."""
    return getattr(g, 'request_id', 'unknown')


def log_auth_context(user_dict: dict):
    """
    Update logging context with authenticated user info.
    Call this after successful authentication.
    """
    if user_dict:
        g.tenant_id = user_dict.get('tenant_id', 1)
        g.user_id = user_dict.get('user_id') or user_dict.get('id')

        if is_level(STANDARD):
            current_app.logger.info(
                "Auth context: tenant_id=%s user_id=%s",
                g.tenant_id,
                str(g.user_id)[:8] + '...' if g.user_id else 'None'
            )
