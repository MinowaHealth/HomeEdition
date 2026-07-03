"""
Three-tier logging configuration for Minowa Flask Application.

Log Levels:
- BASIC: Production-ready minimal logging (one line per request)
- STANDARD: Monitoring and performance tracking (+ query timing)
- DEBUG: Full troubleshooting (+ SQL, headers, bodies, EXPLAIN)

Environment Variable:
    FLASK_LOG_LEVEL=BASIC|STANDARD|DEBUG (default: BASIC)

Output:
- Human-readable format to stdout (read with `docker compose logs`)
"""

import logging
import os
import sys
import time
from typing import Optional, Dict, Any
from flask import g, has_request_context

# Log level constants
BASIC = 'BASIC'
STANDARD = 'STANDARD'
DEBUG = 'DEBUG'

# Parse environment variable. FLASK_LOG_LEVEL is the Flask three-tier vocabulary
# (BASIC/STANDARD/DEBUG); kept distinct from UVICORN_LOG_LEVEL (Python stdlib levels)
# used by the MCP servers, since forwarding "STANDARD" into uvicorn crashes it.
_LOG_LEVEL = os.getenv('FLASK_LOG_LEVEL', 'BASIC').upper()
if _LOG_LEVEL not in (BASIC, STANDARD, DEBUG):
    _LOG_LEVEL = BASIC

# Thresholds
SLOW_QUERY_THRESHOLD_MS = int(os.getenv('SLOW_QUERY_THRESHOLD_MS', '100'))
REQUEST_BODY_TRUNCATE = 2000
RESPONSE_BODY_TRUNCATE = 1000


def get_log_level() -> str:
    """Return current log level string."""
    return _LOG_LEVEL


def is_level(level: str) -> bool:
    """Check if current level meets or exceeds the specified level."""
    levels = {BASIC: 1, STANDARD: 2, DEBUG: 3}
    return levels.get(_LOG_LEVEL, 1) >= levels.get(level, 1)


class RequestContextFilter(logging.Filter):
    """Inject request context (request_id, tenant_id, user_id) into all log records."""

    def filter(self, record):
        # Guard g access outside Flask request/app context.
        if has_request_context():
            record.request_id = getattr(g, 'request_id', '-')
            record.tenant_id = getattr(g, 'tenant_id', '-')
            user_id = getattr(g, 'user_id', None)
        else:
            record.request_id = '-'
            record.tenant_id = '-'
            user_id = None

        if user_id:
            # Truncate UUID for readability
            record.user_id = str(user_id)[:8]
        else:
            record.user_id = '-'

        return True


class HumanReadableFormatter(logging.Formatter):
    """Human-readable log format for stdout/docker logs."""

    FORMATS = {
        BASIC: '%(asctime)s [%(levelname)s] %(message)s',
        STANDARD: '%(asctime)s [%(levelname)s] [rid=%(request_id)s] %(message)s',
        DEBUG: '%(asctime)s [%(levelname)s] [rid=%(request_id)s t=%(tenant_id)s u=%(user_id)s] %(name)s:%(lineno)d - %(message)s',
    }

    def __init__(self, level: str = BASIC):
        fmt = self.FORMATS.get(level, self.FORMATS[BASIC])
        super().__init__(fmt=fmt, datefmt='%Y-%m-%d %H:%M:%S')


class StdoutLogHandler(logging.Handler):
    """Handler that emits human-readable lines to stdout (read with `docker compose logs`)."""

    def __init__(self, level: str = BASIC):
        super().__init__()
        self.level_name = level

        # Human-readable to stdout
        self.stdout_handler = logging.StreamHandler(sys.stdout)
        self.stdout_handler.setFormatter(HumanReadableFormatter(level))
        self.stdout_handler.addFilter(RequestContextFilter())

    def emit(self, record):
        # Use handle() so per-handler filters (RequestContextFilter) are applied.
        self.stdout_handler.handle(record)


def configure_logging(app):
    """Configure Flask app logging based on FLASK_LOG_LEVEL environment variable."""

    # Remove default Flask handlers
    app.logger.handlers = []

    # Determine Python log level
    if _LOG_LEVEL == DEBUG:
        python_level = logging.DEBUG
    else:
        python_level = logging.INFO

    # Create and add stdout handler
    handler = StdoutLogHandler(_LOG_LEVEL)
    handler.setLevel(python_level)

    app.logger.addHandler(handler)
    app.logger.setLevel(python_level)
    app.logger.propagate = False

    # Also configure root logger for libraries
    logging.basicConfig(
        level=python_level,
        format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Suppress noisy loggers
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)

    # Log startup
    app.logger.info(
        "Logging configured: level=%s slow_query_threshold=%dms",
        _LOG_LEVEL, SLOW_QUERY_THRESHOLD_MS
    )


# Global stats for periodic reporting (STANDARD level)
_stats = {
    'requests': 0,
    'slow_queries': 0,
    'errors': 0,
    'rls_context_errors': 0,
    'last_report': time.time()
}


def increment_stat(stat_name: str, value: int = 1):
    """Increment a global stat counter."""
    if stat_name in _stats:
        _stats[stat_name] += value


def get_stats() -> Dict[str, Any]:
    """Get current stats snapshot."""
    return _stats.copy()


def reset_stats():
    """Reset stats (after reporting)."""
    _stats['requests'] = 0
    _stats['slow_queries'] = 0
    _stats['errors'] = 0
    _stats['rls_context_errors'] = 0
    _stats['last_report'] = time.time()
