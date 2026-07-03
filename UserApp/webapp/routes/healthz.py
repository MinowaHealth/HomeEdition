"""GET /api/v1/healthz — anonymous subsystem health probe.

Returns 200 when all *critical* subsystem checks pass, 503 when a critical
check fails. Only the database is critical; embedding_upstream (Ollama) is
best-effort and reports ok:false in the body without failing the probe.
Response body shape is identical for both status codes — clients should
parse `checks.*` rather than rely on status alone.
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from threading import Lock

import httpx
from flask import Blueprint, jsonify

from db_manager import get_admin_connection

logger = logging.getLogger(__name__)

bp = Blueprint('healthz', __name__)

SERVICE_NAME = 'minowa-api'
APP_VERSION = os.getenv('APP_VERSION', 'unknown')
ENVIRONMENT = os.getenv('DEPLOY_ENV', 'pilot')
_PROCESS_START = time.monotonic()

OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://host.docker.internal:11434')
EMBEDDING_MODEL = os.getenv('EMBEDDING_MODEL', 'nomic-embed-text-v2-moe:latest')

_RESPONSE_CACHE_TTL = 3.0
_response_cache: dict = {'expires_at': 0.0, 'status_code': 200, 'body': None}
_cache_lock = Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _short_err(exc: BaseException) -> str:
    msg = str(exc).replace('\n', ' ')[:120]
    return f'{type(exc).__name__}: {msg}' if msg else type(exc).__name__


def _check_database() -> dict:
    started = time.perf_counter()
    try:
        with get_admin_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute('SELECT 1')
                cur.fetchone()
            finally:
                cur.close()
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return {'ok': True, 'latency_ms': latency_ms}
    except Exception as exc:
        return {'ok': False, 'error': _short_err(exc)}


def _check_embedding_upstream() -> dict:
    """Probes Ollama and verifies the configured embedding model is loaded."""
    try:
        resp = httpx.get(f'{OLLAMA_URL}/api/tags', timeout=1.0)
        resp.raise_for_status()
        data = resp.json()
        models = {m.get('name', '') for m in data.get('models', [])}
        if EMBEDDING_MODEL not in models:
            return {
                'ok': False,
                'error': f'embedding model {EMBEDDING_MODEL!r} not loaded in Ollama',
            }
        return {'ok': True}
    except Exception as exc:
        return {'ok': False, 'error': _short_err(exc)}


# (check_fn, timeout_s, critical). Only critical checks drive the overall
# 200/503. embedding_upstream is non-critical: Home Edition embedding is
# best-effort (an unreachable Ollama never blocks a write — see CLAUDE.md), so
# a degraded Ollama reports ok:false in the body but does NOT mark the box
# unhealthy. Clients parse checks.* for degradation; the status code reflects
# only hard dependencies.
_CHECKS = {
    'database':           (_check_database,           0.5, True),
    'embedding_upstream': (_check_embedding_upstream, 1.2, False),
}


def _run_all_checks() -> tuple[int, dict]:
    results: dict = {}
    with ThreadPoolExecutor(max_workers=len(_CHECKS)) as pool:
        futures = {name: pool.submit(fn) for name, (fn, _, _) in _CHECKS.items()}
        for name, future in futures.items():
            _, timeout, _ = _CHECKS[name]
            try:
                results[name] = future.result(timeout=timeout)
            except FutureTimeout:
                results[name] = {'ok': False, 'error': f'check timed out (>{timeout}s)'}
            except Exception as exc:
                results[name] = {'ok': False, 'error': _short_err(exc)}

    all_ok = all(
        results[name].get('ok')
        for name, (_, _, critical) in _CHECKS.items()
        if critical
    )
    body = {
        'service': SERVICE_NAME,
        'version': APP_VERSION,
        'environment': ENVIRONMENT,
        'server_time': _utc_now_iso(),
        'uptime_seconds': int(time.monotonic() - _PROCESS_START),
        'checks': results,
    }
    return (200 if all_ok else 503), body


@bp.route('/api/v1/healthz', methods=['GET'])
def healthz():
    now = time.monotonic()
    with _cache_lock:
        if now < _response_cache['expires_at'] and _response_cache['body'] is not None:
            return jsonify(_response_cache['body']), _response_cache['status_code']

    status_code, body = _run_all_checks()

    with _cache_lock:
        _response_cache['expires_at'] = time.monotonic() + _RESPONSE_CACHE_TTL
        _response_cache['status_code'] = status_code
        _response_cache['body'] = body

    if status_code != 200:
        failed = [name for name, c in body['checks'].items() if not c.get('ok')]
        logger.warning('healthz degraded: failed=%s', failed)

    return jsonify(body), status_code
