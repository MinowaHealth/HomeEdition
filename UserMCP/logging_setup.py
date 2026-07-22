"""
Structured JSON logging for UserMCP.

Outputs JSON to stdout when running in Docker (detected via /.dockerenv),
human-readable format for local development. Matches the same schema as
UserApp and ProviderApp for uniform Loki queries.

When LOKI_URL is set (e.g. http://localhost:3100), logs are also pushed
directly to Loki's HTTP API — this enables the Grafana dashboard for
local dev where there's no Promtail.

JSON schema:
  {"ts": "...", "level": "INFO", "service": "usermcp",
   "logger": "mcp_server", "message": "...", "rid": "abc12345"}
"""

import json
import logging
import os
import time
import uuid
import contextvars
import urllib.request
import urllib.error
import urllib.parse
import threading
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
    """Formats log records as single-line JSON for Loki ingestion."""

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

        # Trace-to-log correlation: when a span is active, stamp its IDs so
        # Grafana can jump from a Loki log row to the matching Tempo trace.
        # Imported lazily; degrades silently if OTel isn't installed.
        try:
            from opentelemetry import trace
            span = trace.get_current_span()
            ctx = span.get_span_context()
            if ctx and ctx.trace_id:
                entry['trace_id'] = format(ctx.trace_id, '032x')
                entry['span_id'] = format(ctx.span_id, '016x')
        except Exception:
            pass

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


class LokiHandler(logging.Handler):
    """Push log entries directly to Loki's HTTP API.

    Uses urllib (not requests) to avoid logging recursion since
    the requests library itself emits log messages.

    Batches entries and flushes every 2 seconds or 50 entries to
    reduce network overhead for local dev.
    """

    FLUSH_INTERVAL = 2.0  # seconds
    BATCH_SIZE = 50

    def __init__(self, loki_url: str, service: str, level: int = logging.NOTSET):
        super().__init__(level)
        scheme = urllib.parse.urlparse(loki_url).scheme.lower()
        if scheme not in ('http', 'https'):
            raise ValueError(
                f"LOKI_URL must use http or https scheme (got {scheme!r}): {loki_url!r}"
            )
        self.push_url = loki_url.rstrip('/') + '/loki/api/v1/push'
        self.service = service
        self.setFormatter(JSONFormatter(service))
        self._buffer: list[tuple[str, str]] = []  # (nano_ts, json_line)
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._start_timer()

    def _start_timer(self) -> None:
        self._timer = threading.Timer(self.FLUSH_INTERVAL, self._flush_timer)
        self._timer.daemon = True
        self._timer.start()

    def _flush_timer(self) -> None:
        self._flush()
        self._start_timer()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            nano_ts = str(int(time.time() * 1e9))
            with self._lock:
                self._buffer.append((nano_ts, line))
                if len(self._buffer) >= self.BATCH_SIZE:
                    self._flush_locked()
        except Exception:
            self.handleError(record)

    def _flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        """Flush buffer to Loki. Must be called with self._lock held."""
        if not self._buffer:
            return
        entries = self._buffer[:]
        self._buffer.clear()

        payload = json.dumps({
            "streams": [{
                "stream": {"service": self.service, "job": "dev"},
                "values": entries
            }]
        }).encode()

        req = urllib.request.Request(  # noqa: S310 — scheme validated http/https in __init__
            self.push_url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        try:
            # push_url scheme is validated to be http/https in __init__
            urllib.request.urlopen(req, timeout=2)  # nosec B310  # noqa: S310
        except (urllib.error.URLError, OSError):
            pass  # Don't block on log shipping failures

    def close(self) -> None:
        if self._timer:
            self._timer.cancel()
        self._flush()
        super().close()


def configure_logging(service: str) -> None:
    """
    Configure root logger for structured output.

    Console: JSON in Docker, human-readable locally.
    Loki: When LOKI_URL env var is set, also pushes to Loki HTTP API.

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

    # Loki push handler (for local dev with docker-compose.dev.yml)
    loki_url = os.getenv('LOKI_URL', '')
    if loki_url:
        loki = LokiHandler(loki_url, service, level=level)
        root.addHandler(loki)
        # Use print to avoid recursion on first log
        print(f"[{service}] Loki push enabled → {loki_url}")


def new_request_id() -> str:
    """Generate a short request ID (8 hex chars)."""
    return uuid.uuid4().hex[:8]
