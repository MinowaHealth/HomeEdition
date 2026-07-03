from unittest.mock import MagicMock
from TestData.three_month_seed.embeddings import (
    EMBEDDING_COLUMNS, dedupe_strings, fill_embeddings,
)

def test_embedding_columns_enumerated():
    # health_conversations removed (Home Edition: AI-assisted diagnostics
    # come via the MCP server, not an in-app conversations feature).
    assert len(EMBEDDING_COLUMNS) == 8
    for table, pk_col, text_col, vec_col in EMBEDDING_COLUMNS:
        assert all(isinstance(s, str) for s in (table, pk_col, text_col, vec_col))

def test_dedupe_strings():
    rows = [(1, "hello"), (2, "world"), (3, "hello")]
    unique = dedupe_strings(rows)
    assert sorted(unique) == ["hello", "world"]

def test_fill_embeddings_calls_get_embedding_once_per_unique(monkeypatch):
    """Batched: one Ollama call per unique source string, not per row."""
    fake_get = MagicMock(return_value=[0.0] * 768)
    monkeypatch.setattr(
        "TestData.three_month_seed.embeddings.get_embedding", fake_get
    )
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchall.side_effect = [
        [(1, "alpha"), (2, "beta"), (3, "alpha")],
    ] + [[]] * (len(EMBEDDING_COLUMNS) - 1)
    fill_embeddings(conn)
    assert fake_get.call_count == 2
