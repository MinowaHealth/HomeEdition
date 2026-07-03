"""Healthz critical-vs-best-effort status mapping.

Guards the Home Edition contract: a degraded embedding upstream (Ollama) must
NOT mark the box unhealthy — only a hard dependency (database) drives a 503.
The test reuses the real `_CHECKS` criticality flags and stubs only the check
functions, so flipping embedding_upstream back to critical fails the test.
"""
from unittest.mock import patch

from routes import healthz


def _checks_returning(db_ok: bool, emb_ok: bool) -> dict:
    """Real _CHECKS structure (timeouts + criticality preserved), stub fns."""
    real = healthz._CHECKS
    return {
        'database': (lambda: {'ok': db_ok}, real['database'][1], real['database'][2]),
        'embedding_upstream': (
            lambda: {'ok': emb_ok}, real['embedding_upstream'][1], real['embedding_upstream'][2],
        ),
    }


def _status(db_ok: bool, emb_ok: bool):
    with patch.object(healthz, '_CHECKS', _checks_returning(db_ok, emb_ok)):
        return healthz._run_all_checks()


def test_all_up_is_200():
    code, body = _status(db_ok=True, emb_ok=True)
    assert code == 200
    assert body['checks']['database']['ok'] is True
    assert body['checks']['embedding_upstream']['ok'] is True


def test_ollama_down_stays_200_but_reports_degraded():
    code, body = _status(db_ok=True, emb_ok=False)
    assert code == 200, 'embedding is best-effort — Ollama down must not 503'
    assert body['checks']['embedding_upstream']['ok'] is False


def test_database_down_is_503():
    code, _ = _status(db_ok=False, emb_ok=True)
    assert code == 503, 'database is the hard dependency — its failure must 503'


if __name__ == '__main__':
    assert _status(True, True)[0] == 200
    assert _status(True, False)[0] == 200
    assert _status(False, True)[0] == 503
    print('healthz critical-check mapping OK')
