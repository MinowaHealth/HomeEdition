"""
Structured JSON logging for UserMCP.

Outputs JSON to stdout when running in Docker (detected via /.dockerenv),
human-readable format for local development. Matches the same JSON schema
as UserApp for uniform log queries (read with `docker compose logs`).

JSON schema:
  {"ts": "...", "level": "INFO", "service": "usermcp",
   "logger": "mcp_server", "message": "...", "rid": "abc12345"}
"""

import json
import logging
import os
import uuid
import contextvars
from datetime import datetime, timezone
from pathlib import Path

# Per-request ID, set by handle_sse / handle_messages
request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    'request_id', default=''
)


def _is_docker() -> bool:
    """Detect if running inside a Docker container."""
    return Path('/.dockerenv').exists()


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON for stdout."""

    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'level': record.levelname,
            'service': self.service,
            'logger': record.name,
            'message': record.getMessage(),
        }

        rid = request_id.get('')
        if rid:
            entry['rid'] = rid

        # Include extra fields passed via logger.info("msg", extra={...})
        for key in ('duration_ms', 'tool', 'patient_id', 'endpoint',
                    'method', 'status_code'):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val

        if record.exc_info and record.exc_info[0] is not None:
            entry['exception'] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


class HumanFormatter(logging.Formatter):
    """Readable format for local terminal development."""

    def __init__(self, service: str):
        super().__init__(
            fmt=f'%(asctime)s [{service}:%(name)s] %(levelname)s: %(message)s'
        )


def configure_logging(service: str) -> None:
    """
    Configure root logger for structured output.

    Console: JSON in Docker, human-readable locally.

    Args:
        service: Service name embedded in every log line (e.g. 'usermcp')
    """
    level_name = os.getenv('UVICORN_LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers (prevents duplicate output)
    root.handlers.clear()

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(level)
    if _is_docker():
        console.setFormatter(JSONFormatter(service))
    else:
        console.setFormatter(HumanFormatter(service))
    root.addHandler(console)


def new_request_id() -> str:
    """Generate a short request ID (8 hex chars)."""
    return uuid.uuid4().hex[:8]
