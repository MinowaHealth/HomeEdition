"""Contract tests for /api/v1/healthz.

Verifies the response shape and status-code contract that the HealthAI mobile
app and the CLI probe (`scripts/api-probe.py`) depend on. Test data is the
*real* /healthz response from the configured base URL — these are
black-box assertions on a live endpoint, not in-process unit tests.

Run:
    cd UserApp/tests && RUN_INTEGRATION_TESTS=1 pytest test_healthz_contract.py -v

Configure target via tests/config.toml:
    [server]
    base_url = "https://localhost"   # or http://localhost:80 for local

Status semantics: 200 when every check is ok; 503 when any check is not ok.
The body shape is identical for both, so the assertions below run regardless.
"""

import re

import pytest
import httpx


HEALTHZ_PATH = '/api/v1/healthz'

REQUIRED_TOP_FIELDS = {
    'service',
    'version',
    'environment',
    'server_time',
    'uptime_seconds',
    'checks',
}

REQUIRED_CHECK_NAMES = {
    'database',
    'embedding_upstream',
}

ISO_UTC_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')


@pytest.fixture(scope='module')
def healthz_response(config):
    """Hit /api/v1/healthz once and reuse the response across assertions."""
    base = config['server']['base_url'].rstrip('/')  # type: ignore[index]
    resp = httpx.get(f'{base}{HEALTHZ_PATH}', timeout=5)
    return resp


def test_status_code_is_200_or_503(healthz_response):
    """No other status code is valid for this endpoint."""
    assert healthz_response.status_code in (200, 503), (
        f'expected 200 or 503, got {healthz_response.status_code}'
    )


def test_response_is_json(healthz_response):
    assert healthz_response.headers.get('content-type', '').startswith('application/json'), (
        f'content-type should be JSON, got {healthz_response.headers.get("content-type")!r}'
    )
    healthz_response.json()


def test_top_level_fields_present(healthz_response):
    body = healthz_response.json()
    missing = REQUIRED_TOP_FIELDS - set(body.keys())
    assert not missing, f'missing required top-level fields: {missing}'


def test_service_identifier(healthz_response):
    body = healthz_response.json()
    assert body['service'] == 'minowa-api', (
        f'service should be "minowa-api", got {body["service"]!r} — '
        f'a CDN/proxy error page might be impersonating /healthz'
    )


def test_version_is_present(healthz_response):
    """Version should be a git short SHA or 'unknown'; never empty."""
    body = healthz_response.json()
    assert isinstance(body['version'], str) and body['version'], (
        'version must be a non-empty string'
    )


def test_environment_label(healthz_response):
    body = healthz_response.json()
    assert body['environment'] in ('prod', 'pilot', 'testing', 'dev'), (
        f'unexpected environment label: {body["environment"]!r}'
    )


def test_server_time_is_iso_utc(healthz_response):
    body = healthz_response.json()
    assert ISO_UTC_PATTERN.match(body['server_time']), (
        f'server_time must be ISO 8601 UTC (YYYY-MM-DDTHH:MM:SSZ), '
        f'got {body["server_time"]!r}'
    )


def test_uptime_is_non_negative_int(healthz_response):
    body = healthz_response.json()
    assert isinstance(body['uptime_seconds'], int) and body['uptime_seconds'] >= 0


def test_all_required_subsystem_checks_present(healthz_response):
    body = healthz_response.json()
    missing = REQUIRED_CHECK_NAMES - set(body['checks'].keys())
    assert not missing, f'missing required subsystem checks: {missing}'


def test_each_check_has_boolean_ok(healthz_response):
    body = healthz_response.json()
    for name, check in body['checks'].items():
        assert isinstance(check.get('ok'), bool), (
            f'checks.{name}.ok must be a boolean, got {check.get("ok")!r}'
        )


def test_failing_checks_carry_error_field(healthz_response):
    body = healthz_response.json()
    for name, check in body['checks'].items():
        if not check.get('ok'):
            assert 'error' in check and isinstance(check['error'], str) and check['error'], (
                f'failing check {name!r} must carry a non-empty `error` field'
            )


def test_status_code_matches_check_state(healthz_response):
    """200 ⇔ all checks ok; 503 ⇔ at least one check failed."""
    body = healthz_response.json()
    any_failed = any(not c.get('ok') for c in body['checks'].values())
    if any_failed:
        assert healthz_response.status_code == 503, (
            'at least one subsystem check failed but status was not 503'
        )
    else:
        assert healthz_response.status_code == 200, (
            'all subsystem checks passed but status was not 200'
        )


def test_endpoint_is_unauthenticated(config):
    """Confirm /healthz works without any auth header — critical for diagnosing
    auth-subsystem failures and for the CLI probe / mobile dev-settings flow."""
    base = config['server']['base_url'].rstrip('/')
    resp = httpx.get(f'{base}{HEALTHZ_PATH}', timeout=5)
    assert resp.status_code in (200, 503), (
        f'unauthenticated request returned {resp.status_code} — '
        f'endpoint must not require auth'
    )


def test_response_is_fast(healthz_response):
    """Per-check timeouts cap at ~1.2s, plus orchestration overhead; healthz
    should respond well under 3s even when subsystems are slow."""
    assert healthz_response.elapsed.total_seconds() < 3.0, (
        f'healthz took {healthz_response.elapsed.total_seconds():.2f}s — '
        f'expected <3s (per-check timeouts bound worst case)'
    )
