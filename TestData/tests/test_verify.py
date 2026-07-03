from unittest.mock import MagicMock
from TestData.three_month_seed.verify import run_verification
from TestData.three_month_seed.embeddings import EMBEDDING_COLUMNS

_N_EMB = len(EMBEDDING_COLUMNS)


def _conn(cohort_users, health_metrics, emb_pairs):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    # Order matches run_verification:
    #  1. cohort users count (single query — no providers in Home Edition)
    #  2. health_metrics count
    #  3. for each EMBEDDING_COLUMNS entry: (row_count, null_count)
    cur.fetchone.side_effect = [
        (cohort_users,), (health_metrics,),
    ] + emb_pairs
    return conn


def test_passes_on_clean_state():
    conn = _conn(6, 18247, [(10,), (0,)] * _N_EMB)
    report = run_verification(conn)
    assert report.all_pass()
    assert "health_metrics" in str(list(report.assertions.keys()))


def test_fails_on_low_health_metrics():
    conn = _conn(6, 5, [(10,), (0,)] * _N_EMB)
    report = run_verification(conn)
    assert not report.all_pass()
    failures = [name for name, a in report.assertions.items() if not a.passed]
    assert any("health_metrics" in f for f in failures)


def test_fails_on_wrong_cohort_size():
    conn = _conn(17, 18247, [(10,), (0,)] * _N_EMB)
    report = run_verification(conn)
    assert not report.all_pass()


def test_fails_on_null_embeddings():
    conn = _conn(6, 18247, [(10,), (5,)] * _N_EMB)
    report = run_verification(conn)
    assert not report.all_pass()


def test_zero_row_table_passes_with_warn():
    conn = _conn(6, 18247, [(0,), (0,)] * _N_EMB)
    report = run_verification(conn)
    assert report.all_pass()
    for name, a in report.assertions.items():
        if "Embeddings" in name:
            assert "no rows" in a.detail.lower() or "0 rows" in a.detail.lower()
